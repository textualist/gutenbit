"""Shared cache helpers for Project Gutenberg data."""

from __future__ import annotations

import tempfile
from pathlib import Path


def default_cache_dir() -> Path:
    """Return the default user cache directory for gutenbit."""
    return Path.home() / ".gutenbit" / "cache"


def read_cache_bytes(path: Path) -> bytes | None:
    """Read a cache payload, treating empty or unreadable files as missing."""
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if not payload:
        return None
    return payload


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Atomically replace a cache payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def cache_age_seconds(path: Path, *, now: float) -> float | None:
    """Return cache age in seconds, or None when the file is unavailable."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, now - mtime)
