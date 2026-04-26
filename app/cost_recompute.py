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
