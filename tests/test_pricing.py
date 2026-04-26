import pytest
from app.pricing import resolve, RATES


def test_resolve_known_opus_standard():
    rates, unknown = resolve("claude-opus-4-7", "standard")
    assert unknown is False
    assert rates["input"] == 15
    assert rates["output"] == 75
    assert rates["cache_write_5m"] == 18.75
    assert rates["cache_write_1h"] == 30
    assert rates["cache_read"] == 1.5


def test_resolve_known_sonnet_standard():
    rates, unknown = resolve("claude-sonnet-4-6", "standard")
    assert unknown is False
    assert rates["input"] == 3
    assert rates["output"] == 15


def test_resolve_known_haiku_standard():
    rates, unknown = resolve("claude-haiku-4-5", "standard")
    assert unknown is False
    assert rates["input"] == 1
    assert rates["output"] == 5


def test_resolve_unknown_tier_falls_back_to_standard():
    rates, unknown = resolve("claude-opus-4-7", "priority")
    # No (opus, priority) entry in v1 RATES, falls back to (opus, standard)
    assert unknown is False
    assert rates["input"] == 15


def test_resolve_missing_tier_treated_as_standard():
    rates, unknown = resolve("claude-opus-4-7", None)
    assert unknown is False
    assert rates["input"] == 15


def test_resolve_unknown_model_returns_zero_rates():
    rates, unknown = resolve("claude-future-99-0", "standard")
    assert unknown is True
    assert rates["input"] == 0
    assert rates["output"] == 0
    assert rates["cache_write_5m"] == 0
    assert rates["cache_write_1h"] == 0
    assert rates["cache_read"] == 0


def test_rates_keys_well_formed():
    for key, val in RATES.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert set(val.keys()) == {"input", "output", "cache_write_5m", "cache_write_1h", "cache_read"}
