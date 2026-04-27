from datetime import datetime as _dt, timedelta as _td
from app.sessions_query import by_day


def _ins(db, sid, started, family='socratink-app', prompt='hello', cwd='/p'):
    db.execute(
        "INSERT INTO sessions (session_id, family, cwd, started_at, jsonl_path, jsonl_mtime, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?)",
        (sid, family, cwd, started, f"/x/{sid}.jsonl", _dt.now().isoformat()),
    )
    if prompt is not None:
        db.execute("UPDATE sessions SET first_prompt=? WHERE session_id=?", (prompt, sid))
    db.commit()


def test_by_day_groups_by_local_date(db):
    now = _dt.now()
    today = now.strftime("%Y-%m-%d")
    yest = (now - _td(days=1)).strftime("%Y-%m-%d")
    today_ts = now.strftime("%Y-%m-%dT12:00:00Z")
    yest_ts = (now - _td(days=1)).strftime("%Y-%m-%dT12:00:00Z")
    _ins(db, "s1", today_ts, prompt="alpha")
    _ins(db, "s2", today_ts, prompt="beta")
    _ins(db, "s3", yest_ts, prompt="gamma")

    result = by_day(db, days=14)
    by_date = {d["date"]: d for d in result}
    assert today in by_date
    assert yest in by_date
    assert by_date[today]["session_count"] == 2
    assert by_date[yest]["session_count"] == 1


def test_by_day_excludes_old(db):
    now = _dt.now()
    new_ts = now.strftime("%Y-%m-%dT12:00:00Z")
    old_ts = (now - _td(days=30)).strftime("%Y-%m-%dT12:00:00Z")
    _ins(db, "s-new", new_ts)
    _ins(db, "s-old", old_ts)
    result = by_day(db, days=14)
    dates = [d["date"] for d in result]
    assert any(d.startswith(now.strftime("%Y-%m")) for d in dates)
    # old shouldn't be there
    old_local_date = (now - _td(days=30)).strftime("%Y-%m-%d")
    assert old_local_date not in dates


def test_by_day_per_day_limit(db):
    now = _dt.now()
    ts = now.strftime("%Y-%m-%dT12:00:00Z")
    for i in range(30):
        _ins(db, f"s{i}", ts)
    result = by_day(db, days=14, per_day_limit=10)
    assert len(result) == 1
    assert len(result[0]["sessions"]) == 10
    assert result[0]["session_count"] == 30  # full count, not limited
