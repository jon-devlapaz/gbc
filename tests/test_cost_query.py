from datetime import datetime, timedelta, timezone
from datetime import datetime as _dt, timedelta as _td
from app.cost_query import today_total, range_total, by_model, by_session, by_cwd, by_day


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


def test_by_day_groups_by_local_date(db):
    # Two sessions today, one yesterday (UTC times converted to local)
    now = _dt.now()
    today = now.strftime("%Y-%m-%d")
    yest = (now - _td(days=1)).strftime("%Y-%m-%d")

    # Helper inserts ISO ts in UTC; SQLite's date(ts, 'localtime') will convert
    def _ins(uuid, ts_iso, cost, parent=None, session=None):
        sess = session or uuid
        db.execute(
            "INSERT INTO cost_events (message_uuid, session_id, parent_session_id, jsonl_path, ts, model, "
            "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
            "VALUES (?, ?, ?, '/p', ?, 'm', 0,0,0,0,0, ?)",
            (uuid, sess, parent, ts_iso, cost),
        )
        db.commit()

    # Generate timestamps in UTC such that local-time conversion lands in the
    # expected day. We use noon UTC which is safe for any timezone offset
    # within ±12h to land on the same local date.
    today_ts = (now.astimezone()).strftime("%Y-%m-%dT12:00:00Z")
    yest_ts = ((now - _td(days=1)).astimezone()).strftime("%Y-%m-%dT12:00:00Z")
    _ins("u1", today_ts, 1.0, session="s-today-1")
    _ins("u2", today_ts, 2.0, session="s-today-2")
    _ins("u3", yest_ts, 3.0, session="s-yest-1")

    result = by_day(db, days=30)
    by_date = {d["date"]: d for d in result}
    assert today in by_date
    assert yest in by_date
    assert by_date[today]["session_count"] == 2
    assert by_date[today]["day_total"] == 3.0
    assert by_date[yest]["session_count"] == 1


def test_by_day_includes_subagent_rollup(db):
    now = _dt.now()
    today_ts = now.strftime("%Y-%m-%dT12:00:00Z")

    def _ins(uuid, ts, cost, session=None, parent=None):
        sess = session or uuid
        db.execute(
            "INSERT INTO cost_events (message_uuid, session_id, parent_session_id, jsonl_path, ts, model, "
            "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
            "VALUES (?, ?, ?, '/p', ?, 'm', 0,0,0,0,0, ?)",
            (uuid, sess, parent, ts, cost),
        )
        db.commit()

    _ins("u1", today_ts, 5.0, session="parent-1")
    _ins("u2", today_ts, 1.5, session="agent-x", parent="parent-1")
    _ins("u3", today_ts, 0.5, session="agent-y", parent="parent-1")
    _ins("u4", today_ts, 7.0, session="other")

    result = by_day(db, days=30)
    assert len(result) == 1
    day = result[0]
    by_id = {s["session_id"]: s for s in day["sessions"]}
    assert by_id["parent-1"]["cost_usd"] == 7.0   # 5.0 + 1.5 + 0.5
    assert by_id["parent-1"]["subagent_count"] == 2
    assert "agent-x" not in by_id
    assert "agent-y" not in by_id
    assert day["day_total"] == 14.0   # 7.0 (parent) + 7.0 (other)
    assert day["session_count"] == 2


def test_by_day_excludes_old_outside_range(db):
    now = _dt.now()
    new = now.strftime("%Y-%m-%dT12:00:00Z")
    old = (now - _td(days=60)).strftime("%Y-%m-%dT12:00:00Z")

    def _ins(uuid, ts, cost):
        db.execute(
            "INSERT INTO cost_events (message_uuid, session_id, jsonl_path, ts, model, "
            "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, cost_usd) "
            "VALUES (?, ?, '/p', ?, 'm', 0,0,0,0,0, ?)",
            (uuid, uuid, ts, cost),
        )
        db.commit()

    _ins("u-new", new, 1.0)
    _ins("u-old", old, 99.0)
    result = by_day(db, days=30)
    dates = [d["date"] for d in result]
    assert any(d.startswith(now.strftime("%Y-%m")) for d in dates)
    assert not any(d.startswith((now - _td(days=60)).strftime("%Y-%m")) for d in dates)
