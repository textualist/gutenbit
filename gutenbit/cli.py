"""Command-line interface for gutenbit."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, TypedDict

from gutenbit.catalog import Catalog
from gutenbit.db import ChunkRecord, Database, _div_parts_match, _normalize_div_segment

DEFAULT_DB = "gutenbit.db"
JSON_OPENING_LINE_PREVIEW_CHARS = 140
DEFAULT_OPENING_CHUNK_COUNT = 3
DEFAULT_VIEW_FORWARD = 1
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
    opening_line: str


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
    search: str
    view_first_section: str
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
    code: int = 1,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
) -> int:
    if as_json:
        _print_json_envelope(command, ok=False, data=data, warnings=warnings, errors=[message])
    else:
        print(message)
    return code


def _no_chunks_message(db: Database, book_id: int) -> str:
    """Return a descriptive error for a book with no chunks."""
    if db.book(book_id) is None:
        return f"Book {book_id} is not in the database. Use 'gutenbit add {book_id}' to add it."
    return f"No chunks found for book {book_id}."


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _single_line(text: str) -> str:
    """Collapse all whitespace so tabular CLI output stays on one line."""
    return " ".join(text.split())


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


def _format_int(value: int) -> str:
    return f"{value:,}"


def _section_path(*levels: str) -> str:
    return " / ".join(level for level in levels if level) or "(unsectioned opening)"


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
    book: int,
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
        "book": book,
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
        f"book={payload['book']}",
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


def _build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(
        prog="gutenbit",
        formatter_class=fmt,
        description="Project Gutenberg ETL — download, chunk, and search public-domain books.",
        epilog="""\
typical workflow:
  1. gutenbit catalog --author Dickens         # find book IDs
  2. gutenbit add 46 730                       # download & store
  3. gutenbit books                            # list stored books
  4. gutenbit toc 46                           # inspect structure / sections
  5. gutenbit view 46 --section 3 --forward 20  # read part of a book
  6. gutenbit search "Marley's ghost" --book 46 # find relevant passages

chunk kinds:  heading, text
section hierarchy:  level1 > level2 > level3 > level4  (compacted from shallowest heading)

all data is stored in a local SQLite database (default: gutenbit.db).""",
    )
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path (default: %(default)s)")
    p.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = p.add_subparsers(dest="command")

    # --- catalog ---
    cat = sub.add_parser(
        "catalog",
        formatter_class=fmt,
        help="search the Project Gutenberg catalog",
        description="Search the full Project Gutenberg catalog (downloaded on each run).",
        epilog="""\
