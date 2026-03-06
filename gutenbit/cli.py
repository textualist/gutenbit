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
division hierarchy:  div1 > div2 > div3 > div4  (levels compact to fill from div1)

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
  rank, book_id, chunk_id, position, title
  path, score, kind, char_count
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
            "Use exact selectors to retrieve the full book, one chunk by ID, "
            "or chunks under a div path."
        ),
        epilog="""\
examples:
  gutenbit view 2600                                 # structure summary
  gutenbit view 2600 --json                          # structure summary as JSON
  gutenbit view 2600 --all                           # full reconstructed text
  gutenbit view 2600 --chunk-id 12345                # one exact chunk
  gutenbit view 2600 --chunk-id 12345 --around 2     # chunk + neighbors
  gutenbit view 2600 --div "BOOK I/CHAPTER I" -n 10  # chunks in section
  gutenbit view 46 --div "STAVE ONE" --full          # full chunk text

selectors (choose at most one):
  --all | --chunk-id <id> | --div <DIV_PATH>

chunk kinds:  front_matter, heading, paragraph, end_matter
division hierarchy:  div1 > div2 > div3 > div4  (compacted from shallowest level)""",
    )
    vw.add_argument("book_id", type=int, help="Project Gutenberg book ID")
    vw.add_argument(
        "--json",
        action="store_true",
        help="print default summary as JSON (only without selectors)",
    )
    vw.add_argument("--all", action="store_true", help="print full reconstructed text")
    vw.add_argument("--chunk-id", type=int, help="retrieve one exact chunk by chunk id")
    vw.add_argument(
        "--div",
        help="retrieve chunks under div path prefix (e.g. PART ONE/CHAPTER I)",
    )
    vw.add_argument(
        "--around", type=int, default=0, help="neighbors on each side (for --chunk-id)"
    )
    vw.add_argument(
        "--full", action="store_true", help="print full chunk text instead of previews"
    )
    vw.add_argument(
        "--kind", nargs="+", choices=CHUNK_KINDS, help="filter kinds (only with --div)"
    )
    vw.add_argument("-n", "--limit", type=int, default=0, help="max chunks for --div (0=all)")
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
        divs = " / ".join(d for d in [r.div1, r.div2, r.div3, r.div4] if d) or "(root)"
        preview = _preview(r.content, preview_chars)
        print(f"\n{idx:>2}. book={r.book_id} chunk={r.chunk_id} pos={r.position}  title={r.title}")
        print(f"    path={divs}")
        print(f"    score={r.score:.2f}  kind={r.kind}  chars={r.char_count}")
        print(f"    {preview}")
        print()
    print(f"{len(results)} result(s)")
    return 0


def _build_section_summary(db: Database, book_id: int) -> dict[str, object] | None:
    all_chunks = db.chunks(book_id)
    if not all_chunks:
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

    sections: list[dict[str, str | int]] = []
    kind_counts = {kind: 0 for kind in CHUNK_KINDS}
    total_chars = 0
    opening_paragraphs = 0
    opening_chars = 0
    for pos, div1, div2, div3, div4, content, kind, char_count in all_chunks:
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        total_chars += char_count

        if kind == "heading":
            path = " / ".join(d for d in [div1, div2, div3, div4] if d)
            sections.append(
                {
                    "heading": _single_line(content) or "(untitled section)",
                    "path": path,
                    "position": pos,
                    "paragraphs": 0,
                    "chars": 0,
                }
            )
        elif kind == "paragraph" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + char_count
        elif kind == "paragraph":
            opening_paragraphs += 1
            opening_chars += char_count

    if opening_paragraphs:
        sections.insert(
            0,
            {
                "heading": "(unsectioned opening)",
                "path": "",
                "position": -1,
                "paragraphs": opening_paragraphs,
                "chars": opening_chars,
            },
        )

    total_chunks = len(all_chunks)
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
        safe_div = first_path.replace('"', '\\"')
        first_section_cmd = f'gutenbit view {book_id} --div "{safe_div}" -n 20'

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
        "sections": [
            {
                "index": idx,
                "heading": str(sec["heading"]),
                "path": str(sec["path"]),
                "position": int(sec["position"]),
                "paragraphs": int(sec["paragraphs"]),
                "chars": int(sec["chars"]),
            }
            for idx, sec in enumerate(sections, start=1)
        ],
        "quick_actions": {
            "search": search_cmd,
            "view_first_section": first_section_cmd,
        },
    }


