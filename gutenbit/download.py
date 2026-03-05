"""Download and clean Project Gutenberg texts."""

from __future__ import annotations

import re

import httpx

TEXT_URL = "https://www.gutenberg.org/ebooks/{id}.txt.utf-8"

# These patterns match only the exact standard Gutenberg delimiters:
#   *** START OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
#   *** END OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
_START_MARKER_RE = re.compile(
    r"^\*{3}\s+START OF THE PROJECT GUTENBERG EBOOK\b.*\*{3}\s*$",
    re.IGNORECASE,
)
_END_MARKER_RE = re.compile(
    r"^\*{3}\s+END OF THE PROJECT GUTENBERG EBOOK\b.*\*{3}\s*$",
    re.IGNORECASE,
)


def download_text(book_id: int) -> str:
    """Download raw text for a book from Project Gutenberg."""
    url = TEXT_URL.format(id=book_id)
    response = httpx.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return response.text


def strip_headers(text: str) -> str:
    """Remove Project Gutenberg headers and footers, returning only the book content."""
    lines = text.splitlines()
    start: int | None = None
    end = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if _START_MARKER_RE.match(stripped):
                start = i + 1
        else:
            if _END_MARKER_RE.match(stripped):
                end = i
                break

    if start is None:
        return text.strip()

    return "\n".join(lines[start:end]).strip()
