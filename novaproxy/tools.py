"""Built-in local tools (function calling) and context helpers.

Implements the file/shell tools the model can invoke when it supports
function calling, plus @file context attachment and diff utilities.
"""

from __future__ import annotations

import difflib
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_FILE_BYTES = 200_000
MAX_TOOL_OUTPUT = 12_000


def _truncate(s: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated, {len(s) - limit} chars omitted]"


def tool_read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"error: no such file: {path}"
    if p.is_dir():
        entries = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
        return _truncate("\n".join(entries))
    data = p.read_bytes()[:MAX_FILE_BYTES]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"error: {exc}"


def tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"ok: wrote {len(content)} bytes to {path}"


def tool_edit_file(path: str, old: str, new: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"error: no such file: {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    if old not in text:
        return "error: old_string not found in file"
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    return f"ok: edited {path}"


def tool_bash(command: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr else "")).strip()
        return _truncate(out or f"(exit {r.returncode}, no output)")
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    except Exception as exc:
        return f"error: {exc}"


def tool_list_dir(path: str = ".") -> str:
    return tool_read_file(path)


def tool_search(pattern: str, path: str = ".") -> str:
    try:
        r = subprocess.run(
            ["grep", "-rn", "--", pattern, path],
            capture_output=True, text=True, timeout=30,
        )
        return _truncate(r.stdout.strip() or "(no matches)")
    except FileNotFoundError:
        # grep missing (rare): fall back to a python scan
        hits = []
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    for i, line in enumerate(open(fp, encoding="utf-8", errors="ignore"), 1):
                        if pattern in line:
                            hits.append(f"{fp}:{i}:{line.rstrip()}")
                except OSError:
                    continue
        return _truncate("\n".join(hits) or "(no matches)")
    except Exception as exc:
        return f"error: {exc}"


def tool_web_search(query: str) -> str:
    try:
        import urllib.parse
        import requests
        url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        import re
        results = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text)[:5]
        clean = [re.sub(r"<[^>]+>", "", x) for x in results]
        return "\n".join(f"{i+1}. {t}" for i, t in enumerate(clean)) or "(no results)"
    except Exception as exc:
        return f"error: {exc}"


BUILTIN_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file's contents (or list a directory).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write text content to a file (creates parents).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace the first occurrence of old with new in a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command and return its output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "search",
        "description": "Search files recursively for a substring/regex.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web (DuckDuckGo) and return top results.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
]

_DISPATCH = {
    "read_file": lambda a: tool_read_file(a.get("path", "")),
    "write_file": lambda a: tool_write_file(a.get("path", ""), a.get("content", "")),
    "edit_file": lambda a: tool_edit_file(a.get("path", ""), a.get("old", ""), a.get("new", "")),
    "bash": lambda a: tool_bash(a.get("command", ""), int(a.get("timeout", 60) or 60)),
    "list_dir": lambda a: tool_list_dir(a.get("path", ".")),
    "search": lambda a: tool_search(a.get("pattern", ""), a.get("path", ".")),
    "web_search": lambda a: tool_web_search(a.get("query", "")),
}


def dispatch_builtin(name: str, arguments_json: str) -> str:
    import json
    fn = _DISPATCH.get(name)
    if not fn:
        return f"error: unknown tool {name}"
    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError:
        args = {}
    try:
        return fn(args)
    except Exception as exc:
        return f"error: {exc}"


# ---------------------------------------------------------------- @context
def expand_at_references(text: str) -> Tuple[str, List[Tuple[str, int]]]:
    """Replace @path tokens with file contents. Returns (new_text, [(path, lines)])."""
    out_tokens: List[str] = []
    attached: List[Tuple[str, int]] = []
    for tok in text.split():
        if tok.startswith("@") and len(tok) > 1:
            path = tok[1:]
            p = Path(path).expanduser()
            try:
                if p.is_dir():
                    listing = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
                    body = "\n".join(listing)
                    attached.append((path, len(listing)))
                else:
                    body = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
                    attached.append((path, body.count("\n") + 1))
                out_tokens.append(f"\n--- {path} ---\n{body}\n--- end {path} ---\n")
                continue
            except OSError:
                pass
        out_tokens.append(tok)
    return " ".join(out_tokens), attached


# ---------------------------------------------------------------- diffs
def unified_diff(old: str, new: str, path: str = "file") -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


def git_diff_stat() -> str:
    try:
        r = subprocess.run("git diff --stat", shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr).strip() or "нет изменений"
    except Exception as exc:
        return f"error: {exc}"
