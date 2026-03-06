"""Command-line interface for gutenbit."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gutenbit.catalog import Catalog
from gutenbit.db import ChunkRecord, Database

DEFAULT_DB = "gutenbit.db"
CHUNK_KINDS = ["front_matter", "heading", "paragraph", "end_matter"]
JSON_OPENING_LINE_PREVIEW_CHARS = 140


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


def _format_int(value: int) -> str:
    return f"{value:,}"


def _section_path(*levels: str) -> str:
    return " / ".join(level for level in levels if level) or "(root)"


def _section_examples(db: Database, book_id: int, *, limit: int = 5) -> list[str]:
    examples: list[str] = []
    seen: set[str] = set()
    for _pos, div1, div2, div3, div4, _content, _kind, _char_count in db.chunks(
        book_id, kinds=["heading"]
    ):
        section = _section_path(div1, div2, div3, div4)
        if section == "(root)" or section in seen:
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


def _print_key_value_table(
    rows: list[tuple[str, str]], *, show_header: bool = True, key_header: str = "Field", value_header: str = "Value"
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


def _build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(
        prog="gutenbit",
        formatter_class=fmt,
        description="Project Gutenberg ETL — download, chunk, and search public-domain books.",
        epilog="""\
typical workflow:
  1. gutenbit catalog --author Dickens         # find book IDs
  2. gutenbit ingest 46 730                    # download & store
  3. gutenbit books                            # list stored books
  4. gutenbit view 46                          # browse structure / text
  5. gutenbit search "Marley ghost" --book-id 46  # find relevant chunks

chunk kinds:  front_matter, heading, paragraph, end_matter
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
  gutenbit catalog --language en --subject Philosophy -n 50

output columns:  ID  AUTHORS  TITLE
all filters use case-insensitive substring matching (AND logic).""",
    )
    cat.add_argument("--author", default="", help="filter by author (substring match)")
    cat.add_argument("--title", default="", help="filter by title (substring match)")
    cat.add_argument("--language", default="", help="filter by language code, e.g. 'en'")
    cat.add_argument("--subject", default="", help="filter by subject (substring match)")
    cat.add_argument("-n", "--limit", type=int, default=20, help="max results (default: 20)")

    # --- ingest ---
    ing = sub.add_parser(
        "ingest",
        formatter_class=fmt,
        help="download and store books by PG id",
        description=(
            "Download books from Project Gutenberg by ID, parse HTML into chunks, "
            "and store everything in the SQLite database. Already-downloaded books "
            "are skipped."
        ),
        epilog="""\
