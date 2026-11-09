"""NovaProxy CLI application: REPL + one-shot modes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from .api import ApiError, NovaClient, StreamAccumulator
from .config import ConfigStore, key_format_ok, prompt_for_key
from .hud import B, D, R, Hud, art, boot, c, copy_to_clipboard, write
from .mcp import McpManager
from .prompts import PROMPTS, normalize_mode, system_prompt_for
from .render import render_to_string
from .tools import (
    BUILTIN_TOOLS,
    dispatch_builtin,
    expand_at_references,
    git_diff_stat,
)

HELP = [
    ("/help", "список команд"),
    ("/new", "новая сессия"),
    ("/clear", "очистить чат"),
    ("/reset", "сброс контекста"),
    ("/compact", "сжать контекст"),
    ("/model", "сменить модель"),
    ("/models", "список моделей"),
    ("/mode", "режим: code/chat/reasoning"),
    ("/system", "системный промпт"),
    ("/mcp", "MCP-серверы"),
    ("/tools", "инструменты вкл/выкл"),
    ("/key", "сменить API-ключ"),
    ("/balance", "баланс ключа"),
    ("/cost", "токены сессии"),
    ("/status", "инфо о сессии"),
    ("/history", "все запросы"),
    ("/sessions", "сохранённые сессии"),
    ("/resume", "вернуться в сессию"),
    ("/sidebar", "панель вкл/выкл"),
    ("/theme", "сменить цвет"),
    ("/copy", "ответ в буфер"),
    ("/copycode", "последний блок кода в буфер"),
    ("/diff", "git diff --stat"),
    ("/init", "создать NOVA.md"),
    ("/doctor", "проверка окружения"),
    ("/export", "чат в файл"),
    ("/exit", "выход"),
]
EXTRA = [
    ("!cmd", "выполнить команду в shell"),
    ("@file", "прикрепить файл к запросу"),
    ("Tab / ↑↓", "автодополнение / история ввода"),
    ("/ или Enter", "меню команд"),
]
CMDS = [h[0] for h in HELP] + ["/quit"]
THEMES = [141, 114, 203, 39, 221, 208]


class App:
    def __init__(self, cfg: ConfigStore, client: NovaClient, args: argparse.Namespace) -> None:
        self.cfg = cfg
        self.client = client
        self.args = args
        self.models: List[str] = []
        self.mcp = McpManager(cfg.mcp_servers)
        self.tools_enabled = True
        self.messages: List[Dict[str, Any]] = []
        self.S: Dict[str, Any] = {
            "model": cfg.model,
            "sess": 1,
            "name": "untitled",
            "reqs": 0,
            "tin": 0,
            "tout": 0,
            "calls": [],
            "hist": [],
            "chat": [],
            "side": bool(cfg.data.get("sidebar", True)),
            "t0": time.time(),
            "saved": {},
            "ttft": None,
            "tps": None,
        }
        self.acc = int(cfg.data.get("theme", 141))
        self.hud = Hud(self.S, accent=self.acc)
        self._reset_messages()

    # ------------------------------------------------------------- messages
    def _reset_messages(self) -> None:
        sp = system_prompt_for(self.cfg.mode, self.cfg.system_prompt)
        self.messages = [{"role": "system", "content": sp}]

    def _all_tools(self) -> List[Dict[str, Any]]:
        if not self.tools_enabled:
            return []
        return BUILTIN_TOOLS + self.mcp.openai_tools()

    def _run_tool_call(self, name: str, arguments: str) -> str:
        if name.startswith("mcp__"):
            return self.mcp.dispatch(name, arguments)
        return dispatch_builtin(name, arguments)

    # ------------------------------------------------------------ streaming
    def ask(self, prompt: str, on_status=None, on_token=None) -> str:
        """Send the prompt, handle tool calls, stream the reply. Returns text."""
        expanded, attached = expand_at_references(prompt)
        if on_token is None:
            for path, lines in attached:
                self.hud.call("read_file", path, 5)
                self.hud.say(f"{lines} строк прикреплено ({path})")

        self.S["reqs"] += 1
        self.S["tin"] += max(1, int(len(expanded.split()) * 1.4))
        self.S["hist"].append((time.strftime("%H:%M"), prompt))
        if self.S["name"] == "untitled":
            self.S["name"] = prompt[:20]
        if on_token is None:
            self.S["chat"].append(("user", prompt))
        self.messages.append({"role": "user", "content": expanded})

        final_text = ""
        for _round in range(8):  # tool-call loop guard
            acc = StreamAccumulator()
            spin = 0
            tools = self._all_tools()
            effort = self.args.reasoning if getattr(self.args, "reasoning", None) else None
            try:
                stream = self.client.chat_stream(
                    self.messages, self.S["model"], tools=tools or None, reasoning_effort=effort
                )
                for event in stream:
                    piece = acc.feed(event)
                    if piece:
                        final_text += piece if acc.tool_calls == {} else ""
                        if on_token:
                            on_token(piece)
                        else:
                            # update last chat line live
                            if self.S["chat"] and self.S["chat"][-1][0] == "ai" and self.S["chat"][-1][1].endswith("…"):
                                pass
                            if not self.S["chat"] or self.S["chat"][-1][0] != "ai":
                                self.S["chat"].append(("ai", ""))
                            cur = self.S["chat"][-1][1]
                            self.S["chat"][-1] = ("ai", cur + piece)
                            self.S["tout"] += 1
                            self.hud.frame("stream")
                    elif on_token is None:
                        if on_status:
                            on_status(self.hud.spinner(spin, "thinking"))
                        else:
                            self.hud.frame(self.hud.spinner(spin, "thinking"))
                        spin += 1
            except ApiError as exc:
                err_line = "ошибка API: " + str(exc)
                if on_token is not None:
                    sys.stderr.write(err_line + "\n")
                else:
                    self.hud.say(err_line)
                self.messages.pop()  # drop the unanswered user message
                return ""

            msg = acc.assistant_message()
            self.messages.append(msg)
            if acc.usage:
                self.S["tin"] = self.S["tin"] - max(1, int(len(expanded.split()) * 1.4)) + int(acc.usage.get("prompt_tokens", 0))
                self.S["tout"] = int(acc.usage.get("completion_tokens", self.S["tout"]))
            if acc.ttft is not None:
                self.S["ttft"] = acc.ttft
            elapsed = time.monotonic() - acc.started
            out_tokens = (acc.usage or {}).get("completion_tokens") or len(acc.content_parts)
            if elapsed > 0 and out_tokens:
                self.S["tps"] = out_tokens / elapsed

            if not acc.tool_calls:
                if on_token is None and (not self.S["chat"] or self.S["chat"][-1][0] != "ai"):
                    self.S["chat"].append(("ai", acc.text))
                final_text = acc.text
                break

            # Execute tool calls, then continue the conversation.
            final_text = ""
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                t0 = time.time()
                result = self._run_tool_call(fn.get("name", ""), fn.get("arguments", ""))
                ms = int((time.time() - t0) * 1000)
                if on_token is None:
                    self.hud.call(fn.get("name", "tool"), (fn.get("arguments", "") or "")[:30], ms)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "call_0",
                    "content": result,
                })
            if on_token is None:
                self.hud.frame("tools")
        return final_text

    # -------------------------------------------------------------- one-shot
    def oneshot(self, prompt: str) -> int:
        def on_token(piece: str) -> None:
            sys.stdout.write(piece)
            sys.stdout.flush()

        text = self.ask(prompt, on_token=on_token)
        sys.stdout.write("\n")
        meta = f"\n{D}— {self.S['model']} · ttft {self.S['ttft'] or 0:.2f}s · {self.S['tps'] or 0:.1f} tok/s · in/out {self.S['tin']}/{self.S['tout']}{R}\n"
        if not self.args.quiet:
            sys.stderr.write(meta)
        return 0 if text else 1

    # -------------------------------------------------------------- commands
    def handle(self, p: str) -> bool:
        a = p.split(maxsplit=1)
        cm = a[0].lower()
        arg = a[1] if len(a) > 1 else ""
        self.S["hist"].append((time.strftime("%H:%M"), p))
        say = self.hud.say

        if cm in ("/exit", "/quit"):
            return False
        if cm == "/help":
            self.hud.page([c(self.acc) + B + "КОМАНДЫ" + R] + [c(self.acc) + k.ljust(13) + R + v for k, v in HELP + EXTRA])
        elif cm == "/new":
            self.S["saved"][self.S["sess"]] = {"name": self.S["name"], "chat": self.S["chat"], "messages": self.messages}
            self.S["sess"] = max(list(self.S["saved"]) + [self.S["sess"]]) + 1
            self.S["name"] = "untitled"
            self.S["chat"] = []
            self._reset_messages()
        elif cm in ("/clear", "/reset"):
            self.S["chat"] = []
            self.S["tin"] = self.S["tout"] = 0
            self._reset_messages()
        elif cm == "/compact":
            summary_text = "\n".join(f"{r}: {t}" for r, t in self.S["chat"][-20:])
            self.S["chat"] = [("sys", "— контекст сжат —")]
            self._reset_messages()
            self.messages.append({"role": "user", "content": "Сводка предыдущего диалога:\n" + summary_text[:4000]})
            self.S["tin"] = int(self.S["tin"] * 0.3)
            self.S["tout"] = int(self.S["tout"] * 0.3)
        elif cm == "/model":
            if arg:
                self.S["model"] = arg
            else:
                models = self._models()
                j = self.hud.menu("Модель", [(m, "● текущая" if m == self.S["model"] else "") for m in models])
                if j >= 0:
                    self.S["model"] = models[j]
            self.cfg.model = self.S["model"]
            self.cfg.save()
            say("модель: " + self.S["model"])
        elif cm == "/models":
            models = self._models()
            self.hud.page([c(self.acc) + B + "МОДЕЛИ" + R] + [(c(114) + "● " if m == self.S["model"] else "  ") + m + R for m in models])
        elif cm == "/mode":
            if arg and normalize_mode(arg) in PROMPTS:
                self.cfg.mode = normalize_mode(arg)
                self.cfg.save()
                self._reset_messages_with_history()
                say("режим: " + self.cfg.mode)
            else:
                j = self.hud.menu("Режим", [(k, PROMPTS[k][:40] + "…") for k in PROMPTS])
                if j >= 0:
                    self.cfg.mode = list(PROMPTS)[j]
                    self.cfg.save()
                    self._reset_messages_with_history()
                    say("режим: " + self.cfg.mode)
        elif cm == "/system":
            if arg:
                self.cfg.system_prompt = arg
                self.cfg.save()
                self._reset_messages_with_history()
                say("системный промпт обновлён")
            else:
                cur = self.cfg.system_prompt or system_prompt_for(self.cfg.mode)
                self.hud.page([c(self.acc) + B + "SYSTEM PROMPT" + R, cur, "", D + "изменить: /system <текст> · сброс: /system reset" + R])
                if arg == "reset":
                    pass
            if arg.strip() == "reset":
                self.cfg.system_prompt = None
                self.cfg.save()
                self._reset_messages_with_history()
                say("системный промпт сброшен")
        elif cm == "/mcp":
            self._handle_mcp(arg)
        elif cm == "/tools":
            self.tools_enabled = not self.tools_enabled
            say("инструменты: " + ("вкл" if self.tools_enabled else "выкл"))
        elif cm in ("/key", "/auth"):
            self._change_key()
        elif cm == "/balance":
            self._show_balance()
        elif cm == "/cost":
            say("in %d · out %d · ttft %.2fs · %.1f tok/s" % (
                self.S["tin"], self.S["tout"], self.S.get("ttft") or 0.0, self.S.get("tps") or 0.0))
        elif cm == "/status":
            say("сессия #%d · %s · %s · режим %s · запросов %d" % (
                self.S["sess"], self.S["name"], self.S["model"], self.cfg.mode, self.S["reqs"]))
        elif cm == "/history":
            self.hud.page([c(self.acc) + B + "ВСЕ ЗАПРОСЫ" + R] + (
                ["%s%2d%s %s %s" % (D, i + 1, R, tm, t) for i, (tm, t) in enumerate(self.S["hist"])] or ["—"]))
        elif cm == "/sessions":
            self.hud.page([c(self.acc) + B + "СЕССИИ" + R] + (
                ["#%d %s (%d сообщ.)" % (k, v["name"], len(v["chat"])) for k, v in self.S["saved"].items()] or ["пока нет"]))
        elif cm == "/resume":
            self._resume(arg)
        elif cm == "/sidebar":
            self.S["side"] = not self.S["side"]
            self.cfg.data["sidebar"] = self.S["side"]
            self.cfg.save()
            if not self.S["side"]:
                from .hud import size as _size
                cols = _size().columns
                if cols < 76:
                    self.hud.page(self.hud.sidebar(cols - 6, "idle"))
        elif cm == "/theme":
            self.acc = THEMES[(THEMES.index(self.acc) + 1) % len(THEMES)] if self.acc in THEMES else THEMES[0]
            self.hud.acc = self.acc
            self.cfg.data["theme"] = self.acc
            self.cfg.save()
        elif cm == "/copy":
            last = [t for r, t in self.S["chat"] if r == "ai"]
            if not last:
                say("нет ответа для копирования")
            else:
                ok, tool = copy_to_clipboard(last[-1])
                say("скопировано (%s)" % tool if ok else "буфер обмена недоступен (termux-api / xclip / clip)")
        elif cm == "/copycode":
            import re
            last = [t for r, t in self.S["chat"] if r == "ai"]
            code = None
            if last:
                m = re.findall(r"```(?:\w+)?\n(.*?)```", last[-1], re.S)
                code = m[-1] if m else None
            if code is None:
                say("блок кода не найден")
            else:
                ok, tool = copy_to_clipboard(code)
                say("код скопирован (%s)" % tool if ok else "буфер обмена недоступен")
        elif cm == "/diff":
            self.hud.call("bash", "git diff --stat")
            say(*git_diff_stat().splitlines()[:10])
        elif cm == "/init":
            open("NOVA.md", "w", encoding="utf-8").write("# NovaProxy\n\nзаметки проекта\n")
            say("создан NOVA.md")
        elif cm == "/doctor":
            self._doctor()
        elif cm == "/export":
            fn = arg or "nova-chat.md"
            with open(fn, "w", encoding="utf-8") as f:
                f.write("\n\n".join("**%s**: %s" % (r, t) for r, t in self.S["chat"]))
            say("сохранено в " + fn)
        else:
            say("неизвестная команда: " + cm + " (/help)")
        return True

    def _reset_messages_with_history(self) -> None:
        history = [m for m in self.messages if m["role"] != "system"]
        self._reset_messages()
        self.messages.extend(history)

    def _models(self) -> List[str]:
        if not self.models:
            try:
                self.models = self.client.list_models()
            except ApiError as exc:
                self.hud.say("не удалось получить модели: " + str(exc))
                self.models = [self.S["model"]]
        return self.models

    def _change_key(self) -> None:
        write("\x1b[2J\x1b[H\n")
        def validate(k: str):
            return NovaClient(k).validate_key()
        try:
            key = prompt_for_key(validate)
        except SystemExit:
            return
        self.cfg.api_key = key
        self.cfg.save()
        self.client = NovaClient(key)
        self.models = []
        self.hud.say("ключ обновлён")

    def _show_balance(self) -> None:
        try:
            info = self.client.key_info()
        except ApiError as exc:
            self.hud.say("ошибка: " + str(exc))
            return
        lines = [
            c(self.acc) + B + "БАЛАНС КЛЮЧА" + R,
            "имя      " + str(info.get("name", "—")),
            "баланс   %s %s" % (info.get("balance", "—"), info.get("currency", "token")),
            "запросов %s" % info.get("request_count", "—"),
            "активен  %s" % ("да" if info.get("active") else "нет"),
            "истекает %s" % (info.get("expires_at") or "—"),
        ]
        models = info.get("allowed_models") or []
        if models:
            lines.append("модели   " + ", ".join(models[:8]) + (" …" if len(models) > 8 else ""))
        self.hud.page(lines)

    def _handle_mcp(self, arg: str) -> None:
        say = self.hud.say
        parts = arg.split(maxsplit=1) if arg else []
        sub = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if not sub:
            lines = [c(self.acc) + B + "MCP СЕРВЕРЫ" + R]
            status = self.mcp.status_lines()
            if not status:
                lines.append(D + "нет серверов · добавь: /mcp add <имя> <команда...>" + R)
            for name, st in status:
                lines.append("  " + c(114) + name.ljust(12) + R + st)
            lines += ["", D + "/mcp add <имя> <cmd> · /mcp del <имя> · /mcp tools" + R]
            self.hud.page(lines)
        elif sub == "add" and rest:
            name, _, cmdline = rest.partition(" ")
            if not cmdline:
                say("формат: /mcp add <имя> <команда> [args...]")
                return
            import shlex
            cmd_parts = shlex.split(cmdline)
            spec = {"command": cmd_parts[0], "args": cmd_parts[1:]}
            say("подключаю %s..." % name)
            ok, msg = self.mcp.add_server(name, spec)
            self.cfg.mcp_servers = self.mcp.specs
            self.cfg.save()
            say(("✓ " if ok else "✗ ") + name + ": " + msg)
        elif sub == "del" and rest:
            if self.mcp.remove_server(rest):
                self.cfg.mcp_servers = self.mcp.specs
                self.cfg.save()
                say("сервер %s удалён" % rest)
            else:
                say("сервер %s не найден" % rest)
        elif sub == "tools":
            tools = self.mcp.openai_tools()
            self.hud.page([c(self.acc) + B + "MCP ИНСТРУМЕНТЫ" + R] + (
                [t["function"]["name"] for t in tools] or ["нет"]))
        else:
            say("формат: /mcp · /mcp add <имя> <cmd> · /mcp del <имя> · /mcp tools")

    def _resume(self, arg: str) -> None:
        say = self.hud.say
        try:
            if arg:
                k = int(arg)
            else:
                ks = list(self.S["saved"])
                j = self.hud.menu("Сессия", [("#%d" % k, self.S["saved"][k]["name"]) for k in ks])
                if j < 0:
                    return
                k = ks[j]
            v = self.S["saved"].pop(k)
            self.S["saved"][self.S["sess"]] = {"name": self.S["name"], "chat": self.S["chat"], "messages": self.messages}
            self.S["sess"] = k
            self.S["name"] = v["name"]
            self.S["chat"] = v["chat"]
            self.messages = v.get("messages", self.messages)
        except Exception:
            say("нет сохранённых сессий (см. /sessions)")

    def _doctor(self) -> None:
        import platform
        from .hud import size as _size
        cl, rw = _size()
        termux = "да" if "com.termux" in os.environ.get("PREFIX", "") else "нет"
        ok, err = self.client.validate_key()
        self.hud.page([
            c(self.acc) + B + "DOCTOR" + R,
            "python   " + sys.version.split()[0],
            "platform " + platform.system().lower(),
            "term     %dx%d" % (cl, rw),
            "termux   " + termux,
            "key      " + ("ok" if ok else "ошибка: " + err),
            "model    " + self.S["model"],
            "mode     " + self.cfg.mode,
            "mcp      %d серверов" % len(self.mcp.servers),
        ])

    # ------------------------------------------------------------------ repl
    def repl(self) -> int:
        try:
            import readline as rl
            rl.set_completer(lambda t, s: ([x for x in CMDS if x.startswith(t)] + [None])[s])
            rl.set_completer_delims(" ")
            rl.parse_and_bind("tab: complete")
            prompt = "\001" + c(114) + B + "\002 ❯ \001" + R + "\002"
        except Exception:
            prompt = c(114) + B + " ❯ " + R

        if not self.args.no_boot:
            boot()

        if self.mcp.specs:
            self.mcp.connect_all(on_event=lambda n, s: self.hud.say("mcp %s: %s" % (n, s)))

        write("\x1b[2J")
        while True:
            self.hud.frame()
            try:
                p = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                break
            write(R)
            if p in ("", "/"):
                j = self.hud.menu("Команды", HELP)
                if j < 0:
                    write("\x1b[2J")
                    continue
                p = HELP[j][0]
                write("\x1b[2J")
            if p.startswith("/"):
                if not self.handle(p):
                    break
            elif p.startswith("!"):
                self._shell(p[1:])
            else:
                self.ask(p)
            write("\x1b[2J")
        write("\x1b[2J\x1b[H" + R)
        self.mcp.close_all()
        return 0

    def _shell(self, cmd: str) -> None:
        self.S["hist"].append((time.strftime("%H:%M"), "!" + cmd))
        self.S["reqs"] += 1
        self.S["chat"].append(("user", "!" + cmd))
        t = time.time()
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            out = (r.stdout + r.stderr).strip().splitlines()[:14]
        except Exception as e:
            out = [str(e)]
        self.S["calls"].append({"n": "bash", "a": cmd, "st": "ok", "ms": int((time.time() - t) * 1000)})
        self.hud.say(*(out or ["(пусто)"]))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novaproxy",
        description="NovaProxy CLI — интерактивный клиент для NovaProxy API",
    )
    p.add_argument("prompt", nargs="*", help="one-shot запрос (без входа в REPL)")
    p.add_argument("--model", "-m", help="модель (переопределяет сохранённую)")
    p.add_argument("--system", help="переопределить системный промпт")
    p.add_argument("--mode", choices=["code", "chat", "reasoning"], help="режим системного промпта")
    p.add_argument("--reasoning", choices=["low", "medium", "high", "xhigh", "max"],
                   help="reasoning_effort для gpt-6-astra")
    p.add_argument("--key", help="задать API-ключ px_live_... и выйти")
    p.add_argument("--auth", action="store_true", help="сменить API-ключ интерактивно")
    p.add_argument("--no-boot", action="store_true", help="пропустить заставку")
    p.add_argument("--no-stream", action="store_true", help="не использовать стриминг")
    p.add_argument("--quiet", "-q", action="store_true", help="не печатать мета-строку в one-shot")
    p.add_argument("--version", action="store_true", help="версия")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        from . import __version__
        print("novaproxy " + __version__)
        return 0

    cfg = ConfigStore()

    if args.key:
        if not key_format_ok(args.key):
            print("Ошибка: ключ должен начинаться с px_live_", file=sys.stderr)
            return 1
        ok, err = NovaClient(args.key).validate_key()
        if not ok:
            print("Ключ отклонён сервером: " + err, file=sys.stderr)
            return 1
        cfg.api_key = args.key
        cfg.save()
        print("Ключ сохранён в ~/.novaproxy/config.json")
        return 0

    if not cfg.api_key or args.auth:
        def validate(k: str):
            return NovaClient(k).validate_key()
        key = prompt_for_key(validate)
        cfg.api_key = key
        cfg.save()
        if args.auth:
            return 0

    if args.system:
        cfg.system_prompt = args.system
    if args.mode:
        cfg.mode = args.mode

    client = NovaClient(cfg.api_key)
    app = App(cfg, client, args)
    if args.model:
        app.S["model"] = args.model

    # one-shot: argv prompt or piped stdin
    piped = None
    if not sys.stdin.isatty() and not args.prompt:
        data = sys.stdin.read()
        if data.strip():
            piped = data.strip()

    if args.prompt or piped is not None:
        prompt = " ".join(args.prompt) if args.prompt else ""
        if piped:
            prompt = (prompt + "\n\n" + piped).strip() if prompt else piped
        return app.oneshot(prompt)

    return app.repl()


if __name__ == "__main__":
    raise SystemExit(main())
