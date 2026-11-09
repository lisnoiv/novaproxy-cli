"""Markdown rendering for the terminal.

Uses rich when available; falls back to a small ANSI renderer with the
OpenCode palette (headings blue, inline code green, links cyan, etc.).
"""

from __future__ import annotations

import re
from typing import List, Optional

R = "\x1b[0m"
B = "\x1b[1m"
I = "\x1b[3m"
U = "\x1b[4m"

try:
    from rich.console import Console
    from rich.markdown import Markdown
    _console: Optional[Console] = Console(highlight=True, soft_wrap=True)
except Exception:
    _console = None


def _fg(t) -> str:
    return "\x1b[38;2;%d;%d;%dm" % t


_SECONDARY = (92, 156, 245)
_OK = (127, 216, 143)
_INFO = (86, 182, 194)
_MUTED = (106, 106, 106)
_EMPH = (229, 192, 123)
_PRIMARY = (250, 178, 131)


def render_to_string(text: str, width: int = 100) -> str:
    if _console is not None:
        try:
            c = Console(width=max(20, width), highlight=True, soft_wrap=True)
            with c.capture() as cap:
                c.print(Markdown(text))
            return cap.get()
        except Exception:
            pass
    return plain_markdown(text) + "\n"


def render_markdown(text: str) -> None:
    print(render_to_string(text), end="")


def plain_markdown(text: str) -> str:
    out: List[str] = []
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(_fg(_MUTED) + "─" * 40 + R)
            continue
        if in_code:
            out.append(" " + _fg(_OK) + line + R)
            continue
        line = re.sub(r"^(#{1,6})\s*(.*)$", lambda m: _fg(_SECONDARY) + B + m.group(1) + " " + m.group(2) + R, line)
        line = re.sub(r"\*\*([^*]+)\*\*", lambda m: B + m.group(1) + R, line)
        line = re.sub(r"`([^`]+)`", lambda m: _fg(_OK) + m.group(1) + R, line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: _fg(_INFO) + B + m.group(1) + R + " " + _fg(_PRIMARY) + U + m.group(2) + R, line)
        if stripped.startswith(">"):
            line = _fg(_EMPH) + I + "┃ " + line.lstrip("> ") + R
        elif re.match(r"^\s*[-*]\s", line):
            line = re.sub(r"^(\s*)[-*]\s", lambda m: m.group(1) + _fg(_PRIMARY) + "• " + R, line)
        out.append(line)
    return "\n".join(out)