def _render_section_summary(db: Database, book_id: int, *, as_json: bool = False) -> int:
    summary = _build_section_summary(db, book_id)
    if summary is None:
        print(f"No chunks found for book {book_id}.")
        return 1
    if as_json:
        print(json.dumps(summary, indent=2))
        return 0

    book = summary["book"]
    overview = summary["overview"]
    sections = summary["sections"]
    quick_actions = summary["quick_actions"]

    assert isinstance(book, dict)
    assert isinstance(overview, dict)
    assert isinstance(sections, list)
    assert isinstance(quick_actions, dict)

    title = str(book["title"])
    print(f"# {title} (id={book_id})")

    authors = str(book["authors"])
    if authors:
        print(f"by {authors}")

    meta: list[str] = []
    if book["language"]:
        meta.append(f"language={book['language']}")
    if book["issued"]:
        meta.append(f"issued={book['issued']}")
    if book["type"]:
        meta.append(f"type={book['type']}")
    if book["locc"]:
        meta.append(f"locc={book['locc']}")
    if meta:
        print(f"meta: {'  '.join(meta)}")

    subjects = _summarize_semicolon_list(";".join(book["subjects"]), max_items=5)
    if subjects:
        print(f"subjects: {subjects}")
    shelves = _summarize_semicolon_list(";".join(book["bookshelves"]), max_items=4)
    if shelves:
        print(f"shelves: {shelves}")

    print("\nOverview")
    print(
        "  chunks="
        f"{_format_int(int(overview['chunks_total']))} ("
        f"front_matter={_format_int(int(overview['chunk_counts']['front_matter']))}, "
        f"heading={_format_int(int(overview['chunk_counts']['heading']))}, "
        f"paragraph={_format_int(int(overview['chunk_counts']['paragraph']))}, "
        f"end_matter={_format_int(int(overview['chunk_counts']['end_matter']))})"
    )
    print(
        "  "
        f"section(s)={_format_int(int(overview['sections_total']))}  "
        f"paragraphs={_format_int(int(overview['paragraphs_total']))}  "
        f"chars={_format_int(int(overview['chars_total']))}"
    )
    print(
        f"  est_words~{_format_int(int(overview['est_words']))}  "
        f"est_read={overview['est_read_time']}"
    )

    print("\nContents")
    if not sections:
        print("  (no headings found)")
    else:
        name_width = min(54, max(len(str(sec["heading"])) for sec in sections))
        for idx, sec in enumerate(sections, start=1):
            heading = str(sec["heading"])
            if len(heading) > name_width:
                heading = heading[: name_width - 1] + "…"
            paragraphs = _format_int(int(sec["paragraphs"]))
            chars = _format_int(int(sec["chars"]))
            print(f"{idx:>2}. {heading:<{name_width}}  {paragraphs:>7} paras  {chars:>10} chars")

            path = str(sec["path"])
            if "/" in path:
                print(f"    path={path}")

    print("\nQuick actions")
    print(f"  {quick_actions['search']}")
    if quick_actions["view_first_section"]:
        print(f"  {quick_actions['view_first_section']}")
    return 0


def _print_chunk_blocks(
    rows: list[ChunkRecord], *, full: bool, preview_chars: int, title: str = ""
) -> None:
    if title:
        print(title)
    for idx, row in enumerate(rows, start=1):
        divs = " / ".join(d for d in [row.div1, row.div2, row.div3, row.div4] if d) or "(root)"
        body = row.content if full else _preview(row.content, preview_chars)
        print(
            f"\n{idx:>2}. chunk={row.chunk_id} pos={row.position}  "
            f"kind={row.kind}  chars={row.char_count}"
        )
        print(f"    path={divs}")
        print(f"    {body}")
    print(f"\n{len(rows)} chunk(s)")


def _cmd_view(args: argparse.Namespace) -> int:
    selected = int(args.all) + int(args.chunk_id is not None) + int(args.div is not None)
    if selected > 1:
        print("Choose at most one selector: --all, --chunk-id, or --div.")
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
    if args.kind and args.div is None:
        print("--kind can only be used with --div.")
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

        if args.chunk_id is not None:
            rows = db.chunk_window(args.book_id, args.chunk_id, around=args.around)
            if not rows:
                print(f"No chunk found for id {args.chunk_id} in book {args.book_id}.")
                return 1
            _print_chunk_blocks(
                rows,
                full=args.full,
                preview_chars=preview_chars,
                title=(f"book={args.book_id}  chunk_id={args.chunk_id}  around={args.around}"),
            )
            return 0

        if args.div is not None:
            rows = db.chunks_by_div(args.book_id, args.div, kinds=args.kind, limit=args.limit)
            if not rows:
                print(f"No chunks found for book {args.book_id} under div '{args.div}'.")
                return 1
            _print_chunk_blocks(
                rows,
                full=args.full,
                preview_chars=preview_chars,
                title=f"book={args.book_id}  div={args.div!r}",
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