examples:
  gutenbit catalog --author Tolstoy
  gutenbit catalog --title "War and Peace"
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
    _add_global_args(cat)

    # --- add ---
    add = sub.add_parser(
        "add",
        formatter_class=fmt,
        help="download and store books by PG id",
        description=(
            "Download books from Project Gutenberg by ID, parse HTML into chunks, "
            "and store everything in the SQLite database. Already-downloaded books "
            "are skipped."
        ),
        epilog="""\
examples:
  gutenbit add 2600                     # War and Peace
  gutenbit add 46 730 967               # multiple books
  gutenbit add 2600 --delay 2.0         # polite crawling""",
    )
    add.add_argument("book_ids", nargs="+", type=int, help="Project Gutenberg book IDs")
    add.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="seconds between downloads (default: 1.0)",
    )
    add.add_argument("--json", action="store_true", help="output as JSON")
    _add_global_args(add)

    # --- delete ---
    de = sub.add_parser(
        "delete",
        formatter_class=fmt,
        help="delete stored books by PG id",
        description=(
            "Delete previously added books from the SQLite database, including "
            "their reconstructed text and all chunks."
        ),
        epilog="""\
examples:
  gutenbit delete 46
  gutenbit delete 46 730 967
  gutenbit delete 2600 --db my.db

if a book ID is not present, a warning is printed and exit code is 1.""",
    )
    de.add_argument("book_ids", nargs="+", type=int, help="Project Gutenberg book IDs")
    de.add_argument("--json", action="store_true", help="output as JSON")
    _add_global_args(de)

    # --- books ---
    bk = sub.add_parser(
        "books",
        formatter_class=fmt,
        help="list books stored in the database",
        description="List all books that have been added to the database.",
        epilog="""\
examples:
  gutenbit books
  gutenbit books --json
  gutenbit books --db my.db

output columns:  ID  AUTHORS  TITLE""",
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
            "and other punctuation just work. Use --raw for advanced FTS5 "
            "syntax (AND/OR/NOT, prefix*, NEAR, parentheses)."
        ),
        epilog="""\
examples:
  gutenbit search "ghost"                                   # simple search
  gutenbit search "don't stop"                              # punctuation just works
  gutenbit search "half-hour"                               # hyphens just work
  gutenbit search "may it be" --phrase                      # exact phrase match
  gutenbit search "ghost OR spirit" --raw                   # FTS5 boolean query
  gutenbit search "(ghost OR spirit) AND NOT haunt*" --raw  # advanced FTS5
  gutenbit search "battle" --book 2600                      # restrict to one book
  gutenbit search "battle" --section "BOOK ONE"             # restrict to a section
  gutenbit search "door" --mode first                       # reading order (earliest)
  gutenbit search "door" --mode last                        # reverse reading order
  gutenbit search "ghost" --radius 2                        # show surrounding passage
  gutenbit search "ghost" --limit 3                         # limit the result set
  gutenbit search "battle" --count                          # just show match count
  gutenbit search "battle" --json                           # JSON output

query modes:
  (default)  plain text — punctuation is auto-escaped, words are AND'd
  --phrase   exact phrase — word order and adjacency must match exactly
  --raw      FTS5 syntax — AND, OR, NOT, NEAR(), prefix*, "phrases", (groups)

mode ordering:
  ranked  BM25 rank, then book, then position (default)
  first   book ascending, then position ascending
  last    book descending, then position descending

tip: use 'gutenbit toc <id>' first to see a book's structure, then
     narrow searches with --book and --section.""",
    )
    se.add_argument("query", help="search query (plain text by default; see --raw, --phrase)")
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
        "--mode",
        choices=["ranked", "first", "last"],
        default="ranked",
        help=(
            "search mode: ranked (BM25); "
            "first (book asc + position asc); "
            "last (book desc + position desc)"
        ),
    )
    se.add_argument("--author", help="filter results by author (substring match)")
    se.add_argument("--title", help="filter results by title (substring match)")
    se.add_argument("--book", type=int, help="restrict to a single book by PG ID")
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
        help="show structural table of contents for a stored book",
        description=(
            "Show a compact structural summary of one stored book, including "
            "section numbering for ergonomic section selection in `view`."
        ),
        epilog="""\
examples:
  gutenbit toc 2600
  gutenbit toc 2600 --json

section numbers in this output can be passed to:
  gutenbit view 2600 --section <NUMBER>""",
    )
    tc.add_argument("book", type=int, help="Project Gutenberg book ID")
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
            "--radius for surrounding passage windows, or --all for a full book or section."
        ),
        epilog="""\
examples:
  gutenbit toc 2600                                  # inspect structure first
  gutenbit view 2600                                 # first structural section + quick actions
  gutenbit view 2600 --all                           # full reconstructed text
  gutenbit view 2600 --section 3                     # first passage in section 3
  gutenbit view 2600 --section 3 --all               # full section
  gutenbit view 2600 --section 3 --forward 20        # first 20 passages in section 3
  gutenbit view 2600 --section 3 --radius 2          # surrounding passage around the section start
  gutenbit view 2600 --position 12345                # passage at position 12345
  gutenbit view 2600 --position 12345 --forward 20   # continue reading from position
  gutenbit view 2600 --position 12345 --radius 2     # surrounding passage around position
  gutenbit view 2600 --section "BOOK I/CHAPTER I" --forward 10 --json

selectors (choose at most one):
  --position <n> | --section <SECTION_SELECTOR>
""",
    )
    vw.add_argument("book", type=int, help="Project Gutenberg book ID")
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
        help="read the full selected scope (whole book or whole section)",
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
    if args.limit <= 0:
        return _command_error("catalog", "--limit must be > 0.", as_json=as_json)

    if not as_json:
        print("Fetching catalog from Project Gutenberg (English text corpus)…")
    catalog = Catalog.fetch()
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
            "total_matches": len(results),
            "shown": len(shown),
            "items": [_book_payload(book) for book in shown],
        }
        _print_json_envelope("catalog", ok=True, data=data)
        return 0

    if not shown:
        print("No books found.")
        return 0

    print(f"  {'ID':>6}  {'AUTHORS':<40s}  TITLE")
    print(f"  {'------':>6}  {'----------------------------------------':<40s}  -----")
    for b in shown:
        authors = _summarize_semicolon_list(b.authors, max_items=2)[:40]
        title = _single_line(b.title)
        print(f"  {b.id:>6}  {authors:<40s}  {title}")
    if len(results) > args.limit:
        print(f"  … and {len(results) - args.limit} more (use --limit to show more)")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
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

    if not as_json:
        print("Fetching catalog…")
    catalog = Catalog.fetch()
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
                print(f"  warning: {warning}")
            continue
        title = _single_line(rec.title)
        if rec.id != requested_id and not as_json:
            print(f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)")
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

    canonical_statuses: dict[int, str] = {}
    errors: list[str] = []
    with Database(args.db) as db:
        for book in books:
            title = _single_line(book.title)
            was_current = db.has_current_text(book.id)
            if was_current:
                canonical_statuses[book.id] = "skipped_current"
                if not as_json:
                    print(f"  skipping {book.id}: {title} (already downloaded)")
                continue
            was_present = db.has_text(book.id)
            target_status = "reprocessed" if was_present else "added"
            if was_present:
                if not as_json:
                    print(f"  reprocessing {book.id}: {title} (chunker updated)…")
            else:
                if not as_json:
                    print(f"  adding {book.id}: {title}…")
            if as_json:
                previous_disable = logging.root.manager.disable
                logging.disable(logging.CRITICAL)
                try:
                    db.ingest([book], delay=args.delay)
                finally:
                    logging.disable(previous_disable)
            else:
                db.ingest([book], delay=args.delay)

            if db.has_current_text(book.id):
                canonical_statuses[book.id] = target_status
            else:
                canonical_statuses[book.id] = "failed"
                failure = f"Failed to add {book.id}: {title}"
                errors.append(failure)
                if not as_json:
                    print(f"  failed {book.id}: {title}")

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
        print(f"Completed with {len(errors)} failure(s). Database: {Path(args.db).resolve()}")
        return 1
    print(f"Done. Database: {Path(args.db).resolve()}")
    return 0


