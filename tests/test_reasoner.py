import os
from datetime import datetime
from unittest.mock import MagicMock
import pytest
from app.reasoner import Reasoner, build_prompt
from app.models import Entry, EntryKind


def _entry(**kw) -> Entry:
    base = dict(
        path="/Users/jondev/.claude/mystery",
        kind=EntryKind.DIR,
        inode=1,
        size_bytes=500_000,
        mtime=datetime(2025, 1, 1),
        file_count=42,
        sample_files=["a.txt", "b.log"],
    )
    base.update(kw)
    return Entry(**base)


def test_prompt_contains_only_metadata():
    prompt = build_prompt(_entry())
    assert "mystery" in prompt
    assert "a.txt" in prompt
    # guard against accidental content inclusion
    assert "read the file" not in prompt.lower()
    assert "contents:" not in prompt.lower()


def test_reasoner_caches_same_entry():
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text="A cache dir.")]
    r = Reasoner(client=client)
    e = _entry()
    r.purpose(e)
    r.purpose(e)
    assert client.messages.create.call_count == 1


def test_reasoner_handles_api_failure():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    r = Reasoner(client=client)
    assert r.purpose(_entry()) == "(reasoner unavailable)"


def test_reasoner_honors_env_kill_switch(monkeypatch):
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    client = MagicMock()
    r = Reasoner(client=client)
    assert r.purpose(_entry()) == "(reasoner disabled)"
    client.messages.create.assert_not_called()


def test_reasoner_cost_cap():
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text="x")]
    r = Reasoner(client=client, call_cap=2)
    for i in range(5):
        r.purpose(_entry(path=f"/p/{i}", inode=i))
    assert client.messages.create.call_count == 2
