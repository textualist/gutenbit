"""CLI-specific regression tests."""

from __future__ import annotations

import contextlib
import io
from importlib.metadata import version as package_version

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


def test_version_flag_matches_installed_metadata():
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
    assert out.getvalue().strip() == f"gutenbit {package_version('gutenbit')}"


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
