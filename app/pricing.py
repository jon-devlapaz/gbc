"""
Token-cost rates for Anthropic models (USD per 1M tokens).

Update this file when Anthropic changes pricing. Existing cost_events rows
keep the rates they were ingested with — historical totals do not shift.
After updating, run `python -m app.cost_recompute` to backfill any rows
that were ingested with unknown_pricing=1.
"""

# Keys are (model, service_tier). Values are $ per 1M tokens.
RATES = {
    ("claude-opus-4-7",   "standard"): {
        "input": 15.0, "output": 75.0,
        "cache_write_5m": 18.75, "cache_write_1h": 30.0,
        "cache_read": 1.5,
    },
    ("claude-sonnet-4-6", "standard"): {
        "input": 3.0, "output": 15.0,
        "cache_write_5m": 3.75, "cache_write_1h": 6.0,
        "cache_read": 0.3,
    },
    ("claude-haiku-4-5",  "standard"): {
        "input": 1.0, "output": 5.0,
        "cache_write_5m": 1.25, "cache_write_1h": 2.0,
        "cache_read": 0.1,
    },
}

ZERO_RATES = {
    "input": 0.0, "output": 0.0,
    "cache_write_5m": 0.0, "cache_write_1h": 0.0, "cache_read": 0.0,
}


def resolve(model: str, service_tier: str | None) -> tuple[dict, bool]:
    """
    Look up rates for (model, service_tier).

    Resolution order:
      1. exact (model, service_tier)
      2. (model, "standard")  [tier fallback]
      3. ZERO_RATES + unknown=True

    Returns (rates_dict, unknown_pricing_flag).
    """
    tier = service_tier or "standard"
    if (model, tier) in RATES:
        return RATES[(model, tier)], False
    if (model, "standard") in RATES:
        return RATES[(model, "standard")], False
    return ZERO_RATES.copy(), True