examples:
  gutenbit ingest 2600                  # War and Peace
  gutenbit ingest 46 730 967            # multiple books
  gutenbit ingest 2600 --delay 2.0      # polite crawling""",
    )
    ing.add_argument("ids", nargs="+", type=int, help="Project Gutenberg book IDs")
    ing.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="seconds between downloads (default: 1.0)",
    )

    # --- delete ---
    de = sub.add_parser(
        "delete",
        formatter_class=fmt,
        help="delete a stored book by PG id",
        description=(
            "Delete a previously ingested book from the SQLite database, including "
            "its reconstructed text and all chunks."
        ),
        epilog="""\
examples:
  gutenbit delete 46
  gutenbit --db my.db delete 2600

if the book ID is not present, the command returns exit code 1.""",
    )
    de.add_argument("book_id", type=int, help="Project Gutenberg book ID")

    # --- books ---
    sub.add_parser(
        "books",
        formatter_class=fmt,
        help="list books stored in the database",
        description="List all books that have been ingested into the database.",
        epilog="""\
example:
  gutenbit books
  gutenbit --db my.db books

output columns:  ID  AUTHORS  TITLE""",
    )

    # --- search ---
    se = sub.add_parser(
        "search",
        formatter_class=fmt,
        help="full-text search across stored books",
        description=(
            "Full-text search using SQLite FTS5 with BM25 ranking. "
            "Searches across all stored books unless filtered. "
            "Supports standard FTS5 query syntax: quoted phrases, "
            "AND/OR/NOT operators, prefix queries (word*), and positional modes."
        ),
        epilog="""\
examples:
  gutenbit search "battle"                                  # relevance-ranked
  gutenbit search "Levin" --book-id 1399 --mode first       # first occurrence
  gutenbit search "Levin" --book-id 1399 --mode last        # last occurrence
  gutenbit search "may it be" --phrase --book-id 2554 -n 20 # exact phrase
  gutenbit search "freedom" --kind paragraph -n 5           # filtered top hits

output fields:
  rank, book_id, position, title
  section, score, kind, char_count
  preview text

tip: use 'gutenbit view <id>' first to see a book's structure, then
     narrow searches with --book-id and --kind.""",
    )
    se.add_argument("query", help="FTS5 search query (supports phrases, AND/OR/NOT, prefix*)")
    se.add_argument(
        "--phrase",
        action="store_true",
        help="treat query as an exact phrase (auto-wrap in FTS5 quotes)",
    )
    se.add_argument(
        "--mode",
        choices=["ranked", "first", "last"],
        default="ranked",
        help="search mode: ranked (BM25), first (earliest), last (latest)",
    )
    se.add_argument("--author", help="filter results by author (substring match)")
    se.add_argument("--title", help="filter results by title (substring match)")
    se.add_argument("--book-id", type=int, help="restrict to a single book by PG ID")
    se.add_argument(
        "--kind",
        choices=CHUNK_KINDS,
        help="filter by chunk kind (front_matter|heading|paragraph|end_matter)",
    )
    se.add_argument(
        "-n",
        "--limit",
        type=int,
        default=0,
        help="max results (default: ranked=20, first/last=1)",
    )
    se.add_argument(
        "--preview-chars",
        type=int,
        default=140,
        help="preview length per result (default: 140)",
    )

    # --- view ---
    vw = sub.add_parser(
        "view",
        formatter_class=fmt,
        help="browse structure and retrieve text/chunks for a stored book",
        description=(
            "Unified text-view command. By default prints a TOC-style structure summary. "
            "Use exact selectors to retrieve the full book, one chunk by position, "
            "or chunks under a section path."
        ),
        epilog="""\
examples:
  gutenbit view 2600                                 # structure summary
  gutenbit view 2600 --json                          # structure summary as JSON
  gutenbit view 2600 --all                           # full reconstructed text
  gutenbit view 2600 --position 12345                # one exact chunk position
  gutenbit view 2600 --position 12345 --around 2     # position + neighbors
  gutenbit view 2600 --section "BOOK I/CHAPTER I" -n 10  # chunks in section
  gutenbit view 46 --section "STAVE ONE" --full          # full chunk text

selectors (choose at most one):
  --all | --position <n> | --section <SECTION_PATH>

chunk kinds:  front_matter, heading, paragraph, end_matter
section hierarchy:  level1 > level2 > level3 > level4  (compacted from shallowest heading)""",
    )
    vw.add_argument("book_id", type=int, help="Project Gutenberg book ID")
    vw.add_argument(
        "--json",
        action="store_true",
        help="print default summary as JSON (only without selectors)",
    )
    vw.add_argument("--all", action="store_true", help="print full reconstructed text")
    vw.add_argument("--position", type=int, help="retrieve one exact chunk by position")
    vw.add_argument(
        "--section",
        help="retrieve chunks under section path prefix (e.g. PART ONE/CHAPTER I)",
    )
    vw.add_argument(
        "--around", type=int, default=0, help="neighbors on each side (for --position)"
    )
    vw.add_argument(
        "--full", action="store_true", help="print full chunk text instead of previews"
    )
    vw.add_argument(
        "--kind", nargs="+", choices=CHUNK_KINDS, help="filter kinds (only with --section)"
    )
    vw.add_argument(
        "-n",
        "--limit",
        type=int,
        default=0,
        help="max chunks for --section (0=all)",
    )
    vw.add_argument(
        "--preview-chars",
        type=int,
        default=140,
        help="preview length per chunk when not using --full (default: 140)",
    )

    return p


