from pathlib import Path
import pytest
from app.files import read, write, duplicate_dir, FileSafetyError


@pytest.fixture
def claude(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    root.mkdir()
    (root / "skills").mkdir()
    skill = root / "skills" / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("hello\n")
    (root / "sessions").mkdir()
    (root / "sessions" / "secret.jsonl").write_text("don't read me")
    return root


def test_read_returns_content(claude):
    text = read(claude / "skills" / "demo" / "SKILL.md", claude)
    assert text == "hello\n"


def test_read_refuses_outside_claude(tmp_path, claude):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(FileSafetyError):
        read(outside, claude)


def test_read_refuses_blocklisted_subtree(claude):
    with pytest.raises(FileSafetyError):
        read(claude / "sessions" / "secret.jsonl", claude)


def test_write_in_skills_succeeds(claude):
    target = claude / "skills" / "demo" / "SKILL.md"
    write(target, "updated\n", claude)
    assert target.read_text() == "updated\n"


def test_write_atomic_no_tmp_left(claude):
    target = claude / "skills" / "demo" / "SKILL.md"
    write(target, "x", claude)
    assert not (target.parent / (target.name + ".tmp")).exists()


def test_write_refuses_outside_editable_subdir(claude):
    target = claude / "settings.json"
    with pytest.raises(FileSafetyError):
        write(target, "{}", claude)


def test_write_refuses_sensitive_name(claude):
    target = claude / "skills" / "demo" / ".env"
    with pytest.raises(FileSafetyError):
        write(target, "X=1", claude)


def test_duplicate_makes_v2(claude):
    src = claude / "skills" / "demo"
    new = duplicate_dir(src, claude)
    assert new.name == "demo-v2"
    assert (new / "SKILL.md").read_text() == "hello\n"


def test_duplicate_increments_version(claude):
    src = claude / "skills" / "demo"
    duplicate_dir(src, claude)
    new = duplicate_dir(src, claude)
    assert new.name == "demo-v3"


def test_duplicate_refuses_outside_editable(claude):
    src = claude / "sessions"
    with pytest.raises(FileSafetyError):
        duplicate_dir(src, claude)