def _cmd_books(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    with Database(args.db) as db:
        books = db.books()
    if not books:
        if as_json:
            _print_json_envelope(
                "books",
                ok=True,
                data={"count": 0, "items": []},
            )
        else:
            print("No books stored yet. Use 'gutenbit add <id> ...' to add some.")
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
    print(f"  {'ID':>6}  {'AUTHORS':<40s}  TITLE")
    print(f"  {'------':>6}  {'----------------------------------------':<40s}  -----")
    for b in books:
        authors = _summarize_semicolon_list(b.authors, max_items=2)[:40]
        title = _single_line(b.title)
        print(f"  {b.id:>6}  {authors:<40s}  {title}")
    print(f"\n{len(books)} book(s) stored in {args.db}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    any_missing = False
    deleted_count = 0
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with Database(args.db) as db:
        for book_id in args.book_ids:
            deleted = db.delete_book(book_id)
            if not deleted:
                message = f"No book found for id {book_id}."
                errors.append(message)
                results.append({"book_id": book_id, "status": "missing"})
                if not as_json:
                    print(message)
                any_missing = True
            else:
                deleted_count += 1
                results.append({"book_id": book_id, "status": "deleted"})
                if not as_json:
                    print(f"Deleted book {book_id} from {args.db}.")
    if as_json:
        _print_json_envelope(
            "delete",
            ok=not any_missing,
            data={
                "db": str(Path(args.db).resolve()),
                "deleted_count": deleted_count,
                "missing_count": len(args.book_ids) - deleted_count,
                "results": results,
            },
            errors=errors,
        )
    return 1 if any_missing else 0


def _cmd_search(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
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
    # default auto-escapes plain text so punctuation just works.
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

    # --count uses a large limit to get the full count.
    limit = 10_000_000 if args.count else args.limit

    warnings: list[str] = []
    with Database(args.db) as db:
        section_number_for = _section_number_lookup(db)

        if args.book is not None and not db.has_text(args.book):
            warning = f"Book {args.book} is not in the database."
            warnings.append(warning)
            if not as_json:
                print(f"warning: {warning}")

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
                    )
                sections = summary["sections"]
                if section_number > len(sections):
                    return _command_error(
                        "search",
                        f"Section {section_number} is out of range "
                        f"(book {args.book} has {len(sections)} sections).",
                        as_json=as_json,
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

        try:
            results = db.search(
                search_query,
                author=args.author,
                title=args.title,
                book_id=args.book,
                div_path=div_path,
                mode=args.mode,
                limit=limit,
            )
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
                    "filters": {
                        "author": args.author,
                        "title": args.title,
                        "book": args.book,
                        "section": section_arg,
                    },
                    "order": args.mode,
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
                    book=result.book_id,
                    title=_single_line(result.title),
                    author=_single_line(result.authors),
                    section=section,
                    section_number=section_number_for(result.book_id, section),
                    position=result.position,
                    forward=None,
                    radius=radius,
                    content=content,
                    extras={
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
                    "filters": {
                        "author": args.author,
                        "title": args.title,
                        "book": args.book,
                        "section": section_arg,
                    },
                    "count": len(results),
                },
                warnings=warnings,
            )
        else:
            print(len(results))
        return 0

    if as_json:
        data = {
            "query": {
                "raw": args.query,
                "fts": search_query,
                "mode": query_mode,
            },
            "filters": {
                "author": args.author,
                "title": args.title,
                "book": args.book,
                "section": section_arg,
            },
            "order": args.mode,
            "limit": limit,
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
        print("No results.")
        return 0

    print(f"query={args.query!r}  order={args.mode}  results={len(result_items)}")
    for idx, item in enumerate(result_items, start=1):
        print(f"\n{idx:>2}. {_passage_header(item)}")
        print(f"    score={item['score']:.2f}")
        print(_indent_block(item["content"]))
        print()
    print(f"{len(result_items)} result(s)")
    return 0


def _build_section_summary(db: Database, book_id: int) -> _SectionSummary | None:
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
                    "opening_line": "",
                }
            )
        elif rec.kind == "text" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + rec.char_count
            if not sections[-1]["opening_line"]:
                sections[-1]["opening_line"] = _single_line(rec.content)

    total_chunks = len(chunk_records)
    total_sections = len(sections)
    total_paragraphs = kind_counts.get("text", 0)
    est_words = round(total_chars / 5) if total_chars else 0
    read_time = _estimate_read_time(est_words)

    search_cmd = f"gutenbit search <query> --book {book_id}"
    section_rows: list[_SectionRow] = []
    for idx, sec in enumerate(sections, start=1):
        chars = int(sec["chars"])
        est_words_for_section = round(chars / 5)
        section_rows.append(
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
                "opening_line": str(sec["opening_line"]),
            }
        )

    opening_section_num: int | None = None
    opening_rows = _opening_rows(db, book_id, 1)
    if opening_rows:
        opening_section = _section_path(
            opening_rows[0].div1,
            opening_rows[0].div2,
            opening_rows[0].div3,
            opening_rows[0].div4,
        )
        for section in section_rows:
            if section["section"] == opening_section:
                opening_section_num = section["section_number"]
                break

    first_section_cmd = ""
    if opening_section_num is not None:
        first_section_cmd = f"gutenbit view {book_id} --section {opening_section_num} --forward 20"

    view_all_cmd = f"gutenbit view {book_id} --all"

    return {
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
            "paragraphs_total": total_paragraphs,
            "chars_total": total_chars,
            "est_words": est_words,
            "est_read_time": read_time,
        },
        "sections": section_rows,
        "quick_actions": {
            "search": search_cmd,
            "view_first_section": first_section_cmd,
            "view_all": view_all_cmd,
        },
    }


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


