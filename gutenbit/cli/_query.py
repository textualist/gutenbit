"""FTS query utilities, section path helpers, and shared constants."""

from __future__ import annotations

import re
import sqlite3

from gutenbit.db import ChunkRecord, Database

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DOWNLOAD_DELAY = 2.0
DEFAULT_TOC_EXPAND = "2"
DEFAULT_OPENING_CHUNK_COUNT = 3
DEFAULT_VIEW_FORWARD = 1
OPENING_PREVIEW_PARAGRAPH_LIMIT = 4
OPENING_SECTION_SKIP_HEADINGS = frozenset(
    {
        "preface",
        "introduction",
        "foreword",
        "prologue",
        "contents",
        "table of contents",
        "list of illustrations",
        "illustrations",
        "transcriber's note",
        "transcribers note",
        "author's note",
        "authors note",
    }
)

# ---------------------------------------------------------------------------
# Text utility functions
# ---------------------------------------------------------------------------


def _no_chunks_message(db: Database, book_id: int) -> str:
    """Return a descriptive error for a book with no chunks."""
    if db.book(book_id) is None:
        return f"Book {book_id} is not in the database. Use 'gutenbit add {book_id}' to add it."
    return f"No chunks found for book {book_id}."


def _book_id_ref(book_id: int, *, capitalize: bool = True) -> str:
    prefix = "Book ID" if capitalize else "book ID"
    return f"{prefix} {book_id}"


def _no_chunks_display_message(db: Database, book_id: int) -> str:
    """Return the human-facing no-chunks message with an explicit book ID label."""
    if db.book(book_id) is None:
        return (
            f"{_book_id_ref(book_id)} is not in the database. "
            f"Use 'gutenbit add {book_id}' to add it."
        )
    return f"No chunks found for {_book_id_ref(book_id, capitalize=False)}."


# ---------------------------------------------------------------------------
# FTS query utilities
# ---------------------------------------------------------------------------


def _fts_phrase_query(query: str) -> str:
    """Wrap a raw query as an exact FTS5 phrase, escaping inner quotes."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


# FTS5 operator tokens that signal an intentional advanced query.
_FTS_OPERATOR_RE = re.compile(
    r"""
    \bAND\b | \bOR\b | \bNOT\b | \bNEAR\b
    | [*"()\^]
    """,
    re.VERBOSE,
)
_SEARCH_QUERY_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")
_SEARCH_QUERY_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "before",
        "being",
        "call",
        "could",
        "first",
        "from",
        "have",
        "having",
        "however",
        "into",
        "little",
        "never",
        "ought",
        "shall",
        "should",
        "since",
        "some",
        "there",
        "these",
        "those",
        "through",
        "under",
        "until",
        "upon",
        "when",
        "where",
        "which",
        "while",
        "would",
        "years",
    }
)


def _has_fts_operators(query: str) -> bool:
    """Return True if *query* contains FTS5 operator syntax."""
    return bool(_FTS_OPERATOR_RE.search(query))


def _safe_fts_query(query: str) -> str:
    """Escape a plain-text query so punctuation doesn't trigger FTS5 errors.

    Each whitespace-separated token is individually quoted so that
    apostrophes, hyphens, periods, and other punctuation are treated as
    literal characters while FTS5 still performs an implicit-AND across
    tokens.
    """
    tokens = query.split()
    if not tokens:
        return query
    quoted = [_fts_phrase_query(t) for t in tokens]
    return " ".join(quoted)


def _quick_action_search_query(rows: list[ChunkRecord]) -> str:
    """Choose a real in-book token for quick-action search examples."""
    text_rows = [row.content for row in rows if row.kind == "text"]
    for content in text_rows:
        tokens = _SEARCH_QUERY_TOKEN_RE.findall(content)
        for token in tokens:
            if len(token) >= 4 and token.casefold() not in _SEARCH_QUERY_STOPWORDS:
                return token
    for content in text_rows:
        tokens = _SEARCH_QUERY_TOKEN_RE.findall(content)
        if tokens:
            return tokens[0]
    return "chapter"


# ---------------------------------------------------------------------------
# Section path helpers
# ---------------------------------------------------------------------------


def _section_path(*levels: str) -> str:
    return " / ".join(level for level in levels if level) or "(unsectioned opening)"


def _section_path_parts(section: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in section.split(" / ") if part.strip())


def _section_depth(section: str) -> int:
    return len(_section_path_parts(section)) or 1


# ---------------------------------------------------------------------------
# Miscellaneous helpers
# ---------------------------------------------------------------------------


def _format_fts_error(exc: sqlite3.Error) -> str:
    detail = " ".join(str(exc).split()).strip().rstrip(".")
    if not detail:
        return "Invalid FTS query syntax."
    return f"Invalid FTS query syntax: {detail}."


def _toc_expand_depth(expand: str) -> int:
    return 4 if expand == "all" else int(expand)
