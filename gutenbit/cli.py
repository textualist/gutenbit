"""Command-line interface for gutenbit."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, TypedDict, cast

from gutenbit.catalog import BookRecord, Catalog, CatalogFetchInfo
from gutenbit.db import (
    ChunkRecord,
    Database,
    IngestProgressCallback,
    TextState,
    _div_parts_match,
    _normalize_div_segment,
)
from gutenbit.display import CliDisplay, format_summary_stats
from gutenbit.download import describe_download_source, get_last_download_source

STATE_DIR_NAME = ".gutenbit"
DEFAULT_DB_NAME = "gutenbit.db"
DEFAULT_DB = f"{STATE_DIR_NAME}/{DEFAULT_DB_NAME}"
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
_TITLE_STYLE_CONNECTORS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_TITLE_STYLE_WORD_RE = re.compile(r"^[A-Za-z]+(?:['\u2019][A-Za-z]+)*$")
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]*$')

_DISPLAY_CACHE: tuple[int, int, CliDisplay] | None = None


class _CliHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Format help with slightly wider columns for cleaner CLI output."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_help_position", 30)
        super().__init__(*args, **kwargs)


def _display() -> CliDisplay:
    global _DISPLAY_CACHE
    stdout = sys.stdout
    stderr = sys.stderr
    cache_key = (id(stdout), id(stderr))
    if _DISPLAY_CACHE is None or _DISPLAY_CACHE[:2] != cache_key:
        _DISPLAY_CACHE = (*cache_key, CliDisplay(stdout=stdout, stderr=stderr))
    return _DISPLAY_CACHE[2]


def _cli_state_dir() -> Path:
    return (Path.cwd() / STATE_DIR_NAME).resolve()


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


def _load_catalog(args: argparse.Namespace, *, display: CliDisplay, as_json: bool) -> Catalog:
    catalog = Catalog.fetch(
        cache_dir=_catalog_cache_dir(),
        refresh=getattr(args, "refresh", False),
    )
    if not as_json:
        display.status(
            _catalog_status_message(
                catalog.fetch_info,
                refresh=getattr(args, "refresh", False),
            )
        )
    return catalog


def _normalize_apostrophes(s: str) -> str:
    """Replace curly/typographic apostrophes with ASCII for matching."""
    return s.replace("\u2019", "'").replace("\u2018", "'")


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


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _single_line(text: str) -> str:
    """Collapse all whitespace so tabular CLI output stays on one line."""
    return " ".join(text.split())


def _opening_preview_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip("()[]{}\"'“”‘’,;:-")
        if not token:
            continue
        tokens.append(token)
    return tokens


def _is_title_style_token(token: str) -> bool:
    if _ROMAN_NUMERAL_RE.fullmatch(token):
        return True
    if token.isupper() and any(ch.isalpha() for ch in token):
        return True
    if not _TITLE_STYLE_WORD_RE.fullmatch(token):
        return False
    lower = token.casefold()
    if lower in _TITLE_STYLE_CONNECTORS:
        return True
    return token[0].isupper() and token[1:] == token[1:].lower()


def _looks_like_opening_title_line(text: str) -> bool:
    flat = _single_line(text).strip()
    if not flat or _SENTENCE_END_RE.search(flat):
        return False
    if "," in flat or ";" in flat:
        return False
    tokens = _opening_preview_tokens(flat)
    if not tokens or len(tokens) > 8:
        return False
    return all(_is_title_style_token(token) for token in tokens)


def _select_section_opening_line(paragraphs: list[str]) -> str:
    """Choose a representative opening line for a section preview.

    Keep the first paragraph as the fallback, but skip a short title-like
    opening block when it is immediately followed by body text.
    """
    preview_lines: list[str] = []
    for text in paragraphs:
        flat = _single_line(text)
        if flat:
            preview_lines.append(flat)
    if not preview_lines:
        return ""

    prefix_len = 0
    while prefix_len < len(preview_lines) and _looks_like_opening_title_line(
        preview_lines[prefix_len]
    ):
        prefix_len += 1

    if prefix_len < len(preview_lines):
        first_line = preview_lines[0]
        if prefix_len > 1 or first_line.endswith(":"):
            return preview_lines[prefix_len]

    return preview_lines[0]


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


def _format_int(value: int) -> str:
    return f"{value:,}"


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


def _section_selector_parts(raw: str) -> list[str]:
    parts = [_normalize_div_segment(part) for part in raw.split("/") if part.strip()]
    if len(parts) > 4:
        raise ValueError("div path has too many segments (max 4: div1/div2/div3/div4)")
    return parts


def _canonical_section_match(
    summary: _SectionSummary | None, selector: str
) -> tuple[str, int] | None:
    if summary is None:
        return None
    query_parts = _section_selector_parts(selector)
    if not query_parts:
        return None
    for section in summary["sections"]:
        section_path = str(section["section"]).strip()
        if not section_path:
            continue
        if _div_parts_match(query_parts, _section_selector_parts(section_path)):
            return section_path, int(section["section_number"])
    return None


def _truncate_section_label(label: str, width: int) -> str:
    """Truncate a section path, preferring the most specific (deepest) level.

    When the full path ("BOOK TITLE / CHAPTER 1") exceeds *width*,
    show the deepest level with a ".../ " prefix so users see the
    chapter name rather than a truncated book title.
    """
    if len(label) <= width:
        return label
    parts = label.split(" / ")
    if len(parts) > 1:
        deepest = parts[-1]
        prefix = ".../ "
        if len(prefix) + len(deepest) <= width:
            return prefix + deepest
        # Deepest level itself is too long — truncate it with prefix
        keep = max(1, width - len(prefix) - 3)
        return prefix + deepest[:keep] + "..."
    # Single level, just truncate
    keep = max(1, width - 3)
    return label[:keep] + "..."


def _section_examples(db: Database, book_id: int, *, limit: int = 5) -> list[str]:
    summary = _build_section_summary(db, book_id)
    if summary is not None:
        numbered_examples: list[str] = []
        for sec in summary["sections"]:
            if sec["section_number"] > 0 and sec["section"].strip():
                numbered_examples.append(f"{sec['section_number']}. {sec['section'].strip()}")
            if len(numbered_examples) >= limit:
                break
        if numbered_examples:
            return numbered_examples

    examples: list[str] = []
    seen: set[str] = set()
    for _pos, div1, div2, div3, div4, _content, _kind, _char_count in db.chunks(
        book_id, kinds=["heading"]
    ):
        section = _section_path(div1, div2, div3, div4)
        if section == "(unsectioned opening)" or section in seen:
            continue
        seen.add(section)
        examples.append(section)
        if len(examples) >= limit:
            break
    return examples


def _split_semicolon_list(raw: str) -> list[str]:
    return [_single_line(part) for part in raw.split(";") if part.strip()]


def _summarize_semicolon_list(raw: str, *, max_items: int) -> str:
    items = _split_semicolon_list(raw)
    if not items:
        return ""
    if len(items) <= max_items:
        return "; ".join(items)
    shown = "; ".join(items[:max_items])
    return f"{shown}; +{len(items) - max_items} more"


def _estimate_read_time(words: int, *, wpm: int = 250) -> str:
    if words <= 0:
        return "n/a"
    minutes = max(1, round(words / wpm))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


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


def _indent_block(text: str, prefix: str = "    ") -> str:
    lines = text.splitlines()
    if not lines:
        return prefix if text else ""
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)


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


def _passage_header(payload: dict[str, Any]) -> str:
    parts = [
        f"{JSON_BOOK_ID_KEY}={payload[JSON_BOOK_ID_KEY]}",
        f"title={payload['title']}",
    ]
    if payload.get("author"):
        parts.append(f"author={payload['author']}")
    if payload.get("section"):
        parts.append(f"section={payload['section']}")
    if payload.get("section_number") is not None:
        parts.append(f"section_number={payload['section_number']}")
    if payload.get("position") is not None:
        parts.append(f"position={payload['position']}")
    if payload.get("forward") is not None:
        parts.append(f"forward={payload['forward']}")
    if payload.get("radius") is not None:
        parts.append(f"radius={payload['radius']}")
    if payload.get("all"):
        parts.append("all")
    return "  ".join(parts)


def _section_number_lookup(db: Database) -> Any:
    cache: dict[int, dict[str, int]] = {}

    def lookup(book: int, section: str | None) -> int | None:
        if not section:
            return None
        if book not in cache:
            summary = _build_section_summary(db, book)
            cache[book] = (
                {str(sec["section"]): int(sec["section_number"]) for sec in summary["sections"]}
                if summary is not None
                else {}
            )
        return cache[book].get(section)

    return lookup


def _print_key_value_table(
    rows: list[tuple[str, str]],
    *,
    show_header: bool = True,
    key_header: str = "Field",
    value_header: str = "Value",
) -> None:
    if not rows:
        return
    key_width = max(len(key_header), max(len(key) for key, _ in rows))
    if show_header:
        print(f"  {key_header:<{key_width}}  {value_header}")
        print(f"  {'-' * key_width}  {'-' * len(value_header)}")
    for key, value in rows:
        shown = _single_line(value) if value else "-"
        print(f"  {key:<{key_width}}  {shown}")


def _print_table(headers: list[str], rows: list[list[str]], *, right_align: set[int]) -> None:
    if not headers:
        return
    widths = []
    for idx, header in enumerate(headers):
        widest = len(header)
        for row in rows:
            widest = max(widest, len(row[idx]))
        widths.append(widest)

    def _fmt(cell: str, idx: int) -> str:
        width = widths[idx]
        if idx in right_align:
            return f"{cell:>{width}}"
        return f"{cell:<{width}}"

    print("  " + "  ".join(_fmt(header, i) for i, header in enumerate(headers)))
    print("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        print("  " + "  ".join(_fmt(cell, i) for i, cell in enumerate(row)))


def _print_block_header(title: str) -> None:
    print(f"\n[{title.upper()}]")


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Add --db and -v to a subparser so they work after the subcommand too."""
    parser.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help="SQLite database path (works before or after the subcommand)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable debug logging",
    )