def _render_section_summary(db: Database, book_id: int) -> int:
    summary = _build_section_summary(db, book_id)
    if summary is None:
        print(_no_chunks_message(db, book_id))
        return 1

    book = summary["book"]
    overview = summary["overview"]
    sections = summary["sections"]
    quick_actions = summary["quick_actions"]

    authors = book["authors"]
    subjects = _summarize_semicolon_list(";".join(book["subjects"]), max_items=5)
    shelves = _summarize_semicolon_list(";".join(book["bookshelves"]), max_items=7)

    book_rows: list[tuple[str, str]] = []
    book_rows.append(("Title", str(book["title"])))
    book_rows.append(("Gutenberg ID", str(book_id)))
    if authors:
        book_rows.append(("Authors", authors))
    if book["language"]:
        book_rows.append(("Language", book["language"]))
    if book["issued"]:
        book_rows.append(("Issued", book["issued"]))
    if book["type"]:
        book_rows.append(("Type", book["type"]))
    if book["locc"]:
        book_rows.append(("LoCC", book["locc"]))
    if subjects:
        book_rows.append(("Subjects", subjects))
    if shelves:
        book_rows.append(("Shelves", shelves))

    _print_block_header("Book")
    _print_key_value_table(book_rows, show_header=False)

    _print_block_header("Overview")
    _print_table(
        [
            "Sections",
            "Paras",
            "Chars",
            "Est words",
            "Est read",
        ],
        [
            [
                _format_int(overview["chunk_counts"]["heading"]),
                _format_int(overview["chunk_counts"]["text"]),
                _format_int(overview["chars_total"]),
                _format_int(overview["est_words"]),
                overview["est_read_time"],
            ]
        ],
        right_align=set(range(5)),
    )

    _print_block_header("Contents")
    if not sections:
        print("  (no headings found)")
    else:
        number_values = [str(sec["section_number"]) for sec in sections]
        section_values = [str(sec["section"]) for sec in sections]
        paras_values = [_format_int(sec["paras"]) for sec in sections]
        char_values = [_format_int(sec["chars"]) for sec in sections]
        est_word_values = [_format_int(sec["est_words"]) for sec in sections]
        est_read_values = [str(sec["est_read"]) for sec in sections]
        opening_values = [str(sec["opening_line"]) or "-" for sec in sections]

        number_label = "Section #"
        number_width = max(len(number_label), max(len(v) for v in number_values))
        section_width = min(40, max(len("Section"), max(len(v) for v in section_values)))
        paras_width = max(len("Paras"), max(len(v) for v in paras_values))
        chars_width = max(len("Chars"), max(len(v) for v in char_values))
        est_words_width = max(len("Est words"), max(len(v) for v in est_word_values))
        est_read_width = max(len("Est read"), max(len(v) for v in est_read_values))
        opening_width = min(56, max(len("Opening"), max(len(v) for v in opening_values)))

        print(
            f" {number_label:>{number_width}}  {'Section':<{section_width}}  "
            f"{'Paras':>{paras_width}}  {'Chars':>{chars_width}}  "
            f"{'Est words':>{est_words_width}}  {'Est read':>{est_read_width}}  "
            f"{'Opening':<{opening_width}}"
        )
        print(
            f" {'-' * number_width}  {'-' * section_width}  "
            f"{'-' * paras_width}  {'-' * chars_width}  "
            f"{'-' * est_words_width}  {'-' * est_read_width}  {'-' * opening_width}"
        )

        for sec in sections:
            number = str(sec["section_number"])
            section_label = sec["section"]
            if len(section_label) > section_width:
                section_label = _truncate_section_label(section_label, section_width)
            paragraphs = _format_int(sec["paras"])
            chars = _format_int(sec["chars"])
            est_words = _format_int(sec["est_words"])
            est_read = sec["est_read"]
            opening = sec["opening_line"] or "-"
            if len(opening) > opening_width:
                keep = max(1, opening_width - 3)
                opening = opening[:keep] + "..."
            print(
                f" {number:>{number_width}}  {section_label:<{section_width}}  "
                f"{paragraphs:>{paras_width}}  {chars:>{chars_width}}  "
                f"{est_words:>{est_words_width}}  {est_read:>{est_read_width}}  "
                f"{opening:<{opening_width}}"
            )

    print("\nQuick actions")
    print(f"  {quick_actions['search']}")
    if quick_actions["view_first_section"]:
        print(f"  {quick_actions['view_first_section']}")
    if quick_actions["view_all"]:
        print(f"  {quick_actions['view_all']}")
    return 0


