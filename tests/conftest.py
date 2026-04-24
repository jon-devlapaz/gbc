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
