import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  inode INTEGER NOT NULL,
  size_bytes INTEGER,
  mtime TEXT,
  file_count INTEGER,
  sample_files TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  purpose TEXT,
  user_decision TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS entries_scan_path ON entries(scan_id, path);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  entry_id INTEGER REFERENCES entries(id),
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  path TEXT NOT NULL,
  archive_path TEXT,
  state TEXT NOT NULL,
  error_detail TEXT
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn
