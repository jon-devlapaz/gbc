from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_families_page_renders(client):
    r = client.get("/families")
    assert r.status_code == 200
    assert "Families" in r.text


def test_post_family_creates_override(client, tmp_path):
    r = client.post("/families", data={"name": "socratink", "path_prefix": "/Users/jondev/dev/socratink"})
    assert r.status_code == 200
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = conn.execute("SELECT * FROM families WHERE name='socratink'").fetchone()
    assert row is not None


def test_post_redact_deletes_matching(client, tmp_path):
    # First trigger schema creation via a GET
    client.get("/families")
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    conn.execute(
        "INSERT INTO sessions(session_id, jsonl_path, jsonl_mtime, indexed_at) VALUES ('s1','/x', 0.0, 'now')"
    )
    conn.execute(
        "INSERT INTO prompts_fts(session_id, timestamp, content) VALUES ('s1','t','my-secret-token-abc')"
    )
    conn.commit()
    r = client.post("/sessions/redact", data={"pattern": "%secret%"})
    assert r.status_code == 200
    n = conn.execute("SELECT COUNT(*) FROM prompts_fts WHERE content LIKE '%secret%'").fetchone()[0]
    assert n == 0
