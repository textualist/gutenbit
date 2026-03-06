"""Command-line interface for gutenbit."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gutenbit.catalog import Catalog
from gutenbit.db import Database

DEFAULT_DB = "gutenbit.db"


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
  4. gutenbit toc 46                           # view table of contents
  5. gutenbit search "Marley ghost" --book-id 46  # full-text search

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

    # --- chunks ---
    ch = sub.add_parser(
        "chunks",
        formatter_class=fmt,
        help="show chunks for a stored book",
        description=(
            "Display individual chunks (paragraphs, headings, etc.) for a stored book. "
            "Each chunk has a position, div path, kind, and content preview."
        ),
        epilog="""\
examples:
  gutenbit chunks 2600                              # all chunks
  gutenbit chunks 2600 --kind heading               # headings only
  gutenbit chunks 2600 --kind paragraph -n 10       # first 10 paragraphs
  gutenbit chunks 46 --kind front_matter end_matter # bookend material

output columns:  POSITION  [KIND]  DIV_PATH  CONTENT_PREVIEW
chunk kinds:  front_matter, heading, paragraph, end_matter""",
    )
    ch.add_argument("book_id", type=int, help="Project Gutenberg book ID")
    ch.add_argument(
        "--kind",
        nargs="+",
        choices=["front_matter", "heading", "paragraph", "end_matter"],
        help="filter by chunk kind(s)",
    )
    ch.add_argument("-n", "--limit", type=int, default=0, help="max chunks to show (0=all)")

    # --- search ---
    se = sub.add_parser(
        "search",
        formatter_class=fmt,
        help="full-text search across stored books",
        description=(
            "Full-text search using SQLite FTS5 with BM25 ranking. "
            "Searches across all stored books unless filtered. "
            "Supports standard FTS5 query syntax: quoted phrases, "
            "AND/OR/NOT operators, prefix queries (word*)."
        ),
        epilog="""\
examples:
  gutenbit search "battle"                            # across all books
  gutenbit search "Marley ghost" --book-id 46         # one book
  gutenbit search "freedom" --kind paragraph          # paragraphs only
  gutenbit search "prince" --author Tolstoy -n 5      # by author, top 5
  gutenbit search '"to be or not"'                    # exact phrase (FTS5)

output fields:  [BOOK_ID] TITLE  DIV_PATH
                score  kind  char_count
                CONTENT_PREVIEW

tip: use 'gutenbit toc <id>' first to see a book's structure, then
     narrow searches with --book-id and --kind.""",
    )
    se.add_argument("query", help="FTS5 search query (supports phrases, AND/OR/NOT, prefix*)")
    se.add_argument("--author", help="filter results by author (substring match)")
    se.add_argument("--title", help="filter results by title (substring match)")
    se.add_argument("--book-id", type=int, help="restrict to a single book by PG ID")
    se.add_argument(
        "--kind",
        help="filter by chunk kind (front_matter|heading|paragraph|end_matter)",
    )
    se.add_argument("-n", "--limit", type=int, default=20, help="max results (default: 20)")

    # --- toc ---
    toc = sub.add_parser(
        "toc",
        formatter_class=fmt,
        help="print the table of contents for a stored book",
        description=(
            "Print the hierarchical table of contents for a stored book. "
            "Shows each section heading with its div path, paragraph count, "
            "and character count. Use this to understand a book's structure "
            "before searching."
        ),
        epilog="""\
example:
  gutenbit toc 2600

output per section:
  HEADING_TEXT                         (indented by hierarchy depth)
    div=DIV1/DIV2  (N paragraphs, M chars)

division hierarchy:
  div1 — broad divisions (BOOK, PART, VOLUME)
  div2 — chapters (CHAPTER, STAVE, SCENE)
  div3 — sub-sections (SECTION)
  div4 — reserved

workflow: run 'toc' to identify sections, then use the div values
with 'search --book-id <id>' to target specific parts of a book.""",
    )
    toc.add_argument("book_id", type=int, help="Project Gutenberg book ID")

    # --- text ---
    tx = sub.add_parser(
        "text",
        formatter_class=fmt,
        help="print the full reconstructed text of a stored book",
        description=(
            "Print the full plain text of a stored book by joining all chunks. "
            "Useful for piping to other tools or reading offline."
        ),
        epilog="""\
examples:
  gutenbit text 46                # print to terminal
  gutenbit text 46 > carol.txt   # save to file
  gutenbit text 46 | wc -w       # word count""",
    )
    tx.add_argument("book_id", type=int, help="Project Gutenberg book ID")

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


def _cmd_chunks(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        rows = db.chunks(args.book_id, kinds=args.kind)
    if not rows:
        print(f"No chunks found for book {args.book_id}.")
        return 1
    limit = args.limit if args.limit > 0 else len(rows)
    for pos, div1, div2, div3, div4, content, kind, _char_count in rows[:limit]:
        divs = "/".join(d for d in [div1, div2, div3, div4] if d)
        tag = f"[{kind}]"
        preview = content[:120].replace("\n", " ")
        if len(content) > 120:
            preview += "…"
        print(f"  {pos:>5}  {tag:<14s}  {divs:<40s}  {preview}")
    total = len(rows)
    shown = min(limit, total)
    print(f"\n{shown}/{total} chunk(s) shown (book {args.book_id})")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        results = db.search(
            args.query,
            author=args.author,
            title=args.title,
            book_id=args.book_id,
            kind=args.kind,
            limit=args.limit,
        )
    if not results:
        print("No results.")
        return 0
    for r in results:
        divs = "/".join(d for d in [r.div1, r.div2, r.div3, r.div4] if d)
        preview = r.content[:120].replace("\n", " ")
        if len(r.content) > 120:
            preview += "…"
        print(f"  [{r.book_id}] {r.title[:40]:<40s}  {divs}")
        print(f"         score={r.score:.2f}  kind={r.kind}  chars={r.char_count}")
        print(f"         {preview}")
        print()
    print(f"{len(results)} result(s)")
    return 0


def _cmd_toc(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        all_chunks = db.chunks(args.book_id)
        stored_books = db.books()
    if not all_chunks:
        print(f"No chunks found for book {args.book_id}.")
        return 1

    title = ""
    for b in stored_books:
        if b.id == args.book_id:
            title = b.title
            break

    # Build sections: each heading plus counts of paragraphs that follow it
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

    if title:
        print(f"# {title} (id={args.book_id})\n")

    # Print with indentation reflecting hierarchy
    for sec in sections:
        div1 = sec["div1"]
        div2 = sec["div2"]
        div3 = sec["div3"]
        div4 = sec["div4"]

        # Indent: div1-only=0, div2=2, div3=4, div4=6
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
    # Print a hint for agent usage
    bid = args.book_id
    print(f"\nFilter searches with: gutenbit search <query> --book-id {bid} --kind paragraph")
    return 0


def _cmd_text(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        content = db.text(args.book_id)
    if content is None:
        print(f"No text found for book {args.book_id}.")
        return 1
    print(content)
    return 0


_COMMANDS = {
    "catalog": _cmd_catalog,
    "ingest": _cmd_ingest,
    "delete": _cmd_delete,
    "books": _cmd_books,
    "chunks": _cmd_chunks,
    "search": _cmd_search,
    "toc": _cmd_toc,
    "text": _cmd_text,
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
