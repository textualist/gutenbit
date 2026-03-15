"""Click infrastructure, command environment, display singleton, and path helpers."""

from __future__ import annotations

import functools
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from gutenbit.catalog import Catalog, CatalogFetchInfo
from gutenbit.cli._display import CliDisplay

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_DIR_NAME = ".gutenbit"
DEFAULT_DB_NAME = "gutenbit.db"
DEFAULT_DB = f"~/{STATE_DIR_NAME}/{DEFAULT_DB_NAME}"

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
