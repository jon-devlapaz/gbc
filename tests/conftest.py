import os
import sqlite3
from pathlib import Path
import pytest
from app.db import init_schema


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """Mimic a ~/.claude/ tree with dirs, top-level files, symlinks, perm-denied dir."""
    root = tmp_path / ".claude"
    root.mkdir()
    (root / "sessions").mkdir()
    (root / "sessions" / "s1.jsonl").write_text("line\n")
    (root / "paste-cache").mkdir()
    for i in range(3):
        (root / "paste-cache" / f"p{i}.txt").write_text("x")
    (root / "history.jsonl").write_text("abc")
    (root / "settings.json").write_text("{}")
    # symlink loop
    (root / "loop").symlink_to(root)
    # perm-denied dir
    locked = root / "locked"
    locked.mkdir()
    (locked / "hidden").write_text("x")
    locked.chmod(0o000)
    yield root
    locked.chmod(0o755)
