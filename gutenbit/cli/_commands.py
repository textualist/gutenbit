"""Click command implementations for gutenbit."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, cast

import click

from gutenbit.catalog import BookRecord
from gutenbit.cli._context import (
    _CommandEnv,
    _command_error,
    _common_options,
    _display_cli_path,
    _load_catalog,
    _resolved_cli_path,
)
from gutenbit.cli._display import CliDisplay, format_summary_stats
from gutenbit.cli._json import (
    JSON_BOOK_ID_KEY,
    _book_payload,
    _joined_chunk_text,
    _json_search_filters,
    _passage_payload,
    _print_json_envelope,
)
from gutenbit.cli._query import (
    DEFAULT_DOWNLOAD_DELAY,
    DEFAULT_OPENING_CHUNK_COUNT,
    DEFAULT_TOC_EXPAND,
    DEFAULT_VIEW_FORWARD,
    _book_id_ref,
    _format_fts_error,
    _fts_phrase_query,
    _no_chunks_messages,
    _safe_fts_query,
    _section_path,
    _toc_expand_depth,
)
from gutenbit.cli._sections import (
    _build_section_summary,
    _canonical_section_match,
    _estimate_read_time,
    _opening_rows,
    _print_passage,
    _render_section_summary,
    _resolve_toc_book_id,
    _section_examples,
    _section_number_lookup,
    _section_reading_window,
    _section_selector_parts,
    _section_summary_json_payload,
    _view_action_hints,
)
from gutenbit.cli._text_utils import _single_line
from gutenbit.db import (
    ChunkRecord,
    Database,
    IngestProgressCallback,
    SearchOrder,
    TextState,
)
from gutenbit.download import describe_download_source, get_last_download_source

# ---------------------------------------------------------------------------
# Book ingestion helpers
# ---------------------------------------------------------------------------


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
    kwargs: dict[str, Any] = dict(delay=delay, force=force, state=state)
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback

    if as_json:
        previous_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            return db._ingest_book(book, **kwargs)
        finally:
            logging.disable(previous_disable)

    return db._ingest_book(book, **kwargs)


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


# ---------------------------------------------------------------------------
# catalog command
# ---------------------------------------------------------------------------


@click.command(
    "catalog",
    help="search the Project Gutenberg catalog",
    epilog="""
examples:
  gutenbit catalog --author Tolstoy
  gutenbit catalog --title "War and Peace"
  gutenbit catalog --author Dickens --refresh
  gutenbit catalog --language en --subject Philosophy --limit 50

output columns:  ID  AUTHORS  TITLE
all filters use case-insensitive substring matching (AND logic).""",
)
@click.option("--author", default="", help="filter by author (substring match)")
@click.option("--title", default="", help="filter by title (substring match)")
@click.option("--language", default="", help="filter by language code, e.g. 'en'")
@click.option("--subject", default="", help="filter by subject (substring match)")
@click.option("--limit", type=int, default=20, help="max results (default: 20)")
@click.option(
    "--refresh",
    is_flag=True,
    help="ignore the catalog cache and redownload it now",
)
@_common_options
def _cmd_catalog(
    env: _CommandEnv,
    author: str,
    title: str,
    language: str,
    subject: str,
    limit: int,
    refresh: bool,
) -> int:
    if limit <= 0:
        return _command_error("catalog", "--limit must be > 0.", as_json=env.as_json)

    catalog = _load_catalog(refresh, display=env.display, as_json=env.as_json)
    results = catalog.search(
        author=author,
        title=title,
        language=language,
        subject=subject,
    )

    shown = results[:limit]
    if env.as_json:
        data = {
            "filters": {
                "author": author,
                "title": title,
                "language": language,
                "subject": subject,
            },
            "limit": limit,
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "total_matches": len(results),
            "shown": len(shown),
            "items": [_book_payload(rec) for rec in shown],
        }
        _print_json_envelope("catalog", ok=True, data=data)
        return 0

    if not shown:
        env.display.status("No books found.")
        return 0

    env.display.catalog(shown, remaining_count=len(results) - len(shown))
    return 0


# ---------------------------------------------------------------------------
# add command
# ---------------------------------------------------------------------------


@click.command(
    "add",
    help="download and store books by PG id",
    epilog="""
