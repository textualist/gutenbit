"""Command-line interface for gutenbit."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any, cast

import click

from gutenbit._cli_sections import (
    _QuickActions,
    _SectionRow,
    _SectionSummary,
    _build_section_summary,
    _canonical_section_match,
    _collapse_section_rows,
    _opening_rows,
    _section_depth,
    _section_examples,
    _section_number_lookup,
    _section_path,
    _section_path_parts,
    _section_reading_window,
    _section_selector_parts,
    _section_summary_json_payload,
    _select_section_opening_line,
    _truncate_section_label,
    _visible_section_number,
)
from gutenbit._cli_utils import (
    _estimate_read_time,
    _format_fts_error,
    _format_int,
    _fts_phrase_query,
    _has_fts_operators,
    _indent_block,
    _joined_chunk_text,
    _package_version,
    _preview,
    _print_block_header,
    _print_key_value_table,
    _print_table,
    _quick_action_search_query,
    _safe_fts_query,
    _single_line,
    _split_semicolon_list,
    _summarize_semicolon_list,
    _toc_expand_depth,
)
from gutenbit.catalog import BookRecord, Catalog, CatalogFetchInfo
from gutenbit.db import (
    ChunkRecord,
    Database,
    IngestProgressCallback,
    SearchOrder,
    TextState,
)
from gutenbit.display import CliDisplay, format_summary_stats
from gutenbit.download import describe_download_source, get_last_download_source

STATE_DIR_NAME = ".gutenbit"
DEFAULT_DB_NAME = "gutenbit.db"
DEFAULT_DB = f"~/{STATE_DIR_NAME}/{DEFAULT_DB_NAME}"
DEFAULT_DOWNLOAD_DELAY = 2.0
DEFAULT_TOC_EXPAND = "2"
DEFAULT_OPENING_CHUNK_COUNT = 3
DEFAULT_VIEW_FORWARD = 1
JSON_BOOK_ID_KEY = "book_id"

_DISPLAY_CACHE: tuple[int, int, CliDisplay] | None = None

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

_EPILOG = """\b
quick start:
  1. gutenbit catalog --author "Austen, Jane"                   # find Pride and Prejudice
  2. gutenbit add 1342                                          # download and store it
  3. gutenbit toc 1342                                          # inspect numbered sections
  4. gutenbit view 1342                                         # read the opening
  5. gutenbit search "truth universally acknowledged" --book 1342 --phrase

\b
learn more:
  gutenbit COMMAND --help    detailed help for one command

\b
gutenbit is an open-source project not affiliated with Project Gutenberg. It is for
individual downloads, not bulk downloading. By default, all application data is
stored at ~/.gutenbit."""


class _GutenbitGroup(click.Group):
    """Click Group with gutenbit-style help formatting."""

    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_usage(ctx.command_path, "[OPTIONS] COMMAND ...")

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or getattr(cmd, "hidden", False):
                continue
            commands.append((name, cmd))
        if commands:
            limit = formatter.width - 6 - max(len(name) for name, _ in commands)
            rows = [(name, cmd.get_short_help_str(limit)) for name, cmd in commands]
            if rows:
                with formatter.section("commands"):
                    formatter.write_dl(rows)


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


# ---------------------------------------------------------------------------
# CLI group and subcommand definitions
# ---------------------------------------------------------------------------


@click.group(
    cls=_GutenbitGroup,
    invoke_without_command=True,
    context_settings=_CONTEXT_SETTINGS,
    epilog=_EPILOG,
)
@click.option("--db", default=DEFAULT_DB, metavar="DB", help=_DB_HELP)
@click.version_option(_package_version(), prog_name="gutenbit", message="%(prog)s %(version)s")
@click.option("-v", "--verbose", is_flag=True, help=_VERBOSE_HELP)
@click.pass_context
def _cli(ctx: click.Context, db: str, verbose: bool) -> None:
    """A tool for fast local search across public-domain literary works.
    Find, browse and search books from your terminal."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["verbose"] = verbose
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def _resolve_db(ctx: click.Context, db: str | None) -> str:
    """Return effective db path: subcommand override takes precedence over group default."""
    if db is not None:
        return db
    return ctx.obj.get("db", DEFAULT_DB)


def _resolve_verbose(ctx: click.Context, verbose: bool) -> bool:
    """Return effective verbose flag: either source activates it."""
    return verbose or ctx.obj.get("verbose", False)



