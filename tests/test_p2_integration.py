# tests/test_p2_integration.py
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    projects = claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app"
    projects.mkdir(parents=True)

    def write(sid, cwd, prompts):
        f = projects / f"{sid}.jsonl"
        events = [
            {"sessionId": sid, "type": "user", "timestamp": f"2026-01-0{i+1}T00:00:00Z",
             "cwd": cwd, "message": {"role": "user", "content": p}}
            for i, p in enumerate(prompts)
        ]
        f.write_text("\n".join(json.dumps(e) for e in events))

    write("uuid-1", "/Users/jondev/dev/socratink/prod/socratink-app",
          ["auth bug in middleware", "add stripe webhook"])
    write("uuid-2", "/Users/jondev/dev/socratink/prod/socratink-app",
          ["refactor learning graph"])

    # Different family
    other = claude / "projects" / "-Users-jondev-dev-other"
    other.mkdir()
    (other / "uuid-3.jsonl").write_text(json.dumps(
        {"sessionId": "uuid-3", "type": "user", "timestamp": "2026-01-04T00:00:00Z",
         "cwd": "/Users/jondev/dev/other",
         "message": {"role": "user", "content": "unrelated work"}}
    ))

    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_end_to_end_session_index(client):
    # 1. Visit home → reindexes lazily
    r = client.get("/")
    assert r.status_code == 200

    # 2. Dashboard shows 3 sessions
    assert "3" in r.text or "socratink-app" in r.text

    # 3. Search for "stripe" finds uuid-1
    r = client.get("/sessions", params={"q": "stripe"})
    assert "uuid-1" in r.text
    assert "uuid-2" not in r.text

    # 4. Family filter to socratink-app excludes uuid-3
    r = client.get("/sessions", params={"family": "socratink-app"})
    assert "uuid-1" in r.text and "uuid-2" in r.text
    assert "uuid-3" not in r.text

    # 5. Detail page streams transcript
    r = client.get("/sessions/uuid-1")
    assert "auth bug" in r.text

    # 6. Reindex is idempotent
    r = client.post("/reindex")
    assert r.status_code == 200
