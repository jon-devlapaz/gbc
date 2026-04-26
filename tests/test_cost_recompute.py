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
