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
        conn = get_db()
        inserted = 0
        skipped = 0
        for e in batch.events:
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
