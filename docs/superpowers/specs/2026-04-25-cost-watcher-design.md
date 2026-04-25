# Claude Workspace Tool — Cost Watcher (ccsniff sidecar)

**Date:** 2026-04-25
**Status:** Design approved, pending spec review
**Owner:** Jon
**Scope:** New feature on top of P2 (session index). Adds live token-cost accounting from JSONL session files into the existing FastAPI tool. Non-blocking on the planned P3 (insight extraction).

## Goals

1. Show running USD spend across all Claude Code sessions on a `/costs` page in the existing tool.
2. Update reactively (no manual reindex) — totals reflect new assistant messages within ~5s of them being written to disk.
3. Attribute subagent costs to their parent session so totals match the user's mental model of "this conversation cost X."
4. Preserve historical accuracy when Anthropic's prices change — past `cost_events` keep the rates they were ingested with.
5. Be idempotent and crash-tolerant — restarting the watcher (or the whole machine) never double-counts.

## Non-Goals

- Real-time per-token streaming display (usage only lands at message end anyway).
- Charts, sparklines, or calendar heatmaps. Three numbers + a horizontal bar.
- Editing or annotating cost rows. Read-only UI.
- Multi-user / multi-machine aggregation. Local only.
- Live cost projections or rate alerting. Pure accounting.
- Replacing Anthropic's billing dashboard. This is for personal workflow visibility, not invoicing.

## Architecture

```
┌──────────────────────────┐         ┌─────────────────────────┐
│ Node sidecar (ccsniff)   │  HTTP   │ FastAPI (existing tool) │
│ watcher/                 │ ──POST──▶ /ingest/usage           │
│  • watch() ~/.claude     │         │  → cost_events table    │
│  • on streaming_complete │         │                         │
│    re-tail JSONL         │         │ /costs   (page)         │
│  • parse new assistant   │         │ /costs/partial (HTMX)   │
│    msgs → usage record   │         │  → renders aggregates   │
│  • POST {session_id,     │         │                         │
│    parent_session_id,    │         │ pricing.py              │
│    model, tier, tokens,  │         │  → applies rates        │
│    message_uuid, ts}     │         │  → writes cost_usd      │
└──────────────────────────┘         └─────────────────────────┘
        ▲                                       ▲
        │ reads                                 │ reads
        ▼                                       ▼
   ~/.claude/projects/*.jsonl            data/workspace.db
```

### Key architectural decisions

1. **Two-process design.** Python FastAPI (existing) + Node watcher (new). Communicate over `127.0.0.1:7878` HTTP. Decoupled lifecycles.
2. **Sidecar sends raw token counts, never dollars.** Pricing lives only in `app/pricing.py`. Single source of truth for rates.
3. **Idempotency via `message_uuid` UNIQUE.** Backfill, restarts, and overlap on resume are all safe.
4. **Subagent attribution from path.** `<projects>/<dir>/<uuid>.jsonl` → `parent_session_id = NULL`. `<projects>/<dir>/<uuid>/subagents/agent-*.jsonl` → `parent_session_id = <uuid>`.
5. **ccsniff is a notifier, not a parser.** Token usage lives at message level in JSONL, not in any of ccsniff's content blocks. We listen to ccsniff for "this conversation got new content," then re-tail the JSONL ourselves.

## Data model

Additive migration in `app/db.py`. One new table.

```sql
CREATE TABLE IF NOT EXISTS cost_events (
  id INTEGER PRIMARY KEY,
  message_uuid TEXT NOT NULL UNIQUE,         -- dedup key (assistant msg uuid)
  session_id TEXT NOT NULL,                  -- derived from JSONL filename
  parent_session_id TEXT,                    -- NULL for main, parent uuid for subagent
  jsonl_path TEXT NOT NULL,                  -- source file (debugging)
  ts TEXT NOT NULL,                          -- assistant message timestamp
  model TEXT NOT NULL,                       -- e.g. "claude-opus-4-7"
  service_tier TEXT,                         -- "standard" | "priority" | NULL
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  -- rate snapshot ($ per million tokens) — preserves historical accuracy
  input_rate REAL NOT NULL,
  output_rate REAL NOT NULL,
  cache_write_5m_rate REAL NOT NULL,
  cache_write_1h_rate REAL NOT NULL,
  cache_read_rate REAL NOT NULL,
  cost_usd REAL NOT NULL,                    -- precomputed at ingest
  unknown_pricing INTEGER NOT NULL DEFAULT 0 -- 1 if model not in rate table
);
CREATE INDEX IF NOT EXISTS cost_events_ts        ON cost_events(ts DESC);
CREATE INDEX IF NOT EXISTS cost_events_session   ON cost_events(session_id);
CREATE INDEX IF NOT EXISTS cost_events_parent    ON cost_events(parent_session_id);
CREATE INDEX IF NOT EXISTS cost_events_model     ON cost_events(model);
```

