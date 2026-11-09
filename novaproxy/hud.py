"""Terminal HUD — faithful port of the reference testCli.py interface.

All visuals (banner art, boot splash, sidebar, frame, menus, colors) are
carried over 1:1 from the prototype; only the data sources are real.
"""

from __future__ import annotations

import os
import random
import re
import select
import shutil
import subprocess
import sys
import textwrap
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:  # Windows
    termios = None  # type: ignore
    tty = None  # type: ignore
    _HAS_TERMIOS = False

try:
    import readline
except ImportError:
    try:
        import pyreadline3 as readline  # type: ignore
    except ImportError:
        readline = None  # type: ignore

R = "\x1b[0m"
B = "\x1b[1m"
D = "\x1b[38;5;244m"


def c(n: int) -> str:
    return "\x1b[38;5;%dm" % n


THEMES = [141, 114, 203, 39, 221, 208]

GLYPHS = {
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "X": ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
    "Y": ["██╗   ██╗", "██║ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
}
GR = [201, 177, 141, 105, 69, 39]

# ASCII splash embedded in the binary so the boot works from any cwd
# (the prototype read it from $TMPDIR/fsoc.txt).
SPLASH_MAIN = r"""
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
XX                                                                          XX
XX   MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMMMMMMMMssssssssssssssssssssssssssMMMMMMMMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMMMss'''                          '''ssMMMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMyy''                                    ''yyMMMMMMMMMMMM   XX
XX   MMMMMMMMyy''                                            ''yyMMMMMMMM   XX
XX   MMMMMy''                                                    ''yMMMMM   XX
XX   MMMy'                                                          'yMMM   XX
XX   Mh'                                                              'hM   XX
XX   -                                                                  -   XX
XX                                                                          XX
XX   ::                                                                ::   XX
XX   MMhh.        ..hhhhhh..                      ..hhhhhh..        .hhMM   XX
XX   MMMMMh   ..hhMMMMMMMMMMhh.                .hhMMMMMMMMMMhh..   hMMMMM   XX
XX   ---MMM .hMMMMdd:::dMMMMMMMhh..        ..hhMMMMMMMd:::ddMMMMh. MMM---   XX
XX   MMMMMM MMmm''      'mmMMMMMMMMyy.  .yyMMMMMMMMmm'      ''mmMM MMMMMM   XX
XX   ---mMM ''             'mmMMMMMMMM  MMMMMMMMmm'             '' MMm---   XX
XX   yyyym'    .              'mMMMMm'  'mMMMMm'              .    'myyyy   XX
XX   mm''    .y'     ..yyyyy..  ''''      ''''  ..yyyyy..     'y.    ''mm   XX
XX           MN    .sMMMMMMMMMss.   .    .   .ssMMMMMMMMMs.    NM           XX
XX           N`    MMMMMMMMMMMMMN   M    M   NMMMMMMMMMMMMM    `N           XX
XX            +  .sMNNNNNMMMMMN+   `N    N`   +NMMMMMNNNNNMs.  +            XX
XX              o+++     ++++Mo    M      M    oM++++     +++o              XX
XX                                oo      oo                                XX
XX           oM                 oo          oo                 Mo           XX
XX         oMMo                M              M                oMMo         XX
XX       +MMMM                 s              s                 MMMM+       XX
XX     +MMMMM+            +++NNNN+        +NNNN+++            +MMMMM+      XX
XX     MMMMMMMMMNN+++NNMMMMMMMMMMMMMMNNNNMMMMMMMMMMMMMMNN+++NNMMMMMMMMM     XX
XX     yMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMy     XX
XX   m  yMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMy  m   XX
XX   MMm yMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMy mMM   XX
XX   MMMm .yyMMMMMMMMMMMMMMMM     MMMMMMMMMM     MMMMMMMMMMMMMMMMyy. mMMM   XX
XX   MMMMd   ''''hhhhh       odddo          obbbo        hhhh''''   dMMMM   XX
XX   MMMMMd             'hMMMMMMMMMMddddddMMMMMMMMMMh'             dMMMMM   XX
XX   MMMMMMd              'hMMMMMMMMMMMMMMMMMMMMMMh'              dMMMMMM   XX
XX   MMMMMMM-               ''ddMMMMMMMMMMMMMMdd''               -MMMMMMM   XX
XX   MMMMMMMM                   '::dddddddd::'                   MMMMMMMM   XX
XX   MMMMMMMM-                                                  -MMMMMMMM   XX
XX   MMMMMMMMM                                                  MMMMMMMMM   XX
XX   MMMMMMMMMy                                                yMMMMMMMMM   XX
XX   MMMMMMMMMMy.                                            .yMMMMMMMMMM   XX
XX   MMMMMMMMMMMMy.                                        .yMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMy.                                    .yMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMMMs.                                .sMMMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMMMMMss.           ....           .ssMMMMMMMMMMMMMMMMMM   XX
XX   MMMMMMMMMMMMMMMMMMMMNo         oNNNNo         oNMMMMMMMMMMMMMMMMMMMM   XX
XX                                                                          XX
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
""".strip("\n").split("\n")

SPLASH_SUB = r"""
    .o88o.                               o8o                .
    888 `"                               `"'              .o8
   o888oo   .oooo.o  .ooooo.   .ooooo.  oooo   .ooooo.  .o888oo oooo    ooo
    888    d88(  "8 d88' `88b d88' `"Y8 `888  d88' `88b   888    `88.  .8'
    888    `"Y88b.  888   888 888        888  888ooo888   888     `88..8'
    888    o.  )88b 888   888 888   .o8  888  888    .o   888 .    `888'
   o888o   8""888P' `Y8bod8P' `Y8bod8P' o888o `Y8bod8P'   "888"      d8'
                                                                .o...P'
                                                                `XER0'
""".strip("\n").split("\n")

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧"


def size() -> os.terminal_size:
    return shutil.get_terminal_size((50, 24))


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


def word(s: str) -> List[str]:
    return ["".join(GLYPHS[ch][i] for ch in s) for i in range(6)]


def art(w: int) -> List[str]:
    if w >= 82:
        return word("NOVAPROXY")
    if w >= 44:
        return word("NOVA") + [""] + word("PROXY")
    return []


def key() -> str:
    if not _HAS_TERMIOS:
        ch = sys.stdin.read(1)
        return ch
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch == "\x1b" and select.select([fd], [], [], 0.05)[0]:
            ch += os.read(fd, 2).decode(errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def shrink(L: List[str], cols: int, rows: int) -> List[str]:
    w = max(len(l) for l in L)
    k = max(1, -(-w // (cols - 1)), -(-len(L) // (rows - 1)))
    if k == 1:
        return L
    out = []
    for r in range(0, len(L), k):
        blk = L[r : r + k]
        line = ""
        for x in range(0, w, k):
            ch = [b[x + j] for b in blk for j in range(k) if x + j < len(b) and b[x + j] != " "]
            line += max(set(ch), key=ch.count) if len(ch) * 3 >= k * k else " "
        out.append(line)
    return out


def flash(m: List[str]) -> None:
    cols, rows = size()
    if not m:
        return
    a = shrink(m, cols, rows)
    w = max(len(l) for l in a)
    left = max(0, (cols - w) // 2)
    top = max(0, (rows - len(a)) // 2)
    pal = [46, 46, 40, 34, 46, 82, 46, 28, 46, 46]
    for i in range(10):
        if i in (2, 6):
            write("\x1b[2J\x1b[H")
            time.sleep(0.05)
            continue
        o = c(pal[i]) + "\x1b[2J\x1b[H" + "\n" * top
        for l in a:
            sh = random.randint(-4, 4) if i in (3, 7) and random.random() < 0.4 else 0
            o += " " * max(0, left + sh) + l + "\n"
        write(o)
        time.sleep(0.05)
    write(R + "\x1b[2J\x1b[H")


def blink(f: List[str]) -> None:
    cols, rows = size()
    w = 0
    if f:
        ind = min(len(l) - len(l.lstrip()) for l in f if l.strip())
        f = [l[ind:] for l in f]
        w = max(len(l) for l in f)
    if not f or w > cols - 1:
        f = ["f s o c i e t y"]
        w = len(f[0])
    left = max(0, (cols - w) // 2)
    top = max(0, (rows - len(f)) // 2)
    on = c(46) + B + "\x1b[2J\x1b[H" + "\n" * top + "".join(" " * left + l + "\n" for l in f) + R
    for _ in range(5):
        write(on)
        time.sleep(0.06)
        write("\x1b[2J\x1b[H")
        time.sleep(0.04)


def boot() -> None:
    write("\x1b[?25l")
    try:
        flash(SPLASH_MAIN)
        blink(SPLASH_SUB)
    finally:
        write("\x1b[2J\x1b[H" + R + "\x1b[?25h")


class Hud:
    """Live terminal frame: chat pane + sidebar + input separator."""

    def __init__(self, state: Dict[str, Any], accent: int = 141, green: int = 114) -> None:
        self.S = state
        self.acc = accent
        self.grn = green

    # ---------------------------------------------------------------- state
    def say(self, *ls: str) -> None:
        for l in ls:
            self.S["chat"].append(("sys", l))

    def call(self, n: str, a: str, ms: Optional[int] = None, ok: bool = True) -> None:
        cl = {"n": n, "a": a, "st": "ok" if ok else "run", "ms": ms or 0}
        self.S["calls"].append(cl)
        self.S["chat"].append(("tool", "%s %s  ✓ %dms" % (n, a, cl["ms"])))

    # ---------------------------------------------------------------- render
    def sidebar(self, W: int, status: str) -> List[str]:
        a = c(self.acc)
        L: List[str] = []

        def h(t: str) -> None:
            L.append(a + B + t + R)

        def row(k: str, v: Any) -> None:
            L.append(D + k.ljust(7) + R + str(v)[: max(1, W - 7)])

        S = self.S
        h("▌ SESSION #%d" % S["sess"])
        row("name", S["name"])
        row("model", S["model"])
        row("status", status)
        row("reqs", S["reqs"])
        row("in/out", "%d/%d" % (S["tin"], S["tout"]))
        ctx = S["tin"] + S["tout"]
        n = min(12, int(ctx / 8000 * 12) + (1 if ctx else 0))
        L.append(D + "ctx    " + R + a + "█" * n + D + "░" * (12 - n) + R + " 8k")
        if S.get("ttft") is not None:
            row("ttft", "%.2fs" % S["ttft"])
        if S.get("tps") is not None:
            row("speed", "%.1f t/s" % S["tps"])
        row("uptime", "%ds" % (time.time() - S["t0"]))
        L.append("")
        h("TOOLS")
        if not S["calls"]:
            L.append(D + "—" + R)
        for cl in S["calls"][-6:]:
            ic = (c(self.grn) + "✓") if cl["st"] == "ok" else (c(221) + "…")
            L.append(ic + R + " " + (cl["n"] + " " + cl["a"])[: W - 9] + D + " %dms" % cl["ms"] + R)
        L.append("")
        h("HISTORY")
        if not S["hist"]:
            L.append(D + "—" + R)
        for tm, t in S["hist"][-8:]:
            L.append(D + tm + R + " " + t[: W - 6])
        return L

    def chatlines(self, MW: int) -> List[str]:
        L: List[str] = []
        for role, t in self.S["chat"]:
            if role == "user":
                pre = c(self.grn) + B + "❯ " + R
            elif role == "ai":
                pre = c(self.acc) + "▌ " + R
            elif role == "tool":
                pre = D + "  ⚙ "
            else:
                pre = D + "  "
            ws = textwrap.wrap(t, max(10, MW - 5)) or [""]
            for i, x in enumerate(ws):
                L.append((pre if (i == 0 or role == "ai") else "  ") + x + R)
            if role in ("user", "ai"):
                L.append("")
        return L

    def frame(self, status: str = "idle") -> None:
        cols, rows = size()
        side = self.S["side"] and cols >= 76
        SW = 30 if side else 0
        MW = cols - SW - 3 if side else cols - 1
        H = max(5, rows - 3)
        if not self.S["chat"]:
            a = art(MW)
            left = [ctr(c(GR[min(5, k * 6 // max(1, len(a)))]) + B + l + R, MW) for k, l in enumerate(a)]
            if not a:
                left = [ctr(c(self.acc) + B + "NOVAPROXY" + R, MW)]
            left += ["", ctr(D + "введи запрос · / или Enter = меню" + R, MW)]
            left = [""] * max(0, (H - len(left)) // 2) + left
        else:
            left = self.chatlines(MW)[-H:]
        right = self.sidebar(SW, status) if side else []
        sep = " " + c(240) + "│" + R + " "
        o = "\x1b[H"
        for i in range(H):
            l = left[i] if i < len(left) else ""
            r = right[i] if i < len(right) else ""
            line = clip(pad(l, MW), MW) + ((sep + clip(r, SW)) if side else "")
            o += clip(line, cols - 1) + "\x1b[K\n"
        o += clip(c(self.acc) + "─── ввод " + "─" * cols, cols - 1) + "\x1b[K\n\x1b[J"
        write(o)

    # ---------------------------------------------------------------- menus
    def menu(self, title: str, items: List[Tuple[str, str]]) -> int:
        i = 0
        while True:
            cols, rows = size()
            vis = max(3, rows - 6)
            st = max(0, min(i - vis // 2, len(items) - vis))
            o = "\x1b[2J\x1b[H\n"
            o += "  " + c(self.acc) + B + title + R + "\n\n"
            for j in range(st, min(len(items), st + vis)):
                lb, ds = items[j]
                if j == i:
                    o += clip("  " + c(self.acc) + B + "❯ " + lb.ljust(13) + R + c(self.acc) + ds + R, cols - 1) + "\n"
                else:
                    o += clip("    " + D + lb.ljust(13) + ds + R, cols - 1) + "\n"
            o += "\n  " + D + "↑↓ / j k · enter · q" + R
            write(o)
            k = key()
            if k in ("\x1b[A", "k"):
                i = (i - 1) % len(items)
            elif k in ("\x1b[B", "j"):
                i = (i + 1) % len(items)
            elif k in ("\r", "\n"):
                return i
            elif k in ("q", "\x1b", "\x03"):
                return -1

    def page(self, lines: List[str]) -> None:
        write("\x1b[2J\x1b[H\n")
        cols = size().columns
        for l in lines:
            print(clip("  " + l, cols - 1))
        print()
        try:
            input("  " + D + "enter → назад" + R)
        except (EOFError, KeyboardInterrupt):
            pass

    def spinner(self, i: int, label: str = "thinking") -> str:
        return SPINNER[i % len(SPINNER)] + " " + label


def copy_to_clipboard(text: str) -> Tuple[bool, str]:
    """Best-effort clipboard copy across Termux, macOS, Linux, Windows."""
    candidates = [
        (["termux-clipboard-set"], None),
        (["pbcopy"], None),
        (["wl-copy"], None),
        (["xclip", "-selection", "clipboard"], None),
        (["xsel", "--clipboard", "--input"], None),
        (["clip"], None),
        (["powershell", "-NoProfile", "-Command", "Set-Clipboard"], None),
    ]
    for cmd, _ in candidates:
        try:
            subprocess.run(cmd, input=text, text=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, cmd[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return False, ""
