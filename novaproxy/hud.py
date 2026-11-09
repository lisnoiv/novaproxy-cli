"""Terminal HUD styled after the OpenCode TUI.

Design faithfully ported from opencode-ai/opencode (bubbletea original):
  - dark truecolor palette (Primary #fab283, Secondary #5c9cf5, ...)
  - centered startup banner with logo, server URL and cwd
  - messages with a thick left border (┃), orange for the assistant,
    blue for the user, muted for tool calls
  - assistant footer ` <model> (1.2s)` in muted text
  - working indicator: spinner + Thinking.../Generating.../...
  - bottom status bar with chips (help / tokens / model)
  - centered overlay dialogs with rounded borders (╭╮╰╯)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

R = "\x1b[0m"
B = "\x1b[1m"
I = "\x1b[3m"
U = "\x1b[4m"

# ---- opencode dark theme -------------------------------------------------
PRIMARY = (250, 178, 131)    # #fab283
SECONDARY = (92, 156, 245)   # #5c9cf5
ACCENT = (157, 124, 216)     # #9d7cd8
ERR = (224, 108, 117)        # #e06c75
WARN = (245, 167, 66)        # #f5a742
OK = (127, 216, 143)         # #7fd88f
INFO = (86, 182, 194)        # #56b6c2
TEXT = (224, 224, 224)       # #e0e0e0
MUTED = (106, 106, 106)      # #6a6a6a
EMPH = (229, 192, 123)       # #e5c07b
BGD = (33, 33, 33)           # #212121
BGD2 = (37, 37, 37)          # #252525
DARKER = (18, 18, 18)        # #121212
BORD = (75, 76, 92)          # #4b4c5c

THEMES = [
    ("opencode", PRIMARY),
    ("emerald", OK),
    ("violet", ACCENT),
    ("cyan", INFO),
    ("amber", WARN),
]

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧"

GLYPHS = {
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "X": ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
    "Y": ["██╗   ██╗", "██║ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
}


def fg(t: Tuple[int, int, int]) -> str:
    return "\x1b[38;2;%d;%d;%dm" % t


def bgc(t: Tuple[int, int, int]) -> str:
    return "\x1b[48;2;%d;%d;%dm" % t


def size() -> os.terminal_size:
    return shutil.get_terminal_size((80, 24))


def vl(s: str) -> int:
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def clip(s: str, w: int) -> str:
    out, n = "", 0
    for part in re.split(r"(\x1b\[[0-9;]*m)", s):
        if part.startswith("\x1b"):
            out += part
        else:
            t = part[: max(0, w - n)]
            out += t
            n += len(t)
    return out + R


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - vl(s))


def ctr(s: str, w: int) -> str:
    return " " * max(0, (w - vl(s)) // 2) + s


def write(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def fmt_dur(seconds: float) -> str:
    if seconds < 1:
        return "%dms" % int(seconds * 1000)
    if seconds < 60:
        return "%.1fs" % seconds
    return "%.1fm" % (seconds / 60)


# ---------------------------------------------------------------- keyboard
def key() -> str:
    """Read one keypress (arrows normalized to \\x1b[A etc.)."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {"H": "\x1b[A", "P": "\x1b[B", "K": "\x1b[D", "M": "\x1b[C"}.get(ch, "")
        return ch
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch == "\x1b":
            import select
            if select.select([fd], [], [], 0.05)[0]:
                ch += os.read(fd, 2).decode(errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


class CancelListener:
    """Background key listener used during streaming; ESC/Ctrl+C cancels."""

    def __init__(self) -> None:
        self.pressed: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ok = sys.stdin.isatty() and os.name != "nt"

    def start(self) -> None:
        if not self._ok:
            return
        try:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception:
            pass

    def _run(self) -> None:
        import select
        import termios
        import tty
        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            while not self._stop.is_set():
                if select.select([fd], [], [], 0.1)[0]:
                    ch = os.read(fd, 1).decode(errors="ignore")
                    if ch in ("\x1b", "\x03"):
                        self.pressed = ch
                        return
        except Exception:
            return
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)


# ---------------------------------------------------------------- banner
class Tui:
    def __init__(self, primary: Tuple[int, int, int] = PRIMARY) -> None:
        self.primary = primary

    # -- banner ------------------------------------------------------------
    def banner_art(self) -> List[str]:
        return ["".join(GLYPHS[ch][i] for ch in "NOVAPROXY") for i in range(6)]

    def print_banner(self, version: str, url: str, cwd: str, model: str) -> None:
        cols = size().columns
        art = self.banner_art()
        aw = len(art[0])
        out = ["", ""]
        if aw + 2 <= cols:
            grad = [self.primary, (247, 165, 122), (244, 152, 113), (238, 140, 105), (233, 127, 96), (227, 114, 88)]
            for i, ln in enumerate(art):
                out.append(ctr(fg(grad[i % len(grad)]) + B + ln + R, cols))
        else:
            out.append(ctr(fg(self.primary) + B + "⌬ NovaProxy" + R, cols))
        out.append("")
        out.append(ctr(fg(MUTED) + "NovaProxy CLI " + version + R, cols))
        out.append(ctr(fg(MUTED) + url + R, cols))
        out.append(ctr(fg(MUTED) + "cwd: " + cwd + R, cols))
        out.append(ctr(fg(MUTED) + "model: " + model + R, cols))
        out.append("")
        out.append(ctr(fg(MUTED) + "enter — отправить" + fg(MUTED) + " · " + R
                       + fg(TEXT) + "/" + fg(MUTED) + " — команды · " + R
                       + fg(TEXT) + "@file" + fg(MUTED) + " — прикрепить · " + R
                       + fg(TEXT) + "esc" + fg(MUTED) + " — отмена генерации" + R, cols))
        out.append("")
        write("\n".join(out) + "\n")

    # -- messages ------------------------------------------------------------
    def _border_lines(self, text: str, color: Tuple[int, int, int], width: int) -> List[str]:
        pre = fg(color) + "┃ " + R
        lines = []
        for raw in text.split("\n"):
            wrapped = textwrap.wrap(raw, max(10, width - 4),
                                    replace_whitespace=False, drop_whitespace=False) or [""]
            lines.extend(pre + fg(TEXT) + w + R for w in wrapped)
        return lines

    def print_user_message(self, text: str) -> None:
        w = size().columns
        for ln in self._border_lines(text, SECONDARY, w):
            write(ln + "\n")
        write("\n")

    def print_assistant_markdown(self, text: str) -> None:
        """Render the finished assistant message as markdown with an orange border."""
        from .render import render_to_string
        w = size().columns
        body = render_to_string(text, max(20, w - 4)).rstrip("\n")
        pre = fg(self.primary) + "┃ " + R
        for ln in body.split("\n"):
            write(pre + ln + R + "\n")

    def print_footer(self, model: str, took: float, note: str = "") -> None:
        extra = (" (" + note + ")") if note else (" (%s)" % fmt_dur(took))
        write(fg(MUTED) + " " + model + extra + R + "\n\n")

    # -- tool calls ----------------------------------------------------------
    TOOL_NAMES = {
        "bash": "Bash", "read_file": "View", "write_file": "Write", "edit_file": "Edit",
        "search": "Grep", "list_dir": "List", "web_search": "Fetch",
    }
    TOOL_ACTIONS = {
        "bash": "Building command...", "read_file": "Reading file...",
        "write_file": "Preparing write...", "edit_file": "Preparing edit...",
        "search": "Searching content...", "list_dir": "Listing directory...",
        "web_search": "Fetching...",
    }

    def tool_name(self, name: str) -> str:
        if name.startswith("mcp__"):
            return name.split("__", 2)[-1]
        return self.TOOL_NAMES.get(name, name)

    def print_tool_header(self, name: str, params: str) -> None:
        disp = self.TOOL_NAMES.get(name, name)
        if name.startswith("mcp__"):
            disp = name.split("__", 2)[-1]
        short = params if len(params) <= 60 else params[:57] + "..."
        write(fg(MUTED) + "┃ " + B + disp + ": " + R + fg(MUTED) + short + R + "\n")

    def print_tool_result(self, result: str, is_error: bool = False, max_lines: int = 10) -> None:
        w = size().columns
        pre = fg(MUTED) + "┃ " + R
        if is_error:
            flat = " ".join(result.split())[: w - 6]
            write(pre + fg(ERR) + "Error: " + flat + R + "\n")
            return
        lines = result.split("\n")[:max_lines]
        for ln in lines:
            write(pre + clip(fg(MUTED) + ln, w - 3) + "\n")
        if len(result.split("\n")) > max_lines:
            write(pre + fg(MUTED) + I + "… (%d more lines)" % (len(result.split("\n")) - max_lines) + R + "\n")

    # -- working indicator ---------------------------------------------------
    def working(self, frame: int, task: str) -> None:
        write("\r\x1b[K" + fg(self.primary) + B + SPINNER[frame % len(SPINNER)] + " " + task + R)
        time.sleep(0.06)

    def clear_working(self) -> None:
        write("\r\x1b[K")

    # -- status bar (prompt_toolkit bottom toolbar) ---------------------------
    def status_bar(self, sess_name: str, model: str, tin: int, tout: int, mode: str) -> str:
        cols = size().columns
        help_chip = bgc(MUTED) + fg(DARKER) + B + " ctrl+c выход · / меню " + R
        tok_chip = bgc(TEXT) + fg(BGD2) + " Tokens: %d/%d " % (tin, tout) + R
        mode_chip = bgc(BGD2) + fg(MUTED) + " " + sess_name + " · " + mode + " " + R
        model_chip = bgc(SECONDARY) + fg(BGD) + B + " " + model + " " + R
        fixed = vl(help_chip) + vl(tok_chip) + vl(mode_chip) + vl(model_chip)
        fill = bgc(BGD2) + " " * max(1, cols - fixed) + R
        return help_chip + tok_chip + fill + mode_chip + model_chip

    # -- dialogs ---------------------------------------------------------------
    def dialog(self, title: str, items: List[Tuple[str, str]], footer: str = "↑↓ / j k · enter · q") -> int:
        """Centered overlay menu, rounded border, inverted selection. Returns index or -1."""
        i = 0
        while True:
            cols, rows = size()
            width = max(40, min(cols - 4, max([vl(t) + vl(d) + 8 for t, d in items] + [vl(title) + 8])))
            vis = max(3, min(10, rows - 10, len(items)))
            st = max(0, min(i - vis // 2, len(items) - vis))
            box: List[str] = []
            border = fg(MUTED)
            box.append(border + "╭" + "─" * (width - 2) + "╮" + R)
            inner = " " + pad(fg(self.primary) + B + title + R, width - 4) + " "
            box.append(border + "│" + R + inner + border + "│" + R)
            box.append(border + "│" + R + " " * (width - 2) + border + "│" + R)
            for j in range(st, min(len(items), st + vis)):
                t, d = items[j]
                line = " " + pad(t + ("  " + d if d else ""), width - 4) + " "
                if j == i:
                    line = bgc(self.primary) + fg(BGD) + B + line + R
                else:
                    line = fg(TEXT) + t + R + (fg(MUTED) + "  " + d if d else "") + R
                    line = " " + pad(line, width - 4) + " "
                box.append(border + "│" + R + line + border + "│" + R)
            if len(items) > vis:
                ind = fg(self.primary) + B + ("↑ " if st > 0 else "  ") + ("↓" if st + vis < len(items) else " ")
                box.append(border + "│" + R + " " * (width - 6) + pad(ind, 3) + " " + border + "│" + R)
            box.append(border + "│" + R + " " * (width - 2) + border + "│" + R)
            box.append(border + "╰" + "─" * (width - 2) + "╯" + R)
            top = max(0, (rows - len(box)) // 2)
            out = "\x1b[2J\x1b[H" + "\n" * top
            for ln in box:
                out += ctr(ln, cols) + "\n"
            out += "\n" + ctr(fg(MUTED) + footer + R, cols)
            write(out)
            k = key()
            if k in ("\x1b[A", "k"):
                i = (i - 1) % len(items)
            elif k in ("\x1b[B", "j"):
                i = (i + 1) % len(items)
            elif k in ("\r", "\n"):
                write("\x1b[2J\x1b[H")
                return i
            elif k in ("q", "\x1b", "\x03"):
                write("\x1b[2J\x1b[H")
                return -1

    def confirm(self, title: str, question: str, options: List[str] = None) -> int:
        """Centered confirm dialog; returns selected option index (default last = cancel)."""
        options = options or ["Yes", "No"]
        sel = len(options) - 1
        while True:
            cols, rows = size()
            width = max(46, min(cols - 4, vl(question) + 8))
            box: List[str] = []
            border = fg(MUTED)
            box.append(border + "╭" + "─" * (width - 2) + "╮" + R)
            box.append(border + "│" + R + " " + pad(fg(self.primary) + B + title + R, width - 4) + " " + border + "│" + R)
            box.append(border + "│" + R + " " * (width - 2) + border + "│" + R)
            for qline in question.split("\n"):
                for wline in textwrap.wrap(qline, width - 6) or [""]:
                    box.append(border + "│" + R + "  " + pad(fg(TEXT) + wline + R, width - 6) + "  " + border + "│" + R)
            box.append(border + "│" + R + " " * (width - 2) + border + "│" + R)
            btns = ""
            for idx, o in enumerate(options):
                if idx == sel:
                    btns += bgc(self.primary) + fg(BGD) + B + " " + o + " " + R + "  "
                else:
                    btns += bgc(BGD2) + fg(self.primary) + " " + o + " " + R + "  "
            box.append(border + "│" + R + pad("", width - 4 - vl(btns)) + btns.rstrip() + "  " + border + "│" + R)
            box.append(border + "╰" + "─" * (width - 2) + "╯" + R)
            top = max(0, (rows - len(box)) // 2)
            out = "\x1b[2J\x1b[H" + "\n" * top
            for ln in box:
                out += ctr(ln, cols) + "\n"
            write(out)
            k = key()
            if k in ("\x1b[D", "h", "\t", "\x1b[C", "l"):
                sel = (sel + 1) % len(options)
            elif k in ("\r", "\n", " "):
                write("\x1b[2J\x1b[H")
                return sel
            elif k in ("q", "\x1b", "\x03", "n", "N"):
                write("\x1b[2J\x1b[H")
                return len(options) - 1
            elif k in ("y", "Y") and len(options) == 2:
                write("\x1b[2J\x1b[H")
                return 0

    def page(self, title: str, lines: List[str]) -> None:
        cols = size().columns
        write("\x1b[2J\x1b[H\n")
        print("  " + fg(self.primary) + B + title + R + "\n")
        for l in lines:
            print(clip("  " + l, cols - 2))
        print()
        try:
            input("  " + fg(MUTED) + "enter → назад" + R)
        except (EOFError, KeyboardInterrupt):
            pass
        write("\x1b[2J\x1b[H")


def copy_to_clipboard(text: str) -> Tuple[bool, str]:
    """Best-effort clipboard copy across Termux, macOS, Linux, Windows."""
    for cmd in (["termux-clipboard-set"], ["pbcopy"], ["wl-copy"],
                ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"],
                ["clip"], ["powershell", "-NoProfile", "-Command", "Set-Clipboard"]):
        try:
            subprocess.run(cmd, input=text, text=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, cmd[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return False, ""
