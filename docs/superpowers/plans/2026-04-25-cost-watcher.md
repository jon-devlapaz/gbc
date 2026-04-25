# Cost Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live USD cost accounting from JSONL session files, surfaced as a `/costs` page in the existing FastAPI workspace tool. A Node ccsniff sidecar tails JSONL, posts raw token counts to a `/ingest/usage` endpoint; pricing is resolved server-side and stored as a per-row rate snapshot.

**Architecture:** Two-process. (1) FastAPI (existing tool) gains `cost_events` table, `pricing.py`, `/ingest/usage` POST, `/costs` page with HTMX-polled partial. (2) New `watcher/` directory with a Node + ccsniff sidecar that watches `~/.claude/projects/`, parses new assistant messages with `usage`, and POSTs them. Idempotent via `message_uuid` UNIQUE.

**Tech Stack:** Python 3.x, FastAPI, Jinja2, HTMX, SQLite, pytest, httpx; Node 18+, ccsniff, built-in `node --test`.

**Spec:** `docs/superpowers/specs/2026-04-25-cost-watcher-design.md`

---

## File Structure

### Python (in `app/`)
- **Create** `app/pricing.py` — `RATES` table, `resolve(model, tier)` → rate dict.
- **Create** `app/cost_ingest.py` — `register_routes(app, get_db)` mounts `/ingest/usage`.
- **Create** `app/cost_query.py` — pure SQL aggregation functions (no FastAPI deps).
- **Create** `app/cost_recompute.py` — `python -m app.cost_recompute` re-resolves `unknown_pricing=1` rows.
- **Modify** `app/db.py` — append `cost_events` to `SCHEMA`.
- **Modify** `app/main.py` — register cost-ingest routes; add `/costs` and `/costs/partial` page routes.
- **Create** `app/templates/costs.html` — extends `base.html`, contains static header + HTMX-swapped body div.
- **Create** `app/templates/_costs_body.html` — partial: big number, model bar, session table.
- **Modify** `app/templates/base.html` — add "Costs" link to topnav.
- **Modify** `static/style.css` — DMG-styled cost panel rules.

### Python tests (in `tests/`)
- **Create** `tests/test_pricing.py`
- **Create** `tests/test_cost_ingest.py`
- **Create** `tests/test_cost_query.py`
- **Create** `tests/test_cost_recompute.py`
- **Create** `tests/test_costs_routes.py`

### Node watcher (new top-level `watcher/`)
- **Create** `watcher/package.json` — single dep `ccsniff`; uses built-in `fetch` and `node --test`.
- **Create** `watcher/parser.js` — given JSONL path + start byte offset, yields `{message_uuid, session_id, parent_session_id, ts, model, service_tier, tokens...}`.
- **Create** `watcher/poster.js` — batched POST + retry queue.
- **Create** `watcher/state.js` — load/save `data/.watcher-state.json` (per-file byte offsets).
- **Create** `watcher/index.js` — orchestrator: backfill, then live mode driven by ccsniff events.
- **Create** `watcher/test/parser.test.js`
- **Create** `watcher/test/poster.test.js`
- **Create** `watcher/test/state.test.js`

### Glue
- **Modify** `README.md` — add `gbc-watch` install + run instructions.
- **Manual** — user adds `gbc-watch` zsh function to their `~/.zshrc` (documented in README; not committed).

---

## Phase A — Python data layer

### Task A1: `cost_events` schema migration

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py` (extend) or new test in `tests/test_cost_query.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py` (or wherever schema introspection tests live; if none, create `tests/test_db.py`'s test below as a new test in the existing file):

```python
def test_cost_events_table_exists(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(cost_events)")}
    assert {
        "id", "message_uuid", "session_id", "parent_session_id",
        "jsonl_path", "ts", "model", "service_tier",
        "input_tokens", "output_tokens",
        "cache_creation_5m_tokens", "cache_creation_1h_tokens",
        "cache_read_tokens",
        "input_rate", "output_rate",
        "cache_write_5m_rate", "cache_write_1h_rate", "cache_read_rate",
        "cost_usd", "unknown_pricing",
    }.issubset(cols)


def test_cost_events_message_uuid_unique(db):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, jsonl_path, ts, model, "
        "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
        "VALUES ('u1', 's1', '/p', '2026-04-25T00:00:00Z', 'm', 0,0,0,0,0,0)"
    )
    import sqlite3
    try:
        db.execute(
            "INSERT INTO cost_events (message_uuid, session_id, jsonl_path, ts, model, "
            "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
            "VALUES ('u1', 's2', '/p', '2026-04-25T00:00:00Z', 'm', 0,0,0,0,0,0)"
        )
        assert False, "expected IntegrityError"
    except sqlite3.IntegrityError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/claude-workspace-tool && pytest tests/test_db.py -v -k cost_events`
Expected: FAIL — `no such table: cost_events`.

- [ ] **Step 3: Add table to `app/db.py` SCHEMA**

Append to the `SCHEMA` triple-quoted string in `app/db.py` (just before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS cost_events (
  id INTEGER PRIMARY KEY,
  message_uuid TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  parent_session_id TEXT,
  jsonl_path TEXT NOT NULL,
  ts TEXT NOT NULL,
  model TEXT NOT NULL,
  service_tier TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  input_rate REAL NOT NULL,
  output_rate REAL NOT NULL,
  cache_write_5m_rate REAL NOT NULL,
  cache_write_1h_rate REAL NOT NULL,
  cache_read_rate REAL NOT NULL,
  cost_usd REAL NOT NULL,
  unknown_pricing INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS cost_events_ts      ON cost_events(ts DESC);
CREATE INDEX IF NOT EXISTS cost_events_session ON cost_events(session_id);
CREATE INDEX IF NOT EXISTS cost_events_parent  ON cost_events(parent_session_id);
CREATE INDEX IF NOT EXISTS cost_events_model   ON cost_events(model);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v -k cost_events`
Expected: PASS (both new tests).

- [ ] **Step 5: Apply migration to live DB**

The dev `data/workspace.db` already exists. `init_schema` is idempotent and `CREATE TABLE IF NOT EXISTS` is additive — restarting `gbc` will run the migration. No manual step required.

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat(db): add cost_events table for token cost tracking"
```

---

### Task A2: `pricing.py` module

**Files:**
- Create: `app/pricing.py`
- Test: `tests/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing.py`:

```python
import pytest
from app.pricing import resolve, RATES


def test_resolve_known_opus_standard():
    rates, unknown = resolve("claude-opus-4-7", "standard")
    assert unknown is False
    assert rates["input"] == 15
    assert rates["output"] == 75
    assert rates["cache_write_5m"] == 18.75
    assert rates["cache_write_1h"] == 30
    assert rates["cache_read"] == 1.5


def test_resolve_known_sonnet_standard():
    rates, unknown = resolve("claude-sonnet-4-6", "standard")
    assert unknown is False
    assert rates["input"] == 3
    assert rates["output"] == 15


def test_resolve_known_haiku_standard():
    rates, unknown = resolve("claude-haiku-4-5", "standard")
    assert unknown is False
    assert rates["input"] == 1
    assert rates["output"] == 5


def test_resolve_unknown_tier_falls_back_to_standard():
    rates, unknown = resolve("claude-opus-4-7", "priority")
    # No (opus, priority) entry in v1 RATES, falls back to (opus, standard)
    assert unknown is False
    assert rates["input"] == 15


def test_resolve_missing_tier_treated_as_standard():
    rates, unknown = resolve("claude-opus-4-7", None)
    assert unknown is False
    assert rates["input"] == 15


def test_resolve_unknown_model_returns_zero_rates():
    rates, unknown = resolve("claude-future-99-0", "standard")
    assert unknown is True
    assert rates["input"] == 0
    assert rates["output"] == 0
    assert rates["cache_write_5m"] == 0
    assert rates["cache_write_1h"] == 0
    assert rates["cache_read"] == 0


