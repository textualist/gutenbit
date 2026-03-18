"""Download Project Gutenberg HTML books."""

from __future__ import annotations

import io
import zipfile
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from gutenbit._http import gutenberg_request_headers

MAIN_SITE_HTML_ZIP_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}-h.zip"
MIRROR_HTML_URL = "https://{host}/cache/epub/{id}/pg{id}-images.html"
GUTENBERG_CANONICAL_HOST = "www.gutenberg.org"
ALEPH_PGLAF_HOST = "aleph.pglaf.org"
GUTENBERG_PGLAF_HOST = "gutenberg.pglaf.org"
HTML_MEMBER_SUFFIXES = (".html", ".htm")
MIRROR_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
MAIN_SITE_ZIP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
_LAST_DOWNLOAD_SOURCE: ContextVar[str | None] = ContextVar("_last_download_source", default=None)


def gutenberg_book_url(book_id: int) -> str:
    """Return the canonical Project Gutenberg HTML URL for a book."""
    return MIRROR_HTML_URL.format(host=GUTENBERG_CANONICAL_HOST, id=book_id)


@dataclass(frozen=True, slots=True)
class _DownloadCandidate:
    source: str
    url: str
    kind: Literal["html", "zip"]


def get_last_download_source() -> str | None:
    return _LAST_DOWNLOAD_SOURCE.get()


def describe_download_source(source: str | None) -> str | None:
    if source in {ALEPH_PGLAF_HOST, GUTENBERG_PGLAF_HOST}:
        return "official mirror"
    if source == GUTENBERG_CANONICAL_HOST:
        return "main site fallback"
    return None


def _mirror_html_url(host: str, book_id: int) -> str:
    return MIRROR_HTML_URL.format(host=host, id=book_id)


def _main_site_html_zip_url(book_id: int) -> str:
    return MAIN_SITE_HTML_ZIP_URL.format(id=book_id)


def _preferred_html_member(names: list[str], *, book_id: int) -> str | None:
    html_names = sorted(name for name in names if name.lower().endswith(HTML_MEMBER_SUFFIXES))
    if not html_names:
        return None

    preferred_basenames = (
        f"pg{book_id}-images.html",
        f"pg{book_id}-images.htm",
        f"pg{book_id}-h.html",
        f"pg{book_id}-h.htm",
    )
    by_basename = {Path(name).name.lower(): name for name in html_names}
    for basename in preferred_basenames:
        preferred = by_basename.get(basename)
        if preferred is not None:
            return preferred

    for name in html_names:
        if "images" in Path(name).name.lower():
            return name

    return html_names[0]


def _request_timeout(candidate: _DownloadCandidate) -> httpx.Timeout:
    if candidate.kind == "html":
        return MIRROR_TIMEOUT
    return MAIN_SITE_ZIP_TIMEOUT


def _fetch_response(candidate: _DownloadCandidate) -> httpx.Response:
    response = httpx.get(
        candidate.url,
        follow_redirects=True,
        headers=gutenberg_request_headers(),
        timeout=_request_timeout(candidate),
    )
    response.raise_for_status()
    return response


def _download_html_page(candidate: _DownloadCandidate) -> str:
    response = _fetch_response(candidate)
    _LAST_DOWNLOAD_SOURCE.set(candidate.source)
    return response.text


def _download_html_zip(candidate: _DownloadCandidate, *, book_id: int) -> str:
    response = _fetch_response(candidate)
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    html_name = _preferred_html_member(archive.namelist(), book_id=book_id)
    if html_name is None:
        raise ValueError(f"No HTML file found in zip for book {book_id}")

    _LAST_DOWNLOAD_SOURCE.set(candidate.source)
    return archive.read(html_name).decode("utf-8-sig")


def _download_candidates(book_id: int) -> list[_DownloadCandidate]:
    return [
        _DownloadCandidate(
            source=ALEPH_PGLAF_HOST,
            url=_mirror_html_url(ALEPH_PGLAF_HOST, book_id),
            kind="html",
        ),
        _DownloadCandidate(
            source=GUTENBERG_PGLAF_HOST,
            url=_mirror_html_url(GUTENBERG_PGLAF_HOST, book_id),
            kind="html",
        ),
        _DownloadCandidate(
            source="www.gutenberg.org",
            url=_main_site_html_zip_url(book_id),
            kind="zip",
        ),
    ]


def download_html(book_id: int) -> str:
    """Download the HTML version of a book from Project Gutenberg.

    The resolver order is:
    1. Generated HTML from aleph.pglaf.org.
    2. Generated HTML from gutenberg.pglaf.org.
    3. Main-site generated HTML zip.
    """
    _LAST_DOWNLOAD_SOURCE.set(None)
    last_error: Exception | None = None

    for candidate in _download_candidates(book_id):
        try:
            if candidate.kind == "html":
                return _download_html_page(candidate)
            return _download_html_zip(candidate, book_id=book_id)
        except (httpx.HTTPError, ValueError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to resolve download candidates for book {book_id}")
