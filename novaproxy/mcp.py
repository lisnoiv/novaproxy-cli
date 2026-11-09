"""Model Context Protocol (MCP) client support.

Connects to external MCP servers over stdio (JSON-RPC 2.0, newline-delimited
JSON), discovers their tools and exposes them to the model in OpenAI
function-calling format. Server definitions live in the config:

    "mcp_servers": {
      "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
      "remote": {"url": "http://localhost:3000/mcp"}
    }

Stdio transport is fully implemented. HTTP transport is used when the entry
has a ``url`` field (POST of JSON-RPC envelopes).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "novaproxy-cli", "version": "1.0.0"}


class McpError(Exception):
    pass


class _StdioTransport:
    def __init__(self, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> None:
        import os
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        self.proc = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=full_env,
        )
        self._responses: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self._id = 0
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        while self._alive:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
        if not self._alive or self.proc.poll() is not None:
            raise McpError("MCP server process exited")
        with self._lock:
            self._id += 1
            rid = self._id
        envelope = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(envelope) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"MCP pipe broken: {exc}") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self._responses.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if msg.get("id") == rid:
                if "error" in msg:
                    raise McpError(str(msg["error"].get("message", msg["error"])))
                return msg.get("result", {})
            # Notification or out-of-band message; ignore.
        raise McpError(f"MCP timeout on {method}")

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if not self._alive or self.proc.poll() is not None:
            return
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self) -> None:
        self._alive = False
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class _HttpTransport:
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.url = url
        self.headers = headers or {}
        self._id = 0

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
        self._id += 1
        envelope = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        try:
            resp = requests.post(self.url, json=envelope, headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self.headers,
            }, timeout=timeout)
        except requests.RequestException as exc:
            raise McpError(f"MCP HTTP error: {exc}") from None
        if resp.status_code >= 400:
            raise McpError(f"MCP HTTP {resp.status_code}: {resp.text[:200]}")
        text = resp.text
        # SSE-framed replies: pick the data: payload.
        if "data:" in text and not text.lstrip().startswith("{"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    text = line[5:].strip()
                    break
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            raise McpError("MCP: invalid JSON reply") from None
        if "error" in msg:
            raise McpError(str(msg["error"].get("message", msg["error"])))
        return msg.get("result", {})

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        try:
            requests.post(self.url, json={"jsonrpc": "2.0", "method": method, "params": params or {}},
                          headers={"Content-Type": "application/json", **self.headers}, timeout=10)
        except requests.RequestException:
            pass

    def close(self) -> None:
        pass


class McpServer:
    """One connected MCP server with discovered tools."""

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.transport: Any = None
        self.tools: List[Dict[str, Any]] = []
        self.status = "off"
        self.error = ""

    def connect(self) -> None:
        self.status = "connecting"
        if "url" in self.spec:
            self.transport = _HttpTransport(self.spec["url"], self.spec.get("headers"))
        elif "command" in self.spec:
            self.transport = _StdioTransport(
                self.spec["command"],
                [str(a) for a in self.spec.get("args", [])],
                {str(k): str(v) for k, v in (self.spec.get("env") or {}).items()},
            )
        else:
            raise McpError("server needs 'command' or 'url'")
        self.transport.request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        }, timeout=float(self.spec.get("timeout", 15)))
        self.transport.notify("notifications/initialized")
        result = self.transport.request("tools/list", {}, timeout=15)
        self.tools = result.get("tools", []) if isinstance(result, dict) else []
        self.status = "ok"

    def call_tool(self, tool: str, arguments: Dict[str, Any], timeout: float = 60.0) -> str:
        result = self.transport.request("tools/call", {
            "name": tool, "arguments": arguments,
        }, timeout=timeout)
        parts = result.get("content", []) if isinstance(result, dict) else []
        out = []
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "text":
                out.append(str(p.get("text", "")))
            else:
                out.append(json.dumps(p, ensure_ascii=False))
        return "\n".join(out) or "(empty result)"

    def close(self) -> None:
        try:
            if self.transport:
                self.transport.close()
        except Exception:
            pass
        self.status = "off"


class McpManager:
    """Owns all MCP connections; converts tools to OpenAI format and routes calls."""

    def __init__(self, specs: Dict[str, Dict[str, Any]]) -> None:
        self.specs = specs or {}
        self.servers: Dict[str, McpServer] = {}

    def connect_all(self, on_event: Optional[Callable[[str, str], None]] = None) -> None:
        for name, spec in self.specs.items():
            srv = McpServer(name, spec)
            try:
                srv.connect()
                self.servers[name] = srv
                if on_event:
                    on_event(name, f"ok ({len(srv.tools)} tools)")
            except Exception as exc:
                srv.status = "error"
                srv.error = str(exc)
                self.servers[name] = srv
                if on_event:
                    on_event(name, f"error: {exc}")

    def add_server(self, name: str, spec: Dict[str, Any]) -> Tuple[bool, str]:
        old = self.servers.get(name)
        if old:
            old.close()
        self.specs[name] = spec
        srv = McpServer(name, spec)
        try:
            srv.connect()
            self.servers[name] = srv
            return True, f"подключено, инструментов: {len(srv.tools)}"
        except Exception as exc:
            srv.status = "error"
            srv.error = str(exc)
            self.servers[name] = srv
            return False, str(exc)

    def remove_server(self, name: str) -> bool:
        srv = self.servers.pop(name, None)
        self.specs.pop(name, None)
        if srv:
            srv.close()
            return True
        return False

    def openai_tools(self) -> List[Dict[str, Any]]:
        tools = []
        for name, srv in self.servers.items():
            if srv.status != "ok":
                continue
            for t in srv.tools:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{name}__{t.get('name', 'tool')}",
                        "description": (t.get("description") or "")[:500],
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })
        return tools

    def dispatch(self, qualified_name: str, arguments_json: str, timeout: float = 60.0) -> str:
        try:
            _, server_name, tool_name = qualified_name.split("__", 2)
        except ValueError:
            return f"error: bad tool name {qualified_name}"
        srv = self.servers.get(server_name)
        if not srv or srv.status != "ok":
            return f"error: MCP server '{server_name}' not connected"
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            args = {}
        try:
            return srv.call_tool(tool_name, args, timeout=timeout)
        except Exception as exc:
            return f"error: {exc}"

    def status_lines(self) -> List[str]:
        lines = []
        for name, srv in self.servers.items():
            if srv.status == "ok":
                lines.append((name, f"ok · {len(srv.tools)} tools"))
            elif srv.status == "error":
                lines.append((name, f"error · {srv.error[:40]}"))
            else:
                lines.append((name, srv.status))
        return lines

    def close_all(self) -> None:
        for srv in self.servers.values():
            srv.close()