# -------------------------------------------------------------------
# Subcommand handlers
# -------------------------------------------------------------------


def _cmd_catalog(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        print("--limit must be > 0.")
        return 1

    print("Fetching catalog from Project Gutenberg (English text corpus)…")
    catalog = Catalog.fetch()
    results = catalog.search(
        author=args.author,
        title=args.title,
        language=args.language,
        subject=args.subject,
    )
    if not results:
        print("No books found.")
        return 0
    shown = results[: args.limit]
    for b in shown:
        authors = _single_line(b.authors)[:30]
        title = _single_line(b.title)
        print(f"  {b.id:>6}  {authors:<30s}  {title}")
    if len(results) > args.limit:
        print(f"  … and {len(results) - args.limit} more (use -n to show more)")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    print("Fetching catalog…")
    catalog = Catalog.fetch()
    selected_by_id = {}
    for requested_id in args.ids:
        rec = catalog.get(requested_id)
        if rec is None:
            print(
                "  warning: book "
                f"{requested_id} is outside the English text catalog boundaries, skipping"
            )
            continue
        if rec.id != requested_id:
            title = _single_line(rec.title)
            print(f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)")
        selected_by_id.setdefault(rec.id, rec)

    books = list(selected_by_id.values())

    if not books:
        print("No valid book IDs provided.")
        return 1

    with Database(args.db) as db:
        for book in books:
            title = _single_line(book.title)
            if db.has_text(book.id):
                print(f"  skipping {book.id}: {title} (already downloaded)")
                continue
            print(f"  ingesting {book.id}: {title}…")
            db.ingest([book], delay=args.delay)
    print(f"Done. Database: {Path(args.db).resolve()}")
    return 0


def _cmd_books(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        books = db.books()
    if not books:
        print("No books stored yet. Use 'gutenbit ingest <id> ...' to add some.")
        return 0
    for b in books:
        authors = _single_line(b.authors)[:30]
        title = _single_line(b.title)
        print(f"  {b.id:>6}  {authors:<30s}  {title}")
    print(f"\n{len(books)} book(s) stored in {args.db}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        deleted = db.delete_book(args.book_id)
    if not deleted:
        print(f"No book found for id {args.book_id}.")
        return 1
    print(f"Deleted book {args.book_id} from {args.db}.")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if args.limit < 0:
        print("--limit must be >= 0.")
        return 1

    search_query = _fts_phrase_query(args.query) if args.phrase else args.query
    default_limit = 1 if args.mode in {"first", "last"} else 20
    limit = args.limit if args.limit > 0 else default_limit
    preview_chars = args.preview_chars if args.preview_chars > 0 else 140

    with Database(args.db) as db:
        results = db.search(
            search_query,
            author=args.author,
            title=args.title,
            book_id=args.book_id,
            kind=args.kind,
            mode=args.mode,
            limit=limit,
        )
    if not results:
        print("No results.")
        return 0

    print(f"query={args.query!r}  mode={args.mode}  shown={len(results)}")
    for idx, r in enumerate(results, start=1):
        section = _section_path(r.div1, r.div2, r.div3, r.div4)
        preview = _preview(r.content, preview_chars)
        print(f"\n{idx:>2}. book={r.book_id} position={r.position}  title={r.title}")
        print(f"    section={section}")
        print(f"    score={r.score:.2f}  kind={r.kind}  chars={r.char_count}")
        print(f"    {preview}")
        print()
    print(f"{len(results)} result(s)")
    return 0


def _build_section_summary(db: Database, book_id: int) -> dict[str, object] | None:
    rows = db._conn.execute(
        "SELECT position, div1, div2, div3, div4, content, kind, char_count "
        "FROM chunks WHERE book_id = ? ORDER BY position",
        (book_id,),
    ).fetchall()
    if not rows:
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

    sections: list[dict[str, object]] = []
    kind_counts = {kind: 0 for kind in CHUNK_KINDS}
    total_chars = 0
    opening_paragraphs = 0
    opening_chars = 0
    opening_first_position: int | None = None
    opening_line = ""
    for row in rows:
        position = int(row["position"])
        div1 = str(row["div1"])
        div2 = str(row["div2"])
        div3 = str(row["div3"])
        div4 = str(row["div4"])
        content = str(row["content"])
        kind = str(row["kind"])
        char_count = int(row["char_count"])
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        total_chars += char_count

        if kind == "heading":
            path = " / ".join(d for d in [div1, div2, div3, div4] if d)
            sections.append(
                {
                    "heading": _single_line(content) or "(untitled section)",
                    "path": path,
                    "position": position,
                    "paragraphs": 0,
                    "chars": 0,
                    "first_position": position,
                    "opening_line": "",
                }
            )
        elif kind == "paragraph" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + char_count
            if not sections[-1]["opening_line"]:
                sections[-1]["opening_line"] = _single_line(content)
        elif kind == "paragraph":
            opening_paragraphs += 1
            opening_chars += char_count
            if opening_first_position is None:
                opening_first_position = position
            if not opening_line:
                opening_line = _single_line(content)

    if opening_paragraphs:
        sections.insert(
            0,
            {
                "heading": "(unsectioned opening)",
                "path": "",
                "position": -1,
                "paragraphs": opening_paragraphs,
                "chars": opening_chars,
                "first_position": opening_first_position,
                "opening_line": opening_line,
            },
        )

    total_chunks = len(rows)
    total_sections = len(sections)
    total_paragraphs = kind_counts.get("paragraph", 0)
    est_words = round(total_chars / 5) if total_chars else 0
    read_time = _estimate_read_time(est_words)

    search_cmd = f"gutenbit search <query> --book-id {book_id} --kind paragraph"
    first_path = ""
    for sec in sections:
        path = str(sec["path"])
        if path:
            first_path = path
            break
    first_section_cmd = ""
    if first_path:
        safe_section = first_path.replace('"', '\\"')
        first_section_cmd = f'gutenbit view {book_id} --section "{safe_section}" -n 20'
    first_position = next(
        (
            int(sec["first_position"])
            for sec in sections
            if sec.get("first_position") is not None
        ),
        None,
    )
    view_first_position_cmd = ""
    view_first_position_around_cmd = ""
    if first_position is not None:
        view_first_position_cmd = f"gutenbit view {book_id} --position {first_position}"
        view_first_position_around_cmd = (
            f"gutenbit view {book_id} --position {first_position} --around 2"
        )

    section_rows: list[dict[str, object]] = []
    for sec in sections:
        chars = int(sec["chars"])
        est_words_for_section = round(chars / 5)
        section_rows.append(
            {
                "position": (
                    int(sec["first_position"])
                    if sec.get("first_position") is not None
                    else int(sec["position"])
                ),
                "section": str(sec["path"]) or str(sec["heading"]),
                "paras": int(sec["paragraphs"]),
                "chars": chars,
                "est_words": est_words_for_section,
                "est_read": _estimate_read_time(est_words_for_section),
                "opening_line": str(sec["opening_line"]),
            }
        )

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
            "view_first_position": view_first_position_cmd,
            "view_first_position_around": view_first_position_around_cmd,
        },
    }


def _render_section_summary(db: Database, book_id: int, *, as_json: bool = False) -> int:
    summary = _build_section_summary(db, book_id)
    if summary is None:
        print(f"No chunks found for book {book_id}.")
        return 1
    if as_json:
        json_summary = dict(summary)
        raw_sections = summary.get("sections", [])
        if isinstance(raw_sections, list):
            json_sections: list[dict[str, object]] = []
            for sec in raw_sections:
                if not isinstance(sec, dict):
                    continue
                sec_json = dict(sec)
                sec_json["opening_line"] = _preview(
                    str(sec_json.get("opening_line", "")),
                    JSON_OPENING_LINE_PREVIEW_CHARS,
                )
                json_sections.append(sec_json)
            json_summary["sections"] = json_sections
        print(json.dumps(json_summary, indent=2))
        return 0

    book = summary["book"]
    overview = summary["overview"]
    sections = summary["sections"]
    quick_actions = summary["quick_actions"]

    assert isinstance(book, dict)
    assert isinstance(overview, dict)
    assert isinstance(sections, list)
    assert isinstance(quick_actions, dict)

    authors = str(book["authors"])
    subjects = _summarize_semicolon_list(";".join(book["subjects"]), max_items=5)
    shelves = _summarize_semicolon_list(";".join(book["bookshelves"]), max_items=7)

    book_rows: list[tuple[str, str]] = []
    book_rows.append(("Title", str(book["title"])))
    book_rows.append(("Gutenberg ID", str(book_id)))
    if authors:
        book_rows.append(("Authors", authors))
    if book["language"]:
        book_rows.append(("Language", str(book["language"])))
    if book["issued"]:
        book_rows.append(("Issued", str(book["issued"])))
    if book["type"]:
        book_rows.append(("Type", str(book["type"])))
    if book["locc"]:
        book_rows.append(("LoCC", str(book["locc"])))
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
                _format_int(int(overview["chunk_counts"]["heading"])),
                _format_int(int(overview["chunk_counts"]["paragraph"])),
                _format_int(int(overview["chars_total"])),
                _format_int(int(overview["est_words"])),
                str(overview["est_read_time"]),
            ]
        ],
        right_align=set(range(5)),
    )

    _print_block_header("Contents")
    if not sections:
        print("  (no headings found)")
    else:
        position_values = [str(sec["position"]) for sec in sections]
        section_values = [str(sec["section"]) for sec in sections]
        paras_values = [_format_int(int(sec["paras"])) for sec in sections]
        char_values = [_format_int(int(sec["chars"])) for sec in sections]
        est_word_values = [_format_int(int(sec["est_words"])) for sec in sections]
        est_read_values = [str(sec["est_read"]) for sec in sections]
        opening_values = [str(sec["opening_line"]) or "-" for sec in sections]

        position_label = "Position"
        position_width = max(len(position_label), max(len(v) for v in position_values))
        section_width = min(40, max(len("Section"), max(len(v) for v in section_values)))
        paras_width = max(len("Paras"), max(len(v) for v in paras_values))
        chars_width = max(len("Chars"), max(len(v) for v in char_values))
        est_words_width = max(len("Est words"), max(len(v) for v in est_word_values))
        est_read_width = max(len("Est read"), max(len(v) for v in est_read_values))
        opening_width = min(56, max(len("Opening"), max(len(v) for v in opening_values)))

        print(
            f" {position_label:>{position_width}}  {'Section':<{section_width}}  "
            f"{'Paras':>{paras_width}}  {'Chars':>{chars_width}}  "
            f"{'Est words':>{est_words_width}}  {'Est read':>{est_read_width}}  "
            f"{'Opening':<{opening_width}}"
        )
        print(
            f" {'-' * position_width}  {'-' * section_width}  "
            f"{'-' * paras_width}  {'-' * chars_width}  "
            f"{'-' * est_words_width}  {'-' * est_read_width}  {'-' * opening_width}"
        )

        for sec in sections:
            position = str(sec["position"])
            section_label = str(sec["section"])
            if len(section_label) > section_width:
                keep = max(1, section_width - 3)
                section_label = section_label[:keep] + "..."
            paragraphs = _format_int(int(sec["paras"]))
            chars = _format_int(int(sec["chars"]))
            est_words = _format_int(int(sec["est_words"]))
            est_read = str(sec["est_read"])
            opening = str(sec["opening_line"]) or "-"
            if len(opening) > opening_width:
                keep = max(1, opening_width - 3)
                opening = opening[:keep] + "..."
            print(
                f" {position:>{position_width}}  {section_label:<{section_width}}  "
                f"{paragraphs:>{paras_width}}  {chars:>{chars_width}}  "
                f"{est_words:>{est_words_width}}  {est_read:>{est_read_width}}  "
                f"{opening:<{opening_width}}"
            )

    print("\nQuick actions")
    print(f"  {quick_actions['search']}")
    if quick_actions["view_first_section"]:
        print(f"  {quick_actions['view_first_section']}")
    if quick_actions["view_first_position"]:
        print(f"  {quick_actions['view_first_position']}")
    if quick_actions["view_first_position_around"]:
        print(f"  {quick_actions['view_first_position_around']}")
    return 0


