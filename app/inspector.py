"""Live filesystem inspector for the entry detail page.

Walks two levels deep, reads small previews of README-like files only, respects a
secret-sensitive blocklist. Never reads file contents from sensitive subtrees.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_CHILDREN = 50
MAX_GRANDCHILDREN = 50
PREVIEW_LINES = 15

PREVIEW_NAMES = frozenset({
    "SKILL.md", "SKILL.yml", "SKILL.yaml",
    "AGENTS.md", "CLAUDE.md",
    "README", "README.md", "README.txt", "README.rst",
    "manifest.json", "mcp.json", "plugin.json", "pyproject.toml",
})

BLOCKLIST_NAME_SUBSTRINGS = ("secret", "credential", "password", "token")
BLOCKLIST_EXTENSIONS = (".key", ".pem", ".p12", ".pfx")
BLOCKLIST_EXACT = frozenset({"history.jsonl", ".env", ".env.local", ".env.production"})
BLOCKLIST_SUBTREES = ("projects", "sessions", "session-env", "paste-cache")


@dataclass
class FileNode:
    name: str
    rel_path: str
    kind: str            # "file" | "dir"
    size_bytes: int
    mtime_iso: str
    preview: str | None = None
    error: str | None = None
    children: list["FileNode"] = field(default_factory=list)


def _is_sensitive_name(name: str) -> bool:
    low = name.lower()
    if low in BLOCKLIST_EXACT:
        return True
    if any(sub in low for sub in BLOCKLIST_NAME_SUBSTRINGS):
        return True
    if low.startswith(".env"):
        return True
    if any(low.endswith(ext) for ext in BLOCKLIST_EXTENSIONS):
        return True
    return False


def _is_in_blocked_subtree(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return any(part in BLOCKLIST_SUBTREES for part in parts)


def _read_preview(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            lines = []
            for _ in range(PREVIEW_LINES):
                line = fp.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
        return "\n".join(lines)
    except OSError as e:
        return f"(read failed: {e})"


def _node(path: Path, rel_path: str) -> FileNode:
    try:
        st = path.lstat()
    except OSError as e:
        return FileNode(
            name=path.name, rel_path=rel_path, kind="file",
            size_bytes=0, mtime_iso="", error=f"stat failed: {e}",
        )
    is_dir = path.is_dir() and not path.is_symlink()
    return FileNode(
        name=path.name,
        rel_path=rel_path,
        kind="dir" if is_dir else "file",
        size_bytes=st.st_size if not is_dir else 0,
        mtime_iso=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    )


def _maybe_preview(node: FileNode, path: Path) -> None:
    if node.kind != "file":
        return
    if _is_sensitive_name(node.name):
        node.preview = "(sensitive name — preview suppressed)"
        return
    if _is_in_blocked_subtree(node.rel_path):
        node.preview = "(in blocked subtree — preview suppressed)"
        return
    if node.name in PREVIEW_NAMES:
        node.preview = _read_preview(path)


def _list(path: Path, limit: int) -> list[Path]:
    try:
        return sorted(path.iterdir())[:limit]
    except (PermissionError, FileNotFoundError):
        return []


def inspect(root: Path) -> FileNode:
    """Walk `root` two levels deep and return a tree of FileNodes."""
    root = root.resolve()
    top = _node(root, "")
    if top.kind != "dir" or _is_in_blocked_subtree(root.name):
        return top

    for child_path in _list(root, MAX_CHILDREN):
        rel = child_path.name
        child = _node(child_path, rel)
        _maybe_preview(child, child_path)

        if child.kind == "dir" and not _is_in_blocked_subtree(rel):
            for grand_path in _list(child_path, MAX_GRANDCHILDREN):
                grand_rel = f"{rel}/{grand_path.name}"
                grand = _node(grand_path, grand_rel)
                _maybe_preview(grand, grand_path)
                child.children.append(grand)

        top.children.append(child)

    return top
