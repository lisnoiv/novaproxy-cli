"""Configuration management for the NovaProxy CLI.

Stores the API key and user preferences in ~/.novaproxy/config.json with
owner-only permissions (0600). The key format is validated locally
(prefix ``px_live_``) and remotely against the gateway before saving.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_DIR = Path(os.environ.get("NOVAPROXY_HOME", Path.home() / ".novaproxy"))
CONFIG_PATH = CONFIG_DIR / "config.json"

KEY_PREFIX = "px_live_"

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": None,
    "model": "qwen3-max",
    "system_prompt": None,
    "mode": "code",
    "mcp_servers": {},
    "theme": 141,
    "sidebar": True,
}


def key_format_ok(key: str) -> bool:
    return isinstance(key, str) and key.startswith(KEY_PREFIX) and len(key) > len(KEY_PREFIX) + 4


class ConfigStore:
    """Loads, validates and persists the CLI configuration."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.data: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged = dict(DEFAULT_CONFIG)
                    merged.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG or k == "mcp_servers"})
                    self.data = merged
        except (OSError, json.JSONDecodeError):
            # Corrupt config must never crash the client; start from defaults.
            self.data = dict(DEFAULT_CONFIG)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # Windows has no POSIX modes; ignore.
        os.replace(tmp, self.path)

    # -- convenience accessors ------------------------------------------------
    @property
    def api_key(self) -> Optional[str]:
        return self.data.get("api_key")

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.data["api_key"] = value

    @property
    def model(self) -> str:
        return self.data.get("model") or DEFAULT_CONFIG["model"]

    @model.setter
    def model(self, value: str) -> None:
        self.data["model"] = value

    @property
    def mode(self) -> str:
        return self.data.get("mode") or "code"

    @mode.setter
    def mode(self, value: str) -> None:
        self.data["mode"] = value

    @property
    def system_prompt(self) -> Optional[str]:
        return self.data.get("system_prompt")

    @system_prompt.setter
    def system_prompt(self, value: Optional[str]) -> None:
        self.data["system_prompt"] = value

    @property
    def mcp_servers(self) -> Dict[str, Any]:
        servers = self.data.get("mcp_servers")
        return servers if isinstance(servers, dict) else {}

    @mcp_servers.setter
    def mcp_servers(self, value: Dict[str, Any]) -> None:
        self.data["mcp_servers"] = value


def prompt_for_key(validate_fn, out=sys.stdout, inp=input) -> str:
    """Interactive first-run key prompt with local + remote validation."""
    while True:
        try:
            key = inp("  Введите ваш API-ключ (формат px_live_...): ").strip()
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            raise SystemExit(1)
        if not key:
            continue
        if not key_format_ok(key):
            out.write("  \x1b[38;5;203mКлюч должен начинаться с px_live_ — проверьте и повторите.\x1b[0m\n")
            continue
        out.write("  Проверяю ключ на сервере NovaProxy...\n")
        ok, err = validate_fn(key)
        if ok:
            out.write("  \x1b[38;5;114mКлюч принят и сохранён в ~/.novaproxy/config.json\x1b[0m\n")
            return key
        out.write(f"  \x1b[38;5;203mСервер отклонил ключ: {err}\x1b[0m\n")
