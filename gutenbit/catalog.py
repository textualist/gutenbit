"""Fetch and search the Project Gutenberg CSV catalog."""

from __future__ import annotations

import csv
import gzip
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Literal

import httpx

from gutenbit._cache import (
    cache_age_seconds,
    default_cache_dir,
    read_cache_bytes,
    write_bytes_atomic,
)
from gutenbit._http import gutenberg_request_headers

CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"
_CATALOG_CACHE_TTL_SECONDS = 2 * 60 * 60

# Ingestion boundaries for this package. These are intentionally explicit and
# centrally defined so they're easy to discover and adjust in one place.
CATALOG_ALLOWED_LANGUAGE_CODES = frozenset({"en"})
CATALOG_ALLOWED_MEDIA_TYPES = frozenset({"text"})
CATALOG_DEDUPE_STRATEGY = "lowest_id_per_work"

CatalogDedupeStrategy = Literal["lowest_id_per_work", "none"]

_TOKEN_SPLIT_RE = re.compile(r"[;,/|]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_AUTHOR_ROLE_SUFFIX_RE = re.compile(r"\s*(?:\[[^\]]+\]|\([^)]+\))\s*$")


@dataclass(frozen=True, slots=True)
class BookRecord:
    """A book entry from the Project Gutenberg catalog."""

    id: int
    title: str
    authors: str
    language: str
    subjects: str
    locc: str
    bookshelves: str
    issued: str
    type: str


@dataclass(frozen=True, slots=True)
class CatalogPolicy:
    """Config for catalog boundary enforcement."""

    allowed_language_codes: frozenset[str] = CATALOG_ALLOWED_LANGUAGE_CODES
    allowed_media_types: frozenset[str] = CATALOG_ALLOWED_MEDIA_TYPES
    dedupe_strategy: CatalogDedupeStrategy = CATALOG_DEDUPE_STRATEGY


DEFAULT_CATALOG_POLICY = CatalogPolicy()


@dataclass(frozen=True, slots=True)
class CatalogFetchInfo:
    """How a catalog payload was loaded."""

    source: Literal["cache", "downloaded", "stale_cache"]
    cache_path: Path
    cache_age_seconds: float | None = None


def _policy_cache_key(policy: CatalogPolicy) -> str:
    langs = "-".join(sorted(policy.allowed_language_codes)) or "none"
    media_types = "-".join(sorted(policy.allowed_media_types)) or "none"
    return f"catalog-{langs}-{media_types}-{policy.dedupe_strategy}"


def _catalog_cache_path(policy: CatalogPolicy, cache_dir: str | Path | None = None) -> Path:
    root = default_cache_dir() if cache_dir is None else Path(cache_dir)
    return root / f"{_policy_cache_key(policy)}.csv.gz"


def _is_fresh_catalog_cache(payload_path: Path, *, now: float) -> bool:
    age = cache_age_seconds(payload_path, now=now)
    return age is not None and age < _CATALOG_CACHE_TTL_SECONDS


def _catalog_from_payload(
    payload: bytes, *, policy: CatalogPolicy = DEFAULT_CATALOG_POLICY
) -> Catalog:
    text = gzip.decompress(payload).decode("utf-8")

    records: list[BookRecord] = []
    for row in csv.DictReader(StringIO(text)):
        try:
            book_id = int(row["Text#"])
        except ValueError:
            continue
        records.append(
            BookRecord(
                id=book_id,
                title=row.get("Title", ""),
                authors=row.get("Authors", ""),
                language=row.get("Language", ""),
                subjects=row.get("Subjects", ""),
                locc=row.get("LoCC", ""),
                bookshelves=row.get("Bookshelves", ""),
                issued=row.get("Issued", ""),
                type=row.get("Type", ""),
            )
        )
    bounded_records, canonical_id_by_id = apply_catalog_policy(records, policy=policy)
    return Catalog(bounded_records, canonical_id_by_id=canonical_id_by_id)


def _normalized_tokens(raw: str) -> set[str]:
    return {tok.strip().lower() for tok in _TOKEN_SPLIT_RE.split(raw) if tok.strip()}


def _normalize_work_text(raw: str) -> str:
    collapsed = " ".join(raw.lower().split())
    return _NON_ALNUM_RE.sub(" ", collapsed).strip()


def _primary_author_text(raw: str) -> str:
    """Return the first listed author, stripped of editorial role suffixes."""
    primary = raw.split(";", 1)[0].strip()
    while primary:
        cleaned = _AUTHOR_ROLE_SUFFIX_RE.sub("", primary).strip()
        if cleaned == primary:
            break
        primary = cleaned
    return primary


def is_record_allowed(
    record: BookRecord, *, policy: CatalogPolicy = DEFAULT_CATALOG_POLICY
) -> bool:
    """Return True when a record is within the catalog policy."""
    media_type = record.type.strip().lower()
    if media_type not in policy.allowed_media_types:
        return False
    language_codes = _normalized_tokens(record.language)
    if not language_codes:
        return False
    return bool(language_codes & policy.allowed_language_codes)


def work_key(record: BookRecord) -> tuple[str, str] | None:
    """Return a conservative key for canonical duplicate detection."""
    title_key = _normalize_work_text(record.title)
    author_key = _normalize_work_text(_primary_author_text(record.authors))
    if not title_key or not author_key:
        return None
    return title_key, author_key


