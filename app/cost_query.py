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
