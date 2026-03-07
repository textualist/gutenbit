"""SQLite storage and full-text search for Project Gutenberg books."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Literal

from gutenbit.catalog import BookRecord, apply_catalog_policy, is_record_allowed
from gutenbit.download import download_html
from gutenbit.html_chunker import CHUNKER_VERSION, Chunk, chunk_html

logger = logging.getLogger(__name__)

SCHEMA = """\
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    subjects TEXT NOT NULL DEFAULT '',
    locc TEXT NOT NULL DEFAULT '',
    bookshelves TEXT NOT NULL DEFAULT '',
    issued TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'Text'
);

CREATE TABLE IF NOT EXISTS texts (
    book_id INTEGER PRIMARY KEY REFERENCES books(id),
    content TEXT NOT NULL,
    chunker_version INTEGER NOT NULL DEFAULT 1,
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    div1 TEXT NOT NULL DEFAULT '',
    div2 TEXT NOT NULL DEFAULT '',
    div3 TEXT NOT NULL DEFAULT '',
    div4 TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'paragraph',
    char_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(book_id, position)
);

CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id);
"""

_FTS_SETUP = """\
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

_SEARCH_SQL = """\
SELECT
    c.id, c.book_id, c.div1, c.div2, c.div3, c.div4,
    c.position, c.content, c.kind, c.char_count,
    b.title, b.authors, b.language, b.subjects,
    rank
FROM chunks_fts
JOIN chunks c ON c.id = chunks_fts.rowid
JOIN books b ON b.id = c.book_id
WHERE chunks_fts MATCH ?
"""

_DIV_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?]+$")
_DIV_PUNCT_SPACING_RE = re.compile(r"\s*([.,;:!?])\s*")


