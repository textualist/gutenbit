"""Download and clean Project Gutenberg texts."""

from __future__ import annotations

import io
import re
import zipfile

import httpx

TEXT_URL = "https://www.gutenberg.org/ebooks/{id}.txt.utf-8"
HTML_ZIP_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}-h.zip"

# These patterns match only the exact standard Gutenberg delimiters:
#   *** START OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
#   *** END OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
# Some older texts use "THIS" instead of "THE".
_START_MARKER_RE = re.compile(
    r"^\*{3}\s+START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b.*\*{3}\s*$",
    re.IGNORECASE,
)
_END_MARKER_RE = re.compile(
    r"^\*{3}\s+END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b.*\*{3}\s*$",
    re.IGNORECASE,
)


def download_text(book_id: int) -> str:
    """Download raw text for a book from Project Gutenberg."""
    url = TEXT_URL.format(id=book_id)
    response = httpx.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return response.text


def download_html(book_id: int) -> str:
    """Download the HTML version of a book from Project Gutenberg.

    Downloads the epub HTML zip, extracts the single HTML file, and returns
    its content as a string.
    """
    url = HTML_ZIP_URL.format(id=book_id)
    response = httpx.get(url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(response.content))
    html_names = [n for n in z.namelist() if n.endswith(".html")]
    if not html_names:
        raise ValueError(f"No HTML file found in zip for book {book_id}")
    return z.read(html_names[0]).decode("utf-8")


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
