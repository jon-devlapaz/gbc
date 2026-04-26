from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def _payload(**overrides):
    base = {
        "message_uuid": "uuid-1",
        "session_id": "sess-1",
        "parent_session_id": None,
        "jsonl_path": "/tmp/sess-1.jsonl",
        "ts": "2026-04-25T10:00:00Z",
        "model": "claude-opus-4-7",
        "service_tier": "standard",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_5m_tokens": 0,
        "cache_creation_1h_tokens": 100_000,
        "cache_read_tokens": 50_000,
    }
    base.update(overrides)
    return base


def test_ingest_happy_path(client):
    r = client.post("/ingest/usage", json={"events": [_payload()]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1
    assert body["skipped"] == 0


def test_ingest_computes_cost_usd(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload()]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute("SELECT cost_usd FROM cost_events WHERE message_uuid='uuid-1'").fetchone()
    # opus standard: input=15, output=75, cache_write_1h=30, cache_read=1.5
    # cost = (1000*15 + 500*75 + 100_000*30 + 50_000*1.5) / 1_000_000
    #      = (15_000 + 37_500 + 3_000_000 + 75_000) / 1e6 = 3.1275
    assert row[0] == pytest.approx(3.1275, rel=1e-6)


def test_ingest_dedupes_by_message_uuid(client):
    p = _payload()
    r1 = client.post("/ingest/usage", json={"events": [p]})
    r2 = client.post("/ingest/usage", json={"events": [p]})
    assert r1.json() == {"inserted": 1, "skipped": 0}
    assert r2.json() == {"inserted": 0, "skipped": 1}


def test_ingest_unknown_model_marks_unknown_pricing(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload(model="claude-future-9", message_uuid="u-future")]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute(
        "SELECT unknown_pricing, cost_usd, input_rate FROM cost_events WHERE message_uuid='u-future'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == 0.0
    assert row[2] == 0.0


def test_ingest_rejects_negative_tokens(client):
    r = client.post("/ingest/usage", json={"events": [_payload(input_tokens=-1)]})
    assert r.status_code in (400, 422)


def test_ingest_rejects_absurd_tokens(client):
    r = client.post("/ingest/usage", json={"events": [_payload(output_tokens=10_000_001)]})
    assert r.status_code in (400, 422)


def test_ingest_rejects_missing_required_field(client):
    bad = _payload()
    del bad["model"]
    r = client.post("/ingest/usage", json={"events": [bad]})
    assert r.status_code in (400, 422)


def test_ingest_batch(client):
    events = [_payload(message_uuid=f"u-{i}") for i in range(10)]
    r = client.post("/ingest/usage", json={"events": events})
    assert r.json() == {"inserted": 10, "skipped": 0}


def test_ingest_tier_fallback_to_standard(client, tmp_path):
    client.post("/ingest/usage", json={"events": [_payload(service_tier="priority", message_uuid="u-pri")]})
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute("SELECT unknown_pricing, input_rate FROM cost_events WHERE message_uuid='u-pri'").fetchone()
    assert row[0] == 0
    assert row[1] == 15.0


def test_ingest_within_batch_duplicate(client):
    p = _payload(message_uuid="dup")
    r = client.post("/ingest/usage", json={"events": [p, p]})
    body = r.json()
    assert body["inserted"] == 1
    assert body["skipped"] == 1


def test_ingest_stores_cwd(client, tmp_path):
    p = _payload(message_uuid="u-cwd", cwd="/Users/jondev/dev/socratink")
    r = client.post("/ingest/usage", json={"events": [p]})
    assert r.status_code == 200
    import sqlite3
    db = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = db.execute("SELECT cwd FROM cost_events WHERE message_uuid='u-cwd'").fetchone()
    assert row[0] == "/Users/jondev/dev/socratink"
