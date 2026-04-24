import pytest
from app.llm import select_provider


def _clear_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "CLAUDE_TOOL_REASONER", "CLAUDE_TOOL_DISABLE_REASONER"):
        monkeypatch.delenv(k, raising=False)


def test_select_returns_none_when_no_keys(monkeypatch):
    _clear_env(monkeypatch)
    assert select_provider() is None


def test_select_returns_none_when_disabled(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    assert select_provider() is None


def test_select_prefers_anthropic_in_auto(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    sel = select_provider()
    assert sel is not None
    assert sel[0] == "anthropic"


def test_select_falls_through_to_gemini_when_only_gemini(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    sel = select_provider()
    assert sel is not None
    assert sel[0] == "gemini"


def test_select_explicit_gemini(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    monkeypatch.setenv("CLAUDE_TOOL_REASONER", "gemini")
    sel = select_provider()
    assert sel is not None
    assert sel[0] == "gemini"


def test_select_explicit_anthropic_no_key_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_TOOL_REASONER", "anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "y")  # other provider has key but pref is anthropic
    assert select_provider() is None