def _add_catalog_cache_args(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "ignore the catalog cache and redownload it now",
) -> None:
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=help_text,
    )


def _opening_rows(db: Database, book_id: int, n: int) -> list[ChunkRecord]:
    """Return a default reading window, skipping common front-matter headings.

    Skips headings that match the book title, byline patterns ("by ..."),
    and well-known front-matter labels (preface, introduction, etc.).
    """
    rows = db.chunk_records(book_id)
    if not rows:
        return []

    skip = set(OPENING_SECTION_SKIP_HEADINGS)
    book = db.book(book_id)
    title_lower = ""
    if book:
        title_lower = _normalize_apostrophes(book.title.casefold())
        skip.add(title_lower)

    first_heading_index = 0
    for idx, row in enumerate(rows):
        if row.kind != "heading":
            continue
        heading = _normalize_apostrophes(row.content.casefold())
        if heading in skip:
            continue
        if heading.startswith("by "):
            continue
        # Skip headings that match the book title or a prefix/expansion of it
        # (e.g. "NOSTROMO" for "Nostromo: A Tale of the Seaboard", or
        # "THE MIRROR OF THE SEA MEMORIES AND IMPRESSIONS" for "The Mirror of the Sea").
        if (
            title_lower
            and len(heading) >= 3
            and (title_lower.startswith(heading) or heading.startswith(title_lower))
        ):
            continue
        first_heading_index = idx
        break

    window = rows[first_heading_index : first_heading_index + n]
    # Ensure the window includes at least one text chunk when possible.
    # Books with nested headings (PART → SUBTITLE → CHAPTER) can exhaust
    # the default window with headings only, showing no prose.
    if window and all(r.kind == "heading" for r in window):
        end = first_heading_index + n
        while end < len(rows) and rows[end].kind == "heading":
            end += 1
        if end < len(rows):
            window = rows[first_heading_index : end + 1]
    return window


def _section_reading_window(rows: list[ChunkRecord], *, text_passages: int) -> list[ChunkRecord]:
    """Return a readable section window with heading context plus prose.

    Includes any leading heading rows, then keeps reading until *text_passages*
    text chunks have been collected. This makes ``view --section`` land on prose
    by default instead of stopping at a bare heading.
    """
    if not rows or text_passages <= 0:
        return []

    window: list[ChunkRecord] = []
    seen_text = 0
    for row in rows:
        window.append(row)
        if row.kind == "text":
            seen_text += 1
            if seen_text >= text_passages:
                break
    return window


def _format_fts_error(exc: sqlite3.Error) -> str:
    detail = " ".join(str(exc).split()).strip().rstrip(".")
    if not detail:
        return "Invalid FTS query syntax."
    return f"Invalid FTS query syntax: {detail}."


def _package_version() -> str:
    try:
        return package_version("gutenbit")
    except PackageNotFoundError:
        try:
            from gutenbit import __version__
        except ImportError:
            return "0.dev0+unknown"
        return __version__


def _toc_expand_depth(expand: str) -> int:
    return 4 if expand == "all" else int(expand)