# -------------------------------------------------------------------
# Subcommand handlers
# -------------------------------------------------------------------


@_cli.command(
    "catalog",
    help="search the Project Gutenberg catalog",
    epilog="""
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
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option(
    "--refresh",
    is_flag=True,
    help="ignore the catalog cache and redownload it now",
)
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_catalog(
    ctx: click.Context,
    author: str,
    title: str,
    language: str,
    subject: str,
    limit: int,
    json_output: bool,
    refresh: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    if limit <= 0:
        return _command_error("catalog", "--limit must be > 0.", as_json=as_json)

    catalog = _load_catalog(refresh, display=display, as_json=as_json)
    results = catalog.search(
        author=author,
        title=title,
        language=language,
        subject=subject,
    )

    shown = results[:limit]
    if as_json:
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


@_cli.command(
    "add",
    help="download and store books by PG id",
    epilog="""
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
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option(
    "--refresh",
    is_flag=True,
    help="ignore the catalog cache, redownload it now, and reprocess matching stored books",
)
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_add(
    ctx: click.Context,
    book_ids: tuple[int, ...],
    delay: float,
    json_output: bool,
    refresh: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    if delay < 0:
        return _command_error("add", "--delay must be >= 0.", as_json=as_json)

    if not book_ids:
        raise click.UsageError("At least one BOOK_ID is required.")

    invalid_ids: list[int] = [bid for bid in book_ids if bid <= 0]
    if invalid_ids:
        return _command_error(
            "add",
            f"Book IDs must be positive integers, got: {', '.join(str(bid) for bid in invalid_ids)}",
            as_json=as_json,
            data={"invalid_ids": invalid_ids},
        )

    catalog = _load_catalog(refresh, display=display, as_json=as_json)
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
            "db": str(_resolved_cli_path(effective_db)),
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
            as_json=as_json,
            data=data,
            warnings=warnings,
        )

    with Database(effective_db) as db_conn:
        canonical_statuses, errors = _process_books_for_ingest(
            db_conn,
            books,
            delay=delay,
            as_json=as_json,
            display=display,
            failure_action="add",
            force=refresh,
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
            "db": str(_resolved_cli_path(effective_db)),
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
        display.error(
            f"Completed with {len(errors)} failure(s). Database: {_display_cli_path(effective_db)}"
        )
        return 1
    display.success(f"Done. Database: {_display_cli_path(effective_db)}")
    return 0


@_cli.command(
    "books",
    help="list or update books stored in the database",
    epilog="""
examples:
  gutenbit books
  gutenbit books --json
  gutenbit books --update
  gutenbit books --update --force
  gutenbit books --db my.db

output columns:  ID  AUTHORS  TITLE""",
)
@click.option("--update", is_flag=True, help="reprocess stored books whose parser version is stale")
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
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_books(
    ctx: click.Context,
    update: bool,
    delay: float,
    force: bool,
    dry_run: bool,
    json_output: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    if not update:
        if delay != DEFAULT_DOWNLOAD_DELAY:
            return _command_error(
                "books",
                "--delay can only be used with --update.",
                as_json=as_json,
            )
        if force:
            return _command_error(
                "books",
                "--force can only be used with --update.",
                as_json=as_json,
            )
        if dry_run:
            return _command_error(
                "books",
                "--dry-run can only be used with --update.",
                as_json=as_json,
            )
    elif delay < 0:
        return _command_error("books", "--delay must be >= 0.", as_json=as_json)

    with Database(effective_db) as db_conn:
        books = db_conn.books()
        if update:
            db_path = str(_resolved_cli_path(effective_db))
            db_display_path = _display_cli_path(effective_db)
            stored_count = len(books)
            selected_books = books if force else db_conn.stale_books()
            selected_count = len(selected_books)
            skipped_current = 0 if force else stored_count - selected_count

            if not books:
                if as_json:
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
                    display.status("No books stored yet. Use 'gutenbit add <id> ...' to add some.")
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
                if as_json:
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
                    display.status(
                        f"Would reprocess {selected_count} of {stored_count} stored book(s):"
                    )
                    for book in selected_books:
                        display.status(f"  {book.id}: {_single_line(book.title)}")
                else:
                    display.status(
                        "All "
                        f"{stored_count} stored book(s) are current. "
                        f"Database: {db_display_path}"
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
                    display.success(
                        "All "
                        f"{stored_count} stored book(s) are current. "
                        f"Database: {db_display_path}"
                    )
                return 0

            if not as_json:
                display.status(f"Checking {stored_count} stored book(s)...")

            statuses, errors = _process_books_for_ingest(
                db_conn,
                selected_books,
                delay=delay,
                as_json=as_json,
                display=display,
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

            if as_json:
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
                display.error(
                    "Completed with "
                    f"{failed_count} failure(s). Updated {updated_count} book(s); "
                    f"{skipped_current} already current. Database: {db_display_path}"
                )
                return 1
            display.success(
                f"Done. Updated {updated_count} book(s); "
                f"{skipped_current} already current. Database: {db_display_path}"
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
    display.books(books, db_path=_display_cli_path(effective_db))
    return 0


@_cli.command(
    "remove",
    help="remove stored books by PG id",
    epilog="""
examples:
  gutenbit remove 46
  gutenbit remove 46 730 967
  gutenbit remove 2600 --db my.db

if a book ID is not present, a warning is printed and exit code is 1.""",
)
@click.argument("book_ids", nargs=-1, type=int, metavar="BOOK_ID")
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_remove(
    ctx: click.Context,
    book_ids: tuple[int, ...],
    json_output: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    if not book_ids:
        raise click.UsageError("At least one BOOK_ID is required.")
    any_missing = False
    removed_count = 0
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with Database(effective_db) as db_conn:
        for book_id in book_ids:
            removed = db_conn.remove_book(book_id)
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
                        f"Removed {_book_id_ref(book_id, capitalize=False)} "
                        f"from {_display_cli_path(effective_db)}."
                    )
    if as_json:
        _print_json_envelope(
            "remove",
            ok=not any_missing,
            data={
                "db": str(_resolved_cli_path(effective_db)),
                "removed_count": removed_count,
                "missing_count": len(book_ids) - removed_count,
                "results": results,
            },
            errors=errors,
        )
    return 1 if any_missing else 0


@_cli.command(
    "search",
    help="full-text search across stored books",
    epilog="""
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


query modes:
  (default)  plain text — punctuation is auto-escaped, words are AND'd
  --phrase   exact phrase — word order and adjacency must match exactly
  --raw      FTS5 syntax — AND, OR, NOT, NEAR(), prefix*, "phrases", (groups)


result order:
  rank    BM25 rank, then book, then position (default)
  first   book ascending, then position ascending
  last    book descending, then position descending


tip: use 'gutenbit toc <id>' first to see a book's structure, then
     narrow searches with --book and --section. Search uses text chunks
     by default; use --kind heading or --kind all when needed.""",
)
@click.argument("query", metavar="QUERY")
@click.option("--phrase", is_flag=True, help="treat query as an exact phrase (word order must match)")
@click.option("--raw", is_flag=True, help="pass query directly to FTS5 (AND/OR/NOT, prefix*, NEAR, groups)")
@click.option(
    "--order",
    type=click.Choice(["rank", "first", "last"]),
    default="rank",
    metavar="ORDER",
    help="search result order: rank (BM25); first (book asc + position asc); last (book desc + position desc)",
)
@click.option("--author", default=None, help="filter results by author (substring match)")
@click.option("--title", default=None, help="filter results by title (substring match)")
@click.option("--book", type=int, default=None, help="restrict to a single book by PG ID")
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
@click.option("--json", "json_output", is_flag=True, help="output results as JSON")
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_search(
    ctx: click.Context,
    query: str,
    phrase: bool,
    raw: bool,
    order: str,
    author: str | None,
    title: str | None,
    book: int | None,
    kind: str,
    section: str | None,
    limit: int,
    radius: int | None,
    count: bool,
    json_output: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()

    if phrase and raw:
        return _command_error("search", "--phrase and --raw are mutually exclusive.", as_json=as_json)
    if limit <= 0:
        return _command_error("search", "--limit must be > 0.", as_json=as_json)
    if radius is not None and radius < 0:
        return _command_error("search", "--radius must be >= 0.", as_json=as_json)
    if count and radius is not None:
        return _command_error(
            "search",
            "--radius cannot be used with --count.",
            as_json=as_json,
        )

    query_text = query.strip()
    if not query_text:
        return _command_error("search", "Search query must not be empty.", as_json=as_json)

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
    with Database(effective_db) as db_conn:
        section_number_for = _section_number_lookup(db_conn)

        if book is not None and not db_conn.has_text(book):
            warning = f"Book {book} is not in the database."
            warnings.append(warning)
            if not as_json:
                display.warning(f"warning: {_book_id_ref(book)} is not in the database.")

        # Resolve section number → div path (requires book_id).
        if section_arg is not None:
            if section_arg.isdigit():
                section_number = int(section_arg)
                if section_number <= 0:
                    return _command_error(
                        "search", "--section number must be >= 1.", as_json=as_json
                    )
                if book is None:
                    return _command_error(
                        "search",
                        "--section with a number requires --book.",
                        as_json=as_json,
                    )
                summary = _build_section_summary(db_conn, book)
                if summary is None:
                    return _command_error(
                        "search",
                        f"Book {book} has no sections.",
                        as_json=as_json,
                        display_message=f"{_book_id_ref(book)} has no sections.",
                    )
                sections = summary["sections"]
                if section_number > len(sections):
                    return _command_error(
                        "search",
                        f"Section {section_number} is out of range "
                        f"(book {book} has {len(sections)} sections).",
                        as_json=as_json,
                        display_message=(
                            f"Section {section_number} is out of range "
                            f"({_book_id_ref(book, capitalize=False)} "
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
                if book is not None:
                    matched_section = _canonical_section_match(
                        _build_section_summary(db_conn, book), section_arg
                    )
                    div_path = matched_section[0] if matched_section is not None else section_arg
                else:
                    div_path = section_arg

        search_author = author
        search_title = title
        search_book_id = book
        search_kind = None if kind == "all" else kind
        search_div_path = div_path

        try:
            if count:
                total_results = db_conn.search_count(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_id=search_book_id,
                    kind=search_kind,
                    div_path=search_div_path,
                )
                results = []
            else:
                search_page = db_conn.search_page(
                    search_query,
                    author=search_author,
                    title=search_title,
                    book_id=search_book_id,
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
                as_json=as_json,
                data={
                    "query": {
                        "raw": query,
                        "fts": search_query,
                        "mode": query_mode,
                    },
                    "filters": _json_search_filters(
                        author=author,
                        title=title,
                        book_id=book,
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
        if as_json:
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
                        book_id=book,
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

    if as_json:
        data = {
            "query": {
                "raw": query,
                "fts": search_query,
                "mode": query_mode,
            },
            "filters": _json_search_filters(
                author=author,
                title=title,
                book_id=book,
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
        display.status("No results.")
        return 0

    display.search_results(
        query=query,
        order=order,
        items=result_items,
        total_results=total_results,
    )
    return 0


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
    refresh: bool = False,
    display: CliDisplay,
    as_json: bool,
) -> tuple[int | None, list[str]]:
    """Resolve a toc request to stored text, auto-adding the canonical book when needed."""
    if db.has_text(requested_id):
        return requested_id, []

    catalog = _load_catalog(refresh, display=display, as_json=as_json)
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


@_cli.command(
    "toc",
    help="show structural table of contents for a book",
    epilog="""
examples:
  gutenbit toc 2600
  gutenbit toc 100 --expand all
  gutenbit toc 2600 --json


if the book is missing, `toc` adds it automatically before rendering.


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
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_toc(
    ctx: click.Context,
    book: int,
    expand: str,
    json_output: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    expand_depth = _toc_expand_depth(expand)
    with Database(effective_db) as db_conn:
        resolved_book_id, ingest_errors = _resolve_toc_book_id(
            db_conn,
            book,
            refresh=False,
            display=display,
            as_json=as_json,
        )
        if resolved_book_id is None:
            if as_json:
                _print_json_envelope(
                    "toc",
                    ok=False,
                    data={JSON_BOOK_ID_KEY: book},
                    errors=ingest_errors or [f"Failed to add book {book}."],
                )
            return 1
        if as_json:
            summary = _build_section_summary(db_conn, resolved_book_id, expand_depth=expand_depth)
            if summary is None:
                return _command_error(
                    "toc",
                    _no_chunks_message(db_conn, resolved_book_id),
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


@_cli.command(
    "view",
    help="read stored book text, or focused parts of it",
    epilog="""
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


selectors (choose at most one):
  --position <n> | --section <SECTION_SELECTOR>
""",
)
@click.argument("book", type=int, metavar="BOOK_ID")
@click.option("--position", type=int, default=None, help="select the passage at this exact position")
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
@click.option("--json", "json_output", is_flag=True, help="output as JSON")
@click.option("--db", default=None, metavar="DB", help=_DB_OVERRIDE_HELP)
@click.option("-v", "--verbose", is_flag=True, default=False, help=_VERBOSE_HELP)
@click.pass_context
def _cmd_view(
    ctx: click.Context,
    book: int,
    position: int | None,
    section: str | None,
    show_all: bool,
    forward: int | None,
    radius: int | None,
    json_output: bool,
    db: str | None,
    verbose: bool,
) -> int:
    effective_db = _resolve_db(ctx, db)
    if _resolve_verbose(ctx, verbose):
        _configure_logging(True)
    as_json = json_output
    display = _display()
    selected = int(position is not None) + int(section is not None)
    if selected > 1:
        return _command_error(
            "view",
            "Choose at most one selector: --position or --section.",
            as_json=as_json,
        )
    if forward is not None and forward <= 0:
        return _command_error("view", "--forward must be > 0.", as_json=as_json)
    if radius is not None and radius < 0:
        return _command_error("view", "--radius must be >= 0.", as_json=as_json)
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
            as_json=as_json,
        )
    if radius is not None and selected == 0:
        return _command_error(
            "view",
            "--radius requires --position or --section.",
            as_json=as_json,
        )
    if show_all and position is not None:
        return _command_error(
            "view",
            "--all can be used with a book or section, not with --position.",
            as_json=as_json,
        )

    def _effective_forward(default: int) -> int:
        return forward if forward is not None else default

    requested_forward = (
        None if radius is not None or show_all else _effective_forward(DEFAULT_VIEW_FORWARD)
    )
    requested_all = True if show_all else None
    with Database(effective_db) as db_conn:
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
                    as_json=as_json,
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
                rows = [
                    row for row in db_conn.chunk_records(book) if row.position >= position
                ]
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
            if as_json:
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
                summary = _build_section_summary(db_conn, book)
                if summary is None:
                    return _command_error(
                        "view",
                        _no_chunks_message(db_conn, book),
                        as_json=as_json,
                        display_message=_no_chunks_display_message(db_conn, book),
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
                                    "tip": f"gutenbit toc {book}",
                                },
                            ),
                        )
                    display.examples(
                        display_message,
                        examples=examples,
                        tip=f"gutenbit toc {book}",
                    )
                    return 1
                selected_section = raw_sections[section_number - 1]
                if not isinstance(selected_section, dict):
                    return _command_error(
                        "view",
                        (
                            f"Unable to resolve section number {section_number} "
                            f"for book {book}."
                        ),
                        as_json=as_json,
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
                        (
                            f"Unable to resolve section number {section_number} "
                            f"for book {book}."
                        ),
                        as_json=as_json,
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

            rows = db_conn.chunks_by_div(book, resolved_section, limit=0)
            if not rows:
                examples = _section_examples(db_conn, book)
                message = f"No chunks found for book {book} under section '{section_query}'."
                display_message = (
                    f"No chunks found for {_book_id_ref(book, capitalize=False)} "
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
                                "tip": f"gutenbit toc {book}",
                            },
                        ),
                    )
                display.examples(
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
            if as_json:
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
                return _command_error(
                    "view",
                    _no_chunks_message(db_conn, book),
                    as_json=as_json,
                    display_message=_no_chunks_display_message(db_conn, book),
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

        rows = _opening_rows(db_conn, book, forward)
        if not rows:
            return _command_error(
                "view",
                _no_chunks_message(db_conn, book),
                as_json=as_json,
                display_message=_no_chunks_display_message(db_conn, book),
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


def main(argv: list[str] | None = None) -> int:
    try:
        result = _cli.main(args=argv, standalone_mode=False)
        return result if isinstance(result, int) else 0
    except click.exceptions.UsageError as exc:
        exc.show()
        sys.exit(2)
    except click.exceptions.Abort:
        _display().error("\nInterrupted.")
        return 130
    except KeyboardInterrupt:
        _display().error("\nInterrupted.")
        return 130
    except Exception as exc:
        ctx = click.get_current_context(silent=True)
        verbose = False
        as_json = False
        command = "gutenbit"
        if ctx is not None:
            verbose = ctx.params.get("verbose", False) or (ctx.obj or {}).get(
                "verbose", False
            )
            as_json = ctx.params.get("json_output", False)
            command = ctx.info_name or command
        if verbose:
            traceback.print_exc()
        if as_json:
            _print_json_envelope(command, ok=False, errors=[str(exc)])
        else:
            _display().error(f"Error: {exc}", err=True)
        return 1


def _entry_point() -> None:
    """Console-scripts entry point."""
    sys.exit(main())