def _print_chunk_blocks(
    rows: list[ChunkRecord], *, full: bool, preview_chars: int, title: str = ""
) -> None:
    if title:
        print(title)
    for idx, row in enumerate(rows, start=1):
        section = _section_path(row.div1, row.div2, row.div3, row.div4)
        body = row.content if full else _preview(row.content, preview_chars)
        print(
            f"\n{idx:>2}. position={row.position}  "
            f"kind={row.kind}  chars={row.char_count}"
        )
        print(f"    section={section}")
        print(f"    {body}")
    print(f"\n{len(rows)} chunk(s)")


def _cmd_view(args: argparse.Namespace) -> int:
    selected = int(args.all) + int(args.position is not None) + int(args.section is not None)
    if selected > 1:
        print("Choose at most one selector: --all, --position, or --section.")
        return 1
    if args.json and selected > 0:
        print("--json can only be used with the default summary view.")
        return 1
    if args.around < 0:
        print("--around must be >= 0.")
        return 1
    if args.limit < 0:
        print("--limit must be >= 0.")
        return 1
    if args.kind and args.section is None:
        print("--kind can only be used with --section.")
        return 1

    preview_chars = args.preview_chars if args.preview_chars > 0 else 140
    with Database(args.db) as db:
        if args.all:
            content = db.text(args.book_id)
            if content is None:
                print(f"No text found for book {args.book_id}.")
                return 1
            print(content)
            return 0

        if args.position is not None:
            rows = db.chunk_window(args.book_id, args.position, around=args.around)
            if not rows:
                print(f"No chunk found at position {args.position} in book {args.book_id}.")
                return 1
            _print_chunk_blocks(
                rows,
                full=args.full,
                preview_chars=preview_chars,
                title=(f"book={args.book_id}  position={args.position}  around={args.around}"),
            )
            return 0

        if args.section is not None:
            rows = db.chunks_by_div(args.book_id, args.section, kinds=args.kind, limit=args.limit)
            if not rows:
                print(f"No chunks found for book {args.book_id} under section '{args.section}'.")
                examples = _section_examples(db, args.book_id)
                if examples:
                    print("Available sections include:")
                    for section in examples:
                        print(f"  {section}")
                print(f"Tip: run `gutenbit view {args.book_id}` to list all sections.")
                return 1
            _print_chunk_blocks(
                rows,
                full=args.full,
                preview_chars=preview_chars,
                title=f"book={args.book_id}  section={args.section!r}",
            )
            return 0

        return _render_section_summary(db, args.book_id, as_json=args.json)


_COMMANDS = {
    "catalog": _cmd_catalog,
    "ingest": _cmd_ingest,
    "delete": _cmd_delete,
    "books": _cmd_books,
    "search": _cmd_search,
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
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def _entry_point() -> None:
    """Console-scripts entry point."""
    sys.exit(main())