def _print_passage(payload: dict[str, Any]) -> None:
    print(_passage_header(payload))
    print()
    print(payload["content"])

def _view_action_hints(book_id: int, summary: _SectionSummary | None) -> dict[str, str]:
    quick_actions: _QuickActions = (
        summary["quick_actions"]
        if summary is not None
        else {
            "search": "",
            "view_first_section": "",
            "view_all": "",
        }
    )
    return {
        "toc": f"gutenbit toc {book_id}",
        "view_first_section": quick_actions["view_first_section"],
        "view_all": quick_actions["view_all"],
        "search": quick_actions["search"],
    }


def _print_action_hints(action_hints: dict[str, str]) -> None:
    print("\nQuick actions")
    for key in [
        "toc",
        "view_first_section",
        "view_all",
        "search",
    ]:
        cmd = action_hints.get(key, "")
        if cmd:
            print(f"  {cmd}")


def _cmd_toc(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    with Database(args.db) as db:
        if as_json:
            summary = _build_section_summary(db, args.book)
            if summary is None:
                return _command_error(
                    "toc",
                    _no_chunks_message(db, args.book),
                    as_json=True,
                    data={"book": args.book},
                )
            _print_json_envelope(
                "toc",
                ok=True,
                data={
                    "book": args.book,
                    "toc": _section_summary_json_payload(summary),
                },
            )
            return 0
        return _render_section_summary(db, args.book)


def _cmd_view(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
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
        None
        if radius is not None or args.all
        else _effective_forward(DEFAULT_VIEW_FORWARD)
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
                book=args.book,
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

        if args.position is not None:
            anchor = db.chunk_by_position(args.book, args.position)
            if anchor is None:
                return _command_error(
                    "view",
                    f"No chunk found at position {args.position} in book {args.book}.",
                    as_json=as_json,
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
                    row
                    for row in db.chunk_records(args.book)
                    if row.position >= args.position
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
            _print_passage(record)
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
                    print(message)
                    if examples:
                        print("Available sections include:")
                        for section in examples:
                            print(f"  {section}")
                    print(f"Tip: run `gutenbit toc {args.book}` to list all sections.")
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
                message = (
                    f"No chunks found for book {args.book} under section '{section_query}'."
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
                print(message)
                if examples:
                    print("Available sections include:")
                    for section in examples:
                        print(f"  {section}")
                print(f"Tip: run `gutenbit toc {args.book}` to list all sections.")
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
            _print_passage(record)
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
            _print_passage(record)
            return 0

        rows = _opening_rows(db, args.book, forward)
        if not rows:
            return _command_error(
                "view",
                _no_chunks_message(db, args.book),
                as_json=as_json,
                data=_view_payload(
                    section=first_section["section"] if first_section else None,
                    section_number=(
                        first_section["section_number"] if first_section else None
                    ),
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
        _print_passage(record)
        _print_action_hints(action_hints)
        return 0


_COMMANDS = {
    "catalog": _cmd_catalog,
    "add": _cmd_add,
    "delete": _cmd_delete,
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
            print("\nInterrupted.")
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json_envelope(args.command, ok=False, errors=[f"Error: {exc}"])
            if args.verbose:
                import traceback

                traceback.print_exc()
        else:
            print(f"Error: {exc}", file=sys.stderr)
            if args.verbose:
                import traceback

                traceback.print_exc()
        return 1


def _entry_point() -> None:
    """Console-scripts entry point."""
    sys.exit(main())
