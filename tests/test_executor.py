import os
import subprocess
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import pytest
from app.executor import Executor, ExecutorError
from app.models import Entry, EntryKind, Status


def _insert_entry(db: sqlite3.Connection, scan_id: int, path: str, status: str, inode: int, mtime_iso: str) -> int:
    cur = db.execute(
        "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (scan_id, path, "dir", inode, 0, mtime_iso, 0, "[]", status),
    )
    db.commit()
    return cur.lastrowid


def _start_scan(db: sqlite3.Connection) -> int:
    cur = db.execute("INSERT INTO scans(started_at) VALUES (?)", (datetime.now().isoformat(),))
    db.commit()
    return cur.lastrowid


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    root.mkdir()
    (root / "paste-cache").mkdir()
    (root / "paste-cache" / "x.txt").write_text("x")
    return root


def test_executor_refuses_non_kill_candidate(db, claude_root, tmp_path):
    scan_id = _start_scan(db)
    p = claude_root / "sessions"
    p.mkdir()
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "harness_protected", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)
    assert result.executed == []
    assert any(a.state == "skipped" for a in result.actions)


def test_executor_refuses_realpath_outside_claude(db, claude_root, tmp_path):
    scan_id = _start_scan(db)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = claude_root / "bad"
    link.symlink_to(outside)
    st = link.lstat()
    eid = _insert_entry(db, scan_id, str(link), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)
    assert outside.exists()
    assert any(a.state == "skipped" for a in result.actions)


def test_executor_refuses_inode_mismatch(db, claude_root, tmp_path):
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino + 999, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)
    assert p.exists()
    assert any(a.state == "skipped" and "inode" in (a.error_detail or "") for a in result.actions)


def test_executor_dry_run_does_not_touch_disk(db, claude_root, tmp_path):
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=False)
    assert p.exists()
    assert all(a.state == "planned" for a in result.actions)


def test_executor_happy_path_archives_and_deletes(db, claude_root, tmp_path, monkeypatch):
    # Redirect Path.home() so the archive lands in tmp_path, not the real home dir.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)

    assert not p.exists()
    assert result.archive_path and Path(result.archive_path).exists()
    subprocess.run(["tar", "tzf", result.archive_path], check=True)
    rows = db.execute("SELECT state FROM actions WHERE entry_id=?", (eid,)).fetchall()
    states = [r[0] for r in rows]
    assert "executed" in states


def test_executor_aborts_run_on_corrupt_tar(db, claude_root, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")

    orig_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        result = orig_run(cmd, *a, **kw)
        if cmd[:2] == ["tar", "czf"]:
            Path(cmd[2]).write_bytes(b"not a real tar")
        return result

    monkeypatch.setattr("app.executor.subprocess.run", fake_run)

    with pytest.raises(ExecutorError):
        ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)
    assert p.exists()
