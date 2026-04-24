"""File read/write/duplicate ops with explicit safety gates.

Read   — any text file under `claude_root`, except blocklisted (sensitive) files.
Write  — only files whose realpath sits under one of EDITABLE_ROOTS, never blocklisted.
Dup    — only directories under EDITABLE_ROOTS, atomic-ish via shutil.copytree.

Zen: explicit > implicit. Every public function calls `_assert_*` first.
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path

from app.inspector import (
    BLOCKLIST_EXACT,
    BLOCKLIST_EXTENSIONS,
    BLOCKLIST_NAME_SUBSTRINGS,
    BLOCKLIST_SUBTREES,
)

EDITABLE_SUBDIRS = ("skills", "commands", "agents", "notes", "scripts")
MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB cap


class FileSafetyError(ValueError):
    pass


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _assert_inside(path: Path, root: Path) -> Path:
    real = _real(path)
    try:
        real.relative_to(_real(root))
    except ValueError as e:
        raise FileSafetyError(f"{real} is outside {root}") from e
    return real


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in BLOCKLIST_EXACT or name.startswith(".env"):
        return True
    if any(sub in name for sub in BLOCKLIST_NAME_SUBSTRINGS):
        return True
    if any(name.endswith(ext) for ext in BLOCKLIST_EXTENSIONS):
        return True
    parts = {p.lower() for p in path.parts}
    if parts & set(BLOCKLIST_SUBTREES):
        return True
    return False


def _assert_editable(path: Path, claude_root: Path) -> Path:
    real = _assert_inside(path, claude_root)
    parts = real.parts
    croot_parts = _real(claude_root).parts
    rel_parts = parts[len(croot_parts):]
    if not rel_parts or rel_parts[0] not in EDITABLE_SUBDIRS:
        raise FileSafetyError(
            f"{real} is not under an editable subdir ({', '.join(EDITABLE_SUBDIRS)})"
        )
    if _is_sensitive(real):
        raise FileSafetyError(f"{real} is on the sensitive blocklist")
    return real


def is_editable(path: Path, claude_root: Path) -> bool:
    try:
        _assert_editable(path, claude_root)
        return True
    except FileSafetyError:
        return False


def read(path: Path, claude_root: Path) -> str:
    real = _assert_inside(path, claude_root)
    if _is_sensitive(real):
        raise FileSafetyError(f"{real} is on the sensitive blocklist")
    if not real.is_file():
        raise FileSafetyError(f"{real} is not a regular file")
    if real.stat().st_size > MAX_READ_BYTES:
        raise FileSafetyError(f"{real} exceeds {MAX_READ_BYTES} byte cap")
    return real.read_text(encoding="utf-8", errors="replace")


def write(path: Path, content: str, claude_root: Path) -> Path:
    real = _assert_editable(path, claude_root)
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_suffix(real.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(real)
    return real


def duplicate_dir(src: Path, claude_root: Path) -> Path:
    src_real = _assert_editable(src, claude_root)
    if not src_real.is_dir():
        raise FileSafetyError(f"{src_real} is not a directory")

    parent = src_real.parent
    base = src_real.name
    for n in range(2, 100):
        candidate = parent / f"{base}-v{n}"
        if not candidate.exists():
            shutil.copytree(src_real, candidate, symlinks=False)
            return candidate
    raise FileSafetyError("could not find a free -vN suffix (tried 2-99)")