def _build_parser() -> argparse.ArgumentParser:
    fmt = _CliHelpFormatter
    p = argparse.ArgumentParser(
        prog="gutenbit",
        formatter_class=fmt,
        description=(
            "Find, store, inspect, read, and search Project Gutenberg books from your terminal."
        ),
        epilog="""\
quick start:
  1. gutenbit catalog --author "Austen, Jane"                   # find Pride and Prejudice
  2. gutenbit add 1342                                          # download and store it
  3. gutenbit toc 1342                                          # inspect numbered sections
  4. gutenbit view 1342                                         # read the opening
  5. gutenbit search "truth universally acknowledged" --book 1342 --phrase

learn more:
  gutenbit COMMAND --help    detailed help for one command

gutenbit is an open-source project not affiliated with Project Gutenberg.
It is for individual downloads, not bulk downloading.

By default, gutenbit stores its SQLite database and catalog cache in
.gutenbit/ in the current directory (default database: .gutenbit/gutenbit.db).""",
    )
    p._optionals.title = "global options"
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path (default: %(default)s)")
    p.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    p.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = p.add_subparsers(dest="command", title="commands", metavar="COMMAND")

    # --- catalog ---
    cat = sub.add_parser(
        "catalog",
        formatter_class=fmt,
        help="search the Project Gutenberg catalog",
        description="Search the Project Gutenberg catalog (cached for 2 hours by default).",
        epilog="""\
examples:
  gutenbit catalog --author Tolstoy
  gutenbit catalog --title "War and Peace"
  gutenbit catalog --author Dickens --refresh
  gutenbit catalog --language en --subject Philosophy --limit 50

output columns:  ID  AUTHORS  TITLE
all filters use case-insensitive substring matching (AND logic).""",
    )
    cat.add_argument("--author", default="", help="filter by author (substring match)")
    cat.add_argument("--title", default="", help="filter by title (substring match)")
    cat.add_argument("--language", default="", help="filter by language code, e.g. 'en'")
    cat.add_argument("--subject", default="", help="filter by subject (substring match)")
    cat.add_argument("--limit", type=int, default=20, help="max results (default: 20)")
    cat.add_argument("--json", action="store_true", help="output as JSON")
    _add_catalog_cache_args(cat)
    _add_global_args(cat)

    # --- add ---
    add = sub.add_parser(
        "add",
        formatter_class=fmt,
        help="download and store books by PG id",
        description=(
            "Download books from Project Gutenberg by ID, parse HTML into chunks, "
            "and store everything in the SQLite database. Already-downloaded books "
            "are skipped unless --refresh forces a re-download and reprocessing."
        ),
        epilog="""\
examples:
  gutenbit add 2600                     # War and Peace
  gutenbit add 46 730 967               # multiple books
  gutenbit add 2600 --refresh           # refresh the catalog and reprocess the book
  gutenbit add 2600 --delay 2.0         # polite crawling""",
    )
    add.add_argument(
        "book_ids",
        nargs="+",
        metavar="BOOK_ID",
        type=int,
        help="Project Gutenberg book IDs",
    )
    add.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DOWNLOAD_DELAY,
        help="seconds between downloads (default: %(default)s)",
    )
    add.add_argument("--json", action="store_true", help="output as JSON")
    _add_catalog_cache_args(
        add,
        help_text=(
            "ignore the catalog cache, redownload it now, and reprocess matching stored books"
        ),
    )
    _add_global_args(add)

    # --- remove ---
    de = sub.add_parser(
        "remove",
        formatter_class=fmt,
        help="remove stored books by PG id",
        description=(
            "Remove previously added books from the SQLite database, including "
            "their reconstructed text and all chunks."
        ),
        epilog="""\
examples:
  gutenbit remove 46
  gutenbit remove 46 730 967
  gutenbit remove 2600 --db my.db

if a book ID is not present, a warning is printed and exit code is 1.""",
    )
    de.add_argument(
        "book_ids",
        nargs="+",
        metavar="BOOK_ID",
        type=int,
        help="Project Gutenberg book IDs",
    )
    de.add_argument("--json", action="store_true", help="output as JSON")
    _add_global_args(de)

    # --- books ---
    bk = sub.add_parser(
        "books",
        formatter_class=fmt,
        help="list or update books stored in the database",
        description=(
            "List all books that have been added to the database. With --update, "
            "reprocess stored books whose parser version is stale."
        ),
        epilog="""\
examples:
  gutenbit books
  gutenbit books --json
  gutenbit books --update
  gutenbit books --update --force
  gutenbit books --db my.db

output columns:  ID  AUTHORS  TITLE""",
    )
    bk.add_argument(
        "--update",
        action="store_true",
        help="reprocess stored books whose parser version is stale",
    )
    bk.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DOWNLOAD_DELAY,
        help="seconds between downloads in update mode (default: %(default)s)",
    )
    bk.add_argument(
        "--force",
        action="store_true",
        help="reprocess all stored books in update mode, even if already current",
    )
    bk.add_argument(
        "--dry-run",
        action="store_true",
        help="show which stored books would be updated without downloading",
    )
    bk.add_argument("--json", action="store_true", help="output as JSON")
    _add_global_args(bk)

    # --- search ---
    se = sub.add_parser(
        "search",
        formatter_class=fmt,
        help="full-text search across stored books",
        description=(
            "Full-text search using SQLite FTS5 with BM25 ranking. "
            "Plain-text queries are auto-escaped so apostrophes, hyphens, "
            "and other punctuation are ok. Use --raw for advanced FTS5 "
            "syntax (AND/OR/NOT, prefix*, NEAR, parentheses)."
        ),
        epilog="""\
examples:
  gutenbit search "bennet"                                  # simple search
  gutenbit search "don't stop"                              # punctuation is ok
  gutenbit search "half-hour"                               # hyphens just work
  gutenbit search "truth universally acknowledged" --phrase # exact phrase match
  gutenbit search "ghost OR spirit" --raw                   # FTS5 boolean query
  gutenbit search "(ghost OR spirit) AND NOT haunt*" --raw  # advanced FTS5
  gutenbit search "bennet" --book 1342                      # restrict to one book
  gutenbit search "truth universally acknowledged" --book 1342 --section 1 --phrase
  gutenbit search "chapter" --book 1342 --kind heading      # search headings only
  gutenbit search "bennet" --book 1342 --order first        # reading order (earliest)
  gutenbit search "bennet" --book 1342 --order last         # reverse reading order
  gutenbit search "bennet" --book 1342 --radius 1           # show surrounding passage
  gutenbit search "bennet" --book 1342 --limit 3            # limit the result set
  gutenbit search "bennet" --book 1342 --count              # just show match count
  gutenbit search "bennet" --book 1342 --json               # JSON output

query modes:
  (default)  plain text — punctuation is auto-escaped, words are AND'd
  --phrase   exact phrase — word order and adjacency must match exactly
  --raw      FTS5 syntax — AND, OR, NOT, NEAR(), prefix*, "phrases", (groups)

result order:
  rank    BM25 rank, then book, then position (default)
  first   book ascending, then position ascending
  last    book descending, then position descending

tip: use 'gutenbit toc <id>' first to see a book's structure, then
     narrow searches with --book and --section. Search uses text chunks
     by default; use --kind heading or --kind all when needed.""",
    )
    se.add_argument(
        "query",
        metavar="QUERY",
        help="search query (plain text by default; see --raw, --phrase)",
    )
    query_group = se.add_mutually_exclusive_group()
    query_group.add_argument(
        "--phrase",
        action="store_true",
        help="treat query as an exact phrase (word order must match)",
    )
    query_group.add_argument(
        "--raw",
        action="store_true",
        help="pass query directly to FTS5 (AND/OR/NOT, prefix*, NEAR, groups)",
    )
    se.add_argument(
        "--order",
        choices=["rank", "first", "last"],
        default="rank",
        metavar="ORDER",
        help=(
            "search result order: rank (BM25); "
            "first (book asc + position asc); "
            "last (book desc + position desc)"
        ),
    )
    se.add_argument("--author", help="filter results by author (substring match)")
    se.add_argument("--title", help="filter results by title (substring match)")
    se.add_argument("--book", type=int, help="restrict to a single book by PG ID")
    se.add_argument(
        "--kind",
        choices=["text", "heading", "all"],
        default="text",
        metavar="KIND",
        help="chunk kind to search (default: %(default)s)",
    )
    se.add_argument(
        "--section",
        help=(
            "restrict to a section by path prefix (e.g. 'STAVE ONE') or section number from 'toc'"
        ),
    )
    se.add_argument(
        "--limit",
        type=int,
        default=10,
        help="max results (default: 10)",
    )
    se.add_argument(
        "--radius",
        type=int,
        default=None,
        help="surrounding passage on each side of each hit, in reading order",
    )
    se.add_argument(
        "--count",
        action="store_true",
        help="just print the number of matches",
    )
    se.add_argument("--json", action="store_true", help="output results as JSON")
    _add_global_args(se)

    # --- toc ---
    tc = sub.add_parser(
        "toc",
        formatter_class=fmt,
        help="show structural table of contents for a book",
        description=(
            "Show a compact structural summary of one stored book, including "
            "section numbering for easy section selection in `view`. "
            "Use --expand to control how many heading levels the table shows."
        ),
        epilog="""\
examples:
  gutenbit toc 2600
  gutenbit toc 100 --expand all
  gutenbit toc 2600 --json

if the book is missing, `toc` adds it automatically before rendering.

section numbers in this output can be passed to:
  gutenbit view 2600 --section <NUMBER>""",
    )
    tc.add_argument("book", metavar="BOOK_ID", type=int, help="Project Gutenberg book ID")
    tc.add_argument(
        "--expand",
        choices=["1", "2", "3", "4", "all"],
        default=DEFAULT_TOC_EXPAND,
        metavar="DEPTH",
        help="show heading levels up to this depth (default: 2; use 'all' for every level)",
    )
    tc.add_argument("--json", action="store_true", help="output as JSON")
    _add_global_args(tc)

    # --- view ---
    vw = sub.add_parser(
        "view",
        formatter_class=fmt,
        help="read stored book text, or focused parts of it",
        description=(
            "Read from the first structural section by default, or focus from an exact position "
            "or section selector. Section selectors accept path text or a section "
            "number from `gutenbit toc <book>`. Use --forward for forward reading, "
            "--radius for surrounding passage windows, or --all for a full book or selected "
            "section subtree."
        ),
        epilog="""\
examples:
  gutenbit toc 1342                                  # inspect structure first
  gutenbit view 1342                                 # first structural section + quick actions
  gutenbit view 1342 --all                           # full reconstructed text
  gutenbit view 1342 --section 1                     # first passage in section 1
  gutenbit view 1342 --section 1 --all               # full section, including nested subsections
  gutenbit view 1342 --section 1 --forward 5         # first 5 passages in section 1
  gutenbit view 1342 --section 1 --radius 1          # surrounding passage around the section start
  gutenbit view 1342 --position 1                    # passage at position 1
  gutenbit view 1342 --position 1 --forward 5        # continue reading from position
  gutenbit view 1342 --position 1 --radius 1         # surrounding passage around position
  gutenbit view 1342 --section "Chapter 1" --forward 5 --json

selectors (choose at most one):
  --position <n> | --section <SECTION_SELECTOR>
""",
    )
    vw.add_argument("book", metavar="BOOK_ID", type=int, help="Project Gutenberg book ID")
    vw.add_argument("--position", type=int, help="select the passage at this exact position")
    vw.add_argument(
        "--section",
        help=(
            "read from a section selector: path prefix "
            '(e.g. PART ONE/CHAPTER I) or section number from `toc` (e.g. "3")'
        ),
    )
    vw.add_argument(
        "--all",
        action="store_true",
        help=(
            "read the full selected scope "
            "(whole book or selected section, including nested subsections)"
        ),
    )
    vw.add_argument(
        "--forward",
        type=int,
        default=None,
        help="passages to read forward (default: opening=3, section/position=1)",
    )
    vw.add_argument(
        "--radius",
        type=int,
        default=None,
        help="surrounding passage on each side of the selected passage",
    )
    vw.add_argument(
        "--json",
        action="store_true",
        help="output as JSON",
    )
    _add_global_args(vw)

    return p


# -------------------------------------------------------------------
# Subcommand handlers
# -------------------------------------------------------------------


