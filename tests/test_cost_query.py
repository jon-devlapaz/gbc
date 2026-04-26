from datetime import datetime, timedelta, timezone
from app.cost_query import today_total, range_total, by_model, by_session, by_cwd


def _insert(db, *, uuid, session, parent=None, ts, model, cost, cwd=None):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, parent_session_id, jsonl_path, ts, model, "
        "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd, cwd) "
        "VALUES (?, ?, ?, '/p', ?, ?, 0, 0, 0, 0, 0, ?, ?)",
        (uuid, session, parent, ts, model, cost, cwd),
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


def test_by_cwd_aggregates_and_orders_desc(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="m", cost=3.0, cwd="/proj/alpha")
    _insert(db, uuid="u2", session="s2", ts=_iso(now), model="m", cost=1.5, cwd="/proj/alpha")
    _insert(db, uuid="u3", session="s3", ts=_iso(now), model="m", cost=2.0, cwd="/proj/beta")
    _insert(db, uuid="u4", session="s4", ts=_iso(now), model="m", cost=0.5, cwd=None)
    rows = by_cwd(db, days=7)
    by_key = {c: v for c, v in rows}
    assert by_key["/proj/alpha"] == 4.5
    assert by_key["/proj/beta"] == 2.0
    assert by_key["unknown"] == 0.5
    assert rows[0][0] == "/proj/alpha"   # sorted desc


def test_by_cwd_empty_string_treated_as_unknown(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="m", cost=1.0, cwd="")
    rows = by_cwd(db, days=7)
    assert rows[0][0] == "unknown"


def test_by_cwd_excludes_old_events(db):
    now = datetime.now(timezone.utc)
    _insert(db, uuid="u1", session="s1", ts=_iso(now), model="m", cost=1.0, cwd="/proj/recent")
    _insert(db, uuid="u2", session="s2", ts=_iso(now - timedelta(days=10)), model="m", cost=99.0, cwd="/proj/old")
    rows = by_cwd(db, days=7)
    by_key = {c: v for c, v in rows}
    assert "/proj/recent" in by_key
    assert "/proj/old" not in by_key
