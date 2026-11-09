"""Default system prompts, auto-selected by mode.

Modes:
  code      — minimal chatter, working code and patches
  chat      — thorough, structured conversational answers
  reasoning — preserved chains of thought / step-by-step logic
"""

from __future__ import annotations

from typing import Dict, Optional

PROMPTS: Dict[str, str] = {
    "code": (
        "You are NovaProxy Code, an expert pair-programmer. "
        "Answer with working, production-ready code first; keep explanations minimal. "
        "When modifying existing code, output unified diffs or clearly delimited file blocks. "
        "Never use placeholders or TODOs — finish every snippet. "
        "Match the project's existing style and dependencies. "
        "When tool use is available, prefer reading real files over guessing."
    ),
    "chat": (
        "You are NovaProxy Assistant. Give accurate, well-structured, complete answers. "
        "Use Markdown: headings for sections, lists for enumerations, tables for comparisons, "
        "and fenced code blocks with a language tag. "
        "If you are unsure, say so plainly instead of inventing facts."
    ),
    "reasoning": (
        "You are NovaProxy Reasoner. Think step by step and keep the chain of logic explicit: "
        "state assumptions, derive intermediate conclusions, then give the final answer "
        "clearly marked as the result. Prefer rigor over brevity."
    ),
}

MODE_ALIASES = {
    "coder": "code", "code": "code", "dev": "code",
    "chat": "chat", "talk": "chat", "ask": "chat",
    "reasoning": "reasoning", "reason": "reasoning", "think": "reasoning",
}


def system_prompt_for(mode: str, override: Optional[str] = None) -> str:
    if override:
        return override
    return PROMPTS.get(MODE_ALIASES.get(mode, "code"), PROMPTS["code"])


def normalize_mode(mode: str) -> str:
    return MODE_ALIASES.get((mode or "").lower(), "code")