def _cmd_catalog(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    if args.limit <= 0:
        return _command_error("catalog", "--limit must be > 0.", as_json=as_json)

    catalog = _load_catalog(args, display=display, as_json=as_json)
    results = catalog.search(
        author=args.author,
        title=args.title,
        language=args.language,
        subject=args.subject,
    )

    shown = results[: args.limit]
    if as_json:
        data = {
            "filters": {
                "author": args.author,
                "title": args.title,
                "language": args.language,
                "subject": args.subject,
            },
            "limit": args.limit,
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "total_matches": len(results),
            "shown": len(shown),
            "items": [_book_payload(book) for book in shown],
        }
        _print_json_envelope("catalog", ok=True, data=data)
        return 0

    if not shown:
        display.status("No books found.")
        return 0

    display.catalog(shown, remaining_count=len(results) - len(shown))
    return 0


def _ingest_one_book(
    db: Database,
    book: BookRecord,
    *,
    state: TextState,
    delay: float,
    as_json: bool,
    force: bool = False,
    progress_callback: IngestProgressCallback | None = None,
) -> bool:
    def _run_ingest() -> bool:
        if progress_callback is None:
            return db._ingest_book(
                book,
                delay=delay,
                force=force,
                state=state,
            )
        return db._ingest_book(
            book,
            delay=delay,
            force=force,
            state=state,
            progress_callback=progress_callback,
        )

    if as_json:
        previous_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            return _run_ingest()
        finally:
            logging.disable(previous_disable)

    return _run_ingest()


def _process_books_for_ingest(
    db: Database,
    books: list[BookRecord],
    *,
    delay: float,
    as_json: bool,
    display: CliDisplay,
    failure_action: str,
    force: bool = False,
    show_skipped_current: bool = True,
) -> tuple[dict[int, str], list[str]]:
    statuses: dict[int, str] = {}
    errors: list[str] = []
    states = db.text_states([book.id for book in books])
    with display.ingest_progress() as progress:
        for index, book in enumerate(books, start=1):
            title = _single_line(book.title)
            state = states.get(book.id, TextState(has_text=False, has_current_text=False))
            if state.has_current_text and not force:
                statuses[book.id] = "skipped_current"
                if not as_json and show_skipped_current:
                    display.status(f"  skipping {book.id}: {title} (already downloaded)")
                continue

            target_status = "reprocessed" if state.has_text else "added"
            progress_callback = None
            if not as_json:
                if progress is not None:
                    progress.start_book(
                        book_id=book.id,
                        title=title,
                        action="reprocess" if state.has_text else "add",
                        index=index,
                        total=len(books),
                        delay=delay,
                    )
                    progress_callback = progress.update_stage
                elif state.has_text:
                    reason = "forced" if force else "chunker updated"
                    display.status(f"  processing {book.id}: {title} ({reason})...")
                else:
                    display.status(f"  adding {book.id}: {title}...")

            success = _ingest_one_book(
                db,
                book,
                state=state,
                delay=delay,
                as_json=as_json,
                force=force,
                progress_callback=progress_callback,
            )

            if progress is not None:
                progress.finish_book()

            if success:
                statuses[book.id] = target_status
                if not as_json:
                    source = get_last_download_source()
                    source_description = describe_download_source(source)
                    if source:
                        if source_description:
                            display.success(
                                f"  {target_status} {book.id}: {title} "
                                f"({source_description}: {source})"
                            )
                        else:
                            display.success(f"  {target_status} {book.id}: {title} ({source})")
                    else:
                        display.success(f"  {target_status} {book.id}: {title}")
            else:
                statuses[book.id] = "failed"
                failure = f"Failed to {failure_action} {book.id}: {title}"
                errors.append(failure)
                if not as_json:
                    display.error(f"  failed {book.id}: {title}")

    return statuses, errors


def _cmd_add(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    if args.delay < 0:
        return _command_error("add", "--delay must be >= 0.", as_json=as_json)

    invalid_ids = [bid for bid in args.book_ids if bid <= 0]
    if invalid_ids:
        return _command_error(
            "add",
            f"Book IDs must be positive integers, got: {', '.join(map(str, invalid_ids))}",
            as_json=as_json,
            data={"invalid_ids": invalid_ids},
        )

    catalog = _load_catalog(args, display=display, as_json=as_json)
    selected_by_id: dict[int, Any] = {}
    request_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for requested_id in args.book_ids:
        rec = catalog.get(requested_id)
        if rec is None:
            warning = (
                f"book {requested_id} is outside the English text catalog boundaries, skipping"
            )
            warnings.append(warning)
            request_results.append({"requested_id": requested_id, "status": "out_of_policy"})
            if not as_json:
                display.warning(
                    "  warning: "
                    f"{_book_id_ref(requested_id, capitalize=False)} is outside "
                    "the English text catalog boundaries, skipping"
                )
            continue
        title = _single_line(rec.title)
        if rec.id != requested_id and not as_json:
            display.status(f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)")
        if rec.id in selected_by_id:
            request_results.append(
                {
                    "requested_id": requested_id,
                    "canonical_id": rec.id,
                    "title": title,
                    "remapped": rec.id != requested_id,
                    "status": "duplicate_requested",
                }
            )
            continue
        selected_by_id[rec.id] = rec
        request_results.append(
            {
                "requested_id": requested_id,
                "canonical_id": rec.id,
                "title": title,
                "remapped": rec.id != requested_id,
                "status": "selected",
            }
        )

    books = list(selected_by_id.values())

    if not books:
        data = {
            "db": str(Path(args.db).resolve()),
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "requested_ids": args.book_ids,
            "results": request_results,
        }
        return _command_error(
            "add",
            "No valid book IDs provided.",
            as_json=as_json,
            data=data,
            warnings=warnings,
        )

    with Database(args.db) as db:
        canonical_statuses, errors = _process_books_for_ingest(
            db,
            books,
            delay=args.delay,
            as_json=as_json,
            display=display,
            failure_action="add",
            force=args.refresh,
        )

    if as_json:
        result_rows: list[dict[str, Any]] = []
        status_totals: dict[str, int] = {}
        for row in request_results:
            result = dict(row)
            canonical_id = result.get("canonical_id")
            if isinstance(canonical_id, int):
                add_status = canonical_statuses.get(canonical_id)
                if add_status:
                    result["add_status"] = add_status
                    if result["status"] == "selected":
                        result["status"] = add_status
                    status_totals[result["status"]] = status_totals.get(result["status"], 0) + 1
                else:
                    status_totals[result["status"]] = status_totals.get(result["status"], 0) + 1
            else:
                status_totals[result["status"]] = status_totals.get(result["status"], 0) + 1
            result_rows.append(result)

        data = {
            "db": str(Path(args.db).resolve()),
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "delay_seconds": args.delay,
            "requested_ids": args.book_ids,
            "unique_canonical_ids": sorted(selected_by_id.keys()),
            "counts": {
                "requested": len(args.book_ids),
                "canonical": len(books),
            },
            "status_totals": status_totals,
            "results": result_rows,
        }
        failed_canonical_ids = sorted(
            book_id for book_id, status in canonical_statuses.items() if status == "failed"
        )
        data["failed_canonical_ids"] = failed_canonical_ids
        ok = len(failed_canonical_ids) == 0
        _print_json_envelope("add", ok=ok, data=data, warnings=warnings, errors=errors)
        return 0 if ok else 1

    if errors:
        display.error(
            f"Completed with {len(errors)} failure(s). Database: {Path(args.db).resolve()}"
        )
        return 1
    display.success(f"Done. Database: {Path(args.db).resolve()}")
    return 0


def _cmd_books(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    if not args.update:
        if args.delay != DEFAULT_DOWNLOAD_DELAY:
            return _command_error(
                "books",
                "--delay can only be used with --update.",
                as_json=as_json,
            )
        if args.force:
            return _command_error(
                "books",
                "--force can only be used with --update.",
                as_json=as_json,
            )
        if args.dry_run:
            return _command_error(
                "books",
                "--dry-run can only be used with --update.",
                as_json=as_json,
            )
    elif args.delay < 0:
        return _command_error("books", "--delay must be >= 0.", as_json=as_json)

    with Database(args.db) as db:
        books = db.books()
        if args.update:
            db_path = str(Path(args.db).resolve())
            stored_count = len(books)
            selected_books = books if args.force else db.stale_books()
            selected_count = len(selected_books)
            skipped_current = 0 if args.force else stored_count - selected_count

            if not books:
                if as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": args.delay,
                            "force": args.force,
                            "dry_run": args.dry_run,
                            "counts": {
                                "stored": 0,
                                "selected": 0,
                                "updated": 0,
                                "skipped_current": 0,
                                "failed": 0,
                            },
                            "results": [],
                        },
                    )
                else:
                    display.status("No books stored yet. Use 'gutenbit add <id> ...' to add some.")
                return 0

            if args.dry_run:
                results = [
                    {
                        "book_id": book.id,
                        "title": _single_line(book.title),
                        "status": "selected",
                    }
                    for book in selected_books
                ]
                if as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": args.delay,
                            "force": args.force,
                            "dry_run": True,
                            "counts": {
                                "stored": stored_count,
                                "selected": selected_count,
                                "updated": 0,
                                "skipped_current": skipped_current,
                                "failed": 0,
                            },
                            "results": results,
                        },
                    )
                elif selected_books:
                    display.status(
                        f"Would reprocess {selected_count} of {stored_count} stored book(s):"
                    )
                    for book in selected_books:
                        display.status(f"  {book.id}: {_single_line(book.title)}")
                else:
                    display.status(
                        f"All {stored_count} stored book(s) are current. Database: {db_path}"
                    )
                return 0

            if not selected_books:
                if as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": args.delay,
                            "force": args.force,
                            "dry_run": False,
                            "counts": {
                                "stored": stored_count,
                                "selected": 0,
                                "updated": 0,
                                "skipped_current": skipped_current,
                                "failed": 0,
                            },
                            "results": [],
                        },
                    )
                else:
                    display.success(
                        f"All {stored_count} stored book(s) are current. Database: {db_path}"
                    )
                return 0

            if not as_json:
                display.status(f"Checking {stored_count} stored book(s)...")

            statuses, errors = _process_books_for_ingest(
                db,
                selected_books,
                delay=args.delay,
                as_json=as_json,
                display=display,
                failure_action="update",
                force=args.force,
                show_skipped_current=False,
            )
            updated_count = sum(
                1 for status in statuses.values() if status in {"added", "reprocessed"}
            )
            failed_count = sum(1 for status in statuses.values() if status == "failed")
            results = [
                {
                    "book_id": book.id,
                    "title": _single_line(book.title),
                    "status": statuses[book.id],
                }
                for book in selected_books
            ]

            if as_json:
                _print_json_envelope(
                    "books",
                    ok=failed_count == 0,
                    data={
                        "action": "update",
                        "db": db_path,
                        "delay_seconds": args.delay,
                        "force": args.force,
                        "dry_run": False,
                        "counts": {
                            "stored": stored_count,
                            "selected": selected_count,
                            "updated": updated_count,
                            "skipped_current": skipped_current,
                            "failed": failed_count,
                        },
                        "results": results,
                    },
                    errors=errors,
                )
                return 0 if failed_count == 0 else 1

            if failed_count:
                display.error(
                    "Completed with "
                    f"{failed_count} failure(s). Updated {updated_count} book(s); "
                    f"{skipped_current} already current. Database: {db_path}"
                )
                return 1
            display.success(
                f"Done. Updated {updated_count} book(s); "
                f"{skipped_current} already current. Database: {db_path}"
            )
            return 0

    if not books:
        if as_json:
            _print_json_envelope(
                "books",
                ok=True,
                data={"count": 0, "items": []},
            )
        else:
            display.status("No books stored yet. Use 'gutenbit add <id> ...' to add some.")
        return 0
    if as_json:
        _print_json_envelope(
            "books",
            ok=True,
            data={
                "count": len(books),
                "items": [_book_payload(book) for book in books],
            },
        )
        return 0
    display.books(books, db_path=args.db)
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    any_missing = False
    removed_count = 0
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with Database(args.db) as db:
        for book_id in args.book_ids:
            removed = db.remove_book(book_id)
            if not removed:
                message = f"No book found for id {book_id}."
                errors.append(message)
                results.append({"book_id": book_id, "status": "missing"})
                if not as_json:
                    display.error(f"No book found for {_book_id_ref(book_id, capitalize=False)}.")
                any_missing = True
            else:
                removed_count += 1
                results.append({"book_id": book_id, "status": "removed"})
                if not as_json:
                    display.success(
                        f"Removed {_book_id_ref(book_id, capitalize=False)} from {args.db}."
                    )
    if as_json:
        _print_json_envelope(
            "remove",
            ok=not any_missing,
            data={
                "db": str(Path(args.db).resolve()),
                "removed_count": removed_count,
                "missing_count": len(args.book_ids) - removed_count,
                "results": results,
            },
            errors=errors,
        )
    return 1 if any_missing else 0


