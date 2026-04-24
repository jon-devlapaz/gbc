import json
import os
import time
from pathlib import Path
from app.db import connect
from app.session_index import reindex


def _write_session(claude: Path, cwd: str, sid: str, prompts: list[str]) -> Path:
    flat = cwd.replace("/", "-")
    proj = claude / "projects" / flat
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    events = [
        {"sessionId": sid, "type": "user", "timestamp": f"2026-01-01T00:00:0{i}Z",
         "cwd": cwd,
         "message": {"role": "user", "content": p}}
        for i, p in enumerate(prompts)
    ]
    f.write_text("\n".join(json.dumps(e) for e in events))
    return f


def test_reindex_happy_path(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/socratink/prod/socratink-app", "uuid1",
                   ["hello world", "fix the bug"])

    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)

    sessions = db.execute("SELECT * FROM sessions").fetchall()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "uuid1"
    assert sessions[0]["prompt_count"] == 2

    prompts = db.execute("SELECT * FROM prompts_fts").fetchall()
    assert len(prompts) == 2


def test_reindex_idempotent(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a", "b"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    n1 = db.execute("SELECT COUNT(*) FROM prompts_fts").fetchone()[0]
    reindex(db, claude)
    n2 = db.execute("SELECT COUNT(*) FROM prompts_fts").fetchone()[0]
    assert n1 == n2


def test_reindex_picks_up_mtime_bump(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    f = _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    assert db.execute("SELECT prompt_count FROM sessions").fetchone()[0] == 1

    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a", "b", "c"])
    ts = time.time() + 10
    os.utime(f, (ts, ts))

    reindex(db, claude)
    assert db.execute("SELECT prompt_count FROM sessions").fetchone()[0] == 3


def test_reindex_removes_deleted_file(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    f = _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    f.unlink()
    reindex(db, claude)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_reindex_records_run(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    runs = db.execute("SELECT * FROM index_runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["finished_at"] is not None
    assert runs[0]["files_seen"] == 1
    assert runs[0]["files_updated"] == 1