### Pricing table (`app/pricing.py`)

```python
RATES = {  # $ per 1M tokens
  ("claude-opus-4-7",   "standard"): {"input":15, "output":75,  "cache_write_5m":18.75, "cache_write_1h":30, "cache_read":1.5},
  ("claude-sonnet-4-6", "standard"): {"input":3,  "output":15,  "cache_write_5m":3.75,  "cache_write_1h":6,  "cache_read":0.3},
  ("claude-haiku-4-5",  "standard"): {"input":1,  "output":5,   "cache_write_5m":1.25,  "cache_write_1h":2,  "cache_read":0.1},
}
```

Resolution order: `(model, tier)` → `(model, "standard")` → zero-rate + `unknown_pricing=1`.

### JSONL → cost_events field mapping

Source: each assistant message in JSONL has `message.usage` with this shape:

```json
{
  "input_tokens": 6,
  "output_tokens": 175,
  "cache_creation_input_tokens": 32983,
  "cache_read_input_tokens": 0,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 0,
    "ephemeral_1h_input_tokens": 32983
  },
  "service_tier": "standard"
}
```

| `cost_events` column | JSONL source |
|----------------------|--------------|
| `input_tokens` | `message.usage.input_tokens` |
| `output_tokens` | `message.usage.output_tokens` |
| `cache_creation_5m_tokens` | `message.usage.cache_creation.ephemeral_5m_input_tokens` (fallback 0) |
| `cache_creation_1h_tokens` | `message.usage.cache_creation.ephemeral_1h_input_tokens` (fallback 0) |
| `cache_read_tokens` | `message.usage.cache_read_input_tokens` |
| `model` | `message.model` |
| `service_tier` | `message.usage.service_tier` |
| `message_uuid` | top-level `uuid` of the JSONL line |
| `ts` | top-level `timestamp` |
| `session_id` | basename of JSONL path without `.jsonl` (or parent dir name for subagents) |

Note: the top-level `cache_creation_input_tokens` is the *sum* of the two ephemeral buckets; we ignore it and use the breakdown so 5m and 1h cache writes are billed at their distinct rates.

### Schema decisions worth flagging

- **No FK to `sessions` table.** ccsniff may see assistant messages before P2's reindex registers the session row. Loose join via `session_id` text is more forgiving.
- **`cost_usd` is precomputed and stored.** Avoids re-multiplying on every page render and keeps the rate snapshot meaningful.
- **Subagent rollup is a query concern.** Storage is flat; aggregation queries decide whether to fold subagent rows into parents.

## Components

### Python (in `app/`)

| File | Purpose | LOC est. |
|------|---------|----------|
| `db.py` | Add `cost_events` table + indices to existing `SCHEMA` | +25 |
| `pricing.py` | `RATES` dict, `resolve(model, tier) -> rates_dict` | ~40 |
| `cost_ingest.py` | `POST /ingest/usage`. Validates payload, looks up rates, computes `cost_usd`, INSERTs `ON CONFLICT message_uuid DO NOTHING` | ~80 |
| `cost_query.py` | `today_total()`, `range_total(days)`, `by_model(days)`, `by_session(limit)` | ~100 |
| `cost_recompute.py` | CLI module: re-resolves `WHERE unknown_pricing=1` rows after pricing.py is updated | ~40 |
| `main.py` | Mount `/costs` page route + `/costs/partial` HTMX route + include `/ingest/usage` | +30 |
| `templates/costs.html` | Full page extending `base.html` | ~30 |
| `templates/_costs_body.html` | HTMX-swappable partial (big number, model bar, session table) | ~60 |
| `static/style.css` | DMG-styled cost number + bar styles | +50 |

