# P1 Workspace Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI + HTMX tool that scans `~/.claude/`, classifies entries as harness-protected / kill-candidate / active / unknown, safely archives+deletes user-approved kill-candidates, and emits a canonical taxonomy.md. Foundation for future P2 (session search) and P3 (insight extraction).

**Architecture:** Five pure-Python modules behind a thin FastAPI web layer. SQLite persistence. Deny-by-default destructive path (only hardcoded `kill_candidate` names deletable, gated by TOCTOU inode+mtime re-check). Reasoner sends metadata only to Claude Haiku, never file contents. Three web endpoints: `/scan`, `/review/{scan_id}`, `/execute/{scan_id}`, plus `/explain/{entry_id}`.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, SQLite (stdlib `sqlite3`), Pydantic v2, Anthropic SDK (haiku-4-5), pytest, pytest-asyncio, httpx (TestClient).

**Spec:** `docs/superpowers/specs/2026-04-23-claude-workspace-tool-p1-audit-design.md`

---

## File Structure

```
claude-workspace-tool/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app + routes
│   ├── db.py                   # SQLite connection + schema
│   ├── models.py               # Pydantic models (Entry, Verdict, Action)
│   ├── scanner.py              # walk ~/.claude/
│   ├── classifier.py           # rule engine
│   ├── reasoner.py             # metadata-only LLM purpose
│   ├── executor.py             # archive + delete gauntlet
│   ├── taxonomy.py             # markdown writer
│   ├── allowlists.py           # hardcoded harness + kill-candidate lists
│   └── templates/
│       ├── base.html
│       ├── home.html
│       └── review.html
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # fixtures (tmp_path tree, fake db)
│   ├── test_scanner.py
│   ├── test_classifier.py
│   ├── test_reasoner.py
│   ├── test_executor.py
│   ├── test_taxonomy.py
│   ├── test_db.py
│   ├── test_routes.py
│   └── test_integration.py
├── data/
│   └── .gitkeep
├── static/
│   └── style.css
├── pyproject.toml
├── .gitignore
└── README.md
```

Each module is one file, one clear responsibility. `allowlists.py` is isolated so changes to the kill-list leave a git diff trail.

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `tests/__init__.py`
- Create: `data/.gitkeep`
- Create: `README.md`

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "claude-workspace-tool"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "jinja2>=3.1",
  "pydantic>=2.6",
  "anthropic>=0.40",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "httpx>=0.27",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
