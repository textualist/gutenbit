"""Shared CLI utility functions, constants, types, and formatting helpers."""

from __future__ import annotations

import functools
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import click

from gutenbit._text_utils import (
    _format_int,
    _indent_block,
    _preview,
    _single_line,
    _split_semicolon_list,
    _summarize_semicolon_list,
)
from gutenbit.catalog import Catalog, CatalogFetchInfo
from gutenbit.db import ChunkRecord, Database
from gutenbit.display import CliDisplay

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_DIR_NAME = ".gutenbit"
DEFAULT_DB_NAME = "gutenbit.db"
DEFAULT_DB = f"~/{STATE_DIR_NAME}/{DEFAULT_DB_NAME}"
DEFAULT_DOWNLOAD_DELAY = 2.0
DEFAULT_TOC_EXPAND = "2"
JSON_OPENING_LINE_PREVIEW_CHARS = 140
DEFAULT_OPENING_CHUNK_COUNT = 3
DEFAULT_VIEW_FORWARD = 1
JSON_BOOK_ID_KEY = "book_id"
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
# Click infrastructure
# ---------------------------------------------------------------------------

_CONTEXT_SETTINGS: dict[str, Any] = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
}

_DB_HELP = "SQLite database path (default: ~/.gutenbit/gutenbit.db)"
_DB_OVERRIDE_HELP = "SQLite database path (works before or after the subcommand)"
_VERBOSE_HELP = "enable debug logging"

# ---------------------------------------------------------------------------
# Display cache and logging
# ---------------------------------------------------------------------------

_DISPLAY_CACHE: tuple[int, int, CliDisplay] | None = None


def _configure_logging(verbose: bool) -> None:
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stdout,
        )
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _display() -> CliDisplay:
    global _DISPLAY_CACHE
    stdout = sys.stdout
    stderr = sys.stderr
    cache_key = (id(stdout), id(stderr))
    if _DISPLAY_CACHE is None or _DISPLAY_CACHE[:2] != cache_key:
        _DISPLAY_CACHE = (*cache_key, CliDisplay(stdout=stdout, stderr=stderr))
    return _DISPLAY_CACHE[2]


# ---------------------------------------------------------------------------
# Common command options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CommandEnv:
    """Resolved common options injected into every command."""

    db_path: str
    as_json: bool
    display: CliDisplay


