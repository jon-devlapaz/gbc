import sqlite3
from pathlib import Path
from app.taxonomy import write_taxonomy


def _seed(db: sqlite3.Connection) -> int:
    db.execute("INSERT INTO scans(started_at,finished_at) VALUES ('2026-04-23T00:00','2026-04-23T00:05')")
    scan_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    rows = [
        (scan_id, "/x/sessions", "dir", 1, 1_000_000, "2026-04-22T00:00", 42, "[]", "harness_protected", "name match", "Claude Code session transcripts.", None),
        (scan_id, "/x/unknown-thing", "dir", 2, 500, "2024-01-01T00:00", 3, "[]", "unknown", "deny-by-default", "(not reasoned)", None),
    ]
    db.executemany(
        "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status,reason,purpose,user_decision) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    return scan_id


def test_taxonomy_emits_section_per_entry(db, tmp_path):
    scan_id = _seed(db)
    out = tmp_path / "taxonomy.md"
    write_taxonomy(db, scan_id, out)
    text = out.read_text()
    assert "sessions" in text
    assert "unknown-thing" in text
    assert "harness_protected" in text
    assert "unknown" in text


def test_taxonomy_atomic_write(db, tmp_path):
    scan_id = _seed(db)
    out = tmp_path / "taxonomy.md"
    write_taxonomy(db, scan_id, out)
    assert not (tmp_path / "taxonomy.md.tmp").exists()


def test_taxonomy_sorts_deterministically(db, tmp_path):
    scan_id = _seed(db)
    out = tmp_path / "taxonomy.md"
    write_taxonomy(db, scan_id, out)
    text = out.read_text()
    assert text.index("sessions") < text.index("unknown-thing")
