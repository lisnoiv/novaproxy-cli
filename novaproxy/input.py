"""Input reader with prompt_toolkit: history, completion, bottom status bar."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .hud import R, fg, MUTED


class InputReader:
    def __init__(self, commands: List[str], tui, state: Dict[str, Any]) -> None:
        self.commands = commands
        self.tui = tui
        self.S = state
        self._pt = None
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory
            from pathlib import Path
            hist = Path.home() / ".novaproxy" / "history"
            hist.parent.mkdir(parents=True, exist_ok=True)
            completer = WordCompleter(commands, sentence=True)
            self._session = PromptSession(
                history=FileHistory(str(hist)),
                completer=completer,
                complete_while_typing=False,
            )
            self._pt = True
        except Exception:
            self._pt = False

    def read(self) -> Optional[str]:
        if self._pt:
            return self._read_pt()
        return self._read_plain()

    def _toolbar(self) -> str:
        from .hud import SECONDARY, bgc, BGD, BGD2, TEXT
        model = self.S.get("model", "?")
        name = self.S.get("name", "untitled")
        tin, tout = self.S.get("tin", 0), self.S.get("tout", 0)
        help_chip = bgc(MUTED) + fg((18, 18, 18)) + " ctrl+c выход · / меню " + R
        tok_chip = bgc(TEXT) + fg((37, 37, 37)) + " Tokens: %d/%d " % (tin, tout) + R
        model_chip = bgc(SECONDARY) + fg((33, 33, 33)) + " " + model + " " + R
        name_chip = bgc(BGD2) + fg(MUTED) + " " + name + " " + R
        return help_chip + tok_chip + name_chip + model_chip

    def _read_pt(self) -> Optional[str]:
        try:
            return self._session.prompt(
                [("class:prompt", " > ")],
                bottom_toolbar=self._toolbar(),
            )
        except (EOFError, KeyboardInterrupt):
            return None

    def _read_plain(self) -> Optional[str]:
        try:
            return input(fg((250, 178, 131)) + " > " + R)
        except (EOFError, KeyboardInterrupt):
            return None