### Node (new `watcher/` directory)

| File | Purpose |
|------|---------|
| `package.json` | Dep: `ccsniff`. Built-in `fetch` (Node 18+). |
| `index.js` | Entry: instantiate `JsonlWatcher`, hook events, run backfill, then live mode |
| `parser.js` | Given JSONL path + last byte offset, yields usage records for new assistant messages |
| `poster.js` | Batched POST (50/req) to `/ingest/usage`. Retry with exponential backoff. In-memory queue (cap 1000). |
| `state.js` | Read/write `data/.watcher-state.json` (per-file byte offsets) |
| `test/parser.test.js` | Node `--test` runner |
| `test/poster.test.js` | Node `--test` runner |

### Run command

New zsh function alongside existing `gbc`:

```zsh
gbc-watch() {
  cd ~/dev/claude-workspace-tool/watcher && node index.js
}
```

Both are long-running. Order doesn't matter — watcher's retry queue handles startup race.

## Data flow per assistant message

```
JSONL line written by Claude Code
  ↓
ccsniff fires streaming_complete for conversation
  ↓
parser.js seeks to state.offsets[path], reads new lines
  ↓
for each new line with type=assistant && message.usage:
   build record { message_uuid, ts, model, tier, tokens... }
  ↓
poster.js POSTs to /ingest/usage (batched)
  ↓
cost_ingest validates → pricing.resolve → compute cost_usd → INSERT OR IGNORE
  ↓
HTMX poll on /costs/partial picks up new totals on next 5s tick
```

### `cost_usd` formula

```
cost_usd = (input_tokens         * input_rate
         +  output_tokens        * output_rate
         +  cache_creation_5m_tokens * cache_write_5m_rate
         +  cache_creation_1h_tokens * cache_write_1h_rate
         +  cache_read_tokens    * cache_read_rate) / 1_000_000
```

## UI

Page at `/costs`, linked from existing nav.

```
┌───────────────────────────────────────────────────────────┐
│  COSTS                                                    │
│  ┌─────────────────────────────────────────────────────┐ │
│  │   TODAY     $4.27                                   │ │
│  │   7 DAYS  $31.84    30 DAYS  $112.06                │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  BY MODEL  (last 7d)                                      │
│  opus-4-7    ████████████████████████  $24.10  (76%)     │
│  sonnet-4-6  █████                     $ 5.94  (19%)     │
│  haiku-4-5   ▌                         $ 0.80  ( 3%)     │
│  unknown     ▏                         $ 1.00  ( 3%)     │
│                                                           │
│  RECENT SESSIONS                                          │
│  STARTED        TITLE              MODEL    TOKENS  COST  │
│  ...                                                      │
└───────────────────────────────────────────────────────────┘
```

### UI behaviors

- HTMX poll on `<div id="costs-body" hx-get="/costs/partial" hx-trigger="every 5s" hx-swap="innerHTML">`. Header is static, body re-renders.
- **Title column** = `sessions.first_prompt`, truncated at 40 chars.
- **Model column** = model on the most recent assistant message in the session (sessions can switch models mid-conversation).
- **Tokens column** = sum of all token fields, formatted with `k`/`m` suffix.
- **Subagent rollup** in session table: rows aggregate `cost_events WHERE session_id = X OR parent_session_id = X`. Optional `[+N]` badge for sessions with N subagents (v1.5).
- **Unknown pricing**: `unknown` row in BY MODEL plus `⚠ N events without pricing` warning linking to a debug list of distinct unknown `(model, tier)` pairs.

### Empty state

> "No cost events yet. Start the watcher with `gbc-watch` in another terminal — it will backfill from your existing JSONL files, then update live as you work."

## Error handling

### Watcher (Node)

| Failure | Behavior |
|---------|----------|
| FastAPI down (ECONNREFUSED) | In-memory queue (cap 1000). Exp backoff 1s → 30s. Drop oldest if cap hit + log. |
| Malformed JSONL line | `try/catch` around `JSON.parse`. Skip + log + advance offset. |
| File rotated/truncated | Reset offset to 0, log warning, re-process from start. |
| File deleted | Drop from `state.offsets`. |
| ccsniff `error` event | Log + continue. Don't propagate to process exit. |
| Sidecar killed mid-write | `state.json` writes *after* successful POST → small overlap on restart; idempotency handles dupes. |

