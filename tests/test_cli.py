"""CLI-specific regression tests."""

from __future__ import annotations

import contextlib
import re
import io
from pathlib import Path

import pytest

from gutenbit.catalog import BookRecord
from gutenbit.cli import _display_cli_path
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

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(["--version"])

    assert code == 0
    assert err.getvalue() == ""
    rendered = out.getvalue().strip()
    assert rendered.startswith("gutenbit ")
    assert rendered != "gutenbit "


def test_help_shows_project_local_default_db():
    out = io.StringIO()
    err = io.StringIO()

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(["--help"])

    assert code == 0
    assert err.getvalue() == ""
    assert "~/.gutenbit/gutenbit.db" in out.getvalue()


def test_help_shows_pride_and_prejudice_workflow():
    out = io.StringIO()
    err = io.StringIO()

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(["--help"])

    assert code == 0
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

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(["--help"])

    assert code == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert "COMMAND ..." in rendered
    assert "{catalog,add,remove,books,search,toc,view}" not in rendered
    assert "commands:" in rendered
    assert "gutenbit COMMAND --help" in rendered
    assert "project gutenberg:" not in rendered
    assert "local state:" not in rendered
    assert "not affiliated with Project Gutenberg" in rendered
    normalized = re.sub(r"\s+", " ", rendered)
    assert "all application data is stored at ~/.gutenbit" in normalized
    assert "SQLite database path (default: ~/.gutenbit/gutenbit.db)" in rendered


def test_display_cli_path_preserves_relative_and_home_relative_paths():
    assert _display_cli_path(".gutenbit/gutenbit.db") == ".gutenbit/gutenbit.db"
    assert _display_cli_path("~/.gutenbit/gutenbit.db") == "~/.gutenbit/gutenbit.db"
    assert _display_cli_path(Path.home() / ".gutenbit" / "gutenbit.db") == str(
        Path.home() / ".gutenbit" / "gutenbit.db"
    )


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
    assert "No such command 'delete'" in err


def test_books_creates_default_db_under_home_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

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

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    code, out, err = _run_cli("books")
    explicit_code, explicit_out, explicit_err = _run_cli("--db", str(legacy_db), "books")

    assert code == 0
    assert err == ""
    assert "No books stored yet." in out
    assert (tmp_path / ".gutenbit" / "gutenbit.db").exists()

    assert explicit_code == 0
    assert explicit_err == ""
    assert f"1 book(s) stored in {legacy_db}" in explicit_out


def test_books_output_preserves_home_relative_db_path(monkeypatch):
    class _FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> _FakeDatabase:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def books(self) -> list[BookRecord]:
            return [_BOOK]

    monkeypatch.setattr("gutenbit.cli._commands.Database", _FakeDatabase)

    code, out, err = _run_cli("--db", "~/.gutenbit/gutenbit.db", "books")

    assert code == 0
    assert err == ""
    assert "1 book(s) stored in ~/.gutenbit/gutenbit.db" in out


def test_books_update_output_preserves_home_relative_db_path(monkeypatch):
    class _FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> _FakeDatabase:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def books(self) -> list[BookRecord]:
            return [_BOOK]

        def stale_books(self) -> list[BookRecord]:
            return []

    monkeypatch.setattr("gutenbit.cli._commands.Database", _FakeDatabase)

    code, out, err = _run_cli("--db", "~/.gutenbit/gutenbit.db", "books", "--update")

    assert code == 0
    assert err == ""
    assert "All 1 stored book(s) are current. Database: ~/.gutenbit/gutenbit.db" in out


def test_add_done_output_preserves_home_relative_db_path(monkeypatch):
    class _FakeCatalog:
        fetch_info = None

        def get(self, book_id: int) -> BookRecord | None:
            if book_id == _BOOK.id:
                return _BOOK
            return None

    class _FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> _FakeDatabase:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def _fake_load_catalog(_args, *, display, as_json):
        return _FakeCatalog()

    def _fake_process_books_for_ingest(
        _db,
        books: list[BookRecord],
        *,
        delay: float,
        as_json: bool,
        display,
        failure_action: str,
        force: bool = False,
        show_skipped_current: bool = True,
    ) -> tuple[dict[int, str], list[str]]:
        return ({book.id: "added" for book in books}, [])

    monkeypatch.setattr("gutenbit.cli._commands.Database", _FakeDatabase)
    monkeypatch.setattr("gutenbit.cli._commands._load_catalog", _fake_load_catalog)
    monkeypatch.setattr(
        "gutenbit.cli._commands._process_books_for_ingest", _fake_process_books_for_ingest
    )

    code, out, err = _run_cli("--db", "~/.gutenbit/gutenbit.db", "add", "1")

    assert code == 0
    assert err == ""
    assert "Done. Database: ~/.gutenbit/gutenbit.db" in out


def test_remove_output_preserves_home_relative_db_path(monkeypatch):
    class _FakeDatabase:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> _FakeDatabase:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def remove_book(self, book_id: int) -> bool:
            return book_id == _BOOK.id

    monkeypatch.setattr("gutenbit.cli._commands.Database", _FakeDatabase)

    code, out, err = _run_cli("--db", "~/.gutenbit/gutenbit.db", "remove", "1")

    assert code == 0
    assert err == ""
    assert "Removed book ID 1 from ~/.gutenbit/gutenbit.db." in out