examples:
  gutenbit add 2600                     # War and Peace
  gutenbit add 46 730 967               # multiple books
  gutenbit add 2600 --refresh           # refresh the catalog and reprocess the book
  gutenbit add 2600 --delay 2.0         # polite crawling""",
)
@click.argument("book_ids", nargs=-1, type=int, metavar="BOOK_ID")
@click.option(
    "--delay",
    type=float,
    default=DEFAULT_DOWNLOAD_DELAY,
    help="seconds between downloads (default: %(default)s)",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="ignore the catalog cache, redownload it now, and reprocess matching stored books",
)
@_common_options
def _cmd_add(
    env: _CommandEnv,
    book_ids: tuple[int, ...],
    delay: float,
    refresh: bool,
) -> int:
    if delay < 0:
        return _command_error("add", "--delay must be >= 0.", as_json=env.as_json)

    if not book_ids:
        raise click.UsageError("At least one BOOK_ID is required.")

    invalid_ids: list[int] = [bid for bid in book_ids if bid <= 0]
    if invalid_ids:
        return _command_error(
            "add",
            f"Book IDs must be positive integers, got: {', '.join(str(bid) for bid in invalid_ids)}",
            as_json=env.as_json,
            data={"invalid_ids": invalid_ids},
        )

    catalog = _load_catalog(refresh, display=env.display, as_json=env.as_json)
    selected_by_id: dict[int, Any] = {}
    request_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for requested_id in book_ids:
        rec = catalog.get(requested_id)
        if rec is None:
            warning = (
                f"book {requested_id} is outside the English text catalog boundaries, skipping"
            )
            warnings.append(warning)
            request_results.append({"requested_id": requested_id, "status": "out_of_policy"})
            if not env.as_json:
                env.display.warning(
                    "  warning: "
                    f"{_book_id_ref(requested_id, capitalize=False)} is outside "
                    "the English text catalog boundaries, skipping"
                )
            continue
        title = _single_line(rec.title)
        if rec.id != requested_id and not env.as_json:
            env.display.status(
                f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)"
            )
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
            "db": str(_resolved_cli_path(env.db_path)),
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "requested_ids": list(book_ids),
            "results": request_results,
        }
        return _command_error(
            "add",
            "No valid book IDs provided.",
            as_json=env.as_json,
            data=data,
            warnings=warnings,
        )

    with Database(env.db_path) as db_conn:
        canonical_statuses, errors = _process_books_for_ingest(
            db_conn,
            books,
            delay=delay,
            as_json=env.as_json,
            display=env.display,
            failure_action="add",
            force=refresh,
        )

    if env.as_json:
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
            "db": str(_resolved_cli_path(env.db_path)),
            "catalog_source": catalog.fetch_info.source if catalog.fetch_info else "unknown",
            "catalog_cache_path": (
                str(catalog.fetch_info.cache_path) if catalog.fetch_info else ""
            ),
            "delay_seconds": delay,
            "requested_ids": list(book_ids),
            "unique_canonical_ids": sorted(selected_by_id.keys()),
            "counts": {
                "requested": len(book_ids),
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
        env.display.error(
            f"Completed with {len(errors)} failure(s). Database: {_display_cli_path(env.db_path)}"
        )
        return 1
    env.display.success(f"Done. Database: {_display_cli_path(env.db_path)}")
    return 0


# ---------------------------------------------------------------------------
# books command
# ---------------------------------------------------------------------------


@click.command(
    "books",
    help="list or update books stored in the database",
    epilog="""
examples:
  gutenbit books
  gutenbit books --json
  gutenbit books --update
  gutenbit books --update --force
  gutenbit books --db my.db

output columns:  ID  AUTHORS  TITLE""",
)
@click.option(
    "--update", is_flag=True, help="reprocess stored books whose parser version is stale"
)
@click.option(
    "--delay",
    type=float,
    default=DEFAULT_DOWNLOAD_DELAY,
    help="seconds between downloads in update mode (default: %(default)s)",
)
@click.option(
    "--force",
    is_flag=True,
    help="reprocess all stored books in update mode, even if already current",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="show which stored books would be updated without downloading",
)
@_common_options
def _cmd_books(
    env: _CommandEnv,
    update: bool,
    delay: float,
    force: bool,
    dry_run: bool,
) -> int:
    if not update:
        if delay != DEFAULT_DOWNLOAD_DELAY:
            return _command_error(
                "books",
                "--delay can only be used with --update.",
                as_json=env.as_json,
            )
        if force:
            return _command_error(
                "books",
                "--force can only be used with --update.",
                as_json=env.as_json,
            )
        if dry_run:
            return _command_error(
                "books",
                "--dry-run can only be used with --update.",
                as_json=env.as_json,
            )
    elif delay < 0:
        return _command_error("books", "--delay must be >= 0.", as_json=env.as_json)

    with Database(env.db_path) as db_conn:
        books = db_conn.books()
        if update:
            db_path = str(_resolved_cli_path(env.db_path))
            db_display_path = _display_cli_path(env.db_path)
            stored_count = len(books)
            selected_books = books if force else db_conn.stale_books()
            selected_count = len(selected_books)
            skipped_current = 0 if force else stored_count - selected_count

            if not books:
                if env.as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": delay,
                            "force": force,
                            "dry_run": dry_run,
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
                    env.display.status(
                        "No books stored yet. Use 'gutenbit add <id> ...' to add some."
                    )
                return 0

            if dry_run:
                results = [
                    {
                        "book_id": book.id,
                        "title": _single_line(book.title),
                        "status": "selected",
                    }
                    for book in selected_books
                ]
                if env.as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": delay,
                            "force": force,
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
                    env.display.status(
                        f"Would reprocess {selected_count} of {stored_count} stored book(s):"
                    )
                    for book in selected_books:
                        env.display.status(f"  {book.id}: {_single_line(book.title)}")
                else:
                    env.display.status(
                        "All "
                        f"{stored_count} stored book(s) are current. "
                        f"Database: {db_display_path}"
                    )
                return 0

            if not selected_books:
                if env.as_json:
                    _print_json_envelope(
                        "books",
                        ok=True,
                        data={
                            "action": "update",
                            "db": db_path,
                            "delay_seconds": delay,
                            "force": force,
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
                    env.display.success(
                        "All "
                        f"{stored_count} stored book(s) are current. "
                        f"Database: {db_display_path}"
                    )
                return 0

            if not env.as_json:
                env.display.status(f"Checking {stored_count} stored book(s)...")

            statuses, errors = _process_books_for_ingest(
                db_conn,
                selected_books,
                delay=delay,
                as_json=env.as_json,
                display=env.display,
                failure_action="update",
                force=force,
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

            if env.as_json:
                _print_json_envelope(
                    "books",
                    ok=failed_count == 0,
                    data={
                        "action": "update",
                        "db": db_path,
                        "delay_seconds": delay,
                        "force": force,
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
                env.display.error(
                    "Completed with "
                    f"{failed_count} failure(s). Updated {updated_count} book(s); "
                    f"{skipped_current} already current. Database: {db_display_path}"
                )
                return 1
            env.display.success(
                f"Done. Updated {updated_count} book(s); "
                f"{skipped_current} already current. Database: {db_display_path}"
            )
            return 0

    if not books:
        if env.as_json:
            _print_json_envelope(
                "books",
                ok=True,
                data={"count": 0, "items": []},
            )
        else:
            env.display.status("No books stored yet. Use 'gutenbit add <id> ...' to add some.")
        return 0
    if env.as_json:
        _print_json_envelope(
            "books",
            ok=True,
            data={
                "count": len(books),
                "items": [_book_payload(book) for book in books],
            },
        )
        return 0
    env.display.books(books, db_path=_display_cli_path(env.db_path))
    return 0


# ---------------------------------------------------------------------------
# remove command
# ---------------------------------------------------------------------------


@click.command(
    "remove",
    help="remove stored books by PG id",
    epilog="""
examples:
  gutenbit remove 46
  gutenbit remove 46 730 967
  gutenbit remove 2600 --db my.db

if a book ID is not present, a warning is printed and exit code is 1.""",
)
@click.argument("book_ids", nargs=-1, type=int, metavar="BOOK_ID")
@_common_options
def _cmd_remove(
    env: _CommandEnv,
    book_ids: tuple[int, ...],
) -> int:
    if not book_ids:
        raise click.UsageError("At least one BOOK_ID is required.")
    any_missing = False
    removed_count = 0
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with Database(env.db_path) as db_conn:
        for book_id in book_ids:
            removed = db_conn.remove_book(book_id)
            if not removed:
                message = f"No book found for id {book_id}."
                errors.append(message)
                results.append({"book_id": book_id, "status": "missing"})
                if not env.as_json:
                    env.display.error(
                        f"No book found for {_book_id_ref(book_id, capitalize=False)}."
                    )
                any_missing = True
            else:
                removed_count += 1
                results.append({"book_id": book_id, "status": "removed"})
                if not env.as_json:
                    env.display.success(
                        f"Removed {_book_id_ref(book_id, capitalize=False)} "
                        f"from {_display_cli_path(env.db_path)}."
                    )
    if env.as_json:
        _print_json_envelope(
            "remove",
            ok=not any_missing,
            data={
                "db": str(_resolved_cli_path(env.db_path)),
                "removed_count": removed_count,
                "missing_count": len(book_ids) - removed_count,
                "results": results,
            },
            errors=errors,
        )
    return 1 if any_missing else 0


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


def _parse_book_ids(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> tuple[int, ...]:
    if value is None:
        return ()
    try:
        return tuple(int(x) for x in value.split())
    except ValueError:
        raise click.BadParameter("expected space-separated integers")


@click.command(
    "search",
    help="full-text search across stored books",
    epilog="""
examples:
  gutenbit search "bennet"                                  # simple search
  gutenbit search "don't stop"                              # punctuation is ok
  gutenbit search "half-hour"                               # hyphens just work
  gutenbit search "truth universally acknowledged" --phrase # exact phrase match
  gutenbit search "ghost OR spirit" --raw                   # FTS5 boolean query
  gutenbit search "(ghost OR spirit) AND NOT haunt*" --raw  # advanced FTS5
  gutenbit search "bennet" --book 1342                      # restrict to one book
  gutenbit search "the" --book "1 2 3"                      # restrict to several books
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
@click.argument("query", metavar="QUERY")
@click.option(
    "--phrase", is_flag=True, help="treat query as an exact phrase (word order must match)"
)
@click.option(
    "--raw", is_flag=True, help="pass query directly to FTS5 (AND/OR/NOT, prefix*, NEAR, groups)"
)
@click.option(
    "--order",
    type=click.Choice(["rank", "first", "last"]),
    default="rank",
    metavar="ORDER",
    help="search result order: rank (BM25); first (book asc + position asc); last (book desc + position desc)",
)
@click.option("--author", default=None, help="filter results by author (substring match)")
@click.option("--title", default=None, help="filter results by title (substring match)")
@click.option(
    "--book",
    callback=_parse_book_ids,
    default=None,
    expose_value=True,
    is_eager=False,
    help="restrict to one or more books by PG ID (space-separated)",
)
@click.option(
    "--kind",
    type=click.Choice(["text", "heading", "all"]),
    default="text",
    metavar="KIND",
    help="chunk kind to search (default: text)",
)
@click.option(
    "--section",
    default=None,
    help="restrict to a section by path prefix (e.g. 'STAVE ONE') or section number from 'toc'",
)
@click.option("--limit", type=int, default=10, help="max results (default: 10)")
@click.option(
    "--radius",
    type=int,
    default=None,
    help="surrounding passage on each side of each hit, in reading order",
)
@click.option("--count", is_flag=True, help="just print the number of matches")
@_common_options
def _cmd_search(
    env: _CommandEnv,
    query: str,
    phrase: bool,
    raw: bool,
    order: str,
    author: str | None,
    title: str | None,
    book: tuple[int, ...],
    kind: str,
    section: str | None,
    limit: int,
    radius: int | None,
    count: bool,
) -> int:
    if phrase and raw:
        return _command_error(
            "search", "--phrase and --raw are mutually exclusive.", as_json=env.as_json
        )
    if limit <= 0:
        return _command_error("search", "--limit must be > 0.", as_json=env.as_json)
    if radius is not None and radius < 0:
        return _command_error("search", "--radius must be >= 0.", as_json=env.as_json)
    if count and radius is not None:
        return _command_error(
            "search",
            "--radius cannot be used with --count.",
            as_json=env.as_json,
        )

    query_text = query.strip()
    if not query_text:
        return _command_error("search", "Search query must not be empty.", as_json=env.as_json)

    # Query mode: --phrase wraps as exact phrase, --raw passes through to FTS5,
    # default auto-escapes plain text so punctuation is ok.
    if phrase:
        search_query = _fts_phrase_query(query_text)
        query_mode = "phrase"
    elif raw:
        search_query = query_text
        query_mode = "raw"
    else:
        search_query = _safe_fts_query(query_text)
        query_mode = "auto"

    # Resolve --section: accept a section number (from 'toc') or path prefix.
    div_path: str | None = None
    section_arg: str | None = section

    search_order = cast(SearchOrder, order)
    warnings: list[str] = []
    with Database(env.db_path) as db_conn:
        section_number_for = _section_number_lookup(db_conn)

        for bid in book:
            if not db_conn.has_text(bid):
                warning = f"Book {bid} is not in the database."
                warnings.append(warning)
                if not env.as_json:
                    env.display.warning(f"warning: {_book_id_ref(bid)} is not in the database.")

        # Resolve section number → div path (requires book_id).
        if section_arg is not None:
            if section_arg.isdigit():
                section_number = int(section_arg)
                if section_number <= 0:
                    return _command_error(
                        "search", "--section number must be >= 1.", as_json=env.as_json
                    )
                if len(book) != 1:
                    return _command_error(
                        "search",
                        "--section with a number requires exactly one --book.",
                        as_json=env.as_json,
                    )
                summary = _build_section_summary(db_conn, book[0])
                if summary is None:
                    return _command_error(
                        "search",
                        f"Book {book[0]} has no sections.",
                        as_json=env.as_json,
                        display_message=f"{_book_id_ref(book[0])} has no sections.",
                    )
                sections = summary["sections"]
                if section_number > len(sections):
                    return _command_error(
                        "search",
                        f"Section {section_number} is out of range "
                        f"(book {book[0]} has {len(sections)} sections).",
                        as_json=env.as_json,
                        display_message=(
                            f"Section {section_number} is out of range "
                            f"({_book_id_ref(book[0], capitalize=False)} "
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
                        as_json=env.as_json,
                    )
                if len(book) == 1:
                    matched_section = _canonical_section_match(
                        _build_section_summary(db_conn, book[0]), section_arg
                    )
                    div_path = matched_section[0] if matched_section is not None else section_arg
                else:
                    div_path = section_arg

        search_author = author
        search_title = title
        search_book_ids = book if book else None
        search_kind = None if kind == "all" else kind
        search_div_path = div_path

        try:
            if count:
                total_results = db_conn.search_count(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_ids=search_book_ids,
                    kind=search_kind,
                    div_path=search_div_path,
                )
                results = []
            else:
                search_page = db_conn.search_page(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_ids=search_book_ids,
                    kind=search_kind,
                    div_path=search_div_path,
                    order=search_order,
                    limit=limit,
                )
                total_results = search_page.total_results
                results = search_page.items
        except sqlite3.Error as exc:
            return _command_error(
                "search",
                _format_fts_error(exc),
                as_json=env.as_json,
                data={
                    "query": {
                        "raw": query,
                        "fts": search_query,
                        "mode": query_mode,
                    },
                    "filters": _json_search_filters(
                        author=author,
                        title=title,
                        book_ids=book,
                        kind=kind,
                        section=section_arg,
                    ),
                    "order": order,
                    "limit": limit,
                    **({"radius": radius} if radius is not None else {}),
                },
                warnings=warnings,
            )

        result_items: list[dict[str, Any]] = []
        for idx, result in enumerate(results, start=1):
            section = _section_path(result.div1, result.div2, result.div3, result.div4)
            if radius is None:
                chunk_content = result.content
            else:
                rows = db_conn.chunk_window(result.book_id, result.position, around=radius)
                chunk_content = _joined_chunk_text(rows)
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
                    content=chunk_content,
                    extras={
                        "kind": result.kind,
                        "rank": idx,
                        "score": round(result.score, 4),
                    },
                )
            )

    # --count: just print the total.
    if count:
        if env.as_json:
            _print_json_envelope(
                "search",
                ok=True,
                data={
                    "query": {
                        "raw": query,
                        "fts": search_query,
                        "mode": query_mode,
                    },
                    "filters": _json_search_filters(
                        author=author,
                        title=title,
                        book_ids=book,
                        kind=kind,
                        section=section_arg,
                    ),
                    "count": total_results,
                },
                warnings=warnings,
            )
        else:
            print(total_results)
        return 0

    if env.as_json:
        data = {
            "query": {
                "raw": query,
                "fts": search_query,
                "mode": query_mode,
            },
            "filters": _json_search_filters(
                author=author,
                title=title,
                book_ids=book,
                kind=kind,
                section=section_arg,
            ),
            "order": order,
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
        env.display.status("No results.")
        return 0

    env.display.search_results(
        query=query,
        order=order,
        items=result_items,
        total_results=total_results,
    )
    return 0


# ---------------------------------------------------------------------------
# toc command
# ---------------------------------------------------------------------------


@click.command(
    "toc",
    help="show structural table of contents for a book",
    epilog="""
examples:
  gutenbit toc 2600
  gutenbit toc 100 --expand all
  gutenbit toc 2600 --json


if the book is missing, `toc` adds it automatically before rendering.


section numbers in this output can be passed to:
  gutenbit view 2600 --section <NUMBER>""",
)
@click.argument("book", type=int, metavar="BOOK_ID")
@click.option(
    "--expand",
    type=click.Choice(["1", "2", "3", "4", "all"]),
    default=DEFAULT_TOC_EXPAND,
    metavar="DEPTH",
    help="show heading levels up to this depth (default: 2; use 'all' for every level)",
)
@_common_options
def _cmd_toc(
    env: _CommandEnv,
    book: int,
    expand: str,
) -> int:
    expand_depth = _toc_expand_depth(expand)
    with Database(env.db_path) as db_conn:
        resolved_book_id, ingest_errors = _resolve_toc_book_id(
            db_conn,
            book,
            refresh=False,
            display=env.display,
            as_json=env.as_json,
            process_books_for_ingest=_process_books_for_ingest,
        )
        if resolved_book_id is None:
            if env.as_json:
                _print_json_envelope(
                    "toc",
                    ok=False,
                    data={JSON_BOOK_ID_KEY: book},
                    errors=ingest_errors or [f"Failed to add book {book}."],
                )
            return 1
        if env.as_json:
            summary = _build_section_summary(db_conn, resolved_book_id, expand_depth=expand_depth)
            if summary is None:
                return _command_error(
                    "toc",
                    _no_chunks_messages(db_conn, resolved_book_id)[0],
                    as_json=True,
                    data={JSON_BOOK_ID_KEY: book},
                )
            _print_json_envelope(
                "toc",
                ok=True,
                data={
                    JSON_BOOK_ID_KEY: book,
                    "expand": expand,
                    "toc": _section_summary_json_payload(summary),
                },
            )
            return 0
        return _render_section_summary(db_conn, resolved_book_id, expand_depth=expand_depth)


# ---------------------------------------------------------------------------
# view command
# ---------------------------------------------------------------------------


@click.command(
    "view",
    help="read stored book text, or focused parts of it",
    epilog="""
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
@click.argument("book", type=int, metavar="BOOK_ID")
@click.option(
    "--position", type=int, default=None, help="select the passage at this exact position"
)
@click.option(
    "--section",
    default=None,
    help='read from a section selector: path prefix (e.g. PART ONE/CHAPTER I) or section number from `toc` (e.g. "3")',
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="read the full selected scope (whole book or selected section, including nested subsections)",
)
@click.option(
    "--forward",
    type=int,
    default=None,
    help="passages to read forward (default: opening=3, section/position=1)",
)
@click.option(
    "--radius",
    type=int,
    default=None,
    help="surrounding passage on each side of the selected passage",
)
@_common_options
def _cmd_view(
    env: _CommandEnv,
    book: int,
    position: int | None,
    section: str | None,
    show_all: bool,
    forward: int | None,
    radius: int | None,
) -> int:
    selected = int(position is not None) + int(section is not None)
    if selected > 1:
        return _command_error(
            "view",
            "Choose at most one selector: --position or --section.",
            as_json=env.as_json,
        )
    if forward is not None and forward <= 0:
        return _command_error("view", "--forward must be > 0.", as_json=env.as_json)
    if radius is not None and radius < 0:
        return _command_error("view", "--radius must be >= 0.", as_json=env.as_json)
    shapes_selected = sum(
        int(value)
        for value in [
            forward is not None,
            radius is not None,
            show_all,
        ]
    )
    if shapes_selected > 1:
        return _command_error(
            "view",
            (
                "Choose one retrieval shape: --forward for forward reading, "
                "--radius for a surrounding passage window, or --all for a full book or section."
            ),
            as_json=env.as_json,
        )
    if radius is not None and selected == 0:
        return _command_error(
            "view",
            "--radius requires --position or --section.",
            as_json=env.as_json,
        )
    if show_all and position is not None:
        return _command_error(
            "view",
            "--all can be used with a book or section, not with --position.",
            as_json=env.as_json,
        )

    def _effective_forward(default: int) -> int:
        return forward if forward is not None else default

    requested_forward = (
        None if radius is not None or show_all else _effective_forward(DEFAULT_VIEW_FORWARD)
    )
    requested_all = True if show_all else None
    with Database(env.db_path) as db_conn:
        section_number_for = _section_number_lookup(db_conn)
        book_record = db_conn.book(book)
        title = _single_line(book_record.title) if book_record else f"Book {book}"
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
                book_id=book,
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

        if position is not None:
            anchor = db_conn.chunk_by_position(book, position)
            if anchor is None:
                return _command_error(
                    "view",
                    f"No chunk found at position {position} in book {book}.",
                    as_json=env.as_json,
                    display_message=(
                        f"No chunk found at position {position} in "
                        f"{_book_id_ref(book, capitalize=False)}."
                    ),
                    data=_view_payload(
                        section=None,
                        section_number=None,
                        position=position,
                        forward=requested_forward,
                        radius=radius,
                        all_scope=requested_all,
                    ),
                )
            anchor_section = _section_path(anchor.div1, anchor.div2, anchor.div3, anchor.div4)
            anchor_section_number = section_number_for(book, anchor_section)
            if radius is not None:
                rows = db_conn.chunk_window(book, position, around=radius)
                forward = None
                all_scope = None
            else:
                forward = _effective_forward(DEFAULT_VIEW_FORWARD)
                all_scope = None
                rows = [row for row in db_conn.chunk_records(book) if row.position >= position]
                rows = rows[:forward]
            record = _view_payload(
                section=anchor_section,
                section_number=anchor_section_number,
                position=position,
                forward=forward,
                radius=radius,
                all_scope=all_scope,
                content=_joined_chunk_text(rows),
            )
            if env.as_json:
                _print_json_envelope("view", ok=True, data=record)
                return 0
            _print_passage(record, footer_stats=_view_footer_stats(rows))
            return 0

        if section is not None:
            section_query = section.strip()
            if not section_query:
                return _command_error(
                    "view",
                    "--section must not be empty.",
                    as_json=env.as_json,
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
                        as_json=env.as_json,
                        data=_view_payload(
                            section=section_query,
                            section_number=None,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                        ),
                    )
                summary = _build_section_summary(db_conn, book)
                if summary is None:
                    nc_msg, nc_display = _no_chunks_messages(db_conn, book)
                    return _command_error(
                        "view",
                        nc_msg,
                        as_json=env.as_json,
                        display_message=nc_display,
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
                        f"{book} (max {len(raw_sections)})."
                    )
                    display_message = (
                        f"Section {section_number} is out of range for "
                        f"{_book_id_ref(book, capitalize=False)} "
                        f"(max {len(raw_sections)})."
                    )
                    examples = _section_examples(db_conn, book)
                    if env.as_json:
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
                                    "tip": f"gutenbit toc {book}",
                                },
                            ),
                        )
                    env.display.examples(
                        display_message,
                        examples=examples,
                        tip=f"gutenbit toc {book}",
                    )
                    return 1
                selected_section = raw_sections[section_number - 1]
                if not isinstance(selected_section, dict):
                    return _command_error(
                        "view",
                        (f"Unable to resolve section number {section_number} for book {book}."),
                        as_json=env.as_json,
                        display_message=(
                            f"Unable to resolve section number {section_number} "
                            f"for {_book_id_ref(book, capitalize=False)}."
                        ),
                        data=_view_payload(
                            section=section_query,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                            extras={"tip": f"gutenbit toc {book}"},
                        ),
                    )
                resolved_section = selected_section["section"].strip()
                if not resolved_section:
                    return _command_error(
                        "view",
                        (f"Unable to resolve section number {section_number} for book {book}."),
                        as_json=env.as_json,
                        display_message=(
                            f"Unable to resolve section number {section_number} "
                            f"for {_book_id_ref(book, capitalize=False)}."
                        ),
                        data=_view_payload(
                            section=section_query,
                            section_number=section_number,
                            position=None,
                            forward=requested_forward,
                            radius=radius,
                            all_scope=requested_all,
                            extras={"tip": f"gutenbit toc {book}"},
                        ),
                    )
            else:
                section_number = section_number_for(book, resolved_section)
                try:
                    matched_section = _canonical_section_match(
                        _build_section_summary(db_conn, book), resolved_section
                    )
                except ValueError as exc:
                    return _command_error(
                        "view",
                        f"Invalid section selector: {exc}.",
                        as_json=env.as_json,
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

            rows = db_conn.chunks_by_div(book, resolved_section, limit=0)
            if not rows:
                examples = _section_examples(db_conn, book)
                message = f"No chunks found for book {book} under section '{section_query}'."
                display_message = (
                    f"No chunks found for {_book_id_ref(book, capitalize=False)} "
                    f"under section '{section_query}'."
                )
                if env.as_json:
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
                                "tip": f"gutenbit toc {book}",
                            },
                        ),
                    )
                env.display.examples(
                    display_message,
                    examples=examples,
                    tip=f"gutenbit toc {book}",
                )
                return 1
            anchor = rows[0]
            if radius is not None:
                rows = db_conn.chunk_window(book, anchor.position, around=radius)
                forward = None
                all_scope = None
            elif show_all:
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
            if env.as_json:
                _print_json_envelope("view", ok=True, data=record)
                return 0
            _print_passage(record, footer_stats=_view_footer_stats(rows))
            return 0

        forward = _effective_forward(DEFAULT_OPENING_CHUNK_COUNT)
        summary = _build_section_summary(db_conn, book)
        action_hints = _view_action_hints(book, summary)
        first_section = summary["sections"][0] if summary and summary["sections"] else None
        if show_all:
            rows = db_conn.chunk_records(book)
            if not rows:
                nc_msg, nc_display = _no_chunks_messages(db_conn, book)
                return _command_error(
                    "view",
                    nc_msg,
                    as_json=env.as_json,
                    display_message=nc_display,
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
                section_number=section_number_for(book, anchor_section),
                position=anchor.position,
                forward=None,
                radius=None,
                all_scope=True,
                content=_joined_chunk_text(rows),
            )
            if env.as_json:
                _print_json_envelope(
                    "view",
                    ok=True,
                    data={**record, "action_hints": action_hints},
                )
                return 0
            env.display.passage(
                record,
                action_hints=action_hints,
                footer_stats=_view_footer_stats(rows),
            )
            return 0

        rows = _opening_rows(db_conn, book, forward)
        if not rows:
            nc_msg, nc_display = _no_chunks_messages(db_conn, book)
            return _command_error(
                "view",
                nc_msg,
                as_json=env.as_json,
                display_message=nc_display,
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
            section_number=section_number_for(book, anchor_section),
            position=anchor.position,
            forward=forward,
            radius=None,
            all_scope=None,
            content=_joined_chunk_text(rows),
        )
        if env.as_json:
            _print_json_envelope(
                "view",
                ok=True,
                data={**record, "action_hints": action_hints},
            )
            return 0
        env.display.passage(
            record,
            action_hints=action_hints,
            footer_stats=_view_footer_stats(rows),
        )
        return 0
