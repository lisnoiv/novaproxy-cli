"""NovaProxy API client.

Talks to the fixed NovaProxy gateway (OpenAI-compatible surface):
  GET  /v1/models
  POST /v1/chat/completions        (stream + non-stream, tool calling)
  POST /api/key-info               (key validation / balance)

Only stdlib + ``requests`` are used so the client installs cleanly on
Android/Termux, Windows, Linux and macOS without native builds.

NovaProxy-specific behavior:
  - Non-streaming completions include a ``usage`` block we read directly.
  - Streaming completions terminate each SSE stream with ``data: [DONE]``;
    the final ``usage`` arrives either in a dedicated event or as a
    ``{"choices": [], "usage": ...}`` frame before [DONE].
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import requests

BASE_URL = "https://novaproxy.duckdns.org/v1"
KEY_INFO_URL = "https://novaproxy.duckdns.org/api/key-info"
DEFAULT_TIMEOUT = 120


class ApiError(Exception):
    """Raised for any gateway-side failure with a human-readable message."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class NovaClient:
    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "novaproxy-cli/1.0.0",
        })

    # ------------------------------------------------------------------ util
    @staticmethod
    def _error_from_response(resp: requests.Response) -> ApiError:
        try:
            payload = resp.json()
            msg = (payload.get("error") or {}).get("message") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and not msg:
                err = payload.get("error")
                msg = err if isinstance(err, str) else None
            if not msg:
                msg = json.dumps(payload, ensure_ascii=False)[:300]
        except ValueError:
            msg = (resp.text or "").strip()[:300] or f"HTTP {resp.status_code}"
        return ApiError(str(msg), resp.status_code)

    # ---------------------------------------------------------------- models
    def list_models(self) -> List[str]:
        try:
            resp = self.session.get(f"{BASE_URL}/models", timeout=30)
        except requests.RequestException as exc:
            raise ApiError(f"Сеть недоступна: {exc}") from None
        if resp.status_code != 200:
            raise self._error_from_response(resp)
        try:
            data = resp.json().get("data", [])
            return sorted({m.get("id") for m in data if isinstance(m, dict) and m.get("id")})
        except (ValueError, AttributeError):
            raise ApiError("Некорректный ответ /v1/models") from None

    # ------------------------------------------------------------ validation
    def validate_key(self) -> Tuple[bool, str]:
        """Check the key against /api/key-info. Returns (ok, error_message)."""
        try:
            resp = self.session.post(KEY_INFO_URL, json={"api_key": self.api_key}, timeout=20)
        except requests.RequestException as exc:
            return False, f"сервер недоступен ({exc})"
        if resp.status_code == 200:
            return True, ""
        try:
            err = resp.json().get("error") or f"HTTP {resp.status_code}"
        except ValueError:
            err = f"HTTP {resp.status_code}"
        return False, str(err)

    def key_info(self) -> Dict[str, Any]:
        try:
            resp = self.session.post(KEY_INFO_URL, json={"api_key": self.api_key}, timeout=20)
        except requests.RequestException as exc:
            raise ApiError(f"Сеть недоступна: {exc}") from None
        if resp.status_code != 200:
            raise self._error_from_response(resp)
        try:
            return resp.json()
        except ValueError:
            raise ApiError("Некорректный ответ /api/key-info") from None

    # ------------------------------------------------------------------ chat
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        try:
            resp = self.session.post(
                f"{BASE_URL}/chat/completions", json=body, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise ApiError(f"Сеть недоступна: {exc}") from None
        if resp.status_code != 200:
            raise self._error_from_response(resp)
        try:
            return resp.json()
        except ValueError:
            raise ApiError("Некорректный JSON в ответе") from None

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield parsed SSE events. Each event is the decoded `data:` JSON,
        or {"__done__": True} for the [DONE] sentinel."""
        body: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        try:
            resp = self.session.post(
                f"{BASE_URL}/chat/completions",
                json=body,
                stream=True,
                timeout=self.timeout,
                headers={"Accept": "text/event-stream"},
            )
        except requests.RequestException as exc:
            raise ApiError(f"Сеть недоступна: {exc}") from None
        if resp.status_code != 200:
            raise self._error_from_response(resp)

        buf = b""
        try:
            for chunk in resp.iter_content(chunk_size=None):
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        yield {"__done__": True}
                        continue
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        except requests.RequestException as exc:
            raise ApiError(f"Поток прерван: {exc}") from None
        finally:
            resp.close()


class StreamAccumulator:
    """Assembles streamed deltas into a full assistant message."""

    def __init__(self) -> None:
        self.content_parts: List[str] = []
        self.tool_calls: Dict[int, Dict[str, Any]] = {}
        self.finish_reason: Optional[str] = None
        self.usage: Optional[Dict[str, Any]] = None
        self.ttft: Optional[float] = None
        self.started = time.monotonic()

    def feed(self, event: Dict[str, Any]) -> str:
        """Feed one SSE event; returns incremental text to render."""
        if event.get("__done__"):
            return ""
        if isinstance(event.get("usage"), dict):
            self.usage = event["usage"]
        choices = event.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        piece = ""
        content = delta.get("content")
        if content:
            if self.ttft is None:
                self.ttft = time.monotonic() - self.started
            self.content_parts.append(content)
            piece = content
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = self.tool_calls.setdefault(
                idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
        return piece

    @property
    def text(self) -> str:
        return "".join(self.content_parts)

    def assistant_message(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        return msg
