"""Command-line interface for gutenbit."""

from __future__ import annotations

import argparse
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


def _fts_phrase_query(query: str) -> str:
    """Wrap a raw query as an exact FTS5 phrase, escaping inner quotes."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


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
division hierarchy:  div1 (BOOK/PART) > div2 (CHAPTER) > div3 (SECTION) > div4

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
  gutenbit view 2600 --all                           # full reconstructed text
  gutenbit view 2600 --chunk-id 12345                # one exact chunk
  gutenbit view 2600 --chunk-id 12345 --around 2     # chunk + neighbors
  gutenbit view 2600 --div "BOOK I/CHAPTER I" -n 10  # chunks in section
  gutenbit view 46 --div "STAVE ONE" --full          # full chunk text

selectors (choose at most one):
  --all | --chunk-id <id> | --div <DIV_PATH>

chunk kinds:  front_matter, heading, paragraph, end_matter
division hierarchy:  div1/div2/div3/div4""",
    )
    vw.add_argument("book_id", type=int, help="Project Gutenberg book ID")
    vw.add_argument("--all", action="store_true", help="print full reconstructed text")
    vw.add_argument("--chunk-id", type=int, help="retrieve one exact chunk by chunk id")
    vw.add_argument(
        "--div",
        help="retrieve chunks under exact div path prefix (e.g. PART ONE/CHAPTER I)",
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
    print("Fetching catalog from Project Gutenberg…")
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
        print(f"  {b.id:>6}  {b.authors[:30]:<30s}  {b.title}")
    if len(results) > args.limit:
        print(f"  … and {len(results) - args.limit} more (use -n to show more)")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    print("Fetching catalog…")
    catalog = Catalog.fetch()
    by_id = {b.id: b for b in catalog.records}
    books = []
    for book_id in args.ids:
        rec = by_id.get(book_id)
        if rec is None:
            print(f"  warning: book {book_id} not found in catalog, skipping")
            continue
        books.append(rec)

    if not books:
        print("No valid book IDs provided.")
        return 1

    with Database(args.db) as db:
        for book in books:
            print(f"  ingesting {book.id}: {book.title}…")
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
        print(f"  {b.id:>6}  {b.authors[:30]:<30s}  {b.title}")
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


def _render_section_summary(db: Database, book_id: int) -> int:
    all_chunks = db.chunks(book_id)
    if not all_chunks:
        print(f"No chunks found for book {book_id}.")
        return 1

    stored_books = db.books()
    title = ""
    for b in stored_books:
        if b.id == book_id:
            title = b.title
            break

    if title:
        print(f"# {title} (id={book_id})\n")

    sections: list[dict[str, str | int]] = []
    for pos, div1, div2, div3, div4, content, kind, char_count in all_chunks:
        if kind == "heading":
            sections.append(
                {
                    "div1": div1,
                    "div2": div2,
                    "div3": div3,
                    "div4": div4,
                    "heading": content,
                    "position": pos,
                    "paragraphs": 0,
                    "chars": 0,
                }
            )
        elif kind == "paragraph" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + char_count

    for sec in sections:
        div1 = sec["div1"]
        div2 = sec["div2"]
        div3 = sec["div3"]
        div4 = sec["div4"]
        if div4:
            indent = 6
        elif div3:
            indent = 4
        elif div2:
            indent = 2
        else:
            indent = 0
        divs = "/".join(str(d) for d in [div1, div2, div3, div4] if d)
        stats = f"({sec['paragraphs']} paragraphs, {sec['chars']} chars)"
        print(f"{' ' * indent}{sec['heading']}")
        print(f"{' ' * indent}  div={divs}  {stats}")

    print(f"\n{len(sections)} section(s)")
    print(f"\nFilter searches with: gutenbit search <query> --book-id {book_id} --kind paragraph")
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

        return _render_section_summary(db, args.book_id)


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
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

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
