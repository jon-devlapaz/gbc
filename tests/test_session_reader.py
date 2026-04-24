import json
from pathlib import Path
from app.session_reader import stream, EventView


def _write(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events))
    return path


def test_stream_yields_events_in_order(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": "t2",
         "message": {"role": "assistant", "content": "hi"}},
    ])
    evs = stream(p, offset=0, limit=100)
    assert len(evs) == 2
    assert evs[0].kind == "prompt"
    assert evs[1].kind == "assistant"


def test_stream_offset_limit(tmp_path):
    events = [
        {"type": "user", "timestamp": f"t{i}",
         "message": {"role": "user", "content": f"msg-{i}"}}
        for i in range(20)
    ]
    p = _write(tmp_path / "s.jsonl", events)
    evs = stream(p, offset=5, limit=3)
    assert len(evs) == 3
    assert "msg-5" in evs[0].body_preview


def test_stream_skips_malformed_line(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"type": "user", "message": {"role":"user","content":"x"}}\nNOT-JSON\n')
    evs = stream(p, offset=0, limit=100)
    assert len(evs) == 1


def test_stream_missing_file(tmp_path):
    assert stream(tmp_path / "ghost.jsonl", 0, 10) == []


def test_event_classifies_kinds(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "x"}},
        {"type": "user", "message": {"role": "user",
           "content": [{"type": "tool_result", "content": "..."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
        {"type": "summary"},
    ])
    evs = stream(p, 0, 100)
    kinds = [e.kind for e in evs]
    assert kinds == ["prompt", "tool_result", "assistant", "meta"]
