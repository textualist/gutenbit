"""Download Project Gutenberg HTML books."""

from __future__ import annotations

import io
import zipfile

import httpx

HTML_ZIP_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}-h.zip"


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
