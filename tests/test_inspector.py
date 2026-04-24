from pathlib import Path
import pytest
from app.inspector import inspect, PREVIEW_LINES


def test_inspect_file_returns_leaf(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    node = inspect(f)
    assert node.kind == "file"
    assert node.children == []


def test_inspect_dir_lists_children(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("y")
    node = inspect(tmp_path)
    names = {c.name for c in node.children}
    assert names == {"a.txt", "sub"}


def test_inspect_depth_2_grandchildren(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "grand.txt").write_text("z")
    node = inspect(tmp_path)
    sub_node = next(c for c in node.children if c.name == "sub")
    assert {g.name for g in sub_node.children} == {"grand.txt"}


def test_preview_readme_file(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Hello\nWorld\n")
    node = inspect(tmp_path)
    readme = next(c for c in node.children if c.name == "README.md")
    assert readme.preview is not None
    assert "Hello" in readme.preview


def test_preview_suppressed_for_sensitive_name(tmp_path: Path):
    (tmp_path / ".env").write_text("SECRET=abc")
    node = inspect(tmp_path)
    env = next(c for c in node.children if c.name == ".env")
    assert env.preview == "(sensitive name — preview suppressed)"


def test_preview_limited_lines(tmp_path: Path):
    (tmp_path / "README.md").write_text("\n".join(str(i) for i in range(50)))
    node = inspect(tmp_path)
    readme = next(c for c in node.children if c.name == "README.md")
    assert readme.preview.count("\n") < PREVIEW_LINES


def test_no_preview_for_non_preview_files(tmp_path: Path):
    (tmp_path / "data.json").write_text("{}")
    node = inspect(tmp_path)
    data = next(c for c in node.children if c.name == "data.json")
    assert data.preview is None


def test_blocked_subtree_names_get_no_descent_or_preview(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "README.md").write_text("secret project notes")
    node = inspect(tmp_path)
    proj_node = next(c for c in node.children if c.name == "projects")
    # still listed as a child (you can see it exists), but no descent + no preview
    assert proj_node.children == []


def test_survives_permission_denied(tmp_path: Path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden").write_text("x")
    locked.chmod(0o000)
    try:
        node = inspect(tmp_path)
        locked_node = next(c for c in node.children if c.name == "locked")
        assert locked_node.children == []  # unreadable, degrades gracefully
    finally:
        locked.chmod(0o755)
