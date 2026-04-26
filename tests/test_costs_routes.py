from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_costs_page_renders_empty(client):
    r = client.get("/costs")
    assert r.status_code == 200
    assert "COSTS" in r.text or "Costs" in r.text
    assert "$0.00" in r.text or "no cost events" in r.text.lower()


def test_costs_partial_returns_fragment(client):
    r = client.get("/costs/partial")
    assert r.status_code == 200
    # Partial should NOT include the full base layout
    assert "<html" not in r.text.lower()
    assert "TODAY" in r.text or "today" in r.text.lower()


def test_costs_page_after_ingest(client):
    payload = {"events": [{
        "message_uuid": "u1", "session_id": "s1", "jsonl_path": "/p",
        "ts": "2026-04-25T12:00:00Z", "model": "claude-opus-4-7", "service_tier": "standard",
        "input_tokens": 1_000_000, "output_tokens": 0,
        "cache_creation_5m_tokens": 0, "cache_creation_1h_tokens": 0, "cache_read_tokens": 0,
    }]}
    client.post("/ingest/usage", json=payload)
    r = client.get("/costs/partial")
    assert "$15.00" in r.text  # 1M input tokens at opus standard = $15