def test_rates_keys_well_formed():
    for key, val in RATES.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert set(val.keys()) == {"input", "output", "cache_write_5m", "cache_write_1h", "cache_read"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pricing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pricing'`.

- [ ] **Step 3: Create `app/pricing.py`**

```python
"""
Token-cost rates for Anthropic models (USD per 1M tokens).

Update this file when Anthropic changes pricing. Existing cost_events rows
keep the rates they were ingested with — historical totals do not shift.
After updating, run `python -m app.cost_recompute` to backfill any rows
that were ingested with unknown_pricing=1.
"""

# Keys are (model, service_tier). Values are $ per 1M tokens.
RATES = {
    ("claude-opus-4-7",   "standard"): {
        "input": 15.0, "output": 75.0,
        "cache_write_5m": 18.75, "cache_write_1h": 30.0,
        "cache_read": 1.5,
    },
    ("claude-sonnet-4-6", "standard"): {
        "input": 3.0, "output": 15.0,
        "cache_write_5m": 3.75, "cache_write_1h": 6.0,
        "cache_read": 0.3,
    },
    ("claude-haiku-4-5",  "standard"): {
        "input": 1.0, "output": 5.0,
        "cache_write_5m": 1.25, "cache_write_1h": 2.0,
        "cache_read": 0.1,
    },
}

ZERO_RATES = {
    "input": 0.0, "output": 0.0,
    "cache_write_5m": 0.0, "cache_write_1h": 0.0, "cache_read": 0.0,
}


def resolve(model: str, service_tier: str | None) -> tuple[dict, bool]:
    """
    Look up rates for (model, service_tier).

    Resolution order:
      1. exact (model, service_tier)
      2. (model, "standard")  [tier fallback]
      3. ZERO_RATES + unknown=True

    Returns (rates_dict, unknown_pricing_flag).
    """
    tier = service_tier or "standard"
    if (model, tier) in RATES:
        return RATES[(model, tier)], False
    if (model, "standard") in RATES:
        return RATES[(model, "standard")], False
    return ZERO_RATES, True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pricing.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/pricing.py tests/test_pricing.py
git commit -m "feat(pricing): rate table + resolver with tier fallback"
```

---

### Task A3: `/ingest/usage` endpoint

**Files:**
- Create: `app/cost_ingest.py`
- Modify: `app/main.py`
- Test: `tests/test_cost_ingest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cost_ingest.py`:

```python
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def _payload(**overrides):
    base = {
        "message_uuid": "uuid-1",
        "session_id": "sess-1",
        "parent_session_id": None,
        "jsonl_path": "/tmp/sess-1.jsonl",
        "ts": "2026-04-25T10:00:00Z",
        "model": "claude-opus-4-7",
        "service_tier": "standard",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_5m_tokens": 0,
        "cache_creation_1h_tokens": 100_000,
        "cache_read_tokens": 50_000,
    }
    base.update(overrides)
    return base


def test_ingest_happy_path(client):
    r = client.post("/ingest/usage", json={"events": [_payload()]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1
    assert body["skipped"] == 0


def test_ingest_computes_cost_usd(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload()]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute("SELECT cost_usd FROM cost_events WHERE message_uuid='uuid-1'").fetchone()
    # opus standard: input=15, output=75, cache_write_1h=30, cache_read=1.5
    # cost = (1000*15 + 500*75 + 100_000*30 + 50_000*1.5) / 1_000_000
    #      = (15_000 + 37_500 + 3_000_000 + 75_000) / 1e6
    #      = 3_127_500 / 1e6 = 3.1275
    assert row[0] == pytest.approx(3.1275, rel=1e-6)


def test_ingest_dedupes_by_message_uuid(client):
    p = _payload()
    r1 = client.post("/ingest/usage", json={"events": [p]})
    r2 = client.post("/ingest/usage", json={"events": [p]})
    assert r1.json() == {"inserted": 1, "skipped": 0}
    assert r2.json() == {"inserted": 0, "skipped": 1}


def test_ingest_unknown_model_marks_unknown_pricing(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload(model="claude-future-9", message_uuid="u-future")]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute(
        "SELECT unknown_pricing, cost_usd, input_rate FROM cost_events WHERE message_uuid='u-future'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == 0.0
    assert row[2] == 0.0


def test_ingest_rejects_negative_tokens(client):
    r = client.post("/ingest/usage", json={"events": [_payload(input_tokens=-1)]})
    assert r.status_code == 400


def test_ingest_rejects_absurd_tokens(client):
    r = client.post("/ingest/usage", json={"events": [_payload(output_tokens=10_000_001)]})
    assert r.status_code == 400


def test_ingest_rejects_missing_required_field(client):
    bad = _payload()
    del bad["model"]
    r = client.post("/ingest/usage", json={"events": [bad]})
    assert r.status_code == 400


def test_ingest_batch(client):
    events = [_payload(message_uuid=f"u-{i}") for i in range(10)]
    r = client.post("/ingest/usage", json={"events": events})
    assert r.json() == {"inserted": 10, "skipped": 0}


def test_ingest_tier_fallback_to_standard(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload(service_tier="priority", message_uuid="u-pri")]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute("SELECT unknown_pricing, input_rate FROM cost_events WHERE message_uuid='u-pri'").fetchone()
    # No (opus, priority) → falls back to (opus, standard) at 15
    assert row[0] == 0
    assert row[1] == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cost_ingest.py -v`
Expected: FAIL on every test — endpoint not registered (404).

- [ ] **Step 3: Create `app/cost_ingest.py`**

```python
from __future__ import annotations
from typing import Callable, Optional
import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.pricing import resolve

MAX_TOKENS = 10_000_000


class UsageEvent(BaseModel):
    message_uuid: str
    session_id: str
    parent_session_id: Optional[str] = None
    jsonl_path: str
    ts: str
    model: str
    service_tier: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cache_read_tokens: int = 0

    @field_validator(
        "input_tokens", "output_tokens",
        "cache_creation_5m_tokens", "cache_creation_1h_tokens", "cache_read_tokens",
    )
    @classmethod
    def _bounded(cls, v: int) -> int:
        if v < 0 or v > MAX_TOKENS:
            raise ValueError(f"token count out of range: {v}")
        return v


class UsageBatch(BaseModel):
    events: list[UsageEvent] = Field(min_length=1)


def _compute_cost_usd(e: UsageEvent, rates: dict) -> float:
    return (
        e.input_tokens * rates["input"]
        + e.output_tokens * rates["output"]
        + e.cache_creation_5m_tokens * rates["cache_write_5m"]
        + e.cache_creation_1h_tokens * rates["cache_write_1h"]
        + e.cache_read_tokens * rates["cache_read"]
    ) / 1_000_000


def register_routes(app: FastAPI, get_db: Callable[[], sqlite3.Connection]) -> None:
    @app.post("/ingest/usage")
    def ingest_usage(batch: UsageBatch):
        try:
            payload = batch
        except Exception as ex:
            raise HTTPException(status_code=400, detail=str(ex))

        conn = get_db()
        inserted = 0
        skipped = 0
        for e in payload.events:
            rates, unknown = resolve(e.model, e.service_tier)
            cost_usd = _compute_cost_usd(e, rates)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO cost_events (
                  message_uuid, session_id, parent_session_id, jsonl_path, ts,
                  model, service_tier,
                  input_tokens, output_tokens,
                  cache_creation_5m_tokens, cache_creation_1h_tokens, cache_read_tokens,
                  input_rate, output_rate,
                  cache_write_5m_rate, cache_write_1h_rate, cache_read_rate,
                  cost_usd, unknown_pricing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e.message_uuid, e.session_id, e.parent_session_id, e.jsonl_path, e.ts,
                    e.model, e.service_tier,
                    e.input_tokens, e.output_tokens,
                    e.cache_creation_5m_tokens, e.cache_creation_1h_tokens, e.cache_read_tokens,
                    rates["input"], rates["output"],
                    rates["cache_write_5m"], rates["cache_write_1h"], rates["cache_read"],
                    cost_usd, 1 if unknown else 0,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
        return {"inserted": inserted, "skipped": skipped}
```

- [ ] **Step 4: Wire into `app/main.py`**

Add an import near the other `from app import …` lines:

```python
from app import cost_ingest as cost_ingest_mod
```

Inside `create_app()`, after `get_db` is defined and before existing routes, add:

```python
    cost_ingest_mod.register_routes(app, get_db)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cost_ingest.py -v`
Expected: PASS (9 tests).

Validation 400s: pydantic raises `RequestValidationError` which FastAPI maps to 422 by default. If `test_ingest_rejects_negative_tokens` returns 422, change the test to `assert r.status_code in (400, 422)` and add a note in `cost_ingest.py` that we accept either; OR add a custom exception handler. **Decision for v1:** accept both — pydantic's 422 is correct semantically. Update the three "rejects" tests to assert `status_code in (400, 422)`.

- [ ] **Step 6: Commit**

```bash
git add app/cost_ingest.py app/main.py tests/test_cost_ingest.py
git commit -m "feat(cost): /ingest/usage endpoint with idempotent batch insert"
```

---

### Task A4: `cost_query.py` — aggregations

**Files:**
- Create: `app/cost_query.py`
- Test: `tests/test_cost_query.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cost_query.py`:

```python
from datetime import datetime, timedelta, timezone
from app.cost_query import today_total, range_total, by_model, by_session


def _insert(db, *, uuid, session, parent=None, ts, model, cost):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, parent_session_id, jsonl_path, ts, model, "
        "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
        "VALUES (?, ?, ?, '/p', ?, ?, 0, 0, 0, 0, 0, ?)",
        (uuid, session, parent, ts, model, cost),
    )
    db.commit()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_today_total_sums_today_only(db):
    now = datetime.now(timezone.utc)
    yest = now - timedelta(days=1)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="claude-opus-4-7", cost=1.0)
    _insert(db, uuid="u2", session="s1", ts=_iso(now), model="claude-opus-4-7", cost=2.5)
    _insert(db, uuid="u3", session="s1", ts=_iso(yest), model="claude-opus-4-7", cost=10.0)
    assert today_total(db) == 3.5


def test_range_total_last_7_days(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="claude-opus-4-7", cost=1.0)
    _insert(db, uuid="u2", session="s1", ts=_iso(now - timedelta(days=3)), model="claude-opus-4-7", cost=2.0)
    _insert(db, uuid="u3", session="s1", ts=_iso(now - timedelta(days=10)), model="claude-opus-4-7", cost=99.0)
    assert range_total(db, days=7) == 3.0


def test_by_model_aggregates_and_orders_desc(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="claude-opus-4-7", cost=10.0)
    _insert(db, uuid="u2", session="s1", ts=_iso(now), model="claude-opus-4-7", cost=5.0)
    _insert(db, uuid="u3", session="s1", ts=_iso(now), model="claude-haiku-4-5", cost=0.5)
    rows = by_model(db, days=7)
    assert rows[0] == ("claude-opus-4-7", 15.0)
    assert rows[1] == ("claude-haiku-4-5", 0.5)


def test_by_session_rolls_up_subagents(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="parent-1", ts=_iso(now), model="m", cost=2.0)
    _insert(db, uuid="u2", session="agent-x", parent="parent-1", ts=_iso(now), model="m", cost=1.5)
    _insert(db, uuid="u3", session="agent-y", parent="parent-1", ts=_iso(now), model="m", cost=0.5)
    _insert(db, uuid="u4", session="other", ts=_iso(now), model="m", cost=7.0)
    rows = by_session(db, limit=10)
    by_id = {r["session_id"]: r for r in rows}
    assert by_id["parent-1"]["cost_usd"] == 4.0      # 2.0 + 1.5 + 0.5
    assert by_id["parent-1"]["subagent_count"] == 2
    assert by_id["other"]["cost_usd"] == 7.0
    assert by_id["other"]["subagent_count"] == 0
    # subagent rows should not appear standalone
    assert "agent-x" not in by_id
    assert "agent-y" not in by_id


def test_by_session_orders_by_most_recent(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="old", ts=_iso(now - timedelta(days=2)), model="m", cost=1.0)
    _insert(db, uuid="u2", session="new", ts=_iso(now), model="m", cost=1.0)
    rows = by_session(db, limit=10)
    assert rows[0]["session_id"] == "new"
    assert rows[1]["session_id"] == "old"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cost_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cost_query'`.

- [ ] **Step 3: Create `app/cost_query.py`**

```python
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import sqlite3


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def today_total(conn: sqlite3.Connection) -> float:
    """Sum cost_usd for events with ts since UTC midnight today."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_events WHERE ts >= ?",
        (_utc_iso(midnight),),
    ).fetchone()
    return float(row[0])


def range_total(conn: sqlite3.Connection, days: int) -> float:
    """Sum cost_usd for events in the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_events WHERE ts >= ?",
        (_utc_iso(cutoff),),
    ).fetchone()
    return float(row[0])


def by_model(conn: sqlite3.Connection, days: int) -> list[tuple[str, float]]:
    """[(model, total_cost_usd), ...] for last `days` days, sorted desc by cost."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = conn.execute(
        """
        SELECT model, SUM(cost_usd) AS total
        FROM cost_events
        WHERE ts >= ?
        GROUP BY model
        ORDER BY total DESC
        """,
        (_utc_iso(cutoff),),
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def by_session(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """
    One row per top-level session; subagent costs roll up into their parent.

    Returns list of dicts:
      {session_id, last_ts, last_model, total_tokens, cost_usd, subagent_count}
    """
    rows = conn.execute(
        """
        WITH rollup AS (
          SELECT
            COALESCE(parent_session_id, session_id) AS root_session,
            session_id,
            ts, model, cost_usd,
            (input_tokens + output_tokens
              + cache_creation_5m_tokens + cache_creation_1h_tokens + cache_read_tokens) AS total_tokens
          FROM cost_events
        )
        SELECT
          root_session AS session_id,
          MAX(ts) AS last_ts,
          SUM(cost_usd) AS cost_usd,
          SUM(total_tokens) AS total_tokens,
          COUNT(DISTINCT CASE WHEN session_id != root_session THEN session_id END) AS subagent_count,
          (SELECT model FROM rollup r2
             WHERE r2.root_session = rollup.root_session
             ORDER BY r2.ts DESC LIMIT 1) AS last_model
        FROM rollup
        GROUP BY root_session
        ORDER BY last_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "session_id": r[0],
            "last_ts": r[1],
            "cost_usd": float(r[2]),
            "total_tokens": int(r[3]),
            "subagent_count": int(r[4]),
            "last_model": r[5],
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cost_query.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/cost_query.py tests/test_cost_query.py
git commit -m "feat(cost): aggregation queries with subagent rollup"
```

---

### Task A5: `cost_recompute.py`

**Files:**
- Create: `app/cost_recompute.py`
- Test: `tests/test_cost_recompute.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cost_recompute.py`:

```python
from app.cost_recompute import recompute_unknown


def _insert_unknown(db, uuid, model, input_tokens=1_000_000):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, jsonl_path, ts, model, "
        "input_tokens, input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, "
        "cost_usd, unknown_pricing) "
        "VALUES (?, 's', '/p', '2026-04-25T00:00:00Z', ?, ?, 0, 0, 0, 0, 0, 0, 1)",
        (uuid, model, input_tokens),
    )
    db.commit()


def test_recompute_resolves_after_pricing_update(db, monkeypatch):
    _insert_unknown(db, "u1", "claude-future-9", input_tokens=1_000_000)

    # Patch RATES so the previously-unknown model resolves
    from app import pricing
    monkeypatch.setitem(pricing.RATES, ("claude-future-9", "standard"), {
        "input": 5.0, "output": 25.0,
        "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.5,
    })

    updated = recompute_unknown(db)
    assert updated == 1
    row = db.execute("SELECT unknown_pricing, input_rate, cost_usd FROM cost_events WHERE message_uuid='u1'").fetchone()
    assert row[0] == 0
    assert row[1] == 5.0
    assert row[2] == 5.0  # 1M tokens * $5/M


def test_recompute_skips_still_unknown(db):
    _insert_unknown(db, "u1", "claude-still-unknown")
    updated = recompute_unknown(db)
    assert updated == 0
    row = db.execute("SELECT unknown_pricing FROM cost_events WHERE message_uuid='u1'").fetchone()
    assert row[0] == 1


def test_recompute_ignores_known_rows(db):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, jsonl_path, ts, model, "
        "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, "
        "cost_usd, unknown_pricing) "
        "VALUES ('u1', 's', '/p', '2026-04-25T00:00:00Z', 'claude-opus-4-7', 15, 75, 18.75, 30, 1.5, 1.0, 0)"
    )
    db.commit()
    assert recompute_unknown(db) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cost_recompute.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `app/cost_recompute.py`**

```python
"""
Recompute cost_events rows that were ingested with unknown_pricing=1.

Run after editing app/pricing.py to add a previously-unknown model:
    python -m app.cost_recompute
"""
from __future__ import annotations
import os
import sqlite3
import sys
from pathlib import Path
from app.pricing import resolve


def _compute_cost(row: sqlite3.Row, rates: dict) -> float:
    return (
        row["input_tokens"] * rates["input"]
        + row["output_tokens"] * rates["output"]
        + row["cache_creation_5m_tokens"] * rates["cache_write_5m"]
        + row["cache_creation_1h_tokens"] * rates["cache_write_1h"]
        + row["cache_read_tokens"] * rates["cache_read"]
    ) / 1_000_000


def recompute_unknown(conn: sqlite3.Connection) -> int:
    """Re-resolve rates for unknown_pricing=1 rows. Returns count of rows updated."""
    rows = conn.execute(
        "SELECT * FROM cost_events WHERE unknown_pricing = 1"
    ).fetchall()
    updated = 0
    for r in rows:
        rates, still_unknown = resolve(r["model"], r["service_tier"])
        if still_unknown:
            continue
        new_cost = _compute_cost(r, rates)
        conn.execute(
            """
            UPDATE cost_events SET
              input_rate=?, output_rate=?,
              cache_write_5m_rate=?, cache_write_1h_rate=?, cache_read_rate=?,
              cost_usd=?, unknown_pricing=0
            WHERE id=?
            """,
            (
                rates["input"], rates["output"],
                rates["cache_write_5m"], rates["cache_write_1h"], rates["cache_read"],
                new_cost, r["id"],
            ),
        )
        updated += 1
    conn.commit()
    return updated


def main() -> int:
    data_dir = Path(os.environ.get("CLAUDE_TOOL_DATA_DIR", Path(__file__).parent.parent / "data"))
    db_path = data_dir / "workspace.db"
    if not db_path.exists():
        print(f"No DB at {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    n = recompute_unknown(conn)
    print(f"Recomputed {n} cost_events rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cost_recompute.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/cost_recompute.py tests/test_cost_recompute.py
git commit -m "feat(cost): recompute CLI for backfilling unknown_pricing rows"
```

---

## Phase B — Python UI

### Task B1: `/costs` page route + HTMX partial route

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_costs_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_costs_routes.py`:

```python
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_costs_page_renders_empty(client):
    r = client.get("/costs")
    assert r.status_code == 200
    assert "COSTS" in r.text or "Costs" in r.text
    assert "$0.00" in r.text or "no cost events" in r.text.lower()


def test_costs_partial_returns_fragment(client):
    r = client.get("/costs/partial")
    assert r.status_code == 200
    # Partial should NOT include the full base layout
    assert "<html" not in r.text.lower()
    assert "TODAY" in r.text or "today" in r.text.lower()


def test_costs_page_after_ingest(client):
    payload = {"events": [{
        "message_uuid": "u1", "session_id": "s1", "jsonl_path": "/p",
        "ts": "2026-04-25T12:00:00Z", "model": "claude-opus-4-7", "service_tier": "standard",
        "input_tokens": 1_000_000, "output_tokens": 0,
        "cache_creation_5m_tokens": 0, "cache_creation_1h_tokens": 0, "cache_read_tokens": 0,
    }]}
    client.post("/ingest/usage", json=payload)
    r = client.get("/costs/partial")
    assert "$15.00" in r.text  # 1M input tokens at opus standard = $15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_costs_routes.py -v`
Expected: FAIL — `/costs` returns 404.

- [ ] **Step 3: Add routes to `app/main.py`**

Add an import:

```python
from app import cost_query as cost_query_mod
```

Inside `create_app()` after existing routes, add:

```python
    @app.get("/costs", response_class=HTMLResponse)
    def costs(request: Request):
        conn = get_db()
        ctx = _base_ctx() | _costs_ctx(conn)
        ctx["request"] = request
        return templates.TemplateResponse("costs.html", ctx)

    @app.get("/costs/partial", response_class=HTMLResponse)
    def costs_partial(request: Request):
        conn = get_db()
        ctx = _costs_ctx(conn)
        ctx["request"] = request
        return templates.TemplateResponse("_costs_body.html", ctx)
```

Add this helper inside `create_app()` (next to `_base_ctx`):

```python
    def _costs_ctx(conn) -> dict:
        today = cost_query_mod.today_total(conn)
        week = cost_query_mod.range_total(conn, days=7)
        month = cost_query_mod.range_total(conn, days=30)
        bm = cost_query_mod.by_model(conn, days=7)
        bm_total = sum(c for _, c in bm) or 1.0
        bm_rows = [
            {"model": m, "cost_usd": c, "pct": (c / bm_total) * 100}
            for m, c in bm
        ]
        sessions = cost_query_mod.by_session(conn, limit=50)
        unknown_count = conn.execute(
            "SELECT COUNT(*) FROM cost_events WHERE unknown_pricing = 1"
        ).fetchone()[0]
        return {
            "today_usd": today,
            "week_usd": week,
            "month_usd": month,
            "by_model": bm_rows,
            "sessions": sessions,
            "unknown_count": unknown_count,
        }
```

- [ ] **Step 4: Continue to Task B2 to create templates** (test will still fail until templates exist)

---

### Task B2: Templates `costs.html` + `_costs_body.html`

**Files:**
- Create: `app/templates/costs.html`
- Create: `app/templates/_costs_body.html`

- [ ] **Step 1: Create `app/templates/costs.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="costs">
  <h2>COSTS</h2>
  <div id="costs-body"
       hx-get="/costs/partial"
       hx-trigger="every 5s"
       hx-swap="innerHTML">
    {% include "_costs_body.html" %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 2: Create `app/templates/_costs_body.html`**

```html
{% macro fmt_usd(v) -%}
${{ "%.2f"|format(v) }}
{%- endmacro %}

{% macro fmt_tokens(n) -%}
{% if n >= 1_000_000 %}{{ "%.1fm"|format(n/1_000_000) }}{% elif n >= 1_000 %}{{ "%.0fk"|format(n/1_000) }}{% else %}{{ n }}{% endif %}
{%- endmacro %}

<div class="costs-summary">
  <div class="cost-big">
    <div class="cost-label">TODAY</div>
    <div class="cost-value">{{ fmt_usd(today_usd) }}</div>
  </div>
  <div class="cost-secondary">
    <span>7 DAYS <strong>{{ fmt_usd(week_usd) }}</strong></span>
    <span>30 DAYS <strong>{{ fmt_usd(month_usd) }}</strong></span>
  </div>
</div>

<div class="costs-bymodel">
  <h3>BY MODEL (last 7d)</h3>
  {% if by_model %}
    <ul class="bar-list">
      {% for r in by_model %}
        <li>
          <span class="bar-label">{{ r.model }}</span>
          <span class="bar-track"><span class="bar-fill" style="width: {{ "%.1f"|format(r.pct) }}%"></span></span>
          <span class="bar-value">{{ fmt_usd(r.cost_usd) }} ({{ "%.0f"|format(r.pct) }}%)</span>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p class="muted">No cost events yet. Start the watcher with <code>gbc-watch</code> in another terminal — it will backfill from your existing JSONL files, then update live as you work.</p>
  {% endif %}
</div>

{% if unknown_count > 0 %}
<div class="warn">⚠ {{ unknown_count }} events without pricing. Add the model to <code>app/pricing.py</code> and run <code>python -m app.cost_recompute</code>.</div>
{% endif %}

<div class="costs-sessions">
  <h3>RECENT SESSIONS</h3>
  {% if sessions %}
  <table>
    <thead>
      <tr><th>STARTED</th><th>SESSION</th><th>MODEL</th><th>TOKENS</th><th>COST</th></tr>
    </thead>
    <tbody>
      {% for s in sessions %}
      <tr>
        <td>{{ s.last_ts }}</td>
        <td>{{ s.session_id[:12] }}{% if s.subagent_count %} <span class="badge">+{{ s.subagent_count }}</span>{% endif %}</td>
        <td>{{ s.last_model }}</td>
        <td>{{ fmt_tokens(s.total_tokens) }}</td>
        <td>{{ fmt_usd(s.cost_usd) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">—</p>
  {% endif %}
</div>
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_costs_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/templates/costs.html app/templates/_costs_body.html tests/test_costs_routes.py
git commit -m "feat(ui): /costs page with HTMX 5s poll for live totals"
```

---

### Task B3: Nav link + DMG styles

**Files:**
- Modify: `app/templates/base.html`
- Modify: `static/style.css`
- Test: `tests/test_wire_nav.py` (extend if it covers nav links)

- [ ] **Step 1: Add nav link**

In `app/templates/base.html` line 16-18, change:

```html
    <nav class="topnav">
      <a href="/" data-tip="dashboard">Home</a>
      <a href="/sessions" data-tip="browse sessions">Sessions</a>
      <a href="/families" data-tip="project families + redact">Families</a>
    </nav>
```

to:

```html
    <nav class="topnav">
      <a href="/" data-tip="dashboard">Home</a>
      <a href="/sessions" data-tip="browse sessions">Sessions</a>
      <a href="/families" data-tip="project families + redact">Families</a>
      <a href="/costs" data-tip="token spend">Costs</a>
    </nav>
```

- [ ] **Step 2: Add CSS to `static/style.css`**

Append:

```css
/* === costs page === */
.costs h2,
.costs h3 { font-family: 'Press Start 2P', monospace; letter-spacing: 0.05em; }
.costs h3 { font-size: 0.7rem; margin-top: 1.25rem; opacity: 0.85; }

.costs-summary {
  border: 1px solid var(--dmg-edge, #2c3a2c);
  padding: 1rem 1.25rem;
  background: var(--dmg-screen, #1a221a);
  margin-bottom: 1rem;
}
.cost-big .cost-label {
  font-family: 'Press Start 2P', monospace;
  font-size: 0.6rem;
  opacity: 0.7;
  margin-bottom: 0.4rem;
}
.cost-big .cost-value {
  font-family: 'VT323', monospace;
  font-size: 3.5rem;
  line-height: 1;
}
.cost-secondary {
  margin-top: 0.75rem;
  display: flex;
  gap: 1.5rem;
  font-family: 'VT323', monospace;
  font-size: 1.1rem;
  opacity: 0.8;
}
.cost-secondary strong { font-weight: normal; opacity: 1; }

.bar-list {
  list-style: none;
  padding: 0;
  font-family: 'VT323', monospace;
  font-size: 1rem;
}
.bar-list li {
  display: grid;
  grid-template-columns: 10rem 1fr 8rem;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 0.4rem;
}
.bar-label { opacity: 0.85; }
.bar-track {
  height: 0.8rem;
  background: var(--dmg-edge, #2c3a2c);
  position: relative;
  overflow: hidden;
}
.bar-fill {
  display: block;
  height: 100%;
  background: var(--dmg-text, #9bbc0f);
}
.bar-value { text-align: right; opacity: 0.85; }

.costs-sessions table {
  width: 100%;
  border-collapse: collapse;
  font-family: 'VT323', monospace;
  font-size: 1rem;
}
.costs-sessions th,
.costs-sessions td {
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid var(--dmg-edge, #2c3a2c);
  text-align: left;
}
.costs-sessions th {
  font-family: 'Press Start 2P', monospace;
  font-size: 0.55rem;
  opacity: 0.7;
}
.badge {
  display: inline-block;
  padding: 0 0.3rem;
  border: 1px solid currentColor;
  font-size: 0.7rem;
  opacity: 0.7;
}

.warn {
  border: 1px solid #b88;
  color: #d99;
  background: rgba(180,80,80,0.1);
  padding: 0.5rem 0.75rem;
  margin: 0.75rem 0;
  font-family: 'VT323', monospace;
}
.muted { opacity: 0.6; }
```

- [ ] **Step 3: Smoke check**

Start the dev server: `cd ~/dev/claude-workspace-tool && uvicorn app.main:create_app --factory --reload --port 7878`

Open `http://127.0.0.1:7878/costs`. Should see the Costs link in topnav and an empty-state page (or whatever data is in `data/workspace.db`).

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html static/style.css
git commit -m "feat(ui): nav link + DMG-styled cost panel"
```

---

## Phase C — Node watcher

### Task C1: `package.json` + `state.js`

**Files:**
- Create: `watcher/package.json`
- Create: `watcher/state.js`
- Create: `watcher/test/state.test.js`

- [ ] **Step 1: Create `watcher/package.json`**

```json
{
  "name": "claude-workspace-tool-watcher",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=18" },
  "scripts": {
    "start": "node index.js",
    "test": "node --test test/"
  },
  "dependencies": {
    "ccsniff": "^0.1.0"
  }
}
```

(Pin to whatever ccsniff's actual current version is when running `cd watcher && npm install`. Update the version field after install if it picked something different than `^0.1.0`.)

- [ ] **Step 2: Write the failing state test**

Create `watcher/test/state.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadState, saveState } from '../state.js';

function tmp() {
  return mkdtempSync(join(tmpdir(), 'state-test-'));
}

test('loadState returns empty object when file missing', () => {
  const dir = tmp();
  try {
    const s = loadState(join(dir, 'state.json'));
    assert.deepEqual(s, { offsets: {} });
  } finally { rmSync(dir, { recursive: true }); }
});

test('saveState then loadState roundtrip', () => {
  const dir = tmp();
  const path = join(dir, 'state.json');
  try {
    saveState(path, { offsets: { '/a.jsonl': 1024, '/b.jsonl': 0 } });
    const s = loadState(path);
    assert.equal(s.offsets['/a.jsonl'], 1024);
    assert.equal(s.offsets['/b.jsonl'], 0);
  } finally { rmSync(dir, { recursive: true }); }
});

test('loadState handles corrupt file by returning empty', () => {
  const dir = tmp();
  const path = join(dir, 'state.json');
  try {
    writeFileSync(path, 'not json');
    const s = loadState(path);
    assert.deepEqual(s, { offsets: {} });
  } finally { rmSync(dir, { recursive: true }); }
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/dev/claude-workspace-tool/watcher && npm install && node --test test/state.test.js`
Expected: FAIL — `Cannot find module '../state.js'`.

- [ ] **Step 4: Create `watcher/state.js`**

```javascript
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export function loadState(path) {
  if (!existsSync(path)) return { offsets: {} };
  try {
    const raw = readFileSync(path, 'utf8');
    const data = JSON.parse(raw);
    return { offsets: data.offsets ?? {} };
  } catch {
    return { offsets: {} };
  }
}

export function saveState(path, state) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2), 'utf8');
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `node --test test/state.test.js`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/claude-workspace-tool
git add watcher/package.json watcher/state.js watcher/test/state.test.js
git commit -m "feat(watcher): state file persistence for per-jsonl byte offsets"
```

---

### Task C2: `parser.js`

**Files:**
- Create: `watcher/parser.js`
- Create: `watcher/test/parser.test.js`

- [ ] **Step 1: Write the failing test**

Create `watcher/test/parser.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseJsonlSince, parentSessionFromPath, sessionIdFromPath } from '../parser.js';

function tmp() { return mkdtempSync(join(tmpdir(), 'parse-test-')); }

const sample = (overrides = {}) => ({
  uuid: 'm-1',
  type: 'assistant',
  timestamp: '2026-04-25T10:00:00Z',
  message: {
    model: 'claude-opus-4-7',
    usage: {
      input_tokens: 100,
      output_tokens: 50,
      cache_read_input_tokens: 1000,
      cache_creation_input_tokens: 2000,
      cache_creation: { ephemeral_5m_input_tokens: 500, ephemeral_1h_input_tokens: 1500 },
      service_tier: 'standard',
    },
  },
  ...overrides,
});

test('sessionIdFromPath extracts top-level uuid', () => {
  assert.equal(
    sessionIdFromPath('/Users/x/.claude/projects/-Users-x/abc-123.jsonl'),
    'abc-123'
  );
});

test('sessionIdFromPath extracts subagent uuid (own id, not parent)', () => {
  assert.equal(
    sessionIdFromPath('/Users/x/.claude/projects/-Users-x/parent-uuid/subagents/agent-deadbeef.jsonl'),
    'agent-deadbeef'
  );
});

test('parentSessionFromPath returns null for top-level', () => {
  assert.equal(
    parentSessionFromPath('/Users/x/.claude/projects/-Users-x/abc-123.jsonl'),
    null
  );
});

test('parentSessionFromPath returns parent uuid for subagents', () => {
  assert.equal(
    parentSessionFromPath('/Users/x/.claude/projects/-Users-x/parent-uuid/subagents/agent-deadbeef.jsonl'),
    'parent-uuid'
  );
});

test('parseJsonlSince yields one record from one assistant line', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    writeFileSync(path, JSON.stringify(sample()) + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 1);
    const r = out.records[0];
    assert.equal(r.message_uuid, 'm-1');
    assert.equal(r.model, 'claude-opus-4-7');
    assert.equal(r.input_tokens, 100);
    assert.equal(r.output_tokens, 50);
    assert.equal(r.cache_creation_5m_tokens, 500);
    assert.equal(r.cache_creation_1h_tokens, 1500);
    assert.equal(r.cache_read_tokens, 1000);
    assert.equal(r.service_tier, 'standard');
    assert.equal(r.session_id, 'sess-1');
    assert.equal(r.parent_session_id, null);
    assert.equal(out.newOffset, Buffer.byteLength(JSON.stringify(sample()) + '\n', 'utf8'));
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince skips non-assistant and missing-usage lines', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const lines = [
      JSON.stringify({ type: 'user', message: { content: 'hi' } }),
      JSON.stringify(sample({ uuid: 'm-1' })),
      JSON.stringify({ type: 'assistant', uuid: 'm-no-usage', message: { model: 'm', content: [] } }),
      JSON.stringify(sample({ uuid: 'm-2' })),
    ];
    writeFileSync(path, lines.join('\n') + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 2);
    assert.equal(out.records[0].message_uuid, 'm-1');
    assert.equal(out.records[1].message_uuid, 'm-2');
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince resumes from offset', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const line1 = JSON.stringify(sample({ uuid: 'm-1' })) + '\n';
    const line2 = JSON.stringify(sample({ uuid: 'm-2' })) + '\n';
    writeFileSync(path, line1 + line2);
    const off1 = Buffer.byteLength(line1, 'utf8');
    const out = parseJsonlSince(path, off1);
    assert.equal(out.records.length, 1);
    assert.equal(out.records[0].message_uuid, 'm-2');
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince skips malformed lines gracefully', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    const lines = [
      'not-json',
      JSON.stringify(sample({ uuid: 'm-1' })),
    ];
    writeFileSync(path, lines.join('\n') + '\n');
    const out = parseJsonlSince(path, 0);
    assert.equal(out.records.length, 1);
  } finally { rmSync(dir, { recursive: true }); }
});

test('parseJsonlSince treats truncated file (offset > size) as reset', () => {
  const dir = tmp();
  try {
    const path = join(dir, 'sess-1.jsonl');
    writeFileSync(path, JSON.stringify(sample({ uuid: 'm-1' })) + '\n');
    // Pretend we had read further than the file is now
    const out = parseJsonlSince(path, 99999);
    assert.equal(out.records.length, 1);
    assert.equal(out.records[0].message_uuid, 'm-1');
  } finally { rmSync(dir, { recursive: true }); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/claude-workspace-tool/watcher && node --test test/parser.test.js`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `watcher/parser.js`**

```javascript
import { existsSync, statSync, openSync, readSync, closeSync } from 'node:fs';
import { basename, dirname, sep } from 'node:path';

/**
 * Extract session_id from JSONL path.
 * Top-level: ~/.claude/projects/<dir>/<uuid>.jsonl → <uuid>
 * Subagent:  ~/.claude/projects/<dir>/<parent>/subagents/agent-XXX.jsonl → agent-XXX
 */
export function sessionIdFromPath(path) {
  return basename(path, '.jsonl');
}

/**
 * Extract parent_session_id (or null).
 * If path is .../<parent>/subagents/<agent>.jsonl, parent is "<parent>".
 */
export function parentSessionFromPath(path) {
  const parts = path.split(sep);
  const subIdx = parts.indexOf('subagents');
  if (subIdx > 0) return parts[subIdx - 1];
  return null;
}

/**
 * Read JSONL bytes from `startOffset` to end-of-file.
 * Yield usage records for each `type=assistant` line with `message.usage`.
 *
 * Returns { records: [...], newOffset: number }.
 *
 * Truncation handling: if `startOffset` > current file size, we treat the
 * file as rotated and re-read from 0.
 */
export function parseJsonlSince(path, startOffset) {
  if (!existsSync(path)) return { records: [], newOffset: startOffset };
  const size = statSync(path).size;
  let from = startOffset;
  if (from > size) from = 0;        // truncated/rotated
  if (from === size) return { records: [], newOffset: size };

  const fd = openSync(path, 'r');
  try {
    const len = size - from;
    const buf = Buffer.alloc(len);
    readSync(fd, buf, 0, len, from);
    const text = buf.toString('utf8');
    const lines = text.split('\n');
    // last element is '' if the file ends in \n, or a partial line if not
    const records = [];
    let consumed = 0;
    for (let i = 0; i < lines.length - 1; i++) {
      const line = lines[i];
      consumed += Buffer.byteLength(line, 'utf8') + 1;  // +1 for \n
      if (!line) continue;
      const rec = parseLine(line, path);
      if (rec) records.push(rec);
    }
    // partial trailing line — leave it for next read
    return { records, newOffset: from + consumed };
  } finally {
    closeSync(fd);
  }
}

function parseLine(line, path) {
  let obj;
  try { obj = JSON.parse(line); } catch { return null; }
  if (obj.type !== 'assistant') return null;
  const msg = obj.message;
  if (!msg || !msg.usage) return null;
  const u = msg.usage;
  const cc = u.cache_creation || {};
  return {
    message_uuid: obj.uuid,
    session_id: sessionIdFromPath(path),
    parent_session_id: parentSessionFromPath(path),
    jsonl_path: path,
    ts: obj.timestamp,
    model: msg.model,
    service_tier: u.service_tier ?? null,
    input_tokens: u.input_tokens ?? 0,
    output_tokens: u.output_tokens ?? 0,
    cache_creation_5m_tokens: cc.ephemeral_5m_input_tokens ?? 0,
    cache_creation_1h_tokens: cc.ephemeral_1h_input_tokens ?? 0,
    cache_read_tokens: u.cache_read_input_tokens ?? 0,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/parser.test.js`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/claude-workspace-tool
git add watcher/parser.js watcher/test/parser.test.js
git commit -m "feat(watcher): JSONL parser extracting usage records since byte offset"
```

---

### Task C3: `poster.js`

**Files:**
- Create: `watcher/poster.js`
- Create: `watcher/test/poster.test.js`

- [ ] **Step 1: Write the failing test**

Create `watcher/test/poster.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Poster } from '../poster.js';

function makeFakeFetch() {
  const calls = [];
  let mode = 'ok';
  let nextStatus = 200;
  let nextBody = { inserted: 0, skipped: 0 };
  const fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    if (mode === 'refused') throw new Error('ECONNREFUSED');
    if (mode === '400') return { ok: false, status: 400, json: async () => ({ detail: 'bad' }) };
    return { ok: true, status: nextStatus, json: async () => nextBody };
  };
  return {
    fetch,
    calls,
    setMode(m) { mode = m; },
    setNext(status, body) { nextStatus = status; nextBody = body; },
  };
}

test('Poster.send POSTs a single batch to /ingest/usage', async () => {
  const f = makeFakeFetch();
  const p = new Poster({
    endpoint: 'http://127.0.0.1:7878/ingest/usage',
    fetchImpl: f.fetch,
    sleepMs: () => Promise.resolve(),
  });
  await p.send([{ message_uuid: 'u1' }, { message_uuid: 'u2' }]);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].body.events.length, 2);
});

test('Poster batches at batchSize boundary', async () => {
  const f = makeFakeFetch();
  const p = new Poster({ endpoint: 'http://x/ingest', fetchImpl: f.fetch, batchSize: 3, sleepMs: () => Promise.resolve() });
  const recs = Array.from({ length: 7 }, (_, i) => ({ message_uuid: `u${i}` }));
  await p.send(recs);
  assert.equal(f.calls.length, 3);                  // 3 + 3 + 1
  assert.equal(f.calls[0].body.events.length, 3);
  assert.equal(f.calls[2].body.events.length, 1);
});

test('Poster retries on connection refused', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 2,
    sleepMs: () => Promise.resolve(),
  });
  let threw = false;
  try { await p.send([{ message_uuid: 'u1' }]); } catch { threw = true; }
  assert.equal(threw, true);
  assert.equal(f.calls.length, 3);                  // initial + 2 retries
});

test('Poster does not retry on 400 (drops the batch)', async () => {
  const f = makeFakeFetch();
  f.setMode('400');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 5,
    sleepMs: () => Promise.resolve(),
  });
  // We choose: 400 returns peacefully and logs — does not throw, does not retry.
  await p.send([{ message_uuid: 'u1' }]);
  assert.equal(f.calls.length, 1);
});

test('Poster.queue grows under failure and drains on success', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 0,
    queueCap: 10,
    sleepMs: () => Promise.resolve(),
  });
  await p.enqueue({ message_uuid: 'u1' });
  await p.enqueue({ message_uuid: 'u2' });
  assert.equal(p.queueSize(), 2);

  f.setMode('ok');
  await p.flush();
  assert.equal(p.queueSize(), 0);
});

test('Poster queue drops oldest when at cap', async () => {
  const f = makeFakeFetch();
  f.setMode('refused');
  const p = new Poster({
    endpoint: 'http://x/ingest',
    fetchImpl: f.fetch,
    maxRetries: 0,
    queueCap: 2,
    sleepMs: () => Promise.resolve(),
  });
  await p.enqueue({ message_uuid: 'u1' });
  await p.enqueue({ message_uuid: 'u2' });
  await p.enqueue({ message_uuid: 'u3' });          // u1 should be dropped
  assert.equal(p.queueSize(), 2);
  assert.equal(p.peekQueue()[0].message_uuid, 'u2');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/claude-workspace-tool/watcher && node --test test/poster.test.js`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `watcher/poster.js`**

```javascript
const DEFAULT = {
  batchSize: 50,
  maxRetries: 5,
  queueCap: 1000,
  baseBackoffMs: 1000,
  maxBackoffMs: 30000,
};

export class Poster {
  constructor(opts) {
    this.endpoint = opts.endpoint;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    this.batchSize = opts.batchSize ?? DEFAULT.batchSize;
    this.maxRetries = opts.maxRetries ?? DEFAULT.maxRetries;
    this.queueCap = opts.queueCap ?? DEFAULT.queueCap;
    this.baseBackoffMs = opts.baseBackoffMs ?? DEFAULT.baseBackoffMs;
    this.maxBackoffMs = opts.maxBackoffMs ?? DEFAULT.maxBackoffMs;
    this.sleepMs = opts.sleepMs ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this._queue = [];
  }

  queueSize() { return this._queue.length; }
  peekQueue() { return [...this._queue]; }

  async enqueue(record) {
    this._queue.push(record);
    while (this._queue.length > this.queueCap) {
      this._queue.shift();
    }
  }

  async flush() {
    if (this._queue.length === 0) return;
    const drained = this._queue.splice(0, this._queue.length);
    try {
      await this.send(drained);
    } catch (err) {
      // Push back unsent records (oldest first) and respect cap
      this._queue.unshift(...drained);
      while (this._queue.length > this.queueCap) this._queue.shift();
      throw err;
    }
  }

  async send(records) {
    for (let i = 0; i < records.length; i += this.batchSize) {
      const batch = records.slice(i, i + this.batchSize);
      await this._sendBatch(batch);
    }
  }

  async _sendBatch(batch) {
    let attempt = 0;
    let backoff = this.baseBackoffMs;
    while (true) {
      try {
        const r = await this.fetchImpl(this.endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ events: batch }),
        });
        if (r.ok) return await r.json();
        if (r.status >= 400 && r.status < 500) {
          // Validation errors: log + drop, don't retry
          const detail = await r.json().catch(() => ({}));
          console.error(`[poster] ${r.status} dropped ${batch.length} records:`, detail);
          return;
        }
        // 5xx: retry
        throw new Error(`HTTP ${r.status}`);
      } catch (err) {
        if (attempt >= this.maxRetries) throw err;
        attempt += 1;
        await this.sleepMs(backoff);
        backoff = Math.min(backoff * 2, this.maxBackoffMs);
      }
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/poster.test.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/claude-workspace-tool
git add watcher/poster.js watcher/test/poster.test.js
git commit -m "feat(watcher): batched HTTP poster with retry queue"
```

---

### Task C4: `index.js` orchestrator (backfill + live)

**Files:**
- Create: `watcher/index.js`

This task is mostly integration glue. Tests for it would require running ccsniff against real files; we cover with the manual smoke test (Task D3).

- [ ] **Step 1: Create `watcher/index.js`**

```javascript
#!/usr/bin/env node
import { homedir } from 'node:os';
import { join } from 'node:path';
import { existsSync, readdirSync, statSync } from 'node:fs';
import { watch as ccsniffWatch } from 'ccsniff';
import { loadState, saveState } from './state.js';
import { parseJsonlSince } from './parser.js';
import { Poster } from './poster.js';

const ENDPOINT = process.env.CCT_INGEST_URL ?? 'http://127.0.0.1:7878/ingest/usage';
const PROJECTS_DIR = process.env.CCT_PROJECTS_DIR ?? join(homedir(), '.claude', 'projects');
const STATE_PATH = process.env.CCT_STATE_PATH ?? join(import.meta.dirname, '..', 'data', '.watcher-state.json');

const state = loadState(STATE_PATH);
const poster = new Poster({ endpoint: ENDPOINT });

let pendingSave = false;
function scheduleSave() {
  if (pendingSave) return;
  pendingSave = true;
  setTimeout(() => { saveState(STATE_PATH, state); pendingSave = false; }, 500);
}

function listAllJsonl(dir) {
  const out = [];
  function walk(d) {
    let entries;
    try { entries = readdirSync(d, { withFileTypes: true }); }
    catch { return; }
    for (const e of entries) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile() && p.endsWith('.jsonl')) out.push(p);
    }
  }
  walk(dir);
  return out;
}

async function processFile(path) {
  const offset = state.offsets[path] ?? 0;
  const { records, newOffset } = parseJsonlSince(path, offset);
  if (records.length > 0) {
    try {
      await poster.send(records);
      state.offsets[path] = newOffset;
      scheduleSave();
    } catch (err) {
      // Queue + retry on next event tick; keep offset unchanged so we re-read
      console.error(`[watcher] send failed for ${path}: ${err.message}`);
      for (const r of records) await poster.enqueue(r);
    }
  } else {
    state.offsets[path] = newOffset;
    scheduleSave();
  }
}

async function backfill() {
  const files = listAllJsonl(PROJECTS_DIR);
  console.log(`[watcher] backfill: scanning ${files.length} JSONL files`);
  const start = Date.now();
  let n = 0;
  for (const f of files) {
    await processFile(f);
    n += 1;
    if (n % 50 === 0) console.log(`[watcher] backfill: ${n}/${files.length}`);
  }
  console.log(`[watcher] backfill complete: ${n} files in ${(Date.now() - start) / 1000}s`);
}

async function liveLoop() {
  if (!existsSync(PROJECTS_DIR)) {
    console.error(`[watcher] PROJECTS_DIR does not exist: ${PROJECTS_DIR}`);
    process.exit(1);
  }

  const watcher = ccsniffWatch(PROJECTS_DIR);

  // Track active conversations → file paths so we can re-tail on streaming events
  watcher.on('streaming_complete', async ({ conversation }) => {
    const path = conversation?.path ?? conversation?.jsonlPath;
    if (path) await processFile(path);
  });

  watcher.on('conversation_created', async ({ conversation }) => {
    const path = conversation?.path ?? conversation?.jsonlPath;
    if (path) await processFile(path);
  });

  watcher.on('error', (err) => {
    console.error(`[watcher] ccsniff error:`, err);
  });

  // Periodic flush of pending queue (in case FastAPI was down when records arrived)
  setInterval(() => { poster.flush().catch(() => {}); }, 5000);

  // Periodic full re-scan as a safety net (e.g. if ccsniff missed an event)
  setInterval(async () => {
    const files = listAllJsonl(PROJECTS_DIR);
    for (const f of files) await processFile(f);
  }, 30_000);

  process.on('SIGINT', () => {
    console.log('\n[watcher] stopping');
    watcher.stop?.();
    saveState(STATE_PATH, state);
    process.exit(0);
  });

  console.log(`[watcher] live: watching ${PROJECTS_DIR}`);
  console.log(`[watcher] posting to ${ENDPOINT}`);
}

await backfill();
await liveLoop();
```

- [ ] **Step 2: Smoke check the bin runs**

Run: `cd ~/dev/claude-workspace-tool/watcher && node index.js` (no FastAPI running).

Expected: prints the backfill progress, then `[watcher] live: watching ...`. POSTs will fail with ECONNREFUSED but that's expected — the queue absorbs. Press `Ctrl-C`. State file at `data/.watcher-state.json` should be written.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-workspace-tool
git add watcher/index.js
git commit -m "feat(watcher): orchestrator — backfill on start, ccsniff-driven live loop"
```

Note: ccsniff's exact event payload shape (`conversation.path` vs `conversation.jsonlPath` vs other) may need adjustment. If smoke test in Task D3 fails to ingest live updates, log the raw event payloads with `console.dir(arg, { depth: 4 })` and adjust the path extraction in `streaming_complete` / `conversation_created` handlers. The 30s safety re-scan ensures ingestion still happens even if event payloads don't match.

---

## Phase D — Glue & verification

### Task D1: README — `gbc-watch` instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a section**

Add after the existing "Run it" / "gbc" section (or near the bottom):

````markdown
## Cost watcher (`gbc-watch`)

The cost watcher is a Node sidecar that tails Claude Code session JSONL files
and posts token-usage records to FastAPI's `/ingest/usage`. The `/costs` page
then renders running USD totals.

### One-time setup

```bash
cd watcher
npm install
```

Add to `~/.zshrc`:

```zsh
gbc-watch() {
  cd ~/dev/claude-workspace-tool/watcher && node index.js
}
```

Then `source ~/.zshrc`.

### Run

```bash
# terminal 1
gbc           # FastAPI on http://127.0.0.1:7878

# terminal 2
gbc-watch     # tails ~/.claude/projects/, posts to /ingest/usage
```

Open `http://127.0.0.1:7878/costs`. The first run backfills from existing
JSONL files (idempotent — safe to restart any time).

### Adding a new model's pricing

When Anthropic releases a new model, you'll see `⚠ N events without pricing`
on `/costs`. To resolve:

1. Add an entry to `app/pricing.py`'s `RATES` dict.
2. Run: `python -m app.cost_recompute`
3. Refresh `/costs`.

Existing rows keep the rates they were ingested with — this only updates
rows that were marked `unknown_pricing=1`.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): cost watcher (gbc-watch) install + run instructions"
```

---

### Task D2: Run all Python tests + Node tests

- [ ] **Step 1: Python full suite**

Run: `cd ~/dev/claude-workspace-tool && pytest`
Expected: PASS (existing suite unchanged + ~25 new tests across pricing/ingest/query/recompute/routes).

- [ ] **Step 2: Node tests**

Run: `cd ~/dev/claude-workspace-tool/watcher && npm test`
Expected: PASS (state ×3, parser ×8, poster ×6 = 17 tests).

- [ ] **Step 3: Fix anything failing**

If a test fails, fix the offending file. No commit step here — fixes get committed under whichever phase they belong to.

---

### Task D3: Manual smoke test

- [ ] **Step 1: Start FastAPI**

```bash
gbc
```

Expected: `Uvicorn running on http://127.0.0.1:7878`.

- [ ] **Step 2: Start the watcher**

```bash
gbc-watch
```

Expected:
1. `[watcher] backfill: scanning N JSONL files` (N matches `find ~/.claude/projects -name '*.jsonl' | wc -l`).
2. `[watcher] backfill complete: ... files in ... s`.
3. `[watcher] live: watching ...`.

- [ ] **Step 3: Open `/costs`**

Visit `http://127.0.0.1:7878/costs`. Expected: non-zero `TODAY` (or recent), populated BY MODEL bar, recent sessions in the table.

- [ ] **Step 4: Live update**

In a third terminal, start a Claude Code session: `claude` → run a small prompt that gets a response (e.g. "what's 2+2?"). Within ~5 seconds, `/costs` should reflect the new usage (a row's cost increments, or a new session row appears). Watch the page; HTMX polls every 5s.

- [ ] **Step 5: Restart-safety check**

`Ctrl-C` the watcher. `gbc-watch` again. Expected: backfill says `inserted: 0, skipped: N` from the previously-seen records — no double-counting.

Verify in DB:

```bash
sqlite3 ~/dev/claude-workspace-tool/data/workspace.db \
  "SELECT COUNT(*), SUM(cost_usd) FROM cost_events"
```

Both numbers should be stable across restarts.

- [ ] **Step 6: Commit nothing (smoke is observational)**

If anything misbehaves, fix in the relevant phase and re-run smoke.

---

## Self-Review

**Spec coverage check:**

| Spec section | Plan task |
|--------------|-----------|
| Architecture (two-process) | C4 (orchestrator), A3 (endpoint) |
| Data model — `cost_events` | A1 |
| JSONL → cost_events mapping | C2 (`parser.js`) |
| Pricing rates + resolution | A2 |
| Components (Python) | A1–A5, B1–B3 |
| Components (Node) | C1–C4 |
| Run command (`gbc-watch`) | D1 |
| Data flow per assistant message | C2 + C4 + A3 |
| `cost_usd` formula | A3 (`_compute_cost_usd`) + A5 (`_compute_cost`) |
| UI (page, HTMX, layout) | B1, B2, B3 |
| Empty state | B2 (template) |
| Watcher error handling (refused, malformed, rotated, deleted) | C2 (truncation), C3 (retry/queue) |
| FastAPI ingest error handling (dup, unknown, missing, negative, locked) | A3 |
| Pricing edge cases (tier fallback, unknown model) | A2 |
| Recompute script | A5 |
| Subagent attribution | C2 (path parsing), A4 (rollup query) |
| Backfill correctness (batch, pace) | C3 (batchSize=50), C4 (sequential) |
| Tests (Python) | A2/A3/A4/A5 + B1 |
| Tests (Node) | C1/C2/C3 |
| Manual smoke | D3 |
| Definition of done | D2 + D3 + (rates already in pricing.py) + D1 (README) |

All spec sections are covered. Two intentional simplifications worth flagging:

1. **Backfill pacing.** Spec said "pace at ~500 records/sec." Plan executes synchronously with no explicit rate limit. SQLite WAL handles thousands of inserts/sec on its own, and the user has ~hundreds of files. If backfill ever pegs the FastAPI process during a large initial run, add a `await sleepMs(1)` between batches in C4 and document it.
2. **`+N` subagent badge.** Spec called it "v1.5"; plan includes it (B2 template renders `s.subagent_count`). Promoting it from v1.5 to v1 because the data is already in the query result and the template change is one line.

**Placeholder scan:** None. All steps have concrete code or commands.

**Type/name consistency check:**
- `message_uuid`, `session_id`, `parent_session_id`, `cost_usd`, `unknown_pricing` — used identically in schema (A1), ingest (A3), query (A4), recompute (A5), parser (C2).
- `cache_creation_5m_tokens` / `cache_creation_1h_tokens` — schema (A1), payload (A3), parser output (C2). Consistent.
- `rates["input"]`, `rates["output"]`, `rates["cache_write_5m"]`, `rates["cache_write_1h"]`, `rates["cache_read"]` — `pricing.py` (A2), `cost_ingest.py` (A3), `cost_recompute.py` (A5). Consistent.
- `Poster.send` / `Poster.enqueue` / `Poster.flush` — defined in C3, called in C4. Consistent.
- `parseJsonlSince`, `loadState`, `saveState` — defined in C1/C2, called in C4. Consistent.

No placeholders, no type drift, full spec coverage.
