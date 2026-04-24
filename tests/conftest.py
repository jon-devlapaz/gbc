import os
import sqlite3
from pathlib import Path
import urllib.parse
from typing import Any, Mapping

import pytest
from app.db import init_schema

# ---------------------------------------------------------------------------
# Compatibility shim: httpx ≥0.27 deprecated data=<list-of-tuples> as raw
# content instead of form-encoded data.  The tests use the old API.  Patch
# encode_request once per session so list-of-tuples is URL-encoded properly.
# ---------------------------------------------------------------------------
import httpx._content as _hx_content

_orig_encode_request = _hx_content.encode_request


def _patched_encode_request(
    content=None, data=None, files=None, json=None, boundary=None, **kw
):
    # If data is a non-Mapping iterable of 2-tuples, convert to a dict of
    # lists so encode_urlencoded_data handles it correctly.
    if data is not None and not isinstance(data, Mapping):
        try:
            items = list(data)
            if items and isinstance(items[0], (list, tuple)) and len(items[0]) == 2:
                converted: dict[str, list[Any]] = {}
                for k, v in items:
                    converted.setdefault(k, []).append(v)
                data = {k: (vlist[0] if len(vlist) == 1 else vlist) for k, vlist in converted.items()}
        except (TypeError, ValueError):
            pass
    return _orig_encode_request(content=content, data=data, files=files, json=json, boundary=boundary, **kw)


_hx_content.encode_request = _patched_encode_request

# httpx._models imports encode_request via `from ._content import ... encode_request`
# so we must patch it on the _models module too.
try:
    import httpx._models as _hx_models
    _hx_models.encode_request = _patched_encode_request
except Exception:
    pass


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
