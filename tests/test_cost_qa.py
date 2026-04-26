from datetime import datetime, timezone
from app.cost_qa import build_snapshot, ask


def _insert(db, *, uuid, ts, cost, cwd=None, parent=None, model='claude-opus-4-7'):
    db.execute(
        "INSERT INTO cost_events (message_uuid, session_id, parent_session_id, jsonl_path, ts, model, "
        "input_rate, output_rate, cache_write_5m_rate, cache_write_1h_rate, cache_read_rate, "
        "cost_usd, cwd) VALUES (?, ?, ?, '/p', ?, ?, 0,0,0,0,0, ?, ?)",
        (uuid, uuid, parent, ts, model, cost, cwd),
    )
    db.commit()


def test_build_snapshot_shape(db):
    _insert(db, uuid='u1', ts='2026-04-25T12:00:00Z', cost=1.5, cwd='/p1')
    s = build_snapshot(db)
    assert "currency" in s
    assert "today" in s and "date" in s["today"] and "total" in s["today"]
    assert "this_week_total" in s
    assert "this_month_total" in s
    assert "rolling_7d_avg" in s
    assert "daily_last_14d" in s and len(s["daily_last_14d"]) == 14
    assert "by_model_7d" in s
    assert "by_cwd_7d" in s
    assert "top_sessions_7d_by_cost" in s
    assert "unknown_pricing_events" in s
    assert "total_events" in s and s["total_events"] == 1


def test_ask_invokes_call_fn_with_prompt():
    captured = {}

    def fake(prompt):
        captured['prompt'] = prompt
        return "You spent $5.00 today."

    out = ask({"hello": "world"}, "What did I spend?", fake)
    assert out == "You spent $5.00 today."
    assert "hello" in captured['prompt']
    assert "What did I spend?" in captured['prompt']


def test_build_snapshot_empty_db(db):
    s = build_snapshot(db)
    assert s["total_events"] == 0
    assert s["today"]["total"] == 0.0
    assert s["this_week_total"] == 0.0
    assert s["rolling_7d_avg"] == 0.0
    assert s["by_model_7d"] == []
    assert s["by_cwd_7d"] == []
    assert len(s["daily_last_14d"]) == 14


def test_build_snapshot_by_cwd_populated(db):
    _insert(db, uuid='u1', ts='2026-04-25T12:00:00Z', cost=2.0, cwd='/proj/alpha')
    _insert(db, uuid='u2', ts='2026-04-25T13:00:00Z', cost=1.0, cwd='/proj/beta')
    s = build_snapshot(db)
    cwds = [entry["cwd"] for entry in s["by_cwd_7d"]]
    assert '/proj/alpha' in cwds or '/proj/beta' in cwds