def _cmd_search(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    if args.limit <= 0:
        return _command_error("search", "--limit must be > 0.", as_json=as_json)
    if args.radius is not None and args.radius < 0:
        return _command_error("search", "--radius must be >= 0.", as_json=as_json)
    if args.count and args.radius is not None:
        return _command_error(
            "search",
            "--radius cannot be used with --count.",
            as_json=as_json,
        )

    query_text = args.query.strip()
    if not query_text:
        return _command_error("search", "Search query must not be empty.", as_json=as_json)

    # Query mode: --phrase wraps as exact phrase, --raw passes through to FTS5,
    # default auto-escapes plain text so punctuation is ok.
    if args.phrase:
        search_query = _fts_phrase_query(query_text)
        query_mode = "phrase"
    elif args.raw:
        search_query = query_text
        query_mode = "raw"
    else:
        search_query = _safe_fts_query(query_text)
        query_mode = "auto"

    radius = args.radius

    # Resolve --section: accept a section number (from 'toc') or path prefix.
    div_path: str | None = None
    section_arg: str | None = args.section

    limit = args.limit

    warnings: list[str] = []
    with Database(args.db) as db:
        section_number_for = _section_number_lookup(db)

        if args.book is not None and not db.has_text(args.book):
            warning = f"Book {args.book} is not in the database."
            warnings.append(warning)
            if not as_json:
                display.warning(f"warning: {_book_id_ref(args.book)} is not in the database.")

        # Resolve section number → div path (requires book_id).
        if section_arg is not None:
            if section_arg.isdigit():
                section_number = int(section_arg)
                if section_number <= 0:
                    return _command_error(
                        "search", "--section number must be >= 1.", as_json=as_json
                    )
                if args.book is None:
                    return _command_error(
                        "search",
                        "--section with a number requires --book.",
                        as_json=as_json,
                    )
                summary = _build_section_summary(db, args.book)
                if summary is None:
                    return _command_error(
                        "search",
                        f"Book {args.book} has no sections.",
                        as_json=as_json,
                        display_message=f"{_book_id_ref(args.book)} has no sections.",
                    )
                sections = summary["sections"]
                if section_number > len(sections):
                    return _command_error(
                        "search",
                        f"Section {section_number} is out of range "
                        f"(book {args.book} has {len(sections)} sections).",
                        as_json=as_json,
                        display_message=(
                            f"Section {section_number} is out of range "
                            f"({_book_id_ref(args.book, capitalize=False)} "
                            f"has {len(sections)} sections)."
                        ),
                    )
                div_path = sections[section_number - 1]["section"]
            else:
                try:
                    _section_selector_parts(section_arg)
                except ValueError as exc:
                    return _command_error(
                        "search",
                        f"Invalid section selector: {exc}.",
                        as_json=as_json,
                    )
                if args.book is not None:
                    matched_section = _canonical_section_match(
                        _build_section_summary(db, args.book), section_arg
                    )
                    div_path = matched_section[0] if matched_section is not None else section_arg
                else:
                    div_path = section_arg

        search_author = args.author
        search_title = args.title
        search_book_id = args.book
        search_kind = None if args.kind == "all" else args.kind
        search_div_path = div_path

        try:
            if args.count:
                total_results = db.search_count(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_id=search_book_id,
                    kind=search_kind,
                    div_path=search_div_path,
                )
                results = []
            else:
                search_page = db.search_page(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_id=search_book_id,
                    kind=search_kind,
                    div_path=search_div_path,
                    order=args.order,
                    limit=limit,
                )
                total_results = search_page.total_results
                results = search_page.items
        except sqlite3.Error as exc:
            return _command_error(
                "search",
                _format_fts_error(exc),
                as_json=as_json,
                data={
                    "query": {
                        "raw": args.query,
                        "fts": search_query,
                        "mode": query_mode,
                    },
                    "filters": _json_search_filters(
                        author=args.author,
                        title=args.title,
                        book_id=args.book,
                        kind=args.kind,
                        section=section_arg,
                    ),
                    "order": args.order,
                    "limit": limit,
                    **({"radius": radius} if radius is not None else {}),
                },
                warnings=warnings,
            )

        result_items: list[dict[str, Any]] = []
        for idx, result in enumerate(results, start=1):
            section = _section_path(result.div1, result.div2, result.div3, result.div4)
            if radius is None:
                content = result.content
            else:
                rows = db.chunk_window(result.book_id, result.position, around=radius)
                content = _joined_chunk_text(rows)
            result_items.append(
                _passage_payload(
                    book_id=result.book_id,
                    title=_single_line(result.title),
                    author=_single_line(result.authors),
                    section=section,
                    section_number=section_number_for(result.book_id, section),
                    position=result.position,
                    forward=None,
                    radius=radius,
                    content=content,
                    extras={
                        "kind": result.kind,
                        "rank": idx,
                        "score": round(result.score, 4),
                    },
                )
            )

    # --count: just print the total.
    if args.count:
        if as_json:
            _print_json_envelope(
                "search",
                ok=True,
                data={
                    "query": {
                        "raw": args.query,
                        "fts": search_query,
                        "mode": query_mode,
                    },
                    "filters": _json_search_filters(
                        author=args.author,
                        title=args.title,
                        book_id=args.book,
                        kind=args.kind,
                        section=section_arg,
                    ),
                    "count": total_results,
                },
                warnings=warnings,
            )
        else:
            print(total_results)
        return 0

    if as_json:
        data = {
            "query": {
                "raw": args.query,
                "fts": search_query,
                "mode": query_mode,
            },
            "filters": _json_search_filters(
                author=args.author,
                title=args.title,
                book_id=args.book,
                kind=args.kind,
                section=section_arg,
            ),
            "order": args.order,
            "limit": limit,
            "total_results": total_results,
            "shown_results": len(result_items),
            "items": result_items,
        }
        _print_json_envelope(
            "search",
            ok=True,
            data=data,
            warnings=warnings,
        )
        return 0

    if not result_items:
        display.status("No results.")
        return 0

    display.search_results(
        query=args.query,
        order=args.order,
        items=result_items,
        total_results=total_results,
    )
    return 0


def _collapse_section_rows(
    section_rows: list[_SectionRow], *, expand_depth: int
) -> list[_SectionRow]:
    if expand_depth >= 4:
        return [cast(_SectionRow, dict(section)) for section in section_rows]

    visible_rows: list[_SectionRow] = []
    visible_parts: list[tuple[str, ...]] = []
    for section in section_rows:
        row = cast(_SectionRow, dict(section))
        parts = _section_path_parts(str(row["section"]))
        if len(parts) <= expand_depth:
            visible_rows.append(row)
            visible_parts.append(parts)
            continue

        for idx in range(len(visible_rows) - 1, -1, -1):
            ancestor_parts = visible_parts[idx]
            if len(ancestor_parts) > len(parts) or parts[: len(ancestor_parts)] != ancestor_parts:
                continue
            visible_rows[idx]["paras"] = int(visible_rows[idx]["paras"]) + int(row["paras"])
            visible_rows[idx]["chars"] = int(visible_rows[idx]["chars"]) + int(row["chars"])
            if (
                not str(visible_rows[idx]["opening_line"]).strip()
                and str(row["opening_line"]).strip()
            ):
                visible_rows[idx]["opening_line"] = str(row["opening_line"])
            break

    for row in visible_rows:
        chars = int(row["chars"])
        words = round(chars / 5) if chars else 0
        row["est_words"] = words
        row["est_read"] = _estimate_read_time(words)
    return visible_rows


def _visible_section_number(
    section_rows: list[_SectionRow],
    *,
    target_section: str,
) -> int | None:
    target_parts = _section_path_parts(target_section)
    best_match: tuple[int, int] | None = None
    for row in section_rows:
        parts = _section_path_parts(str(row["section"]))
        if not parts or len(parts) > len(target_parts):
            continue
        if target_parts[: len(parts)] != parts:
            continue
        candidate = (len(parts), int(row["section_number"]))
        if best_match is None or candidate[0] > best_match[0]:
            best_match = candidate
    return best_match[1] if best_match is not None else None


def _build_section_summary(
    db: Database, book_id: int, *, expand_depth: int | None = None
) -> _SectionSummary | None:
    chunk_records = db.chunk_records(book_id)
    if not chunk_records:
        return None

    book = db.book(book_id)
    title = _single_line(book.title) if book else f"Book {book_id}"
    authors = _single_line(book.authors) if book and book.authors else ""
    language = book.language if book else ""
    issued = book.issued if book else ""
    book_type = book.type if book else ""
    locc = _single_line(book.locc) if book and book.locc else ""
    subjects = _split_semicolon_list(book.subjects) if book else []
    bookshelves = _split_semicolon_list(book.bookshelves) if book else []

    sections: list[_SectionState] = []
    kind_counts: _ChunkCounts = {"heading": 0, "text": 0}
    total_chars = 0
    for rec in chunk_records:
        if rec.kind == "heading":
            kind_counts["heading"] += 1
        elif rec.kind == "text":
            kind_counts["text"] += 1
        total_chars += rec.char_count

        if rec.kind == "heading":
            path = _section_path(rec.div1, rec.div2, rec.div3, rec.div4)
            if path == "(unsectioned opening)":
                path = ""
            sections.append(
                {
                    "heading": _single_line(rec.content) or "(untitled section)",
                    "path": path,
                    "position": rec.position,
                    "paragraphs": 0,
                    "chars": 0,
                    "first_position": rec.position,
                    "opening_candidates": [],
                }
            )
        elif rec.kind == "text" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + rec.char_count
            opening_candidates = sections[-1]["opening_candidates"]
            if len(opening_candidates) < OPENING_PREVIEW_PARAGRAPH_LIMIT:
                opening_candidates.append(rec.content)

    total_chunks = len(chunk_records)
    total_sections = len(sections)
    total_paragraphs = kind_counts.get("text", 0)
    est_words = round(total_chars / 5) if total_chars else 0
    read_time = _estimate_read_time(est_words)

    raw_section_rows: list[_SectionRow] = []
    for idx, sec in enumerate(sections, start=1):
        chars = int(sec["chars"])
        est_words_for_section = round(chars / 5)
        opening_line = _select_section_opening_line(sec["opening_candidates"])
        raw_section_rows.append(
            {
                "section_number": idx,
                "section": str(sec["path"]) or str(sec["heading"]),
                "position": (
                    int(sec["first_position"])
                    if sec.get("first_position") is not None
                    else int(sec["position"])
                ),
                "paras": int(sec["paragraphs"]),
                "chars": chars,
                "est_words": est_words_for_section,
                "est_read": _estimate_read_time(est_words_for_section),
                "opening_line": opening_line,
            }
        )

    visible_section_rows: list[_SectionRow]
    if expand_depth is not None:
        visible_section_rows = _collapse_section_rows(
            raw_section_rows,
            expand_depth=expand_depth,
        )
    else:
        visible_section_rows = [cast(_SectionRow, dict(section)) for section in raw_section_rows]

    total_levels = max(
        (_section_depth(str(row["section"])) for row in raw_section_rows),
        default=0,
    )
    shown_levels = max(
        (_section_depth(str(row["section"])) for row in visible_section_rows),
        default=0,
    )

    opening_section_num: int | None = None
    opening_position: int | None = None
    opening_rows = _opening_rows(db, book_id, 1)
    if opening_rows:
        opening_position = opening_rows[0].position
        opening_section = _section_path(
            opening_rows[0].div1,
            opening_rows[0].div2,
            opening_rows[0].div3,
            opening_rows[0].div4,
        )
        opening_section_num = _visible_section_number(
            visible_section_rows,
            target_section=opening_section,
        )

    opening_example_rows = opening_rows or chunk_records
    search_query = _quick_action_search_query(opening_example_rows)
    search_cmd = f'gutenbit search "{search_query}" --book {book_id}'

    first_section_cmd = ""
    if opening_section_num is not None:
        first_section_cmd = f"gutenbit view {book_id} --section {opening_section_num} --forward 20"

    view_position_cmd = ""
    if opening_position is not None:
        view_position_cmd = f"gutenbit view {book_id} --position {opening_position} --forward 20"

    toc_expand_all_cmd = ""
    if expand_depth is not None and expand_depth < 4:
        toc_expand_all_cmd = f"gutenbit toc {book_id} --expand all"

    view_all_cmd = f"gutenbit view {book_id} --all"

    summary: _SectionSummary = {
        "book": {
            "id": book_id,
            "title": title,
            "authors": authors,
            "language": language,
            "issued": issued,
            "type": book_type,
            "locc": locc,
            "subjects": subjects,
            "bookshelves": bookshelves,
        },
        "overview": {
            "chunks_total": total_chunks,
            "chunk_counts": kind_counts,
            "sections_total": total_sections,
            "sections_shown": len(visible_section_rows),
            "levels_total": total_levels,
            "levels_shown": shown_levels,
            "paragraphs_total": total_paragraphs,
            "chars_total": total_chars,
            "est_words": est_words,
            "est_read_time": read_time,
        },
        "sections": visible_section_rows,
        "quick_actions": {
            "toc_expand_all": toc_expand_all_cmd,
            "search": search_cmd,
            "view_first_section": first_section_cmd,
            "view_by_position": view_position_cmd,
            "view_all": view_all_cmd,
        },
    }
    return summary


def _section_summary_json_payload(summary: _SectionSummary) -> dict[str, Any]:
    json_sections: list[dict[str, Any]] = []
    for sec in summary["sections"]:
        sec_json = dict(sec)
        sec_json.pop("position", None)
        sec_json["opening_line"] = _preview(sec["opening_line"], JSON_OPENING_LINE_PREVIEW_CHARS)
        json_sections.append(sec_json)

    return {
        "book": dict(summary["book"]),
        "overview": {
            **summary["overview"],
            "chunk_counts": dict(summary["overview"]["chunk_counts"]),
        },
        "sections": json_sections,
        "quick_actions": dict(summary["quick_actions"]),
    }


def _render_section_summary(db: Database, book_id: int, *, expand_depth: int) -> int:
    summary = _build_section_summary(db, book_id, expand_depth=expand_depth)
    if summary is None:
        _display().error(_no_chunks_display_message(db, book_id))
        return 1
    _display().section_summary(summary)
    return 0


def _print_passage(
    payload: dict[str, Any],
    *,
    action_hints: dict[str, str] | None = None,
    footer_stats: list[str] | None = None,
) -> None:
    _display().passage(payload, action_hints=action_hints, footer_stats=footer_stats)


def _view_action_hints(book_id: int, summary: _SectionSummary | None) -> dict[str, str]:
    quick_actions: _QuickActions = (
        summary["quick_actions"]
        if summary is not None
        else {
            "toc_expand_all": "",
            "search": "",
            "view_first_section": "",
            "view_by_position": "",
            "view_all": "",
        }
    )
    return {
        "toc": f"gutenbit toc {book_id}",
        "view_first_section": quick_actions["view_first_section"],
        "view_all": quick_actions["view_all"],
        "search": quick_actions["search"],
    }


def _resolve_toc_book_id(
    db: Database,
    requested_id: int,
    *,
    args: argparse.Namespace,
    display: CliDisplay,
    as_json: bool,
) -> tuple[int | None, list[str]]:
    """Resolve a toc request to stored text, auto-adding the canonical book when needed."""
    if db.has_text(requested_id):
        return requested_id, []

    catalog = _load_catalog(args, display=display, as_json=as_json)
    rec = catalog.get(requested_id)
    if rec is None:
        return requested_id, []

    title = _single_line(rec.title)
    if rec.id != requested_id and not as_json:
        display.status(f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)")

    state = db.text_states([rec.id]).get(rec.id, TextState(has_text=False, has_current_text=False))
    if state.has_current_text:
        return rec.id, []

    statuses, errors = _process_books_for_ingest(
        db,
        [rec],
        delay=DEFAULT_DOWNLOAD_DELAY,
        as_json=as_json,
        display=display,
        failure_action="add",
        force=False,
    )
    if statuses.get(rec.id) == "failed":
        return None, errors
    return rec.id, []


def _cmd_toc(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    expand_depth = _toc_expand_depth(args.expand)
    with Database(args.db) as db:
        resolved_book_id, ingest_errors = _resolve_toc_book_id(
            db,
            args.book,
            args=args,
            display=display,
            as_json=as_json,
        )
        if resolved_book_id is None:
            if as_json:
                _print_json_envelope(
                    "toc",
                    ok=False,
                    data={JSON_BOOK_ID_KEY: args.book},
                    errors=ingest_errors or [f"Failed to add book {args.book}."],
                )
            return 1
        if as_json:
            summary = _build_section_summary(db, resolved_book_id, expand_depth=expand_depth)
            if summary is None:
                return _command_error(
                    "toc",
                    _no_chunks_message(db, resolved_book_id),
                    as_json=True,
                    data={JSON_BOOK_ID_KEY: args.book},
                )
            _print_json_envelope(
                "toc",
                ok=True,
                data={
                    JSON_BOOK_ID_KEY: args.book,
                    "expand": args.expand,
                    "toc": _section_summary_json_payload(summary),
                },
            )
            return 0
        return _render_section_summary(db, resolved_book_id, expand_depth=expand_depth)


def _cmd_view(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    display = _display()
    selected = int(args.position is not None) + int(args.section is not None)
    if selected > 1:
        return _command_error(
            "view",
            "Choose at most one selector: --position or --section.",
            as_json=as_json,
        )
    if args.forward is not None and args.forward <= 0:
        return _command_error("view", "--forward must be > 0.", as_json=as_json)
    if args.radius is not None and args.radius < 0:
        return _command_error("view", "--radius must be >= 0.", as_json=as_json)
    shapes_selected = sum(
        int(value)
        for value in [
            args.forward is not None,
            args.radius is not None,
            args.all,
        ]
    )
    if shapes_selected > 1:
        return _command_error(
            "view",
            (
                "Choose one retrieval shape: --forward for forward reading, "
                "--radius for a surrounding passage window, or --all for a full book or section."
            ),
            as_json=as_json,
        )
    if args.radius is not None and selected == 0:
        return _command_error(
            "view",
            "--radius requires --position or --section.",
            as_json=as_json,
        )
    if args.all and args.position is not None:
        return _command_error(
            "view",
            "--all can be used with a book or section, not with --position.",
            as_json=as_json,
        )

    def _effective_forward(default: int) -> int:
        return args.forward if args.forward is not None else default

    radius = args.radius
    requested_forward = (
        None if radius is not None or args.all else _effective_forward(DEFAULT_VIEW_FORWARD)
    )
    requested_all = True if args.all else None
    with Database(args.db) as db:
        section_number_for = _section_number_lookup(db)
        book_record = db.book(args.book)
        title = _single_line(book_record.title) if book_record else f"Book {args.book}"
        author = _single_line(book_record.authors) if book_record and book_record.authors else ""

        def _view_payload(
            *,
            section: str | None,
            section_number: int | None,
            position: int | None,
            forward: int | None,
            radius: int | None,
            all_scope: bool | None,
            content: str = "",
            extras: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return _passage_payload(
                book_id=args.book,
                title=title,
                author=author,
                section=section,
                section_number=section_number,
                position=position,
                forward=forward,
                radius=radius,
                all_scope=all_scope,
                content=content,
                extras=extras,
            )

        def _view_footer_stats(rows: list[ChunkRecord]) -> list[str]:
            text_rows = [row for row in rows if row.kind == "text"]
            chars = sum(row.char_count for row in text_rows)
            words = round(chars / 5) if chars else 0
            return format_summary_stats(
                paragraphs=len(text_rows),
                words=words,
                read=_estimate_read_time(words),
            )

        if args.position is not None:
            anchor = db.chunk_by_position(args.book, args.position)
            if anchor is None:
                return _command_error(
                    "view",
                    f"No chunk found at position {args.position} in book {args.book}.",
                    as_json=as_json,
                    display_message=(
                        f"No chunk found at position {args.position} in "
                        f"{_book_id_ref(args.book, capitalize=False)}."
                    ),
                    data=_view_payload(
                        section=None,
                        section_number=None,
                        position=args.position,
                        forward=requested_forward,
                        radius=radius,
                        all_scope=requested_all,
                    ),
                )
            anchor_section = _section_path(anchor.div1, anchor.div2, anchor.div3, anchor.div4)
            anchor_section_number = section_number_for(args.book, anchor_section)
            if radius is not None:
                rows = db.chunk_window(args.book, args.position, around=radius)
                forward = None
                all_scope = None
            else:
                forward = _effective_forward(DEFAULT_VIEW_FORWARD)
                all_scope = None
                rows = [
                    row for row in db.chunk_records(args.book) if row.position >= args.position
                ]
                rows = rows[:forward]
            record = _view_payload(
                section=anchor_section,
                section_number=anchor_section_number,
                position=args.position,
                forward=forward,
                radius=radius,
                all_scope=all_scope,
                content=_joined_chunk_text(rows),
            )
            if as_json:
                _print_json_envelope("view", ok=True, data=record)
                return 0
            _print_passage(record, footer_stats=_view_footer_stats(rows))
            return 0

        if args.section is not None:
            section_query = args.section.strip()
            if not section_query:
                return _command_error(
                    "view",
                    "--section must not be empty.",
                    as_json=as_json,
                    data=_view_payload(
                        section=None,
                        section_number=None,
                        position=None,
                        forward=requested_forward,
                        radius=radius,
                        all_scope=requested_all,
                    ),
                )

            section_number: int | None = None
            resolved_section = section_query
            if section_query.isdigit():
                section_number = int(section_query)
                if section_number <= 0:
                    return _command_error(
                        "view",
                        "--section number must be >= 1.",
                        as_json=as_json,
                        data=_view_payload(
                            section=section_query,
                            section_number=None,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                        ),
                    )
                summary = _build_section_summary(db, args.book)
                if summary is None:
                    return _command_error(
                        "view",
                        _no_chunks_message(db, args.book),
                        as_json=as_json,
                        display_message=_no_chunks_display_message(db, args.book),
                        data=_view_payload(
                            section=section_query,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                        ),
                    )
                raw_sections = summary["sections"]
                if section_number > len(raw_sections):
                    message = (
                        f"Section {section_number} is out of range for book "
                        f"{args.book} (max {len(raw_sections)})."
                    )
                    display_message = (
                        f"Section {section_number} is out of range for "
                        f"{_book_id_ref(args.book, capitalize=False)} "
                        f"(max {len(raw_sections)})."
                    )
                    examples = _section_examples(db, args.book)
                    if as_json:
                        return _command_error(
                            "view",
                            message,
                            as_json=True,
                            data=_view_payload(
                                section=section_query,
                                section_number=section_number,
                                position=None,
                                forward=requested_forward,
                                radius=radius,
                                all_scope=requested_all,
                                extras={
                                    "max_section_number": len(raw_sections),
                                    "available_sections": examples,
                                    "tip": f"gutenbit toc {args.book}",
                                },
                            ),
                        )
                    display.examples(
                        display_message,
                        examples=examples,
                        tip=f"gutenbit toc {args.book}",
                    )
                    return 1
                selected_section = raw_sections[section_number - 1]
                if not isinstance(selected_section, dict):
                    return _command_error(
                        "view",
                        (
                            f"Unable to resolve section number {section_number} "
                            f"for book {args.book}."
                        ),
                        as_json=as_json,
                        display_message=(
                            f"Unable to resolve section number {section_number} "
                            f"for {_book_id_ref(args.book, capitalize=False)}."
                        ),
                        data=_view_payload(
                            section=section_query,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                            extras={"tip": f"gutenbit toc {args.book}"},
                        ),
                    )
                resolved_section = selected_section["section"].strip()
                if not resolved_section:
                    return _command_error(
                        "view",
                        (
                            f"Unable to resolve section number {section_number} "
                            f"for book {args.book}."
                        ),
                        as_json=as_json,
                        display_message=(
                            f"Unable to resolve section number {section_number} "
                            f"for {_book_id_ref(args.book, capitalize=False)}."
                        ),
                        data=_view_payload(
                            section=section_query,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                            extras={"tip": f"gutenbit toc {args.book}"},
                        ),
                    )
            else:
                section_number = section_number_for(args.book, resolved_section)
                try:
                    matched_section = _canonical_section_match(
                        _build_section_summary(db, args.book), resolved_section
                    )
                except ValueError as exc:
                    return _command_error(
                        "view",
                        f"Invalid section selector: {exc}.",
                        as_json=as_json,
                        data=_view_payload(
                            section=section_query,
                            section_number=None,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                        ),
                    )
                if matched_section is not None:
                    resolved_section, section_number = matched_section

            rows = db.chunks_by_div(args.book, resolved_section, limit=0)
            if not rows:
                examples = _section_examples(db, args.book)
                message = f"No chunks found for book {args.book} under section '{section_query}'."
                display_message = (
                    f"No chunks found for {_book_id_ref(args.book, capitalize=False)} "
                    f"under section '{section_query}'."
                )
                if as_json:
                    return _command_error(
                        "view",
                        message,
                        as_json=True,
                        data=_view_payload(
                            section=resolved_section,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                            extras={
                                "available_sections": examples,
                                "tip": f"gutenbit toc {args.book}",
                            },
                        ),
                    )
                display.examples(
                    display_message,
                    examples=examples,
                    tip=f"gutenbit toc {args.book}",
                )
                return 1
            anchor = rows[0]
            if radius is not None:
                rows = db.chunk_window(args.book, anchor.position, around=radius)
                forward = None
                all_scope = None
            elif args.all:
                forward = None
                all_scope = True
            else:
                forward = _effective_forward(DEFAULT_VIEW_FORWARD)
                all_scope = None
                rows = _section_reading_window(rows, text_passages=forward)
            record = _view_payload(
                section=resolved_section,
                section_number=section_number,
                position=anchor.position,
                forward=forward,
                radius=radius,
                all_scope=all_scope,
                content=_joined_chunk_text(rows),
            )
            if as_json:
                _print_json_envelope("view", ok=True, data=record)
                return 0
            _print_passage(record, footer_stats=_view_footer_stats(rows))
            return 0

        forward = _effective_forward(DEFAULT_OPENING_CHUNK_COUNT)
        summary = _build_section_summary(db, args.book)
        action_hints = _view_action_hints(args.book, summary)
        first_section = summary["sections"][0] if summary and summary["sections"] else None
        if args.all:
            rows = db.chunk_records(args.book)
            if not rows:
                return _command_error(
                    "view",
                    _no_chunks_message(db, args.book),
                    as_json=as_json,
                    display_message=_no_chunks_display_message(db, args.book),
                    data=_view_payload(
                        section=first_section["section"] if first_section else None,
                        section_number=(
                            first_section["section_number"] if first_section else None
                        ),
                        position=first_section["position"] if first_section else None,
                        forward=None,
                        radius=None,
                        all_scope=True,
                    ),
                )
            anchor = rows[0]
            anchor_section = _section_path(anchor.div1, anchor.div2, anchor.div3, anchor.div4)
            record = _view_payload(
                section=anchor_section,
                section_number=section_number_for(args.book, anchor_section),
                position=anchor.position,
                forward=None,
                radius=None,
                all_scope=True,
                content=_joined_chunk_text(rows),
            )
            if as_json:
                _print_json_envelope(
                    "view",
                    ok=True,
                    data={**record, "action_hints": action_hints},
                )
                return 0
            display.passage(
                record,
                action_hints=action_hints,
                footer_stats=_view_footer_stats(rows),
            )
            return 0

        rows = _opening_rows(db, args.book, forward)
        if not rows:
            return _command_error(
                "view",
                _no_chunks_message(db, args.book),
                as_json=as_json,
                display_message=_no_chunks_display_message(db, args.book),
                data=_view_payload(
                    section=first_section["section"] if first_section else None,
                    section_number=(first_section["section_number"] if first_section else None),
                    position=first_section["position"] if first_section else None,
                    forward=forward,
                    radius=None,
                    all_scope=None,
                ),
            )
        anchor = rows[0]
        anchor_section = _section_path(anchor.div1, anchor.div2, anchor.div3, anchor.div4)
        record = _view_payload(
            section=anchor_section,
            section_number=section_number_for(args.book, anchor_section),
            position=anchor.position,
            forward=forward,
            radius=None,
            all_scope=None,
            content=_joined_chunk_text(rows),
        )
        if as_json:
            _print_json_envelope(
                "view",
                ok=True,
                data={**record, "action_hints": action_hints},
            )
            return 0
        display.passage(
            record,
            action_hints=action_hints,
            footer_stats=_view_footer_stats(rows),
        )
        return 0


_COMMANDS = {
    "catalog": _cmd_catalog,
    "add": _cmd_add,
    "remove": _cmd_remove,
    "books": _cmd_books,
    "search": _cmd_search,
    "toc": _cmd_toc,
    "view": _cmd_view,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stdout,
        )
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)

    # Suppress verbose transport logs unless users explicitly inspect networking.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if not args.command:
        parser.print_help()
        return 0

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return handler(args)
    except KeyboardInterrupt:
        if getattr(args, "json", False):
            _print_json_envelope(args.command, ok=False, errors=["Interrupted."])
        else:
            _display().error("\nInterrupted.")
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json_envelope(args.command, ok=False, errors=[f"Error: {exc}"])
            if args.verbose:
                import traceback

                traceback.print_exc()
        else:
            _display().error(f"Error: {exc}", err=True)
            if args.verbose:
                import traceback

                traceback.print_exc()
        return 1


def _entry_point() -> None:
    """Console-scripts entry point."""
    sys.exit(main())