def apply_catalog_policy(
    records: Iterable[BookRecord], *, policy: CatalogPolicy = DEFAULT_CATALOG_POLICY
) -> tuple[list[BookRecord], dict[int, int]]:
    """Filter and canonicalize records according to catalog policy.

    Returns a tuple of:
    1. Canonical records after boundaries are applied.
    2. Mapping from original record id -> canonical record id.
    """
    allowed = sorted(
        (record for record in records if is_record_allowed(record, policy=policy)),
        key=lambda record: record.id,
    )

    if policy.dedupe_strategy == "none":
        unique_by_id: dict[int, BookRecord] = {}
        for record in allowed:
            unique_by_id.setdefault(record.id, record)
        canonical = [unique_by_id[book_id] for book_id in sorted(unique_by_id)]
        return canonical, {book_id: book_id for book_id in unique_by_id}

    if policy.dedupe_strategy != "lowest_id_per_work":
        raise ValueError("dedupe_strategy must be one of: 'lowest_id_per_work', 'none'")

    canonical_by_work: dict[tuple[str, str], BookRecord] = {}
    canonical_id_by_id: dict[int, int] = {}
    canonical_records_by_id: dict[int, BookRecord] = {}

    for record in allowed:
        key = work_key(record)
        if key is None:
            canonical_id_by_id[record.id] = record.id
            canonical_records_by_id.setdefault(record.id, record)
            continue

        canonical = canonical_by_work.get(key)
        if canonical is None:
            canonical_by_work[key] = record
            canonical = record

        canonical_id_by_id[record.id] = canonical.id
        canonical_records_by_id.setdefault(canonical.id, canonical)

    canonical = [canonical_records_by_id[book_id] for book_id in sorted(canonical_records_by_id)]
    return canonical, canonical_id_by_id


class Catalog:
    """The Project Gutenberg catalog, searchable in memory."""

    def __init__(
        self,
        records: list[BookRecord],
        *,
        canonical_id_by_id: dict[int, int] | None = None,
        fetch_info: CatalogFetchInfo | None = None,
    ) -> None:
        self.records = sorted(records, key=lambda record: record.id)
        self._by_id = {record.id: record for record in self.records}
        if canonical_id_by_id is None:
            canonical_id_by_id = {record.id: record.id for record in self.records}
        self._canonical_id_by_id = dict(canonical_id_by_id)
        self.fetch_info = fetch_info

    @classmethod
    def fetch(
        cls,
        *,
        policy: CatalogPolicy = DEFAULT_CATALOG_POLICY,
        cache_dir: str | Path | None = None,
        refresh: bool = False,
    ) -> Catalog:
        """Download the CSV catalog from Project Gutenberg."""
        payload_path = _catalog_cache_path(policy, cache_dir)
        cached = read_cache_bytes(payload_path)
        now = time.time()
        cached_age = cache_age_seconds(payload_path, now=now)

        if cached is not None and not refresh and _is_fresh_catalog_cache(payload_path, now=now):
            try:
                catalog = _catalog_from_payload(cached, policy=policy)
            except (OSError, ValueError):
                cached = None
            else:
                catalog.fetch_info = CatalogFetchInfo(
                    source="cache",
                    cache_path=payload_path,
                    cache_age_seconds=cached_age,
                )
                return catalog

        try:
            response = httpx.get(
                CATALOG_URL,
                follow_redirects=True,
                headers=gutenberg_request_headers(),
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            if cached is not None and not refresh:
                catalog = _catalog_from_payload(cached, policy=policy)
                catalog.fetch_info = CatalogFetchInfo(
                    source="stale_cache",
                    cache_path=payload_path,
                    cache_age_seconds=cached_age,
                )
                return catalog
            raise

        payload = response.content
        try:
            write_bytes_atomic(payload_path, payload)
        except OSError:
            pass
        catalog = _catalog_from_payload(payload, policy=policy)
        catalog.fetch_info = CatalogFetchInfo(
            source="downloaded",
            cache_path=payload_path,
            cache_age_seconds=None,
        )
        return catalog

    def canonical_id(self, book_id: int) -> int | None:
        """Resolve any known id to the canonical id under current policy."""
        return self._canonical_id_by_id.get(book_id)

    def get(self, book_id: int) -> BookRecord | None:
        """Return a canonical book record for a requested id."""
        canonical_id = self.canonical_id(book_id)
        if canonical_id is None:
            return None
        return self._by_id.get(canonical_id)

    def is_canonical_id(self, book_id: int) -> bool:
        """Return True when an id is already canonical under current policy."""
        canonical_id = self.canonical_id(book_id)
        if canonical_id is None:
            return False
        return canonical_id == book_id

    def search(
        self,
        *,
        author: str = "",
        title: str = "",
        language: str = "",
        subject: str = "",
    ) -> list[BookRecord]:
        """Search for books matching all given criteria.

        All filters use case-insensitive matching. Each query is first tried as
        a contiguous substring; if it contains multiple words and the substring
        fails, every word must appear individually (so ``"Jane Austen"`` matches
        ``"Austen, Jane, 1775-1817"``).
        """
        results = self.records
        filters = {
            "authors": author,
            "title": title,
            "language": language,
            "subjects": subject,
        }
        for field, value in filters.items():
            if value:
                q = value.lower()
                words = q.split()
                results = [
                    b
                    for b in results
                    if q in getattr(b, field).lower()
                    or (len(words) > 1 and all(w in getattr(b, field).lower() for w in words))
                ]
        return results
