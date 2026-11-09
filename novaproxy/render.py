"""Markdown rendering for the terminal via rich (optional).

Falls back to plain text with basic ANSI styling when rich is unavailable.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    from rich.console import Console
    from rich.markdown import Markdown
    _console: Optional[Console] = Console(highlight=True, soft_wrap=False)
except Exception:  # pragma: no cover - rich missing
    _console = None

R = "\x1b[0m"
B = "\x1b[1m"
D = "\x1b[38;5;244m"


def render_markdown(text: str) -> None:
    if _console is not None:
        try:
            _console.print(Markdown(text))
            return
        except Exception:
            pass
    print(plain_markdown(text))


def plain_markdown(text: str) -> str:
    out = []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(D + "─" * 40 + R)
            continue
        if in_code:
            out.append("\x1b[38;5;114m" + line + R)
            continue
        line = re.sub(r"^#{1,6}\s*(.*)$", lambda m: B + m.group(1) + R, line)
        line = re.sub(r"\*\*([^*]+)\*\*", lambda m: B + m.group(1) + R, line)
        line = re.sub(r"`([^`]+)`", lambda m: "\x1b[38;5;221m" + m.group(1) + R, line)
        out.append(line)
    return "\n".join(out)


def render_to_string(text: str) -> str:
    if _console is not None:
        try:
            with _console.capture() as cap:
                _console.print(Markdown(text))
            return cap.get()
        except Exception:
            pass
    return plain_markdown(text) + "\n"
