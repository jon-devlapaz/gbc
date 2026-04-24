# tests/test_scanner.py
from pathlib import Path
from app.scanner import walk
from app.models import EntryKind


def test_walk_returns_top_level_entries(fake_claude: Path):
    entries = walk(fake_claude)
    names = {Path(e.path).name for e in entries}
    assert "sessions" in names
    assert "paste-cache" in names
    assert "history.jsonl" in names
    assert "settings.json" in names


def test_walk_marks_file_kind(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    assert entries["history.jsonl"].kind == EntryKind.FILE
    assert entries["sessions"].kind == EntryKind.DIR


def test_walk_counts_dir_children(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    assert entries["paste-cache"].file_count == 3


def test_walk_samples_filenames(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    samples = entries["paste-cache"].sample_files
    assert len(samples) <= 5
    assert all(s.startswith("p") for s in samples)


def test_walk_survives_permission_denied(fake_claude: Path):
    entries = {Path(e.path).name: e for e in walk(fake_claude)}
    assert "locked" in entries
    assert entries["locked"].file_count == 0
    assert entries["locked"].size_bytes == 0


def test_walk_does_not_follow_outbound_symlinks(fake_claude: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (fake_claude / "bad_link").symlink_to(outside)
    paths = {e.path for e in walk(fake_claude)}
    assert str(outside) not in paths


def test_walk_captures_inode(fake_claude: Path):
    entries = walk(fake_claude)
    assert all(e.inode > 0 for e in entries)
