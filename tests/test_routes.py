from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "paste-cache").mkdir()
    (claude / "paste-cache" / "x.txt").write_text("x")
    (claude / "sessions").mkdir()

    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")

    from app.main import create_app
    app = create_app()
    return TestClient(app)


def test_home_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "scan" in r.text.lower()


def test_scan_creates_entries(client):
    r = client.post("/scan")
    assert r.status_code == 200
    assert "paste-cache" in r.text
    assert "sessions" in r.text


def test_execute_dry_run_does_not_delete(client, tmp_path):
    scan = client.post("/scan")
    assert scan.status_code == 200
    scan_id = _latest_scan_id(tmp_path)
    entry_ids = _kill_candidate_ids(tmp_path, scan_id)
    r = client.post(
        f"/execute/{scan_id}",
        data={"entry_id": [str(eid) for eid in entry_ids], "armed": "false"},
    )
    assert r.status_code == 200
    assert (tmp_path / ".claude" / "paste-cache").exists()


def test_entry_detail_renders(client, tmp_path):
    client.post("/scan")
    scan_id = _latest_scan_id(tmp_path)
    entry_ids = _kill_candidate_ids(tmp_path, scan_id)
    assert entry_ids
    r = client.get(f"/entry/{entry_ids[0]}")
    assert r.status_code == 200
    assert "Metadata" in r.text
    assert "Action history" in r.text
    assert "Inode" in r.text


def test_entry_detail_not_found(client):
    r = client.get("/entry/999999")
    assert r.status_code == 404


def test_execute_armed_deletes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    client.post("/scan")
    scan_id = _latest_scan_id(tmp_path)
    entry_ids = _kill_candidate_ids(tmp_path, scan_id)
    r = client.post(
        f"/execute/{scan_id}",
        data={"entry_id": [str(eid) for eid in entry_ids], "armed": "true"},
    )
    assert r.status_code == 200
    assert not (tmp_path / ".claude" / "paste-cache").exists()


def _latest_scan_id(tmp_path: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    return conn.execute("SELECT MAX(id) FROM scans").fetchone()[0]


def _kill_candidate_ids(tmp_path: Path, scan_id: int) -> list[int]:
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    rows = conn.execute(
        "SELECT id FROM entries WHERE scan_id=? AND status='kill_candidate'", (scan_id,)
    ).fetchall()
    return [r[0] for r in rows]
