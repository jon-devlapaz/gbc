# app/scanner.py
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from app.models import Entry, EntryKind

SKIP_SAMPLE_SUFFIXES = (".lock",)
SKIP_SAMPLE_NAMES = frozenset({".DS_Store"})


def walk(root: Path) -> list[Entry]:
    """Yield one Entry per top-level item under root. Never raise."""
    root = root.resolve()
    out: list[Entry] = []
    try:
        children = sorted(root.iterdir())
    except PermissionError:
        return out

    for child in children:
        try:
            entry = _describe(child, root)
        except (PermissionError, FileNotFoundError):
            continue
        if entry is not None:
            out.append(entry)
    return out


def _describe(path: Path, root: Path) -> Entry | None:
    st = path.lstat()
    is_dir = path.is_dir() and not path.is_symlink()
    kind = EntryKind.DIR if is_dir else EntryKind.FILE

    if is_dir:
        size, count, samples = _summarize_dir(path, root)
    else:
        size = st.st_size
        count = 1
        samples = []

    return Entry(
        path=str(path),
        kind=kind,
        inode=st.st_ino,
        size_bytes=size,
        mtime=datetime.fromtimestamp(st.st_mtime),
        file_count=count,
        sample_files=samples,
    )


def _summarize_dir(path: Path, root: Path) -> tuple[int, int, list[str]]:
    total_size = 0
    total_count = 0
    samples: list[str] = []
    visited: set[int] = set()

    for dirpath, dirnames, filenames in os.walk(path, followlinks=False, onerror=lambda _e: None):
        dp = Path(dirpath).resolve()
        try:
            dp.relative_to(root)
        except ValueError:
            dirnames.clear()
            continue
        try:
            st = dp.stat()
        except (PermissionError, FileNotFoundError):
            dirnames.clear()
            continue
        if st.st_ino in visited:
            dirnames.clear()
            continue
        visited.add(st.st_ino)

        for fn in filenames:
            total_count += 1
            try:
                total_size += (Path(dirpath) / fn).stat().st_size
            except (FileNotFoundError, PermissionError):
                pass
            if (
                len(samples) < 5
                and fn not in SKIP_SAMPLE_NAMES
                and not fn.endswith(SKIP_SAMPLE_SUFFIXES)
            ):
                samples.append(fn)
    return total_size, total_count, samples