data/*.db
data/*.md
data/.scan.lock
!data/.gitkeep
.env
```

- [ ] **Step 3: Create empty `app/__init__.py` and `tests/__init__.py`**

```bash
touch app/__init__.py tests/__init__.py data/.gitkeep
```

- [ ] **Step 4: Write minimal README.md**

```markdown
# claude-workspace-tool

Local FastAPI + HTMX tool for auditing and cleaning `~/.claude/`.

## Run

```
pip install -e ".[dev]"
uvicorn app.main:app --port 7878
```

Open http://localhost:7878.

## Test

```
pytest
```
```

- [ ] **Step 5: Install + verify**

Run: `pip install -e ".[dev]" && pytest --version`
Expected: pytest version prints, no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore app/__init__.py tests/__init__.py data/.gitkeep README.md
git commit -m "chore: project scaffold"
```

---

## Task 2: Pydantic Models

**Files:**
- Create: `app/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_models.py
from datetime import datetime
from app.models import Entry, Verdict, Action, EntryKind, Status, ActionState


def test_entry_roundtrip():
    e = Entry(
        path="/Users/jondev/.claude/paste-cache",
        kind=EntryKind.DIR,
        inode=123456,
        size_bytes=4096,
        mtime=datetime(2026, 1, 1),
        file_count=84,
        sample_files=["a.txt", "b.txt"],
    )
    assert e.kind == "dir"
    assert e.sample_files == ["a.txt", "b.txt"]


def test_verdict_statuses():
    v = Verdict(status=Status.KILL_CANDIDATE, reason="matches paste-cache allowlist")
    assert v.status == "kill_candidate"


def test_action_state_enum():
    a = Action(
        scan_id=1,
        entry_id=2,
        ts=datetime.now(),
        action="delete",
        path="/tmp/x",
        state=ActionState.PLANNED,
    )
    assert a.state == "planned"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`.

- [ ] **Step 3: Implement `app/models.py`**

```python
# app/models.py
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class EntryKind(str, Enum):
    DIR = "dir"
    FILE = "file"


class Status(str, Enum):
    HARNESS_PROTECTED = "harness_protected"
    KILL_CANDIDATE = "kill_candidate"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class ActionState(str, Enum):
    PLANNED = "planned"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Entry(BaseModel):
    id: int | None = None
    scan_id: int | None = None
    path: str
    kind: EntryKind
    inode: int
    size_bytes: int
    mtime: datetime
    file_count: int
    sample_files: list[str] = Field(default_factory=list)
    status: Status | None = None
    reason: str | None = None
    purpose: str | None = None
    user_decision: str | None = None


class Verdict(BaseModel):
    status: Status
    reason: str


class Action(BaseModel):
    id: int | None = None
    scan_id: int
    entry_id: int | None
    ts: datetime
    action: str  # archive | delete
    path: str
    archive_path: str | None = None
    state: ActionState
    error_detail: str | None = None
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_models.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat(models): add Entry, Verdict, Action pydantic models"
```

---

## Task 3: Allowlists Module

**Files:**
- Create: `app/allowlists.py`
- Create: `tests/test_allowlists.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_allowlists.py
from app.allowlists import HARNESS_PROTECTED, KILL_CANDIDATES, is_harness_protected, is_kill_candidate


def test_harness_names_present():
    for name in ["sessions", "projects", "history.jsonl", "settings.json", "hooks", "plugins", "skills"]:
        assert name in HARNESS_PROTECTED


def test_kill_candidate_names_present():
    for name in ["paste-cache", "backups", "skills-archive", "debug", "downloads", "file-history", ".DS_Store"]:
        assert name in KILL_CANDIDATES


def test_lookup_helpers():
    assert is_harness_protected("sessions") is True
    assert is_harness_protected("random-new-dir") is False
    assert is_kill_candidate("paste-cache") is True
    assert is_kill_candidate("sessions") is False


def test_no_overlap_between_lists():
    assert HARNESS_PROTECTED.isdisjoint(KILL_CANDIDATES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_allowlists.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/allowlists.py`**

```python
# app/allowlists.py
"""Hardcoded name allowlists for the classifier.

Changes here leave a clear git diff. Do not make these config-driven.
"""

HARNESS_PROTECTED: frozenset[str] = frozenset({
    "sessions",
    "projects",
    "history.jsonl",
    "settings.json",
    "settings.local.json",
    "hooks",
    "ide",
    "shell-snapshots",
    "session-env",
    "mcp.json",
    "statusline-command.sh",
    "cache",
    "cowork_plugins",
    "cowork_settings.json",
    "telemetry",
    "usage-data",
    "plugins",
    "commands",
    "agents",
    "skills",
})

KILL_CANDIDATES: frozenset[str] = frozenset({
    "paste-cache",
    ".window-cleaner-backups",
    "backups",
    "skills-archive",
    "debug",
    "downloads",
    "file-history",
    ".DS_Store",
    "stats-cache.json",
    "RTK.md",
})


def is_harness_protected(name: str) -> bool:
    return name in HARNESS_PROTECTED


def is_kill_candidate(name: str) -> bool:
    return name in KILL_CANDIDATES
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_allowlists.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/allowlists.py tests/test_allowlists.py
git commit -m "feat(allowlists): hardcoded harness-protected + kill-candidate sets"
```

---

## Task 4: DB Module + Schema

**Files:**
- Create: `app/db.py`
- Create: `tests/test_db.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write conftest fixture for a temp DB**

```python
# tests/conftest.py
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
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_db.py
import sqlite3


def test_schema_has_expected_tables(db: sqlite3.Connection):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"scans", "entries", "actions"}.issubset(names)


def test_entries_unique_index(db: sqlite3.Connection):
    db.execute("INSERT INTO scans(started_at) VALUES ('2026-01-01')")
    db.execute(
        "INSERT INTO entries(scan_id,path,kind,inode,status) VALUES (1,'/x','dir',1,'unknown')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO entries(scan_id,path,kind,inode,status) VALUES (1,'/x','dir',2,'unknown')"
        )


def test_actions_state_column_exists(db: sqlite3.Connection):
    cols = [r[1] for r in db.execute("PRAGMA table_info(actions)")]
    assert "state" in cols and "error_detail" in cols and "entry_id" in cols
```

Add import at top: `import pytest`.

- [ ] **Step 3: Run test to verify fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `app/db.py`**

```python
# app/db.py
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
```

- [ ] **Step 5: Run test to verify pass**

Run: `pytest tests/test_db.py -v`
Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py tests/conftest.py
git commit -m "feat(db): sqlite schema + connect helper"
```

---

## Task 5: Scanner

**Files:**
- Create: `app/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Extend conftest with a fake-claude-tree fixture**

Add to `tests/conftest.py`:

```python
import os


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
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_scanner.py
from pathlib import Path
from app.scanner import walk
from app.models import EntryKind


def test_walk_returns_top_level_entries(fake_claude: Path):
    entries = walk(fake_claude)
    names = {Path(e.path).name for e in entries}
    # top-level entries only
    assert "sessions" in names
    assert "paste-cache" in names
    assert "history.jsonl" in names
    assert "settings.json" in names


def test_walk_marks_file_kind(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    assert entries["history.jsonl"].kind == EntryKind.FILE
    assert entries["sessions"].kind == EntryKind.DIR


def test_walk_counts_dir_children(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    assert entries["paste-cache"].file_count == 3


def test_walk_samples_filenames(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    samples = entries["paste-cache"].sample_files
    assert len(samples) <= 5
    assert all(s.startswith("p") for s in samples)


def test_walk_survives_permission_denied(fake_claude: Path):
    entries = walk(fake_claude)
    # locked/ itself is a top-level dir; scanner should emit it without crashing
    names = {Path(e.path).name for e in entries}
    assert "locked" in names


def test_walk_does_not_follow_outbound_symlinks(fake_claude: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (fake_claude / "bad_link").symlink_to(outside)
    paths = {e.path for e in walk(fake_claude)}
    assert str(outside) not in paths


def test_walk_captures_inode(fake_claude: Path):
    entries = walk(fake_claude)
    assert all(e.inode > 0 for e in entries)
```

- [ ] **Step 3: Run test to verify fail**

Run: `pytest tests/test_scanner.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `app/scanner.py`**

```python
# app/scanner.py
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from app.models import Entry, EntryKind

SKIP_SAMPLE_SUFFIXES = (".lock",)
SKIP_SAMPLE_NAMES = frozenset({".DS_Store"})


def walk(root: Path) -> list[Entry]:
    """Yield one Entry per top-level item under root. Never raise."""
    root = root.resolve()
    out: list[Entry] = []
    try:
        children = sorted(root.iterdir())
    except PermissionError:
        return out

    for child in children:
        try:
            entry = _describe(child, root)
        except (PermissionError, FileNotFoundError):
            continue
        if entry is not None:
            out.append(entry)
    return out


def _describe(path: Path, root: Path) -> Entry | None:
    st = path.lstat()
    is_dir = path.is_dir() and not path.is_symlink()
    kind = EntryKind.DIR if is_dir else EntryKind.FILE

    if is_dir:
        size, count, samples = _summarize_dir(path, root)
    else:
        size = st.st_size
        count = 1
        samples = []

    return Entry(
        path=str(path),
        kind=kind,
        inode=st.st_ino,
        size_bytes=size,
        mtime=datetime.fromtimestamp(st.st_mtime),
        file_count=count,
        sample_files=samples,
    )


def _summarize_dir(path: Path, root: Path) -> tuple[int, int, list[str]]:
    total_size = 0
    total_count = 0
    samples: list[str] = []
    visited: set[int] = set()

    for dirpath, dirnames, filenames in os.walk(path, followlinks=False, onerror=lambda _e: None):
        dp = Path(dirpath).resolve()
        # stay inside root
        try:
            dp.relative_to(root)
        except ValueError:
            dirnames.clear()
            continue
        st = dp.stat()
        if st.st_ino in visited:
            dirnames.clear()
            continue
        visited.add(st.st_ino)

        for fn in filenames:
            total_count += 1
            try:
                total_size += (Path(dirpath) / fn).stat().st_size
            except (FileNotFoundError, PermissionError):
                pass
            if (
                len(samples) < 5
                and fn not in SKIP_SAMPLE_NAMES
                and not fn.endswith(SKIP_SAMPLE_SUFFIXES)
            ):
                samples.append(fn)
    return total_size, total_count, samples
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_scanner.py -v`
Expected: 7 PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/scanner.py tests/test_scanner.py tests/conftest.py
git commit -m "feat(scanner): walk ~/.claude/ with inode + dir summary"
```

---

## Task 6: Classifier

**Files:**
- Create: `app/classifier.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_classifier.py
from datetime import datetime, timedelta
import pytest
from app.classifier import classify
from app.models import Entry, EntryKind, Status


def _entry(name: str, mtime: datetime, kind: EntryKind = EntryKind.DIR) -> Entry:
    return Entry(
        path=f"/Users/jondev/.claude/{name}",
        kind=kind,
        inode=1,
        size_bytes=0,
        mtime=mtime,
        file_count=0,
        sample_files=[],
    )


NOW = datetime.now()


@pytest.mark.parametrize("name", ["sessions", "settings.json", "plugins", "history.jsonl"])
def test_harness_names_protected(name):
    v = classify(_entry(name, NOW))
    assert v.status == Status.HARNESS_PROTECTED


@pytest.mark.parametrize("name", ["paste-cache", "backups", "file-history", ".DS_Store"])
def test_kill_candidate_names(name):
    v = classify(_entry(name, NOW))
    assert v.status == Status.KILL_CANDIDATE


def test_recent_unknown_is_active():
    v = classify(_entry("unknown-dir", NOW - timedelta(days=2)))
    assert v.status == Status.ACTIVE


def test_old_unknown_stays_unknown_not_dead():
    v = classify(_entry("mystery-dir", NOW - timedelta(days=200)))
    assert v.status == Status.UNKNOWN


def test_new_upstream_harness_addition_not_deletable():
    """Simulates a hypothetical new harness dir not yet in allowlist."""
    v = classify(_entry("brand-new-harness-thing", NOW - timedelta(days=400)))
    assert v.status == Status.UNKNOWN  # never kill_candidate


def test_reason_is_populated():
    v = classify(_entry("sessions", NOW))
    assert v.reason
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_classifier.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/classifier.py`**

```python
# app/classifier.py
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from app.allowlists import is_harness_protected, is_kill_candidate
from app.models import Entry, Status, Verdict

ACTIVE_THRESHOLD = timedelta(days=7)


def classify(entry: Entry, now: datetime | None = None) -> Verdict:
    now = now or datetime.now()
    name = Path(entry.path).name

    if is_harness_protected(name):
        return Verdict(status=Status.HARNESS_PROTECTED, reason=f"'{name}' is a harness-owned path")
    if is_kill_candidate(name):
        return Verdict(status=Status.KILL_CANDIDATE, reason=f"'{name}' is a hardcoded kill-candidate")
    if (now - entry.mtime) < ACTIVE_THRESHOLD:
        return Verdict(status=Status.ACTIVE, reason="touched within 7 days")
    return Verdict(status=Status.UNKNOWN, reason="not on any allowlist; deny-by-default")
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_classifier.py -v`
Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/classifier.py tests/test_classifier.py
git commit -m "feat(classifier): deny-by-default rule engine"
```

---

## Task 7: Reasoner (Metadata-Only)

**Files:**
- Create: `app/reasoner.py`
- Create: `tests/test_reasoner.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_reasoner.py
import os
from datetime import datetime
from unittest.mock import MagicMock
import pytest
from app.reasoner import Reasoner, build_prompt
from app.models import Entry, EntryKind


def _entry(**kw) -> Entry:
    base = dict(
        path="/Users/jondev/.claude/mystery",
        kind=EntryKind.DIR,
        inode=1,
        size_bytes=500_000,
        mtime=datetime(2025, 1, 1),
        file_count=42,
        sample_files=["a.txt", "b.log"],
    )
    base.update(kw)
    return Entry(**base)


def test_prompt_contains_only_metadata():
    prompt = build_prompt(_entry())
    assert "mystery" in prompt
    assert "a.txt" in prompt
    # guard against accidental content inclusion
    assert "read the file" not in prompt.lower()
    assert "contents:" not in prompt.lower()


def test_reasoner_caches_same_entry():
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text="A cache dir.")]
    r = Reasoner(client=client)
    e = _entry()
    r.purpose(e)
    r.purpose(e)
    assert client.messages.create.call_count == 1


def test_reasoner_handles_api_failure():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    r = Reasoner(client=client)
    assert r.purpose(_entry()) == "(reasoner unavailable)"


def test_reasoner_honors_env_kill_switch(monkeypatch):
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    client = MagicMock()
    r = Reasoner(client=client)
    assert r.purpose(_entry()) == "(reasoner disabled)"
    client.messages.create.assert_not_called()


def test_reasoner_cost_cap():
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text="x")]
    r = Reasoner(client=client, call_cap=2)
    for i in range(5):
        r.purpose(_entry(path=f"/p/{i}", inode=i))
    assert client.messages.create.call_count == 2
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_reasoner.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/reasoner.py`**

```python
# app/reasoner.py
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from app.models import Entry

MODEL = "claude-haiku-4-5"
DEFAULT_CALL_CAP = 50


def _size_bucket(n: int) -> str:
    if n < 1_000_000:
        return "< 1 MB"
    if n < 100_000_000:
        return "1-100 MB"
    return "> 100 MB"


def _age_bucket(mtime: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    days = (now - mtime).days
    if days < 7:
        return "< 7 days"
    if days < 30:
        return "1-4 weeks"
    if days < 180:
        return "1-6 months"
    return "> 6 months"


def build_prompt(entry: Entry) -> str:
    name = Path(entry.path).name
    return (
        "Guess, in 1-2 sentences, the likely purpose of this filesystem entry. "
        "Reply with ONLY the guess, no preamble.\n\n"
        f"Name: {name}\n"
        f"Kind: {entry.kind.value}\n"
        f"Size: {_size_bucket(entry.size_bytes)}\n"
        f"Age: {_age_bucket(entry.mtime)}\n"
        f"File count: {entry.file_count}\n"
        f"Sample filenames: {', '.join(entry.sample_files) or '(none)'}\n"
    )


class Reasoner:
    def __init__(self, client=None, call_cap: int = DEFAULT_CALL_CAP):
        self.client = client
        self.call_cap = call_cap
        self._calls = 0
        self._cache: dict[tuple[str, str], str] = {}

    def purpose(self, entry: Entry) -> str:
        if os.environ.get("CLAUDE_TOOL_DISABLE_REASONER") == "1":
            return "(reasoner disabled)"

        key = (entry.path, entry.mtime.isoformat(timespec="seconds"))
        if key in self._cache:
            return self._cache[key]

        if self._calls >= self.call_cap:
            return "(not reasoned)"

        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=120,
                messages=[{"role": "user", "content": build_prompt(entry)}],
            )
            text = resp.content[0].text.strip()
        except Exception:
            text = "(reasoner unavailable)"

        self._calls += 1
        self._cache[key] = text
        return text
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_reasoner.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/reasoner.py tests/test_reasoner.py
git commit -m "feat(reasoner): metadata-only purpose with cache, cap, kill-switch"
```

---

## Task 8: Executor (Destructive Path)

**Files:**
- Create: `app/executor.py`
- Create: `tests/test_executor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_executor.py
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


def test_executor_happy_path_archives_and_deletes(db, claude_root, tmp_path):
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")
    result = ex.run(scan_id=scan_id, entry_ids=[eid], armed=True)

    assert not p.exists()
    assert result.archive_path and Path(result.archive_path).exists()
    # verify tar is readable
    subprocess.run(["tar", "tzf", result.archive_path], check=True)
    # DB action rows
    rows = db.execute("SELECT state FROM actions WHERE entry_id=?", (eid,)).fetchall()
    states = [r[0] for r in rows]
    assert "executed" in states


def test_executor_aborts_run_on_corrupt_tar(db, claude_root, tmp_path, monkeypatch):
    scan_id = _start_scan(db)
    p = claude_root / "paste-cache"
    st = p.stat()
    eid = _insert_entry(db, scan_id, str(p), "kill_candidate", st.st_ino, datetime.fromtimestamp(st.st_mtime).isoformat())

    ex = Executor(db=db, claude_root=claude_root, data_dir=tmp_path / "data")

    # corrupt the archive after create, before verify
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
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_executor.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/executor.py`**

```python
# app/executor.py
from __future__ import annotations
import fcntl
import os
import subprocess
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from app.allowlists import is_harness_protected
from app.models import ActionState, Status


class ExecutorError(RuntimeError):
    pass


@dataclass
class ActionRow:
    entry_id: int | None
    action: str
    path: str
    state: str
    error_detail: str | None = None


@dataclass
class RunResult:
    scan_id: int
    armed: bool
    archive_path: str | None = None
    executed: list[str] = field(default_factory=list)
    actions: list[ActionRow] = field(default_factory=list)


class Executor:
    def __init__(self, db: sqlite3.Connection, claude_root: Path, data_dir: Path):
        self.db = db
        self.claude_root = claude_root.resolve()
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.data_dir / ".scan.lock"

    def run(self, scan_id: int, entry_ids: list[int], armed: bool) -> RunResult:
        result = RunResult(scan_id=scan_id, armed=armed)

        with open(self.lock_path, "w") as lock_fp:
            fcntl.flock(lock_fp, fcntl.LOCK_EX)
            try:
                entries = self._load_entries(scan_id, entry_ids)
                approved = []
                for row in entries:
                    err = self._gate(row)
                    if err is not None:
                        action = ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.SKIPPED.value, error_detail=err,
                        )
                        self._write_action(scan_id, action)
                        result.actions.append(action)
                        continue
                    approved.append(row)

                if not approved:
                    return result

                if not armed:
                    for row in approved:
                        planned = ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.PLANNED.value,
                        )
                        self._write_action(scan_id, planned)
                        result.actions.append(planned)
                    return result

                archive_path = self._archive([row["path"] for row in approved])
                self._verify_archive(archive_path)
                result.archive_path = str(archive_path)

                for row in approved:
                    planned = ActionRow(
                        entry_id=row["id"], action="delete", path=row["path"],
                        state=ActionState.PLANNED.value,
                    )
                    action_id = self._write_action(scan_id, planned, archive_path=str(archive_path))
                    try:
                        subprocess.run(["rm", "-rf", "--", row["path"]], shell=False, check=True)
                        self._update_action(action_id, ActionState.EXECUTED.value)
                        result.actions.append(ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.EXECUTED.value,
                        ))
                        result.executed.append(row["path"])
                    except subprocess.CalledProcessError as e:
                        self._update_action(action_id, ActionState.FAILED.value, str(e))
                        result.actions.append(ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.FAILED.value, error_detail=str(e),
                        ))

                return result
            finally:
                fcntl.flock(lock_fp, fcntl.LOCK_UN)

    def _load_entries(self, scan_id: int, entry_ids: list[int]) -> list[sqlite3.Row]:
        if not entry_ids:
            return []
        placeholders = ",".join("?" * len(entry_ids))
        rows = self.db.execute(
            f"SELECT * FROM entries WHERE scan_id=? AND id IN ({placeholders})",
            (scan_id, *entry_ids),
        ).fetchall()
        return list(rows)

    def _gate(self, row: sqlite3.Row) -> str | None:
        path = row["path"]
        name = Path(path).name

        if row["status"] != Status.KILL_CANDIDATE.value:
            return f"status {row['status']} is not kill_candidate"
        if is_harness_protected(name):
            return f"'{name}' is harness_protected; refuse regardless of approval"

        try:
            real = Path(os.path.realpath(path))
        except OSError as e:
            return f"realpath failed: {e}"
        try:
            real.relative_to(self.claude_root)
        except ValueError:
            return f"realpath {real} is outside {self.claude_root}"

        try:
            st = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return "path no longer exists"
        if st.st_ino != row["inode"]:
            return f"inode mismatch (recorded {row['inode']}, now {st.st_ino})"
        recorded = datetime.fromisoformat(row["mtime"])
        now_mtime = datetime.fromtimestamp(st.st_mtime)
        if abs((now_mtime - recorded).total_seconds()) > 1.0:
            return f"mtime drifted (recorded {recorded}, now {now_mtime})"
        return None

    def _archive(self, paths: list[str]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        archive = Path.home() / f".claude-archive-{stamp}.tar.gz"
        subprocess.run(["tar", "czf", str(archive), "--", *paths], shell=False, check=True)
        return archive

    def _verify_archive(self, archive: Path) -> None:
        try:
            subprocess.run(["tar", "tzf", str(archive)], shell=False, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise ExecutorError(f"archive integrity check failed: {e.stderr!r}") from e

    def _write_action(self, scan_id: int, row: ActionRow, archive_path: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO actions(scan_id,entry_id,ts,action,path,archive_path,state,error_detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (scan_id, row.entry_id, datetime.now().isoformat(), row.action,
             row.path, archive_path, row.state, row.error_detail),
        )
        self.db.commit()
        return cur.lastrowid

    def _update_action(self, action_id: int, state: str, error_detail: str | None = None) -> None:
        self.db.execute(
            "UPDATE actions SET state=?, error_detail=? WHERE id=?",
            (state, error_detail, action_id),
        )
        self.db.commit()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_executor.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/executor.py tests/test_executor.py
git commit -m "feat(executor): gauntlet-gated archive+delete with TOCTOU + file lock"
```

---

## Task 9: Taxonomy Writer

**Files:**
- Create: `app/taxonomy.py`
- Create: `tests/test_taxonomy.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_taxonomy.py
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
    # sessions should appear before unknown-thing alphabetically within the same kind
    assert text.index("sessions") < text.index("unknown-thing")
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_taxonomy.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/taxonomy.py`**

```python
# app/taxonomy.py
from __future__ import annotations
import sqlite3
from pathlib import Path


def write_taxonomy(db: sqlite3.Connection, scan_id: int, out_path: Path) -> None:
    rows = db.execute(
        "SELECT path, kind, size_bytes, mtime, file_count, status, reason, purpose "
        "FROM entries WHERE scan_id=? ORDER BY path",
        (scan_id,),
    ).fetchall()

    lines = [f"# ~/.claude/ Taxonomy — scan {scan_id}", ""]
    for r in rows:
        name = Path(r["path"]).name
        lines.append(f"## {name}")
        lines.append(f"- **Kind:** {r['kind']}")
        lines.append(f"- **Status:** {r['status']}")
        lines.append(f"- **Rule:** {r['reason']}")
        lines.append(f"- **Purpose:** {r['purpose'] or '(not reasoned)'}")
        lines.append(f"- **Size:** {r['size_bytes']} bytes")
        lines.append(f"- **Last mtime:** {r['mtime']}")
        lines.append(f"- **File count:** {r['file_count']}")
        lines.append("")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(out_path)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_taxonomy.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/taxonomy.py tests/test_taxonomy.py
git commit -m "feat(taxonomy): atomic markdown writer from entries table"
```

---

## Task 10: FastAPI App + Routes

**Files:**
- Create: `app/main.py`
- Create: `app/templates/base.html`
- Create: `app/templates/home.html`
- Create: `app/templates/review.html`
- Create: `static/style.css`
- Create: `tests/test_routes.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_routes.py
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
    r = client.post(f"/execute/{scan_id}", data={"entry_id": entry_ids, "armed": "false"})
    assert r.status_code == 200
    assert (tmp_path / ".claude" / "paste-cache").exists()


def test_execute_armed_deletes(client, tmp_path):
    client.post("/scan")
    scan_id = _latest_scan_id(tmp_path)
    entry_ids = _kill_candidate_ids(tmp_path, scan_id)
    r = client.post(f"/execute/{scan_id}", data={"entry_id": entry_ids, "armed": "true"})
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
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `app/templates/base.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>claude-workspace-tool</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
</head>
<body>
  <header><h1>claude-workspace-tool</h1></header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 4: Write `app/templates/home.html`**

```html
{% extends "base.html" %}
{% block content %}
<p>Scan <code>~/.claude/</code> to classify and audit entries.</p>
<form method="post" action="/scan" hx-post="/scan" hx-target="main">
  <button type="submit">New scan</button>
</form>
{% if last_scan %}
  <p>Last scan: #{{ last_scan.id }} at {{ last_scan.started_at }}</p>
  <p><a href="/review/{{ last_scan.id }}">Review</a></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Write `app/templates/review.html`**

```html
{% extends "base.html" %}
{% block content %}
<h2>Scan #{{ scan_id }}</h2>

{% for group_label, status in groups %}
  <section>
    <h3>{{ group_label }} ({{ by_status[status]|length }})</h3>
    {% if status == "kill_candidate" %}
      <form method="post" action="/execute/{{ scan_id }}">
        <ul>
          {% for e in by_status[status] %}
            <li>
              <label>
                <input type="checkbox" name="entry_id" value="{{ e.id }}">
                <strong>{{ e.path.split('/')[-1] }}</strong>
                ({{ e.kind }}, {{ e.size_bytes }} bytes) — {{ e.purpose or "(no purpose)" }}
              </label>
            </li>
          {% endfor %}
        </ul>
        <label><input type="checkbox" name="armed" value="true"> Armed (actually delete)</label>
        <button type="submit">Execute</button>
      </form>
    {% else %}
      <ul>
        {% for e in by_status[status] %}
          <li>{{ e.path.split('/')[-1] }} ({{ e.kind }}) — {{ e.purpose or e.reason }}</li>
        {% endfor %}
      </ul>
    {% endif %}
  </section>
{% endfor %}

{% if result %}
  <h3>Execute result</h3>
  <p>Armed: {{ result.armed }}</p>
  {% if result.archive_path %}<p>Archive: <code>{{ result.archive_path }}</code></p>{% endif %}
  <ul>
    {% for a in result.actions %}
      <li>{{ a.state }} — {{ a.path }}{% if a.error_detail %} ({{ a.error_detail }}){% endif %}</li>
    {% endfor %}
  </ul>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Write `static/style.css`**

```css
body { font-family: ui-monospace, Menlo, monospace; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
header h1 { font-size: 1.25rem; }
section { border: 1px solid #ddd; padding: 1rem; margin: 1rem 0; }
h3 { margin-top: 0; }
code { background: #f4f4f4; padding: 0 .25rem; }
button { padding: .4rem .8rem; }
```

- [ ] **Step 7: Implement `app/main.py`**

```python
# app/main.py
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from anthropic import Anthropic

from app import db as db_mod
from app.classifier import classify
from app.executor import Executor
from app.models import Status
from app.reasoner import Reasoner
from app.scanner import walk
from app.taxonomy import write_taxonomy


def _env_path(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v) if v else default


def create_app() -> FastAPI:
    app = FastAPI()
    claude_root = _env_path("CLAUDE_TOOL_CLAUDE_ROOT", Path.home() / ".claude")
    data_dir = _env_path("CLAUDE_TOOL_DATA_DIR", Path(__file__).parent.parent / "data")
    db_path = data_dir / "workspace.db"
    templates_dir = Path(__file__).parent / "templates"

    templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")

    anthropic_client = None
    if os.environ.get("CLAUDE_TOOL_DISABLE_REASONER") != "1" and os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_client = Anthropic()

    def get_db() -> sqlite3.Connection:
        return db_mod.connect(db_path)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        conn = get_db()
        row = conn.execute("SELECT id, started_at FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        return templates.TemplateResponse("home.html", {"request": request, "last_scan": row})

    @app.post("/scan", response_class=HTMLResponse)
    def scan(request: Request):
        conn = get_db()
        cur = conn.execute("INSERT INTO scans(started_at) VALUES (?)", (datetime.now().isoformat(),))
        scan_id = cur.lastrowid
        conn.commit()

        reasoner = Reasoner(client=anthropic_client) if anthropic_client else Reasoner(client=None)
        # If client is None but kill-switch not set, reasoner will also return sentinel on first .purpose()

        for entry in walk(claude_root):
            verdict = classify(entry)
            purpose = reasoner.purpose(entry) if anthropic_client else "(reasoner disabled)"
            conn.execute(
                "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status,reason,purpose) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, entry.path, entry.kind.value, entry.inode, entry.size_bytes,
                 entry.mtime.isoformat(), entry.file_count, json.dumps(entry.sample_files),
                 verdict.status.value, verdict.reason, purpose),
            )
        conn.execute("UPDATE scans SET finished_at=? WHERE id=?", (datetime.now().isoformat(), scan_id))
        conn.commit()
        return _render_review(request, templates, conn, scan_id, result=None)

    @app.get("/review/{scan_id}", response_class=HTMLResponse)
    def review(request: Request, scan_id: int):
        conn = get_db()
        return _render_review(request, templates, conn, scan_id, result=None)

    @app.post("/execute/{scan_id}", response_class=HTMLResponse)
    async def execute(request: Request, scan_id: int):
        form = await request.form()
        entry_ids = [int(v) for v in form.getlist("entry_id")]
        armed = form.get("armed") == "true"
        conn = get_db()

        ex = Executor(db=conn, claude_root=claude_root, data_dir=data_dir)
        result = ex.run(scan_id=scan_id, entry_ids=entry_ids, armed=armed)

        if armed and result.executed:
            write_taxonomy(conn, scan_id, data_dir / "taxonomy.md")

        return _render_review(request, templates, conn, scan_id, result=result)

    @app.post("/explain/{entry_id}", response_class=HTMLResponse)
    def explain(request: Request, entry_id: int):
        conn = get_db()
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            return HTMLResponse("(not found)", status_code=404)
        if anthropic_client is None:
            return HTMLResponse("(reasoner disabled)")
        from app.models import Entry, EntryKind
        entry = Entry(
            path=row["path"], kind=EntryKind(row["kind"]), inode=row["inode"],
            size_bytes=row["size_bytes"] or 0, mtime=datetime.fromisoformat(row["mtime"]),
            file_count=row["file_count"] or 0,
            sample_files=json.loads(row["sample_files"] or "[]"),
        )
        reasoner = Reasoner(client=anthropic_client)
        purpose = reasoner.purpose(entry)
        conn.execute("UPDATE entries SET purpose=? WHERE id=?", (purpose, entry_id))
        conn.commit()
        return HTMLResponse(purpose)

    return app


def _render_review(request, templates, conn, scan_id, result):
    rows = conn.execute(
        "SELECT * FROM entries WHERE scan_id=? ORDER BY path", (scan_id,)
    ).fetchall()
    by_status: dict[str, list[dict]] = {s.value: [] for s in Status}
    for r in rows:
        by_status[r["status"]].append(dict(r))
    groups = [
        ("Kill candidates", "kill_candidate"),
        ("Unknown (deny-by-default)", "unknown"),
        ("Active (recent)", "active"),
        ("Harness-protected", "harness_protected"),
    ]
    return templates.TemplateResponse(
        "review.html",
        {"request": request, "scan_id": scan_id, "groups": groups,
         "by_status": by_status, "result": result},
    )


app = create_app()
```

- [ ] **Step 8: Run tests to verify pass**

Run: `pytest tests/test_routes.py -v`
Expected: 4 PASSED.

- [ ] **Step 9: Manual smoke test**

Run: `uvicorn app.main:app --port 7878`
Open `http://localhost:7878` in browser. Click **New scan**. Verify page loads, groups render, kill-candidate checkboxes visible.

- [ ] **Step 10: Commit**

```bash
git add app/main.py app/templates/ static/style.css tests/test_routes.py
git commit -m "feat(web): FastAPI routes + HTMX review UI"
```

---

## Task 11: Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write failing test**

```python
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


def test_end_to_end(tmp_path: Path):
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
    assert "paste-cache" in text  # entry row still in DB even after disk delete
    assert "sessions" in text

    # archive verifiable
    subprocess.run(["tar", "tzf", real.archive_path], check=True)
```

- [ ] **Step 2: Run test to verify pass**

Run: `pytest tests/test_integration.py -v`
Expected: 1 PASSED.

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: all tests pass across scanner, classifier, reasoner, executor, taxonomy, db, routes, integration, models, allowlists.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end integration scan→execute→taxonomy"
```

---

## Task 12: Manual Verification + README Polish

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the app against the real `~/.claude/`**

```bash
uvicorn app.main:app --port 7878
```

Open `http://localhost:7878`. Click **New scan**. Verify:
- All expected harness dirs appear under "Harness-protected" (sessions, projects, plugins, etc).
- `paste-cache`, `skills-archive`, `backups`, `file-history` appear under "Kill candidates".
- Unknown dirs (if any) appear under "Unknown" and have NO checkboxes.
- Submit without armed → dry-run result shows "planned" rows, nothing deleted.

Do NOT check "armed" on first pass. Inspect the DB:

```bash
sqlite3 data/workspace.db "SELECT status, COUNT(*) FROM entries GROUP BY status"
```

- [ ] **Step 2: If satisfied, re-scan and execute armed on one small kill-candidate**

Pick a single low-risk entry (e.g. `downloads/` if empty). Check it + armed, execute. Verify:
- Archive at `~/.claude-archive-YYYYMMDD-HHMM.tar.gz` exists.
- `tar tzf ~/.claude-archive-*.tar.gz` lists the deleted path.
- Original path is gone.
- `data/taxonomy.md` written.

- [ ] **Step 3: Expand README**

Replace current README with:

```markdown
# claude-workspace-tool

Local FastAPI + HTMX tool for auditing and cleaning `~/.claude/`.

## Safety model

- **Deny-by-default.** Only entry names on a hardcoded kill-candidate allowlist (`app/allowlists.py`) are deletable. Unknown entries are shown but not deletable.
- **TOCTOU-checked.** Inode and mtime are re-verified between scan and delete.
- **No shell.** All subprocess calls use list args; no shell interpolation.
- **Archive-before-delete.** Tarball at `~/.claude-archive-YYYYMMDD-HHMM.tar.gz`, integrity-checked before any `rm`.
- **Dry-run default.** UI will not delete anything unless you explicitly check "armed".

## Run

```
pip install -e ".[dev]"
uvicorn app.main:app --port 7878
```

Open http://localhost:7878. Click "New scan". Review results. Toggle "armed" only when you're sure.

## Environment

- `ANTHROPIC_API_KEY` — required for reasoner; otherwise purposes show as sentinels.
- `CLAUDE_TOOL_DISABLE_REASONER=1` — skip LLM calls entirely.
- `CLAUDE_TOOL_CLAUDE_ROOT` — override scan root (tests use this).
- `CLAUDE_TOOL_DATA_DIR` — override data dir (tests use this).

## Test

```
pytest
```

## Layout

See `docs/superpowers/plans/2026-04-23-p1-workspace-audit.md` for implementation plan.
See `docs/superpowers/specs/2026-04-23-claude-workspace-tool-p1-audit-design.md` for design spec.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: expand README with safety model + env vars"
```

---

## Done Criteria

- All unit tests pass (`pytest -v` green).
- Integration test passes.
- Manual smoke test on real `~/.claude/` — scan works, dry-run leaves disk untouched, armed delete on one low-risk entry produces archive + taxonomy.
- `data/taxonomy.md` is a human-readable map of `~/.claude/`.
- Git log shows one commit per task.

After P1 ships: brainstorm P2 (session index + search) as its own spec + plan.