def _normalize_div_segment(value: str) -> str:
    """Normalize a div path segment for stable matching."""
    cleaned = " ".join(value.split()).strip().casefold()
    cleaned = _DIV_PUNCT_SPACING_RE.sub(r"\1", cleaned)
    return _DIV_TRAILING_PUNCT_RE.sub("", cleaned)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search hit — one chunk with its book metadata."""

    chunk_id: int
    book_id: int
    title: str
    authors: str
    language: str
    subjects: str
    div1: str
    div2: str
    div3: str
    div4: str
    position: int
    content: str
    kind: str
    char_count: int
    score: float


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """One stored chunk with structural metadata."""

    chunk_id: int
    book_id: int
    div1: str
    div2: str
    div3: str
    div4: str
    position: int
    content: str
    kind: str
    char_count: int


class Database:
    """SQLite database for storing and searching Project Gutenberg books."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._ensure_schema_migrations()
        self._conn.executescript(_FTS_SETUP)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, books: list[BookRecord], *, delay: float = 1.0) -> None:
        """Download, chunk, and store books.

        Enforces package ingestion boundaries: English text records only, with
        in-request duplicate work IDs collapsed to a canonical edition.
        """
        allowed_books: list[BookRecord] = []
        for book in books:
            if not is_record_allowed(book):
                logger.info(
                    "Skipping %s (outside ingest policy: English Text catalog only)",
                    book.title,
                )
                continue
            allowed_books.append(book)

        canonical_books, canonical_id_by_id = apply_catalog_policy(allowed_books)
        for book in allowed_books:
            canonical_id = canonical_id_by_id.get(book.id, book.id)
            if canonical_id != book.id:
                logger.info(
                    "Skipping %s (duplicate work; canonical id=%d)",
                    book.title,
                    canonical_id,
                )

        for book in canonical_books:
            if self._has_current_text(book.id):
                logger.info("Skipping %s (already downloaded)", book.title)
                continue
            if self._has_text(book.id):
                logger.info("Reprocessing %s (chunker version updated)", book.title)
            logger.info("Downloading %s (id=%d)", book.title, book.id)
            try:
                html = download_html(book.id)
                chunks = chunk_html(html)
                self._store(book, chunks)
            except Exception:
                logger.exception("Failed to download %s (id=%d)", book.title, book.id)
            time.sleep(delay)

    def delete_book(self, book_id: int) -> bool:
        """Delete a stored book and all associated rows. Returns False if missing."""
        row = self._conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            return False

        with self._conn:
            self._conn.execute("DELETE FROM chunks WHERE book_id = ?", (book_id,))
            self._conn.execute("DELETE FROM texts WHERE book_id = ?", (book_id,))
            self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        return True

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def books(self) -> list[BookRecord]:
        """Return all stored books."""
        rows = self._conn.execute("SELECT * FROM books ORDER BY id").fetchall()
        return [BookRecord(**row) for row in rows]

    def book(self, book_id: int) -> BookRecord | None:
        """Return one stored book by Project Gutenberg id."""
        row = self._conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return BookRecord(**row) if row else None

    def text(self, book_id: int) -> str | None:
        """Return the clean text for a book, or None if not found."""
        row = self._conn.execute(
            "SELECT content FROM texts WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["content"] if row else None

    def has_text(self, book_id: int) -> bool:
        """Return True when a book has already been downloaded and stored."""
        return self._has_text(book_id)

    def chunk_records(
        self,
        book_id: int,
        *,
        kinds: list[str] | None = None,
    ) -> list[ChunkRecord]:
        """Return all chunks for a book as ChunkRecord objects."""
        fields = "id, book_id, div1, div2, div3, div4, position, content, kind, char_count"
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            sql = (
                f"SELECT {fields} FROM chunks"
                f" WHERE book_id = ? AND kind IN ({placeholders})"
                f" ORDER BY position"
            )
            rows = self._conn.execute(sql, [book_id, *kinds]).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {fields} FROM chunks WHERE book_id = ? ORDER BY position",
                (book_id,),
            ).fetchall()
        return [
            ChunkRecord(
                chunk_id=r["id"],
                book_id=r["book_id"],
                div1=r["div1"],
                div2=r["div2"],
                div3=r["div3"],
                div4=r["div4"],
                position=r["position"],
                content=r["content"],
                kind=r["kind"],
                char_count=r["char_count"],
            )
            for r in rows
        ]

    def has_current_text(self, book_id: int) -> bool:
        """Return True when stored text matches the current chunker version."""
        return self._has_current_text(book_id)

    def chunks(
        self,
        book_id: int,
        *,
        kinds: list[str] | None = None,
    ) -> list[tuple[int, str, str, str, str, str, str, int]]:
        """Return chunks as ``(position, div1, div2, div3, div4, content, kind, char_count)``."""
        fields = "position, div1, div2, div3, div4, content, kind, char_count"
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            sql = (
                f"SELECT {fields} FROM chunks"
                f" WHERE book_id = ? AND kind IN ({placeholders})"
                f" ORDER BY position"
            )
            rows = self._conn.execute(sql, [book_id, *kinds]).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {fields} FROM chunks WHERE book_id = ? ORDER BY position",
                (book_id,),
            ).fetchall()
        return [
            (
                r["position"],
                r["div1"],
                r["div2"],
                r["div3"],
                r["div4"],
                r["content"],
                r["kind"],
                r["char_count"],
            )
            for r in rows
        ]

    def chunk_by_id(self, book_id: int, chunk_id: int) -> ChunkRecord | None:
        """Return one chunk by internal row id within a specific book."""
        row = self._conn.execute(
            "SELECT * FROM chunks WHERE book_id = ? AND id = ?",
            (book_id, chunk_id),
        ).fetchone()
        if row is None:
            return None
        return ChunkRecord(
            chunk_id=row["id"],
            book_id=row["book_id"],
            div1=row["div1"],
            div2=row["div2"],
            div3=row["div3"],
            div4=row["div4"],
            position=row["position"],
            content=row["content"],
            kind=row["kind"],
            char_count=row["char_count"],
        )

    def chunk_by_position(self, book_id: int, position: int) -> ChunkRecord | None:
        """Return one chunk by structural position within a specific book."""
        row = self._conn.execute(
            "SELECT * FROM chunks WHERE book_id = ? AND position = ?",
            (book_id, position),
        ).fetchone()
        if row is None:
            return None
        return ChunkRecord(
            chunk_id=row["id"],
            book_id=row["book_id"],
            div1=row["div1"],
            div2=row["div2"],
            div3=row["div3"],
            div4=row["div4"],
            position=row["position"],
            content=row["content"],
            kind=row["kind"],
            char_count=row["char_count"],
        )

    def chunk_window(self, book_id: int, position: int, *, around: int = 0) -> list[ChunkRecord]:
        """Return the selected position and N neighboring chunks on each side."""
        center = self.chunk_by_position(book_id, position)
        if center is None:
            return []
        lo = max(0, center.position - around)
        hi = center.position + around
        rows = self._conn.execute(
            "SELECT * FROM chunks "
            "WHERE book_id = ? AND position BETWEEN ? AND ? "
            "ORDER BY position",
            (book_id, lo, hi),
        ).fetchall()
        return [
            ChunkRecord(
                chunk_id=row["id"],
                book_id=row["book_id"],
                div1=row["div1"],
                div2=row["div2"],
                div3=row["div3"],
                div4=row["div4"],
                position=row["position"],
                content=row["content"],
                kind=row["kind"],
                char_count=row["char_count"],
            )
            for row in rows
        ]

    def chunks_by_div(
        self,
        book_id: int,
        div_path: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 0,
    ) -> list[ChunkRecord]:
        """Return chunks under a division path prefix.

        Matching is exact by segment except that trailing punctuation is ignored.
        """
        parts = [_normalize_div_segment(p) for p in div_path.split("/") if p.strip()]
        if len(parts) > 4:
            raise ValueError("div path has too many segments (max 4: div1/div2/div3/div4)")

        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE book_id = ? ORDER BY position",
            (book_id,),
        ).fetchall()
        out: list[ChunkRecord] = []
        for row in rows:
            if kinds and row["kind"] not in kinds:
                continue
            row_parts = [
                _normalize_div_segment(d)
                for d in [row["div1"], row["div2"], row["div3"], row["div4"]]
                if d
            ]
            if parts and row_parts[: len(parts)] != parts:
                continue
            out.append(
                ChunkRecord(
                    chunk_id=row["id"],
                    book_id=row["book_id"],
                    div1=row["div1"],
                    div2=row["div2"],
                    div3=row["div3"],
                    div4=row["div4"],
                    position=row["position"],
                    content=row["content"],
                    kind=row["kind"],
                    char_count=row["char_count"],
                )
            )
            if limit > 0 and len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        author: str | None = None,
        title: str | None = None,
        language: str | None = None,
        subject: str | None = None,
        book_id: int | None = None,
        kind: str | None = None,
        mode: Literal["ranked", "first", "last"] = "ranked",
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search chunks via FTS5 with BM25 ranking."""
        sql = _SEARCH_SQL
        params: list[object] = [query]

        like_filters = {
            "b.authors": author,
            "b.title": title,
            "b.language": language,
            "b.subjects": subject,
        }
        for column, value in like_filters.items():
            if value is not None:
                sql += f" AND {column} LIKE ?"
                params.append(f"%{value}%")
        if book_id is not None:
            sql += " AND c.book_id = ?"
            params.append(book_id)
        if kind is not None:
            sql += " AND c.kind = ?"
            params.append(kind)

        if mode == "ranked":
            sql += " ORDER BY rank, c.book_id, c.position"
        elif mode == "first":
            sql += " ORDER BY c.book_id, c.position, rank"
        elif mode == "last":
            sql += " ORDER BY c.book_id DESC, c.position DESC, rank"
        else:
            raise ValueError("mode must be one of: ranked, first, last")

        sql += " LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            SearchResult(
                chunk_id=row["id"],
                book_id=row["book_id"],
                title=row["title"],
                authors=row["authors"],
                language=row["language"],
                subjects=row["subjects"],
                div1=row["div1"],
                div2=row["div2"],
                div3=row["div3"],
                div4=row["div4"],
                position=row["position"],
                content=row["content"],
                kind=row["kind"],
                char_count=row["char_count"],
                score=-row["rank"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_schema_migrations(self) -> None:
        """Apply lightweight schema migrations for existing databases."""
        columns = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(texts)").fetchall()
        }
        if "chunker_version" not in columns:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE texts ADD COLUMN chunker_version INTEGER NOT NULL DEFAULT 1"
                )

    def _has_text(self, book_id: int) -> bool:
        row = self._conn.execute("SELECT 1 FROM texts WHERE book_id = ?", (book_id,)).fetchone()
        return row is not None

    def _has_current_text(self, book_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM texts WHERE book_id = ? AND chunker_version = ?",
            (book_id, CHUNKER_VERSION),
        ).fetchone()
        return row is not None

    def _store(self, book: BookRecord, chunks: list[Chunk]) -> None:
        """Store a book and its chunks."""
        text = "\n\n".join(c.content for c in chunks)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO books"
                " (id, title, authors, language, subjects, locc, bookshelves, issued, type)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                astuple(book),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO texts (book_id, content, chunker_version) "
                "VALUES (?, ?, ?)",
                (book.id, text, CHUNKER_VERSION),
            )
            self._conn.execute("DELETE FROM chunks WHERE book_id = ?", (book.id,))
            self._conn.executemany(
                "INSERT INTO chunks"
                " (book_id, div1, div2, div3, div4, position, content, kind, char_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        book.id,
                        c.div1,
                        c.div2,
                        c.div3,
                        c.div4,
                        c.position,
                        c.content,
                        c.kind,
                        len(c.content),
                    )
                    for c in chunks
                ],
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
