"""Command-line interface for gutenbit."""

from __future__ import annotations

import sys
import traceback
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

import click

from gutenbit._cli_commands import (
    _cmd_add,
    _cmd_books,
    _cmd_catalog,
    _cmd_remove,
    _cmd_search,
    _cmd_toc,
    _cmd_view,
)

# ---------------------------------------------------------------------------
# Re-exports: these names are imported by tests and other consumers from
# gutenbit.cli — keep them accessible here.
# ---------------------------------------------------------------------------
from gutenbit._cli_helpers import (  # noqa: F401
    _CONTEXT_SETTINGS,
    _DB_HELP,
    _VERBOSE_HELP,
    DEFAULT_DB,
    _display,
    _display_cli_path,
    _passage_payload,
    _print_json_envelope,
)
from gutenbit._cli_sections import (  # noqa: F401
    _build_section_summary,
    _select_section_opening_line,
)
from gutenbit.catalog import Catalog as Catalog  # noqa: F401

__all__ = [
    "Catalog",
    "DEFAULT_DB",
    "_CONTEXT_SETTINGS",
    "_DB_HELP",
    "_VERBOSE_HELP",
    "_build_section_summary",
    "_cli",
    "_display",
    "_display_cli_path",
    "_entry_point",
    "_passage_payload",
    "_print_json_envelope",
    "_select_section_opening_line",
    "main",
]

# ---------------------------------------------------------------------------
# CLI epilog
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Custom Click Group
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Package version
# ---------------------------------------------------------------------------


def _package_version() -> str:
    try:
        return package_version("gutenbit")
    except PackageNotFoundError:
        try:
            from gutenbit import __version__
        except ImportError:
            return "0.dev0+unknown"
        return __version__


# ---------------------------------------------------------------------------
# CLI group definition
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


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------

_cli.add_command(_cmd_catalog)
_cli.add_command(_cmd_add)
_cli.add_command(_cmd_books)
_cli.add_command(_cmd_remove)
_cli.add_command(_cmd_search)
_cli.add_command(_cmd_toc)
_cli.add_command(_cmd_view)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


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
