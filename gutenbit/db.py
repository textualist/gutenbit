"""SQLite storage and full-text search for Project Gutenberg books."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from gutenbit.catalog import BookRecord
from gutenbit.chunker import chunk_text
from gutenbit.download import download_text, strip_headers

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
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(book_id, position)
);

CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id);
"""

# FTS5 and its sync triggers are created separately because virtual tables
# don't support IF NOT EXISTS in all SQLite builds.
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
    c.id, c.book_id, c.chapter, c.position, c.content,
    b.title, b.authors, b.language, b.subjects,
    rank
FROM chunks_fts
JOIN chunks c ON c.id = chunks_fts.rowid
JOIN books b ON b.id = c.book_id
WHERE chunks_fts MATCH ?
"""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search hit — one paragraph with its book metadata."""

    chunk_id: int
    book_id: int
    title: str
    authors: str
    language: str
    subjects: str
    chapter: str
    position: int
    content: str
    score: float


class Database:
    """SQLite database for storing Project Gutenberg books and their texts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.executescript(_FTS_SETUP)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, books: list[BookRecord], *, delay: float = 1.0) -> None:
        """Download, clean, chunk, and store books. Skips already-downloaded books."""
        for book in books:
            if self._has_text(book.id):
                logger.info("Skipping %s (already downloaded)", book.title)
                continue

            logger.info("Downloading %s (id=%d)", book.title, book.id)
            try:
                raw = download_text(book.id)
                clean = strip_headers(raw)
                self._store(book, clean)
            except Exception:
                logger.exception("Failed to download %s (id=%d)", book.title, book.id)

            time.sleep(delay)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def books(self) -> list[BookRecord]:
        """Return all stored books as BookRecords."""
        rows = self._conn.execute("SELECT * FROM books ORDER BY id").fetchall()
        return [
            BookRecord(
                id=row["id"],
                title=row["title"],
                authors=row["authors"],
                language=row["language"],
                subjects=row["subjects"],
                locc=row["locc"],
                bookshelves=row["bookshelves"],
                issued=row["issued"],
                type=row["type"],
            )
            for row in rows
        ]

    def text(self, book_id: int) -> str | None:
        """Return the clean text for a book, or None if not found."""
        row = self._conn.execute(
            "SELECT content FROM texts WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["content"] if row else None

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
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search chunks via FTS5 with BM25 ranking.

        *query* is an FTS5 match expression (e.g. ``"moby dick"`` or
        ``whale OR sea``).  Optional keyword filters narrow results by book
        metadata using case-insensitive substring matching.
        """
        sql = _SEARCH_SQL
        params: list[object] = [query]

        if author is not None:
            sql += " AND b.authors LIKE ?"
            params.append(f"%{author}%")
        if title is not None:
            sql += " AND b.title LIKE ?"
            params.append(f"%{title}%")
        if language is not None:
            sql += " AND b.language LIKE ?"
            params.append(f"%{language}%")
        if subject is not None:
            sql += " AND b.subjects LIKE ?"
            params.append(f"%{subject}%")
        if book_id is not None:
            sql += " AND c.book_id = ?"
            params.append(book_id)

        sql += " ORDER BY rank LIMIT ?"
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
                chapter=row["chapter"],
                position=row["position"],
                content=row["content"],
                score=-row["rank"],  # FTS5 rank is negative; negate for intuitive scoring
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _has_text(self, book_id: int) -> bool:
        row = self._conn.execute("SELECT 1 FROM texts WHERE book_id = ?", (book_id,)).fetchone()
        return row is not None

    def _store(self, book: BookRecord, text: str) -> None:
        chunks = chunk_text(text)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO books"
                " (id, title, authors, language, subjects, locc, bookshelves, issued, type)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    book.id,
                    book.title,
                    book.authors,
                    book.language,
                    book.subjects,
                    book.locc,
                    book.bookshelves,
                    book.issued,
                    book.type,
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO texts (book_id, content) VALUES (?, ?)",
                (book.id, text),
            )
            # Clear any existing chunks for this book before re-inserting.
            self._conn.execute("DELETE FROM chunks WHERE book_id = ?", (book.id,))
            self._conn.executemany(
                "INSERT INTO chunks (book_id, chapter, position, content) VALUES (?, ?, ?, ?)",
                [(book.id, c.chapter, c.position, c.content) for c in chunks],
            )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
