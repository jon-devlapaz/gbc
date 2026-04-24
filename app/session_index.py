from __future__ import annotations
import fcntl
import sqlite3
from datetime import datetime
from pathlib import Path
from app.events import parse_file
from app.families import FamilyOverride, detect


def _load_overrides(db: sqlite3.Connection) -> list[FamilyOverride]:
    rows = db.execute(
        "SELECT name, path_prefix FROM families WHERE is_override=1"
    ).fetchall()
    return [FamilyOverride(name=r["name"], path_prefix=r["path_prefix"]) for r in rows]


def _db_dir(db: sqlite3.Connection) -> Path:
    """Resolve the directory containing the SQLite db file."""
    for row in db.execute("PRAGMA database_list"):
        # row: (seq, name, file_path)
        if row[1] == "main" and row[2]:
            return Path(row[2]).parent
    return Path.cwd()


def _record_start(db: sqlite3.Connection) -> int:
    cur = db.execute(
        "INSERT INTO index_runs(started_at) VALUES (?)",
        (datetime.now().isoformat(),),
    )
    db.commit()
    return cur.lastrowid


def _record_end(db: sqlite3.Connection, run_id: int, seen: int, updated: int, errors: int) -> None:
    db.execute(
        "UPDATE index_runs SET finished_at=?, files_seen=?, files_updated=?, error_count=? WHERE id=?",
        (datetime.now().isoformat(), seen, updated, errors, run_id),
    )
    db.commit()


def reindex(db: sqlite3.Connection, claude_root: Path, force_rebuild: bool = False) -> None:
    projects = claude_root / "projects"
    if not projects.exists():
        return

    lock_dir = _db_dir(db)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".index.lock"

    with open(lock_path, "w") as lock_fp:
        fcntl.flock(lock_fp, fcntl.LOCK_EX)
        try:
            run_id = _record_start(db)
            overrides = _load_overrides(db)

            on_disk: dict[str, float] = {}
            for f in projects.rglob("*.jsonl"):
                try:
                    on_disk[str(f)] = f.stat().st_mtime
                except OSError:
                    continue

            # Remove DB entries whose files vanished
            for row in db.execute("SELECT session_id, jsonl_path FROM sessions").fetchall():
                if row["jsonl_path"] not in on_disk:
                    db.execute("DELETE FROM sessions WHERE session_id=?", (row["session_id"],))
                    db.execute("DELETE FROM prompts_fts WHERE session_id=?", (row["session_id"],))
            db.commit()

            existing = {
                r["jsonl_path"]: r["jsonl_mtime"]
                for r in db.execute("SELECT jsonl_path, jsonl_mtime FROM sessions").fetchall()
            }

            updated = 0
            total_errors = 0

            for jsonl_str, mtime in on_disk.items():
                if not force_rebuild and existing.get(jsonl_str) == mtime:
                    continue
                jsonl_path = Path(jsonl_str)
                pr = parse_file(jsonl_path)
                if pr.session.cwd:
                    family_map = detect([pr.session.cwd], overrides=overrides)
                    family = family_map.get(pr.session.cwd, "unsorted")
                else:
                    family = "unsorted"

                db.execute("DELETE FROM sessions WHERE jsonl_path=?", (jsonl_str,))
                db.execute("DELETE FROM prompts_fts WHERE session_id=?", (pr.session.session_id,))
                db.execute(
                    """INSERT INTO sessions
                       (session_id, family, cwd, started_at, ended_at, message_count,
                        prompt_count, first_prompt, jsonl_path, jsonl_mtime, indexed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (pr.session.session_id, family, pr.session.cwd,
                     pr.session.started_at, pr.session.ended_at,
                     pr.session.message_count, pr.session.prompt_count, pr.session.first_prompt,
                     jsonl_str, mtime, datetime.now().isoformat()),
                )
                for p in pr.prompts:
                    db.execute(
                        "INSERT INTO prompts_fts(session_id, timestamp, content) VALUES (?, ?, ?)",
                        (p.session_id, p.timestamp, p.content),
                    )
                db.commit()
                updated += 1
                total_errors += len(pr.errors)

            _record_end(db, run_id, seen=len(on_disk), updated=updated, errors=total_errors)
        finally:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)
