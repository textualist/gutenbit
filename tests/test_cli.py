"""CLI-specific regression tests."""

from __future__ import annotations

import contextlib
import io

import pytest

from gutenbit.catalog import BookRecord
from gutenbit.cli import main as cli_main
from gutenbit.db import Database
from gutenbit.html_chunker import chunk_html

_BOOK = BookRecord(
    id=1,
    title="Moby Dick",
    authors="Melville, Herman",
    language="en",
    subjects="Whaling; Sea stories",
    locc="PS",
    bookshelves="Best Books Ever Listings",
    issued="2001-06-01",
    type="Text",
)

_BOOK_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><title>Moby Dick</title></head>
<body>
<h2><a id="ch1"></a>CHAPTER 1</h2>
<p>Call me Ishmael.</p>
</body>
</html>
"""


def _run_cli(*args: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(list(args))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def test_version_flag_prints_non_empty_version():
    out = io.StringIO()
    err = io.StringIO()

    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main(["--version"])

    assert excinfo.value.code == 0
    assert err.getvalue() == ""
    rendered = out.getvalue().strip()
    assert rendered.startswith("gutenbit ")
    assert rendered != "gutenbit "


def test_help_shows_project_local_default_db():
    out = io.StringIO()
    err = io.StringIO()

    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main(["--help"])

    assert excinfo.value.code == 0
    assert err.getvalue() == ""
    assert ".gutenbit/gutenbit.db" in out.getvalue()


def test_help_shows_pride_and_prejudice_workflow():
    out = io.StringIO()
    err = io.StringIO()

    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main(["--help"])

    assert excinfo.value.code == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert 'gutenbit catalog --author "Austen, Jane"' in rendered
    assert "gutenbit add 1342" in rendered
    assert "gutenbit toc 1342" in rendered
    assert "gutenbit view 1342" in rendered
    assert 'gutenbit search "truth universally acknowledged" --book 1342 --phrase' in rendered


def test_help_uses_command_placeholder_instead_of_choice_braces():
    out = io.StringIO()
    err = io.StringIO()

    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main(["--help"])

    assert excinfo.value.code == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert "COMMAND ..." in rendered
    assert "{catalog,add,remove,books,search,toc,view}" not in rendered
    assert "commands:" in rendered
    assert "gutenbit COMMAND --help" in rendered
    assert "project gutenberg:" not in rendered
    assert "local state:" not in rendered
    assert "not affiliated with Project Gutenberg" in rendered
    assert "stores its SQLite database and catalog cache in" in rendered


@pytest.mark.parametrize(
    ("argv", "forbidden", "expected"),
    [
        (("search", "-h"), "{rank,first,last}", "--order ORDER"),
        (("search", "-h"), "{text,heading,all}", "--kind KIND"),
        (("toc", "-h"), "{1,2,3,4,all}", "--expand DEPTH"),
    ],
)
def test_choice_help_uses_named_metavars(argv, forbidden, expected):
    code, out, err = _run_cli(*argv)

    assert code == 0
    assert err == ""
    assert forbidden not in out
    assert expected in out


def test_delete_subcommand_is_rejected():
    code, _out, err = _run_cli("delete", "1")

    assert code == 2
    assert "invalid choice: 'delete'" in err
    assert "remove" in err


def test_books_creates_default_db_under_project_state_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code, out, err = _run_cli("books")

    assert code == 0
    assert err == ""
    assert "No books stored yet." in out
    assert (tmp_path / ".gutenbit" / "gutenbit.db").exists()
    assert not (tmp_path / "gutenbit.db").exists()


def test_default_cli_db_does_not_auto_discover_legacy_root_db(tmp_path, monkeypatch):
    legacy_db = tmp_path / "gutenbit.db"
    with Database(legacy_db) as db:
        db._store(_BOOK, chunk_html(_BOOK_HTML))

    monkeypatch.chdir(tmp_path)

    code, out, err = _run_cli("books")
    explicit_code, explicit_out, explicit_err = _run_cli("--db", "gutenbit.db", "books")

    assert code == 0
    assert err == ""
    assert "No books stored yet." in out
    assert (tmp_path / ".gutenbit" / "gutenbit.db").exists()

    assert explicit_code == 0
    assert explicit_err == ""
    assert "1 book(s) stored in gutenbit.db" in explicit_out
