"""Cost Q&A: stats snapshot + LLM-driven question answering."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.cost_query import today_total, range_total, by_model, by_session, by_cwd


def build_snapshot(conn: sqlite3.Connection) -> dict:
    """Compact stats summary for LLM context. ~1-2 KB JSON."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    today = today_total(conn)
    week = range_total(conn, days=7)
    month = range_total(conn, days=30)
    rolling_7d_avg = week / 7.0 if week else 0.0

    def _ts(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Daily series for last 14 days (so anomaly questions can compare day to day)
    daily = []
    for i in range(14):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE ts >= ? AND ts < ?",
            (_ts(day_start), _ts(day_end)),
        ).fetchone()
        daily.append({"date": day_start.strftime("%Y-%m-%d"), "total": round(float(row[0]), 4)})

    bm = [{"model": m, "cost": round(c, 4)} for m, c in by_model(conn, days=7)]
    bc = [{"cwd": c, "cost": round(v, 4)} for c, v in by_cwd(conn, days=7)][:15]
    sessions = by_session(conn, limit=10)
    top_sessions = [
        {
            "session_id": s["session_id"][:16],
            "last_ts": s["last_ts"],
            "model": s["last_model"],
            "cost": round(s["cost_usd"], 4),
            "tokens": s["total_tokens"],
            "subagents": s["subagent_count"],
        }
        for s in sessions
    ]

    unknown = conn.execute("SELECT COUNT(*) FROM cost_events WHERE unknown_pricing = 1").fetchone()[0]
    total_events = conn.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0]

    return {
        "currency": "USD",
        "today": {"date": today_str, "total": round(today, 4)},
        "this_week_total": round(week, 4),
        "this_month_total": round(month, 4),
        "rolling_7d_avg": round(rolling_7d_avg, 4),
        "daily_last_14d": daily,
        "by_model_7d": bm,
        "by_cwd_7d": bc,
        "top_sessions_7d_by_cost": top_sessions,
        "unknown_pricing_events": int(unknown),
        "total_events": int(total_events),
    }


SYSTEM_PROMPT = """You are a cost-analytics assistant for Claude Code token spend. \
You answer questions concisely (3-6 sentences max) using only the JSON snapshot below. \
If the snapshot doesn't contain the data needed, say so plainly — don't guess. \
Round dollars to 2 decimals. Mention specific session IDs, models, or cwds when they're in the snapshot. \
If asked for a recommendation or interpretation, give one short opinion.

SNAPSHOT:
{snapshot}

QUESTION: {question}

ANSWER:"""


def ask(snapshot: dict, question: str, call_fn: Callable[[str], str]) -> str:
    import json as _json
    prompt = SYSTEM_PROMPT.format(
        snapshot=_json.dumps(snapshot, indent=2),
        question=question.strip(),
    )
    return call_fn(prompt).strip()
