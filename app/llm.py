"""LLM provider adapters for the reasoner.

Each factory returns a callable `(prompt: str) -> str` so the rest of the app
stays provider-agnostic. Reasoner just calls `call_fn(prompt)`.
"""
from __future__ import annotations
import os
from typing import Callable

ANTHROPIC_MODEL = "claude-haiku-4-5"
GEMINI_MODEL = "gemini-2.5-flash"


def make_anthropic_caller() -> Callable[[str], str] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    client = Anthropic()

    def call(prompt: str) -> str:
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    return call


def make_gemini_caller() -> Callable[[str], str] | None:
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
    except ImportError:
        return None
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def call(prompt: str) -> str:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return (resp.text or "").strip()

    return call


def select_provider() -> tuple[str, Callable[[str], str]] | None:
    """Returns (provider_name, call_fn) honoring CLAUDE_TOOL_REASONER preference.

    `auto` (default) picks anthropic first, then gemini.
    Explicit `anthropic` or `gemini` only tries that provider.
    Returns None if no provider is available.
    """
    if os.environ.get("CLAUDE_TOOL_DISABLE_REASONER") == "1":
        return None

    pref = os.environ.get("CLAUDE_TOOL_REASONER", "auto").lower()

    if pref == "anthropic":
        call = make_anthropic_caller()
        return ("anthropic", call) if call else None
    if pref == "gemini":
        call = make_gemini_caller()
        return ("gemini", call) if call else None

    # auto
    call = make_anthropic_caller()
    if call:
        return ("anthropic", call)
    call = make_gemini_caller()
    if call:
        return ("gemini", call)
    return None
