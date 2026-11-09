"""NovaProxy CLI application: OpenCode-style REPL + one-shot modes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .api import ApiError, NovaClient, StreamAccumulator
from .config import ConfigStore, key_format_ok, prompt_for_key
from .hud import (
    B, I, R, THEMES, THEMES as _T, CancelListener, MUTED, PRIMARY, SECONDARY,
    Tui, copy_to_clipboard, fg, write, ERR,
)
from .mcp import McpManager
from .prompts import PROMPTS, normalize_mode, system_prompt_for
from .tools import BUILTIN_TOOLS, dispatch_builtin, expand_at_references, git_diff_stat

HELP = [
    ("/help", "список команд"),
    ("/init", "создать/обновить NOVA.md (анализ кодовой базы)"),
    ("/compact", "сжать сессию в сводку"),
    ("/new", "новая сессия"),
    ("/clear", "сброс контекста"),
    ("/model", "сменить модель"),
    ("/models", "список моделей"),
    ("/mode", "режим: code/chat/reasoning"),
    ("/system", "системный промпт"),
    ("/mcp", "MCP-серверы"),
    ("/tools", "инструменты вкл/выкл"),
    ("/permissions", "разрешения инструментов"),
    ("/key", "сменить API-ключ"),
    ("/balance", "баланс ключа"),
    ("/cost", "токены сессии"),
    ("/status", "инфо о сессии"),
    ("/history", "все запросы"),
    ("/sessions", "сохранённые сессии"),
    ("/resume", "вернуться в сессию"),
    ("/theme", "сменить тему"),
    ("/copy", "ответ в буфер"),
    ("/copycode", "последний блок кода в буфер"),
    ("/diff", "git diff --stat"),
    ("/doctor", "проверка окружения"),
    ("/export", "чат в файл"),
    ("/exit", "выход"),
]
EXTRA = [
    ("!cmd", "выполнить команду в shell"),
    ("@file", "прикрепить файл к запросу"),
    ("Tab / ↑↓", "автодополнение / история ввода"),
    ("esc", "отменить генерацию"),
]
CMDS = [h[0] for h in HELP] + ["/quit"]

CONTEXT_LIMIT = 128_000


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
            "hist": [],
            "chat": [],
            "t0": time.time(),
            "saved": {},
        }
        theme_map = dict(THEMES)
        theme_name = cfg.data.get("theme", "opencode")
        self.theme = theme_name if theme_name in theme_map else "opencode"
        self.tui = Tui(primary=theme_map[self.theme])
        self._reset_messages()

    # ------------------------------------------------------------- messages
    def _reset_messages(self) -> None:
        sp = system_prompt_for(self.cfg.mode, self.cfg.system_prompt)
        self.messages = [{"role": "system", "content": sp}]

    def _all_tools(self) -> List[Dict[str, Any]]:
        if not self.tools_enabled:
            return []
        disabled = set(self.cfg.disabled_tools)
        tools = [t for t in BUILTIN_TOOLS if t["function"]["name"] not in disabled]
        return tools + self.mcp.openai_tools()

    def _tool_risk(self, name: str, args: Dict[str, Any]) -> str:
        if name in ("write_file", "edit_file"):
            return "write"
        if name == "bash":
            return "bash"
        return "read"

    def _check_permission(self, name: str, arguments: str) -> bool:
        risk = self._tool_risk(name, json.loads(arguments or "{}"))
        mode = self.cfg.permissions.get(risk, "ask")
        if mode == "allow":
            return True
        if mode == "deny":
            return False
        if getattr(self, "_session_allow_" + risk, False):
            return True
        detail = arguments[:120].replace("\n", " ")
        idx = self.tui.confirm(
            "Permission Required",
            f"Tool: {name}\n{risk}: {detail}",
            ["Allow (a)", "Allow for session (s)", "Deny (d)"],
        )
        if idx == 1:
            setattr(self, "_session_allow_" + risk, True)
            return True
        return idx == 0

    def _run_tool_call(self, name: str, arguments: str) -> str:
        if name.startswith("mcp__"):
            return self.mcp.dispatch(name, arguments)
        return dispatch_builtin(name, arguments)

    # ------------------------------------------------------------ streaming
    def ask(self, prompt: str, on_status=None, on_token=None) -> str:
        interactive = on_token is None
        expanded, attached = expand_at_references(prompt)
        for path, lines in attached:
            if interactive:
                self.tui.print_tool_header("read_file", path)
                self.tui.print_tool_result("%d строк прикреплено" % lines)

        self.S["reqs"] += 1
        self.S["tin"] += max(1, int(len(expanded.split()) * 1.4))
        self.S["hist"].append((time.strftime("%H:%M"), prompt))
        if self.S["name"] == "untitled":
            self.S["name"] = prompt[:20]
        self.messages.append({"role": "user", "content": expanded})

        if interactive:
            write("\n")
            self.tui.print_user_message(prompt)

        final_text = ""
        t0 = time.monotonic()
        for _round in range(8):
            acc = StreamAccumulator()
            tools = self._all_tools()
            effort = getattr(self.args, "reasoning", None)
            listener = CancelListener() if interactive else None
            if listener:
                listener.start()
            canceled = False
            frame = 0
            try:
                stream = self.client.chat_stream(
                    self.messages, self.S["model"], tools=tools or None, reasoning_effort=effort
                )
                for event in stream:
                    if listener and listener.pressed:
                        canceled = True
                        break
                    piece = acc.feed(event)
                    if piece:
                        if on_token:
                            on_token(piece)
                        else:
                            write(piece)
                    elif interactive and not piece:
                        pass
                    frame += 1
            except ApiError as exc:
                err_line = "ошибка API: " + str(exc)
                if interactive:
                    write("\n")
                    self.tui.print_tool_result(err_line, is_error=True)
                    write("\n")
                else:
                    sys.stderr.write(err_line + "\n")
                self.messages.pop()
                return ""
            finally:
                if listener:
                    listener.stop()
                if interactive:
                    self.tui.clear_working()

            took = time.monotonic() - t0
            msg = acc.assistant_message()
            self.messages.append(msg)
            if acc.usage:
                self.S["tin"] = self.S["tin"] - max(1, int(len(expanded.split()) * 1.4)) + int(acc.usage.get("prompt_tokens", 0))
                self.S["tout"] = int(acc.usage.get("completion_tokens", self.S["tout"]))

            if not acc.tool_calls:
                final_text = acc.text
                if interactive:
                    write("\n\n")
                    self.tui.print_footer(self.S["model"], took, "canceled" if canceled else "")
                break

            final_text = ""
            if interactive:
                write("\n")
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "") or ""
                if interactive:
                    self.tui.print_tool_header(name, args)
                if not self._check_permission(name, args):
                    result = "error: permission denied by user"
                    if interactive:
                        self.tui.print_tool_result("Permission denied", is_error=True)
                else:
                    t1 = time.time()
                    result = self._run_tool_call(name, args)
                    ms = int((time.time() - t1) * 1000)
                    if interactive:
                        self.tui.print_tool_result(result, is_error=result.startswith("error"))
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "call_0",
                    "content": result,
                })
            t0 = time.monotonic()
        return final_text

    # -------------------------------------------------------------- one-shot
    def oneshot(self, prompt: str) -> int:
        def on_token(piece: str) -> None:
            sys.stdout.write(piece)
            sys.stdout.flush()

        text = self.ask(prompt, on_token=on_token)
        sys.stdout.write("\n")
        if not self.args.quiet:
            took_tokens = self.S["tout"]
            meta = "\n" + fg(MUTED) + "— %s · in/out %d/%d" % (
                self.S["model"], self.S["tin"], took_tokens) + R + "\n"
            sys.stderr.write(meta)
        return 0 if text else 1

    # -------------------------------------------------------------- commands
    def handle(self, p: str) -> bool:
        a = p.split(maxsplit=1)
        cm = a[0].lower()
        arg = a[1] if len(a) > 1 else ""
        self.S["hist"].append((time.strftime("%H:%M"), p))
        say = lambda *ls: [write("  " + fg(MUTED) + l + R + "\n") for l in ls]

        if cm in ("/exit", "/quit"):
            return False
        if cm == "/help":
            lines = [fg(SECONDARY) + k.ljust(14) + R + fg(MUTED) + v + R for k, v in HELP + EXTRA]
            self.tui.page("Команды", lines)
        elif cm == "/init":
            self._init_project()
        elif cm == "/compact":
            self._compact()
        elif cm == "/new":
            self.S["saved"][self.S["sess"]] = {"name": self.S["name"], "chat": self.S["chat"], "messages": self.messages}
            self.S["sess"] = max(list(self.S["saved"]) + [self.S["sess"]]) + 1
            self.S["name"] = "untitled"
            self.S["chat"] = []
            self._reset_messages()
            say("новая сессия #%d" % self.S["sess"])
        elif cm in ("/clear", "/reset"):
            self.S["chat"] = []
            self.S["tin"] = self.S["tout"] = 0
            self._reset_messages()
            say("контекст сброшен")
        elif cm == "/model":
            if arg:
                self.S["model"] = arg
            else:
                models = self._models()
                j = self.tui.dialog("Select Model", [(m, "current" if m == self.S["model"] else "") for m in models])
                if j >= 0:
                    self.S["model"] = models[j]
            self.cfg.model = self.S["model"]
            self.cfg.save()
            say("модель: " + self.S["model"])
        elif cm == "/models":
            models = self._models()
            self.tui.page("Модели", [("● " if m == self.S["model"] else "  ") + m for m in models])
        elif cm == "/mode":
            if arg and normalize_mode(arg) in PROMPTS:
                self.cfg.mode = normalize_mode(arg)
                self.cfg.save()
                self._reset_messages_with_history()
                say("режим: " + self.cfg.mode)
            else:
                j = self.tui.dialog("Mode", [(k, PROMPTS[k][:40] + "…") for k in PROMPTS])
                if j >= 0:
                    self.cfg.mode = list(PROMPTS)[j]
                    self.cfg.save()
                    self._reset_messages_with_history()
                    say("режим: " + self.cfg.mode)
        elif cm == "/system":
            if arg.strip() == "reset":
                self.cfg.system_prompt = None
                self.cfg.save()
                self._reset_messages_with_history()
                say("системный промпт сброшен")
            elif arg:
                self.cfg.system_prompt = arg
                self.cfg.save()
                self._reset_messages_with_history()
                say("системный промпт обновлён")
            else:
                cur = self.cfg.system_prompt or system_prompt_for(self.cfg.mode)
                self.tui.page("System prompt", [cur, "", "изменить: /system <текст> · сброс: /system reset"])
        elif cm == "/mcp":
            self._handle_mcp(arg)
        elif cm == "/tools":
            self._handle_tools(arg)
        elif cm == "/permissions":
            self._handle_permissions()
        elif cm in ("/key", "/auth"):
            self._change_key()
        elif cm == "/balance":
            self._show_balance()
        elif cm == "/cost":
            say("in %d · out %d" % (self.S["tin"], self.S["tout"]))
        elif cm == "/status":
            say("сессия #%d · %s · %s · режим %s · запросов %d" % (
                self.S["sess"], self.S["name"], self.S["model"], self.cfg.mode, self.S["reqs"]))
        elif cm == "/history":
            self.tui.page("Все запросы", ["%2d  %s %s" % (i + 1, tm, t) for i, (tm, t) in enumerate(self.S["hist"])] or ["—"])
        elif cm == "/sessions":
            self.tui.page("Сессии", ["#%d %s (%d сообщ.)" % (k, v["name"], len(v["chat"])) for k, v in self.S["saved"].items()] or ["пока нет"])
        elif cm == "/resume":
            self._resume(arg)
        elif cm == "/theme":
            names = [n for n, _ in THEMES]
            j = self.tui.dialog("Select Theme", [(n, "current" if n == self.theme else "") for n in names])
            if j >= 0:
                self.theme = names[j]
                self.tui.primary = dict(THEMES)[self.theme]
                self.cfg.data["theme"] = self.theme
                self.cfg.save()
                say("тема: " + self.theme)
        elif cm == "/copy":
            last = [t for r, t in self.S["chat"] if r == "ai"]
            if not last:
                say("нет ответа для копирования")
            else:
                ok, tool = copy_to_clipboard(last[-1])
                say("скопировано (%s)" % tool if ok else "буфер обмена недоступен (termux-api / xclip / clip)")
        elif cm == "/copycode":
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
            say(*git_diff_stat().splitlines()[:10])
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

    # --------------------------------------------------------- command impls
    def _reset_messages_with_history(self) -> None:
        history = [m for m in self.messages if m["role"] != "system"]
        self._reset_messages()
        self.messages.extend(history)

    def _models(self) -> List[str]:
        if not self.models:
            try:
                self.models = self.client.list_models()
            except ApiError:
                self.models = [self.S["model"]]
        return self.models

    def _init_project(self) -> None:
        prompt = (
            "Analyze this project and create a NOVA.md file (~20 lines) with: "
            "build/lint/test commands, code style guidelines, project structure notes. "
            "If NOVA.md exists, improve it. Use the write_file tool."
        )
        self.ask(prompt)

    def _compact(self) -> None:
        history = [m for m in self.messages if m["role"] != "system"]
        if not history:
            return
        text = "\n".join("%s: %s" % (m.get("role", "?"), str(m.get("content") or "")[:500]) for m in history[-30:])
        self._reset_messages()
        self.messages.append({"role": "user", "content": "Сводка предыдущего диалога:\n" + text[:6000]})
        self.S["tin"] = int(self.S["tin"] * 0.3)
        self.S["tout"] = int(self.S["tout"] * 0.3)
        write("  " + fg(MUTED) + "контекст сжат" + R + "\n")

    def _change_key(self) -> None:
        write("\n")
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
        write("  " + fg(MUTED) + "ключ обновлён" + R + "\n")

    def _show_balance(self) -> None:
        try:
            info = self.client.key_info()
        except ApiError as exc:
            self.tui.page("Баланс", ["ошибка: " + str(exc)])
            return
        lines = [
            "имя      " + str(info.get("name", "—")),
            "баланс   %s %s" % (info.get("balance", "—"), info.get("currency", "token")),
            "запросов %s" % info.get("request_count", "—"),
            "активен  %s" % ("да" if info.get("active") else "нет"),
            "истекает %s" % (info.get("expires_at") or "—"),
        ]
        models = info.get("allowed_models") or []
        if models:
            lines.append("модели   " + ", ".join(models[:8]) + (" …" if len(models) > 8 else ""))
        self.tui.page("Баланс ключа", lines)

    def _handle_mcp(self, arg: str) -> None:
        parts = arg.split(maxsplit=1) if arg else []
        sub = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        say = lambda *ls: [write("  " + fg(MUTED) + l + R + "\n") for l in ls]
        if not sub:
            lines = []
            status = self.mcp.status_lines()
            if not status:
                lines.append("нет серверов · добавь: /mcp add <имя> <команда...>")
            for name, st in status:
                lines.append(name.ljust(14) + st)
            lines += ["", "/mcp add <имя> <cmd> · /mcp del <имя> · /mcp tools"]
            self.tui.page("MCP серверы", lines)
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
            self.tui.page("MCP инструменты", [t["function"]["name"] for t in tools] or ["нет"])
        else:
            say("формат: /mcp · /mcp add <имя> <cmd> · /mcp del <имя> · /mcp tools")

    def _handle_tools(self, arg: str) -> None:
        say = lambda *ls: [write("  " + fg(MUTED) + l + R + "\n") for l in ls]
        if not arg:
            self.tools_enabled = not self.tools_enabled
            say("инструменты: " + ("вкл" if self.tools_enabled else "выкл"))
            return
        if arg == "list":
            disabled = set(self.cfg.disabled_tools)
            self.tui.page("Инструменты", [
                (("✓ " if t["function"]["name"] not in disabled else "✗ ") + t["function"]["name"])
                for t in BUILTIN_TOOLS
            ])
        elif arg.startswith("disable "):
            name = arg.split(None, 1)[1]
            d = list(self.cfg.disabled_tools)
            if name not in d:
                d.append(name)
            self.cfg.disabled_tools = d
            self.cfg.save()
            say("отключён: " + name)
        elif arg.startswith("enable "):
            name = arg.split(None, 1)[1]
            d = [x for x in self.cfg.disabled_tools if x != name]
            self.cfg.disabled_tools = d
            self.cfg.save()
            say("включён: " + name)
        else:
            say("/tools · /tools list · /tools disable <имя> · /tools enable <имя>")

    def _handle_permissions(self) -> None:
        cur = self.cfg.permissions
        j = self.tui.dialog("Permissions", [
            ("bash: " + cur.get("bash", "ask"), "выполнение shell-команд"),
            ("write: " + cur.get("write", "ask"), "запись и правка файлов"),
            ("сбросить", "вернуть ask для всего"),
        ])
        if j == 0:
            cur["bash"] = {"ask": "allow", "allow": "deny", "deny": "ask"}[cur.get("bash", "ask")]
        elif j == 1:
            cur["write"] = {"ask": "allow", "allow": "deny", "deny": "ask"}[cur.get("write", "ask")]
        elif j == 2:
            cur = {"bash": "ask", "write": "ask"}
        self.cfg.permissions = cur
        self.cfg.save()

    def _resume(self, arg: str) -> None:
        say = lambda *ls: [write("  " + fg(MUTED) + l + R + "\n") for l in ls]
        try:
            if arg:
                k = int(arg)
            else:
                ks = list(self.S["saved"])
                j = self.tui.dialog("Switch Session", [("#%d" % k, self.S["saved"][k]["name"]) for k in ks])
                if j < 0:
                    return
                k = ks[j]
            v = self.S["saved"].pop(k)
            self.S["saved"][self.S["sess"]] = {"name": self.S["name"], "chat": self.S["chat"], "messages": self.messages}
            self.S["sess"] = k
            self.S["name"] = v["name"]
            self.S["chat"] = v["chat"]
            self.messages = v.get("messages", self.messages)
            say("сессия #%d восстановлена" % k)
        except Exception:
            say("нет сохранённых сессий (см. /sessions)")

    def _doctor(self) -> None:
        import platform
        cols = os.get_terminal_size().columns if hasattr(os, "get_terminal_size") else 80
        termux = "да" if "com.termux" in os.environ.get("PREFIX", "") else "нет"
        ok, err = self.client.validate_key()
        self.tui.page("Doctor", [
            "python   " + sys.version.split()[0],
            "platform " + platform.system().lower(),
            "termux   " + termux,
            "key      " + ("ok" if ok else "ошибка: " + err),
            "model    " + self.S["model"],
            "mode     " + self.cfg.mode,
            "mcp      %d серверов" % len(self.mcp.servers),
        ])

    def _shell(self, cmd: str) -> None:
        self.S["hist"].append((time.strftime("%H:%M"), "!" + cmd))
        self.S["reqs"] += 1
        self.tui.print_tool_header("Bash", cmd)
        t = time.time()
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            out = (r.stdout + r.stderr).strip()
        except Exception as e:
            out = str(e)
        self.tui.print_tool_result(out or "(пусто)")
        write("\n")

    # ------------------------------------------------------------------ repl
    def repl(self) -> int:
        write("\x1b[2J\x1b[H")
        self.tui.print_banner(__version__, "https://novaproxy.duckdns.org", os.getcwd(), self.S["model"])

        if self.mcp.specs:
            events: List[str] = []
            self.mcp.connect_all(on_event=lambda n, s: events.append("mcp %s: %s" % (n, s)))
            for e in events:
                write("  " + fg(MUTED) + e + R + "\n")
            write("\n")

        from .input import InputReader
        reader = InputReader(CMDS, self.tui, self.S)

        while True:
            p = reader.read()
            if p is None:
                break
            p = p.strip()
            if not p:
                continue
            if p == "/":
                j = self.tui.dialog("Commands", HELP)
                if j < 0:
                    continue
                p = HELP[j][0]
            if p.startswith("/"):
                if not self.handle(p):
                    break
            elif p.startswith("!"):
                self._shell(p[1:])
            else:
                self.ask(p)
        write("\n")
        self.mcp.close_all()
        return 0


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
    p.add_argument("--quiet", "-q", action="store_true", help="не печатать мета-строку в one-shot")
    p.add_argument("--version", action="store_true", help="версия")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
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

    def validate(k: str):
        return NovaClient(k).validate_key()

    # First-run / auth: no key -> ask. Existing key -> verify once; if the
    # server rejects it (revoked/expired), ask again instead of dying.
    if not cfg.api_key or args.auth:
        key = prompt_for_key(validate)
        cfg.api_key = key
        cfg.save()
        if args.auth and not args.prompt:
            return 0
    else:
        ok, err = validate(cfg.api_key)
        if not ok:
            print("Сохранённый ключ отклонён (%s). Введите новый." % err, file=sys.stderr)
            key = prompt_for_key(validate)
            cfg.api_key = key
            cfg.save()

    if args.system:
        cfg.system_prompt = args.system
    if args.mode:
        cfg.mode = args.mode

    client = NovaClient(cfg.api_key)
    app = App(cfg, client, args)
    if args.model:
        app.S["model"] = args.model

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
