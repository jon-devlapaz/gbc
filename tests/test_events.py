import json
from pathlib import Path
from app.events import parse_file


def _write(path: Path, lines: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_happy_path_yields_one_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s1", "type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "cwd": "/Users/jondev/dev/socratink/prod/socratink-app",
         "message": {"role": "user", "content": "hello world"}},
        {"sessionId": "s1", "type": "assistant", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": "hi there"}},
    ])
    r = parse_file(p)
    assert r.session.session_id == "a"   # filename stem
    assert r.session.cwd.endswith("socratink-app")
    assert r.session.started_at == "2026-01-01T00:00:00Z"
    assert r.session.ended_at == "2026-01-01T00:00:05Z"
    assert r.session.message_count == 2
    assert r.session.prompt_count == 1
    assert r.session.first_prompt == "hello world"
    assert len(r.prompts) == 1
    assert r.prompts[0].content == "hello world"


def test_tool_result_is_not_a_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s2", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "[file contents]"}
         ]}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 0
    assert r.prompts == []


def test_ismeta_prompt_is_skipped(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s3", "type": "user", "timestamp": "t1", "isMeta": True,
         "message": {"role": "user", "content": "system injected"}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 0


def test_array_content_text_is_a_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s4", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "hello"}
         ]}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 1
    assert r.prompts[0].content == "hello"


def test_malformed_line_captured_as_error(tmp_path: Path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"good": true}\nnot json at all\n{"also": "good"}\n')
    r = parse_file(p)
    assert len(r.errors) == 1
    assert r.errors[0].line_number == 2


def test_missing_cwd_and_timestamps_tolerated(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s5", "type": "user",
         "message": {"role": "user", "content": "hi"}},
    ])
    r = parse_file(p)
    assert r.session.session_id == "a"
    assert r.session.cwd is None
    assert r.session.started_at is None
    assert r.session.prompt_count == 1


def test_prompt_content_clamped_when_huge(tmp_path: Path):
    big = "x" * 300_000
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s6", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": big}},
    ])
    r = parse_file(p)
    assert len(r.prompts[0].content) <= 256_100
    assert r.prompts[0].content.endswith("[…truncated]")


def test_first_prompt_truncated_to_200(tmp_path: Path):
    long = "a" * 500
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s7", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": long}},
    ])
    r = parse_file(p)
    assert len(r.session.first_prompt) <= 200