### FastAPI ingest

| Failure | Behavior |
|---------|----------|
| Duplicate `message_uuid` | `INSERT OR IGNORE`, returns `{"inserted": 0}`. |
| Unknown `(model, tier)` | Zeros + `unknown_pricing=1`. UI surfaces warning. |
| Missing required field | 400 with field name. Watcher logs + drops (no retry loop). |
| DB locked | 3 retries 50ms apart, then 503. Watcher requeues. |
| Negative or absurd token counts (< 0 or > 10M) | 400. Don't trust upstream blindly. |

### Pricing edge cases

- **Service tier `priority`/`batch`/`flex`/`""`/missing**: lookup `(model, tier)` first; fall back to `(model, "standard")`. If still unknown, `unknown_pricing=1`. The 5x cost differential between standard and priority means we don't silently default — record what we matched.
- **New model name**: `unknown_pricing=1` until added to `pricing.py`. Recompute backfills.
- **Recompute script**: `python -m app.cost_recompute` walks `WHERE unknown_pricing=1`, retries `pricing.resolve`, updates rate columns + `cost_usd` if now resolvable.

### Subagent attribution edge cases

- **Orphan subagent** (parent JSONL deleted): `parent_session_id` set, no parent in `sessions`. UI does `LEFT JOIN` so it renders standalone — degraded but not broken.
- **Nested subagents**: filesystem path is flat (`<top>/subagents/agent-*.jsonl`), so we attribute everything to the top-level session. No nested chain walk.

### Backfill correctness

- **Batch POSTs** at 50 records/request.
- **Pace at ~500 records/sec** so SQLite writer keeps up and live ingest stays responsive.
- Backfill mode logs `Backfilled N records from M files in Xs` on completion.

## Testing

### Python (pytest)

| Test | Coverage |
|------|---------|
| `test_pricing.py` | `resolve()` known `(model, tier)`. Falls back to `standard`. Zeros + sentinel for unknown model. |
| `test_cost_ingest.py` | Happy path. Duplicate `message_uuid`. Negative tokens → 400. Unknown model → `unknown_pricing=1`. Tier fallback. |
| `test_cost_query.py` | Fixtures: ~20 rows, 3 sessions, 2 models, 2 days. Assert `today_total`, `range_total(7)`, `by_model`, `by_session`. Subagent rollup. |
| `test_cost_recompute.py` | Insert `unknown_pricing=1`, add model to RATES, run recompute, row updates. |

Real SQLite (existing `tests/conftest.py` pattern). Synthetic JSONL strings — no dependency on real `~/.claude` state.

### Node (built-in `node --test`)

| Test | Coverage |
|------|---------|
| `parser.test.js` | Fixture JSONL, offset 0 → expected records. Mid-file offset. Skip malformed lines. Extract `parent_session_id` from path. |
| `poster.test.js` | Mock `fetch`. Success → queue empty. ECONNREFUSED → backoff retry. 400 → drop record. 50-record batching boundary. |

### Manual smoke (in README)

1. `gbc` running, `gbc-watch` running.
2. Run any Claude Code prompt.
3. Visit `/costs`. New row appears within ~5s.
4. Stop watcher, run another prompt, restart watcher. Row appears (no duplicates).

### Out of scope for tests

- ccsniff itself.
- HTMX/Jinja rendering. Manual smoke covers it.
- End-to-end Node→Python over real network. Python tested with `httpx.AsyncClient`; Node tested with mocked fetch. Seam = `/ingest/usage` contract, asserted on both sides.

## Definition of done

1. All Python tests green.
2. All Node tests green.
3. Manual smoke checklist passes: backfill produces non-zero rows; live update appears in `/costs` within 5s of a new assistant message.
4. Pricing for `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5` populated in `pricing.py` with current standard-tier rates.
5. README updated with `gbc-watch` install + run instructions.

## Open questions deferred to v1.5

- Auto-start watcher via launchd (currently manual `gbc-watch`).
- Per-day calendar heatmap.
- Per-model column in session table (currently shows last-message model only).
- Cost-by-cwd / per-project breakdown.
- Export `cost_events` to CSV.
