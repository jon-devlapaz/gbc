import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    (claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app").mkdir(parents=True)
    f = claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app" / "uuid-abc.jsonl"
    ev = [
        {"sessionId": "uuid-abc", "type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "cwd": "/Users/jondev/dev/socratink/prod/socratink-app",
         "message": {"role": "user", "content": "find the auth bug"}},
        {"sessionId": "uuid-abc", "type": "assistant", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": "checking middleware"}},
    ]
    f.write_text("\n".join(json.dumps(e) for e in ev))
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_sessions_list_renders(client):
    r = client.get("/sessions")
    assert r.status_code == 200
    assert "uuid-abc" in r.text
    assert "find the auth bug" in r.text


def test_sessions_search_filters(client):
    r = client.get("/sessions", params={"q": "auth"})
    assert r.status_code == 200
    assert "uuid-abc" in r.text
    r2 = client.get("/sessions", params={"q": "missingzzz"})
    assert "uuid-abc" not in r2.text


def test_sessions_family_filter(client):
    r = client.get("/sessions", params={"family": "socratink-app"})
    assert "uuid-abc" in r.text


def test_session_detail_renders(client):
    client.get("/sessions")  # trigger reindex first
    r = client.get("/sessions/uuid-abc")
    assert r.status_code == 200
    assert "find the auth bug" in r.text


def test_reindex_post(client):
    r = client.post("/reindex")
    assert r.status_code == 200
