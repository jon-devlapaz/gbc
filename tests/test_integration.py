# tests/test_integration.py
import json
import subprocess
import sqlite3
from pathlib import Path
from datetime import datetime
import pytest
from app.db import connect
from app.scanner import walk
from app.classifier import classify
from app.executor import Executor
from app.taxonomy import write_taxonomy


def test_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "sessions").mkdir()
    (claude / "paste-cache").mkdir()
    for i in range(3):
        (claude / "paste-cache" / f"p{i}.txt").write_text("x")
    (claude / "history.jsonl").write_text("x")

    data = tmp_path / "data"
    db = connect(data / "workspace.db")

    # scan
    cur = db.execute("INSERT INTO scans(started_at) VALUES (?)", (datetime.now().isoformat(),))
    scan_id = cur.lastrowid
    db.commit()

    entries = walk(claude)
    kill_ids = []
    for entry in entries:
        v = classify(entry)
        cur = db.execute(
            "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status,reason,purpose) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (scan_id, entry.path, entry.kind.value, entry.inode, entry.size_bytes,
             entry.mtime.isoformat(), entry.file_count, json.dumps(entry.sample_files),
             v.status.value, v.reason, "(test)"),
        )
        if v.status.value == "kill_candidate":
            kill_ids.append(cur.lastrowid)
    db.commit()
    assert kill_ids, "expected at least one kill_candidate (paste-cache)"

    # dry-run
    ex = Executor(db=db, claude_root=claude, data_dir=data)
    dry = ex.run(scan_id=scan_id, entry_ids=kill_ids, armed=False)
    assert (claude / "paste-cache").exists()
    assert all(a.state == "planned" for a in dry.actions)

    # armed
    real = ex.run(scan_id=scan_id, entry_ids=kill_ids, armed=True)
    assert not (claude / "paste-cache").exists()
    assert Path(real.archive_path).exists()

    # taxonomy
    out = data / "taxonomy.md"
    write_taxonomy(db, scan_id, out)
    text = out.read_text()
    assert "paste-cache" in text
    assert "sessions" in text

    # archive verifiable
    subprocess.run(["tar", "tzf", real.archive_path], check=True)
