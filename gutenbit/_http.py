"""Shared HTTP helpers for Project Gutenberg access."""

from __future__ import annotations

from gutenbit import __version__

PROJECT_HOMEPAGE_URL = "https://gutenbit.textualist.org"


def gutenberg_request_headers() -> dict[str, str]:
    """Return identifying headers for Gutenberg and PGLAF requests."""
    # Keep Gutenberg/PGLAF requests attributable to the project via a stable URL.
    return {"User-Agent": f"gutenbit/{__version__} (+{PROJECT_HOMEPAGE_URL})"}