def _common_options(fn: Any) -> Any:
    """Decorator that adds --json, --db, --verbose options and resolves them.

    Replaces the ``ctx``, ``json_output``, ``db``, ``verbose`` parameters with
    a single ``env: _CommandEnv`` first argument containing the resolved values.
    """

    @click.option("--json", "json_output", is_flag=True, help="output as JSON")
    @click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
    @click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
    @click.pass_context
    @functools.wraps(fn)
    def wrapper(
        ctx: click.Context,
        json_output: bool,
        db: str | None,
        verbose: bool,
        **kwargs: Any,
    ) -> Any:
        effective_db = _resolve_db(ctx, db)
        if _resolve_verbose(ctx, verbose):
            _configure_logging(True)
        env = _CommandEnv(db_path=effective_db, as_json=json_output, display=_display())
        return fn(env, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Path management
# ---------------------------------------------------------------------------


def _cli_state_dir() -> Path:
    return Path.home() / STATE_DIR_NAME


def _resolved_cli_path(path: str | Path) -> Path:
    """Resolve a CLI path the same way Database() will interpret it."""
    return Path(path).expanduser().resolve()


def _collapse_home_path(path: Path) -> str:
    """Render paths under the home directory with a leading tilde."""
    home = Path.home()
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    return str(Path("~") / relative) if relative.parts else "~"


def _display_cli_path(path: str | Path) -> str:
    """Render a user-facing path without turning ``~/...`` into ``<cwd>/~/...``."""
    raw = str(path)
    if raw.startswith("~"):
        return _collapse_home_path(_resolved_cli_path(path))
    return raw


def _catalog_cache_dir() -> Path:
    return _cli_state_dir() / "cache"


def _catalog_status_message(fetch_info: CatalogFetchInfo | None, *, refresh: bool) -> str:
    corpus = "English text corpus"
    if fetch_info is None:
        return f"Loading catalog ({corpus})."
    if fetch_info.source == "cache":
        return f"Using cached catalog ({corpus})."
    if fetch_info.source == "stale_cache":
        return f"Catalog download failed; using stale cached catalog ({corpus})."
    if refresh:
        return f"Refreshed catalog from Project Gutenberg ({corpus})."
    return f"Downloaded catalog from Project Gutenberg ({corpus})."


# ---------------------------------------------------------------------------
# Catalog and text normalization
# ---------------------------------------------------------------------------


def _load_catalog(refresh: bool = False, *, display: CliDisplay, as_json: bool) -> Catalog:
    catalog = Catalog.fetch(
        cache_dir=_catalog_cache_dir(),
        refresh=refresh,
    )
    if not as_json:
        display.status(
            _catalog_status_message(
                catalog.fetch_info,
                refresh=refresh,
            )
        )
    return catalog


def _normalize_apostrophes(s: str) -> str:
    """Replace curly/typographic apostrophes with ASCII for matching."""
    return s.replace("\u2019", "'").replace("\u2018", "'")


# ---------------------------------------------------------------------------
# TypedDict definitions
# ---------------------------------------------------------------------------


class _SectionState(TypedDict):
    heading: str
    path: str
    position: int
    paragraphs: int
    chars: int
    first_position: int
    opening_candidates: list[str]


class _BookSummary(TypedDict):
    id: int
    title: str
    authors: str
    language: str
    issued: str
    type: str
    locc: str
    subjects: list[str]
    bookshelves: list[str]


class _ChunkCounts(TypedDict):
    heading: int
    text: int


class _OverviewSummary(TypedDict):
    chunks_total: int
    chunk_counts: _ChunkCounts
    sections_total: int
    sections_shown: int
    levels_total: int
    levels_shown: int
    paragraphs_total: int
    chars_total: int
    est_words: int
    est_read_time: str


class _SectionRow(TypedDict):
    section_number: int
    section: str
    position: int
    paras: int
    chars: int
    est_words: int
    est_read: str
    opening_line: str


class _QuickActions(TypedDict):
    toc_expand_all: str
    search: str
    view_first_section: str
    view_by_position: str
    view_all: str


class _SectionSummary(TypedDict):
    book: _BookSummary
    overview: _OverviewSummary
    sections: list[_SectionRow]
    quick_actions: _QuickActions


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def _json_envelope(
    command: str,
    *,
    ok: bool,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "data": data,
        "warnings": warnings or [],
        "errors": errors or [],
    }


def _print_json_envelope(
    command: str,
    *,
    ok: bool,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    print(
        json.dumps(
            _json_envelope(command, ok=ok, data=data, warnings=warnings, errors=errors),
            indent=2,
        )
    )


def _command_error(
    command: str,
    message: str,
    *,
    as_json: bool,
    display_message: str | None = None,
    code: int = 1,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
) -> int:
    if as_json:
        _print_json_envelope(command, ok=False, data=data, warnings=warnings, errors=[message])
    else:
        _display().error(display_message or message)
    return code


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
# Formatting and display helpers
# ---------------------------------------------------------------------------


def _json_search_filters(
    *,
    author: str | None,
    title: str | None,
    book_id: int | None,
    kind: str,
    section: str | None,
) -> dict[str, Any]:
    return {
        "author": author,
        "title": title,
        JSON_BOOK_ID_KEY: book_id,
        "kind": kind,
        "section": section,
    }


def _section_path(*levels: str) -> str:
    return " / ".join(level for level in levels if level) or "(unsectioned opening)"


def _section_path_parts(section: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in section.split(" / ") if part.strip())


def _section_depth(section: str) -> int:
    return len(_section_path_parts(section)) or 1


def _book_payload(book: Any) -> dict[str, Any]:
    return {
        "id": book.id,
        "title": _single_line(book.title),
        "authors": _single_line(book.authors),
        "language": _single_line(book.language),
        "subjects": _single_line(book.subjects),
        "locc": _single_line(book.locc),
        "bookshelves": _single_line(book.bookshelves),
        "issued": _single_line(book.issued),
        "type": _single_line(book.type),
    }


def _joined_chunk_text(
    rows: list[ChunkRecord],
) -> str:
    return "\n\n".join(row.content for row in rows)


def _passage_payload(
    *,
    book_id: int,
    title: str,
    author: str,
    section: str | None,
    section_number: int | None,
    position: int | None,
    forward: int | None,
    radius: int | None,
    all_scope: bool | None = None,
    content: str,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        JSON_BOOK_ID_KEY: book_id,
        "title": title,
        "author": author,
        "section": section,
        "section_number": section_number,
        "position": position,
        "forward": forward,
        "radius": radius,
        "all": all_scope,
        "content": content,
    }
    if extras:
        payload.update(extras)
    return payload


# ---------------------------------------------------------------------------
# CLI context resolvers
# ---------------------------------------------------------------------------


def _resolve_db(ctx: click.Context, db: str | None) -> str:
    """Return effective db path: subcommand override takes precedence over group default."""
    if db is not None:
        return db
    return ctx.obj.get("db", DEFAULT_DB)


def _resolve_verbose(ctx: click.Context, verbose: bool) -> bool:
    """Return effective verbose flag: either source activates it."""
    return verbose or ctx.obj.get("verbose", False)


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
