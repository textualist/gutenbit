"""Integration tests for chunk storage and FTS5 search."""

import contextlib
import gzip
import io
import json
import os
import shlex
import time
import zipfile

import httpx
import pytest

from gutenbit.catalog import (
    BookRecord,
    Catalog,
    CatalogFetchInfo,
    apply_catalog_policy,
)
from gutenbit.cli import _select_section_opening_line
from gutenbit.cli import main as cli_main
from gutenbit.db import Database, SearchResult, TextState
from gutenbit.html_chunker import CHUNKER_VERSION, chunk_html

# ------------------------------------------------------------------
# Minimal PG-style HTML builder
# ------------------------------------------------------------------

_PG_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><title>{title}</title></head>
<body>
<section class="pg-boilerplate pgheader" id="pg-header">
  <h2>The Project Gutenberg eBook of {title}</h2>
  <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK {title} ***</div>
</section>
{body}
<section class="pg-boilerplate pgfooter" id="pg-footer">
  <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK {title} ***</div>
</section>
</body>
</html>
"""


def _make_html(title: str, body: str) -> str:
    return _PG_TEMPLATE.format(title=title, body=body)


def _zip_payload(filename: str, html: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, html)
    return buffer.getvalue()


# ------------------------------------------------------------------
# Test fixtures
# ------------------------------------------------------------------

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

_BOOK_HTML = _make_html(
    "Moby Dick",
    """
<p class="toc"><a href="#ch1" class="pginternal">CHAPTER 1</a></p>
<p class="toc"><a href="#ch2" class="pginternal">CHAPTER 2</a></p>
<h2><a id="ch1"></a>CHAPTER 1</h2>
<p>Call me Ishmael. Some years ago, never mind how long precisely,
having little or no money in my purse, and nothing particular to
interest me on shore, I thought I would sail about a little and
see the watery part of the world.</p>
<p>It is a way I have of driving off the spleen and regulating the
circulation. Whenever I find myself growing grim about the mouth;
whenever it is a damp, drizzly November in my soul; I account it
high time to get to sea as soon as I can.</p>
<h2><a id="ch2"></a>CHAPTER 2</h2>
<p>I stuffed a shirt or two into my old carpet-bag, tucked it under
my arm, and started for Cape Horn and the Pacific. The great
flood-gates of the wonder-world swung open, and in the wild
conceits that swayed me to my purpose, two and twenty of the
pagan world came flooding in.</p>
""",
)

_BOOK2 = BookRecord(
    id=2,
    title="Pride and Prejudice",
    authors="Austen, Jane",
    language="en",
    subjects="Love stories; Social classes",
    locc="PR",
    bookshelves="Best Books Ever Listings",
    issued="1998-06-01",
    type="Text",
)

_BOOK2_HTML = _make_html(
    "Pride and Prejudice",
    """
<p class="toc"><a href="#ch1" class="pginternal">Chapter 1</a></p>
<h2><a id="ch1"></a>Chapter 1</h2>
<p>It is a truth universally acknowledged, that a single man in
possession of a good fortune, must be in want of a wife. However
little known the feelings or views of such a man may be on his
first entering a neighbourhood.</p>
""",
)

_BOOK3 = BookRecord(
    id=3,
    title="A Christmas Carol",
    authors="Dickens, Charles",
    language="en",
    subjects="Ghost stories",
    locc="PR",
    bookshelves="Best Books Ever Listings",
    issued="1998-06-01",
    type="Text",
)

_BOOK3_HTML = _make_html(
    "A Christmas Carol",
    """
<p class="toc"><a href="#s1" class="pginternal">STAVE ONE</a></p>
<h2><a id="s1"></a>STAVE ONE.</h2>
<p>Marley was dead: to begin with.</p>
""",
)


def _make_db(tmp_path):
    """Create a Database with test data (bypassing download)."""
    db = Database(tmp_path / "test.db")
    db._store(_BOOK, chunk_html(_BOOK_HTML))
    db._store(_BOOK2, chunk_html(_BOOK2_HTML))
    return db


def _run_cli(db_path, *args):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(["--db", str(db_path), *args])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _run_documented_command(db_path, command: str):
    argv = shlex.split(command)
    assert argv[0] == "gutenbit"
    return _run_cli(db_path, *argv[1:])


# ------------------------------------------------------------------
# Chunk storage
# ------------------------------------------------------------------


def test_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()
    # Book 1: heading + 2 para + heading + 1 para = 5
    # Book 2: heading + 1 para = 2
    assert rows["n"] == 7


def test_chunks_have_chapters(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT div1 FROM chunks WHERE book_id = ? AND kind = 'text' ORDER BY position",
        (1,),
    ).fetchall()
    chapters = [r["div1"] for r in rows]
    assert chapters == ["CHAPTER 1", "CHAPTER 1", "CHAPTER 2"]


def test_chunks_have_kinds(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT kind FROM chunks WHERE book_id = ? ORDER BY position", (1,)
    ).fetchall()
    kinds = [r["kind"] for r in rows]
    assert kinds == [
        "heading",
        "text",
        "text",
        "heading",
        "text",
    ]


def test_heading_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT content FROM chunks WHERE book_id = ? AND kind = 'heading' ORDER BY position",
        (1,),
    ).fetchall()
    assert [r["content"] for r in rows] == ["CHAPTER 1", "CHAPTER 2"]


def test_char_count_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT content, char_count FROM chunks WHERE book_id = ? ORDER BY position",
        (1,),
    ).fetchall()
    for r in rows:
        assert r["char_count"] == len(r["content"])
        assert r["char_count"] > 0


# ------------------------------------------------------------------
# Database.chunks() method
# ------------------------------------------------------------------


def test_chunks_method_returns_all(tmp_path):
    db = _make_db(tmp_path)
    chunks = db.chunks(1)
    assert len(chunks) == 5


def test_chunks_method_filters_by_kind(tmp_path):
    db = _make_db(tmp_path)
    paragraphs = db.chunks(1, kinds=["text"])
    assert len(paragraphs) == 3
    assert all(k == "text" for _, _, _, _, _, _, k, _ in paragraphs)


def test_chunks_method_includes_char_count(tmp_path):
    db = _make_db(tmp_path)
    chunks = db.chunks(1)
    for _, _, _, _, _, content, _, char_count in chunks:
        assert char_count == len(content)


def test_chunks_method_reconstruct_text(tmp_path):
    db = _make_db(tmp_path)
    chunks = db.chunks(1)
    reconstructed = "\n\n".join(content for _, _, _, _, _, content, _, _ in chunks)
    assert "Call me Ishmael" in reconstructed
    assert "CHAPTER 1" in reconstructed


def test_chunks_method_prose_only(tmp_path):
    db = _make_db(tmp_path)
    prose = db.chunks(1, kinds=["text"])
    kinds = {k for _, _, _, _, _, _, k, _ in prose}
    assert kinds == {"text"}
    contents = "\n\n".join(c for _, _, _, _, _, c, _, _ in prose)
    assert "Call me Ishmael" in contents
    assert "CHAPTER" not in contents


# ------------------------------------------------------------------
# Database.delete_book() method
# ------------------------------------------------------------------


def test_delete_book_removes_book_text_chunks_and_search_hits(tmp_path):
    db = _make_db(tmp_path)
    assert db.delete_book(1) is True

    ids = {b.id for b in db.books()}
    assert 1 not in ids
    assert 2 in ids
    assert db.text(1) is None
    assert db.chunks(1) == []
    assert db.search("Ishmael") == []

    other = db.search("truth")
    assert len(other) >= 1
    assert all(r.book_id == 2 for r in other)


def test_delete_book_missing_id_is_noop(tmp_path):
    db = _make_db(tmp_path)
    before = {b.id for b in db.books()}

    assert db.delete_book(99999) is False

    after = {b.id for b in db.books()}
    assert after == before


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------


def test_search_returns_results(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("Ishmael")
    assert len(results) >= 1
    assert isinstance(results[0], SearchResult)
    assert "Ishmael" in results[0].content


def test_search_result_fields(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("Ishmael")
    r = results[0]
    assert r.book_id == 1
    assert r.title == "Moby Dick"
    assert r.authors == "Melville, Herman"
    assert r.div1 == "CHAPTER 1"
    assert r.kind == "text"
    assert r.char_count > 0
    assert r.score > 0


def test_search_filter_by_author(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("truth", author="Austen")
    assert len(results) >= 1
    assert all(r.authors == "Austen, Jane" for r in results)


def test_search_filter_by_title(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("truth", title="Pride")
    assert len(results) >= 1
    assert all("Pride" in r.title for r in results)


def test_search_filter_by_book_id(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("the", book_id=2)
    assert all(r.book_id == 2 for r in results)


def test_search_filter_excludes_non_matching(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("Ishmael", book_id=2)
    assert results == []


def test_search_limit(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("the", limit=1)
    assert len(results) <= 1


def test_search_bm25_ranking(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("Ishmael")
    assert "Ishmael" in results[0].content


def test_fts_porter_stemming(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("sailing")
    assert len(results) >= 1
    assert any("sail" in r.content for r in results)


def test_search_no_results(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("xyzzyplugh")
    assert results == []


def test_search_filter_by_kind(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", kind="heading")
    assert len(results) >= 1
    assert all(r.kind == "heading" for r in results)


def test_search_order_first_orders_by_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", order="first", limit=2)
    assert [r.position for r in results] == [0, 3]


def test_search_order_last_orders_reverse_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", order="last", limit=2)
    assert [r.position for r in results] == [3, 0]


def test_search_rejects_legacy_mode_keyword(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(TypeError):
        db.search("CHAPTER", book_id=1, kind="heading", mode="first", limit=2)


def test_search_help_documents_ordering(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "search", "-h")
    assert code == 0
    assert "--order" in out
    assert "--mode" not in out
    assert "rank" in out and "BM25" in out
    assert "first" in out and "book ascending" in out
    assert "last" in out and "book descending" in out


def test_search_help_shows_post_subcommand_global_flags(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "search", "-h")
    assert code == 0
    assert "--db" in out
    assert "--verbose" in out


def test_search_help_documents_radius(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "search", "-h")
    assert code == 0
    assert "--radius" in out


def test_search_help_documents_kind(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "search", "-h")
    assert code == 0
    assert "--kind" in out
    assert "text" in out
    assert "heading" in out
    assert "all" in out


def test_search_invalid_fts_syntax_returns_friendly_error(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", '"unclosed phrase', "--raw", "--json")
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["errors"] == ["Invalid FTS query syntax: unterminated string."]


def test_search_invalid_fts_syntax_with_radius_keeps_radius_in_json(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "search",
        '"unclosed phrase',
        "--raw",
        "--json",
        "--radius",
        "2",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["data"]["radius"] == 2
    assert payload["errors"] == ["Invalid FTS query syntax: unterminated string."]


def test_search_radius_indents_each_paragraph_in_hit_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--radius", "1")
    assert code == 0
    assert "\n    CHAPTER 1" in out
    assert "\n\n    Call me Ishmael" in out


# --- auto-escape (default query mode) ---


def test_search_auto_escapes_apostrophes(tmp_path):
    db = _make_db(tmp_path)
    # "don't" would crash raw FTS5 due to the apostrophe.
    # With auto-escape it should succeed (may return 0 results, but no error).
    results = db.search('"don\'t"')  # raw FTS5 would fail
    # Just verify no exception was raised; this is an FTS5 syntax test.
    assert isinstance(results, list)


def test_search_cli_auto_escapes_punctuation(tmp_path):
    """Plain-text queries with punctuation succeed without --raw or --phrase."""
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, _out, _err = _run_cli(db_path, "search", "don't")
    assert code == 0

    code, _out, _err = _run_cli(db_path, "search", "Mr.")
    assert code == 0

    code, _out, _err = _run_cli(db_path, "search", "well-known")
    assert code == 0


def test_search_cli_raw_passes_fts5_syntax(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael OR truth", "--raw")
    assert code == 0
    assert "total_results=2  shown_results=2" in out
    assert "2 results · rank order" in out


def test_search_cli_rejects_legacy_mode_flag(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, err = _run_cli(
        db_path,
        "search",
        "CHAPTER",
        "--book",
        "1",
        "--kind",
        "heading",
        "--mode",
        "first",
    )
    assert code == 2
    assert out == ""
    assert "unrecognized arguments: --mode first" in err


def test_search_cli_defaults_to_text_chunks(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "STAVE", "--book", "3")
    assert code == 0
    assert "No results" in out


def test_search_cli_can_search_heading_chunks(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "STAVE", "--book", "3", "--kind", "heading")
    assert code == 0
    assert "STAVE ONE" in out
    assert "total_results=1  shown_results=1" in out


def test_search_cli_kind_all_includes_heading_chunks(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "STAVE", "--book", "3", "--kind", "all")
    assert code == 0
    assert "STAVE ONE" in out
    assert "total_results=1  shown_results=1" in out


def test_search_cli_footer_shows_total_and_shown_when_limited(tmp_path):
    db = _make_db(tmp_path)
    total_results = db.search_count("the", book_id=1)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "the", "--book", "1", "--limit", "1")
    assert code == 0
    assert f"total_results={total_results}  shown_results=1" in out
    assert f"{total_results} results · 1 shown · rank order" in out


def test_search_cli_skips_count_for_untruncated_page(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    def _unexpected_count(*args, **kwargs):
        raise AssertionError("search_count should not be called")

    monkeypatch.setattr(Database, "search_count", _unexpected_count)

    code, out, _err = _run_cli(db_path, "search", "Ishmael")
    assert code == 0
    assert "total_results=1  shown_results=1" in out


def test_search_cli_section_search_does_not_double_count(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db_path = db.path
    total_results = len(db.search("the", book_id=1, div_path="CHAPTER 1", limit=20))
    db.close()

    def _unexpected_count(*args, **kwargs):
        raise AssertionError("search_count should not be called")

    monkeypatch.setattr(Database, "search_count", _unexpected_count)

    code, out, _err = _run_cli(
        db_path, "search", "the", "--book", "1", "--section", "1", "--limit", "1"
    )
    assert code == 0
    assert f"total_results={total_results}  shown_results=1" in out


def test_search_cli_raw_and_phrase_mutually_exclusive(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, _out, _err = _run_cli(db_path, "search", "test", "--raw", "--phrase")
    assert code != 0


# --- --section filter ---


def test_search_filter_by_section_path(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("the", book_id=1, div_path="CHAPTER 1")
    assert len(results) >= 1
    assert all(r.div1 == "CHAPTER 1" for r in results)


def test_search_filter_by_section_excludes_other_sections(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("the", book_id=1, div_path="CHAPTER 2")
    assert all(r.div1 == "CHAPTER 2" for r in results)


def test_search_cli_section_by_path(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path, "search", "Ishmael", "--book", "1", "--section", "CHAPTER 1"
    )
    assert code == 0
    assert "CHAPTER 1" in out


def test_search_cli_section_by_number(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--book", "1", "--section", "1")
    assert code == 0
    assert "CHAPTER 1" in out


def test_search_cli_section_number_requires_book(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, _out, _err = _run_cli(db_path, "search", "test", "--section", "1")
    assert code == 1


# --- --count flag ---


def test_search_cli_count(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "the", "--count")
    assert code == 0
    count = int(out.strip())
    assert count > 0


def test_search_cli_count_json(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "the", "--count", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["data"]["count"] > 0
    assert "items" not in payload["data"]


def test_search_cli_count_with_section(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out_all, _err = _run_cli(db_path, "search", "the", "--book", "1", "--count")
    code2, out_sec, _err2 = _run_cli(
        db_path, "search", "the", "--book", "1", "--section", "CHAPTER 1", "--count"
    )
    assert code == 0 and code2 == 0
    assert int(out_sec.strip()) <= int(out_all.strip())


# --- JSON query mode field ---


def test_search_json_query_mode_auto(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["data"]["query"]["mode"] == "auto"


def test_search_json_query_mode_phrase(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--phrase", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["data"]["query"]["mode"] == "phrase"


def test_search_json_query_mode_raw(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--raw", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["data"]["query"]["mode"] == "raw"


# ------------------------------------------------------------------
# CLI view/toc commands
# ------------------------------------------------------------------


def test_view_default_shows_opening_and_hints(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1")
    assert code == 0
    assert "Call me Ishmael" in out
    assert "Quick actions" in out
    assert "gutenbit toc 1" in out
    assert "gutenbit view 1 --section 1 --forward 20" in out
    assert "gutenbit view 1 --all" in out
    assert "position=0" in out
    assert "section=CHAPTER 1" in out


def test_view_default_skips_unsectioned_front_matter(tmp_path):
    db = Database(tmp_path / "front-matter.db")
    book = BookRecord(
        id=10,
        title="Front Matter Book",
        authors="Author, Test",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="2000-01-01",
        type="Text",
    )
    html = _make_html(
        "Front Matter Book",
        """
<p>Title Page: Printed for Testing.</p>
<p class="toc"><a href="#ch1" class="pginternal">CHAPTER 1</a></p>
<h2><a id="ch1"></a>CHAPTER 1</h2>
<p>First chapter paragraph.</p>
<p>Second chapter paragraph.</p>
""",
    )
    db._store(book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "10")
    assert code == 0
    assert "CHAPTER 1" in out
    assert "First chapter paragraph." in out
    assert "Title Page: Printed for Testing." not in out


def test_view_default_skips_preface_when_main_section_exists(tmp_path):
    db = Database(tmp_path / "preface.db")
    book = BookRecord(
        id=11,
        title="Preface Book",
        authors="Author, Test",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="2000-01-01",
        type="Text",
    )
    html = _make_html(
        "Preface Book",
        """
<p class="toc"><a href="#preface" class="pginternal">PREFACE</a></p>
<p class="toc"><a href="#ch1" class="pginternal">CHAPTER 1</a></p>
<h2><a id="preface"></a>PREFACE</h2>
<p>Preface paragraph.</p>
<h2><a id="ch1"></a>CHAPTER 1</h2>
<p>First chapter paragraph.</p>
""",
    )
    db._store(book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "11")
    assert code == 0
    assert "CHAPTER 1" in out
    assert "First chapter paragraph." in out
    assert "PREFACE" not in out
    assert "Preface paragraph." not in out

    toc_code, toc_out, _toc_err = _run_cli(db_path, "toc", "11", "--json")
    assert toc_code == 0
    toc_payload = json.loads(toc_out)
    assert (
        toc_payload["data"]["toc"]["quick_actions"]["view_first_section"]
        == "gutenbit view 11 --section 2 --forward 20"
    )


def test_toc_default_shows_structure(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "toc", "1")
    assert code == 0
    assert "Moby Dick" in out
    assert "CHAPTER 1" in out
    assert "#  Section" in out
    assert "Section" in out
    assert "Position" in out
    assert "Words" in out
    assert "Read" in out
    assert "Opening" in out
    assert "2 sections · 3 paragraphs · 151 words · 756 chars · 1m read" in out
    assert "gutenbit view 1 --section 1 --forward 20" in out
    assert "gutenbit view 1 --all" in out


def test_view_default_json(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--json")
    assert code == 0

    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "view"
    assert payload["warnings"] == []
    assert payload["errors"] == []

    data = payload["data"]
    assert data["book_id"] == 1
    assert "book" not in data
    assert data["title"] == "Moby Dick"
    assert data["author"] == "Melville, Herman"
    assert data["section"] == "CHAPTER 1"
    assert data["section_number"] == 1
    assert data["position"] == 0
    assert data["forward"] == 3
    assert data["radius"] is None
    assert data["all"] is None
    assert data["content"].startswith("CHAPTER 1")
    assert "Call me Ishmael" in data["content"]
    assert data["action_hints"]["toc"] == "gutenbit toc 1"
    assert data["action_hints"]["view_first_section"] == "gutenbit view 1 --section 1 --forward 20"
    assert data["action_hints"]["search"].startswith('gutenbit search "')
    assert data["action_hints"]["search"].endswith('" --book 1')
    assert "<query>" not in data["action_hints"]["search"]


def test_toc_default_json(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "toc", "1", "--json")
    assert code == 0

    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "toc"
    assert payload["warnings"] == []
    assert payload["errors"] == []

    data = payload["data"]
    assert data["book_id"] == 1
    assert "book" not in data
    summary = data["toc"]
    assert summary["book"]["id"] == 1
    assert summary["book"]["title"] == "Moby Dick"
    assert summary["book"]["authors"] == "Melville, Herman"
    assert summary["overview"]["sections_total"] == 2
    assert summary["overview"]["chunk_counts"]["heading"] == 2
    assert summary["sections"][0]["section"] == "CHAPTER 1"
    assert list(summary["sections"][0].keys()) == [
        "section_number",
        "section",
        "paras",
        "chars",
        "est_words",
        "est_read",
        "opening_line",
    ]
    assert summary["sections"][0]["est_words"] > 0
    assert summary["sections"][0]["opening_line"].endswith("…")
    assert len(summary["sections"][0]["opening_line"]) <= 141
    assert summary["quick_actions"]["search"].startswith('gutenbit search "')
    assert summary["quick_actions"]["search"].endswith('" --book 1')
    assert "<query>" not in summary["quick_actions"]["search"]
    assert (
        summary["quick_actions"]["view_first_section"]
        == "gutenbit view 1 --section 1 --forward 20"
    )
    assert (
        summary["quick_actions"]["view_by_position"]
        == "gutenbit view 1 --position 0 --forward 20"
    )
    assert summary["quick_actions"]["view_all"] == "gutenbit view 1 --all"

    search_code, search_out, search_err = _run_documented_command(
        db_path,
        summary["quick_actions"]["search"],
    )
    assert search_code == 0
    assert search_err == ""
    assert "Moby Dick" in search_out


def test_select_section_opening_line_skips_opening_title_block():
    opening = _select_section_opening_line(
        [
            "Otherwise Called:",
            "The First Book of the Kings",
            "1:1 Now there was a certain man of Ramathaimzophim.",
        ]
    )

    assert opening == "1:1 Now there was a certain man of Ramathaimzophim."


def test_select_section_opening_line_keeps_single_short_opening():
    opening = _select_section_opening_line(
        [
            "The Sea",
            "It was calm when the boats pushed off from shore.",
        ]
    )

    assert opening == "The Sea"


def test_toc_json_skips_title_like_opening_block(tmp_path):
    book = BookRecord(
        id=4,
        title="Sample Testament Book",
        authors="Anon.",
        language="en",
        subjects="Test fixtures",
        locc="BS",
        bookshelves="",
        issued="2001-06-01",
        type="Text",
    )
    html = _make_html(
        "Sample Testament Book",
        """
<p class="toc"><a href="#sam" class="pginternal">The First Book of Samuel</a></p>
<p class="toc"><a href="#eccl" class="pginternal">Ecclesiastes</a></p>
<h2><a id="sam"></a>The First Book of Samuel</h2>
<p>Otherwise Called:</p>
<p>The First Book of the Kings</p>
<p>1:1 Now there was a certain man of Ramathaimzophim.</p>
<h2><a id="eccl"></a>Ecclesiastes</h2>
<p>or</p>
<p>The Preacher</p>
<p>1:1 The words of the Preacher, the son of David, king in Jerusalem.</p>
""",
    )

    db = Database(tmp_path / "opening-preview.db")
    db._store(book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "toc", "4", "--json")
    assert code == 0

    payload = json.loads(out)
    sections = payload["data"]["toc"]["sections"]
    assert sections[0]["opening_line"] == "1:1 Now there was a certain man of Ramathaimzophim."
    assert (
        sections[1]["opening_line"]
        == "1:1 The words of the Preacher, the son of David, king in Jerusalem."
    )


def test_toc_json_preserves_bracketed_numeric_section_labels(tmp_path):
    book = BookRecord(
        id=12,
        title="Bracketed Episodes",
        authors="Anon.",
        language="en",
        subjects="Test fixtures",
        locc="PR",
        bookshelves="",
        issued="2001-06-01",
        type="Text",
    )
    html = _make_html(
        "Bracketed Episodes",
        """
<p class="toc"><a href="#part01" class="pginternal"><b>— I —</b></a></p>
<p class="toc"><a href="#chap01" class="pginternal">[ 1 ]</a></p>
<h2><a id="part01"></a>— I —</h2>
<h3><a id="chap01"></a>[ 1 ]</h3>
<p>Stately, plump Buck Mulligan came from the stairhead.</p>
""",
    )

    db = Database(tmp_path / "bracketed-sections.db")
    db._store(book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "toc", "12", "--json")
    assert code == 0

    payload = json.loads(out)
    sections = payload["data"]["toc"]["sections"]
    assert sections[0]["section"] == "— I —"
    assert sections[1]["section"] == "— I — / [ 1 ]"


def test_view_json_all_for_book(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--json", "--all")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "view"
    assert payload["data"]["forward"] is None
    assert payload["data"]["radius"] is None
    assert payload["data"]["all"] is True
    assert payload["data"]["book_id"] == 1
    assert "Call me Ishmael" in payload["data"]["content"]


def test_view_all_with_missing_book(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    ok_code, ok_out, _ok_err = _run_cli(db_path, "view", "1", "--all")
    assert ok_code == 0
    assert "Call me Ishmael" in ok_out

    miss_code, miss_out, _miss_err = _run_cli(db_path, "view", "999", "--all")
    assert miss_code == 1
    assert "Book ID 999 is not in the database." in miss_out


def test_view_position_with_forward(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    row = db._conn.execute(
        "SELECT position FROM chunks "
        "WHERE book_id = ? AND kind = 'text' "
        "ORDER BY position LIMIT 1",
        (1,),
    ).fetchone()
    assert row is not None
    position = row["position"]
    db.close()

    code, out, _err = _run_cli(
        db_path, "view", "1", "--position", str(position), "--forward", "2"
    )
    assert code == 0
    assert f"position={position}" in out
    assert "forward=2" in out
    assert "Call me Ishmael" in out
    assert "It is a way I have of driving off the spleen" in out


def test_view_position_heading_only_shows_dash_footer_stats(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "0", "--forward", "1")
    assert code == 0
    assert "CHAPTER 1" in out
    assert "0 paragraphs · - words · - read" in out


def test_view_position_with_radius_header(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--position",
        "1",
        "--radius",
        "1",
    )
    assert code == 0
    assert "radius=1" in out
    assert "position=1" in out
    assert "Call me Ishmael" in out
    assert "CHAPTER 1" in out


def test_view_section_with_radius_crosses_section_boundary(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "2",
        "--radius",
        "1",
    )
    assert code == 0
    assert "section=CHAPTER 2" in out
    assert "position=3" in out
    assert "radius=1" in out
    assert "It is a way I have of driving off the spleen" in out
    assert "CHAPTER 2" in out
    assert "I stuffed a shirt or two" in out


def test_view_section_with_forward_header(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "CHAPTER 1",
        "--forward",
        "1",
    )
    assert code == 0
    assert "section=CHAPTER 1" in out
    assert "author=Melville, Herman" in out
    assert "position=0  forward=1" in out
    assert "CHAPTER 1" in out
    assert "Call me Ishmael" in out


def test_view_section_miss_shows_examples(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "BOOK THIRTEEN: 1812 / CHAPTER XII",
    )
    assert code == 1
    assert (
        "No chunks found for book ID 1 under section 'BOOK THIRTEEN: 1812 / CHAPTER XII'."
        in out
    )
    assert "Available sections include:" in out
    assert "1. CHAPTER 1" in out
    assert "2. CHAPTER 2" in out
    assert "Tip: run `gutenbit toc 1` to list all sections." in out


def test_chunks_by_div_ignores_trailing_punctuation(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    rows = db.chunks_by_div(3, "STAVE ONE", kinds=["heading"])
    db.close()

    assert len(rows) == 1
    assert rows[0].div1 == "STAVE ONE"


def test_chunks_by_div_is_case_and_punctuation_spacing_insensitive(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    rows = db.chunks_by_div(3, "stave one", kinds=["heading"])
    assert len(rows) >= 1
    assert rows[0].div1 == "STAVE ONE"
    db.close()


def test_view_section_accepts_punctuation_spacing_variants(tmp_path):
    html = _make_html(
        "Spacing Book",
        """
<p class="toc"><a href="#a" class="pginternal">BOOK ONE</a></p>
<p class="toc"><a href="#b" class="pginternal">CHAPTER I.The Beginning</a></p>
<h2><a id="a"></a>BOOK ONE</h2>
<h3><a id="b"></a>CHAPTER I.The Beginning</h3>
<p>First paragraph.</p>
""",
    )
    db = Database(tmp_path / "spacing.db")
    book = BookRecord(
        id=22,
        title="Spacing Book",
        authors="Author, Test",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="2000-01-01",
        type="Text",
    )
    db._store(book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "22",
        "--section",
        "book one / chapter i. the beginning",
        "--forward",
        "1",
    )
    assert code == 0
    assert "section=BOOK ONE / CHAPTER I.The Beginning" in out
    assert "section_number=2" in out
    assert "CHAPTER I.The Beginning" in out
    assert "First paragraph." in out


def test_view_section_accepts_section_number(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "2", "--forward", "2")
    assert code == 0
    assert "CHAPTER 2" in out
    assert "I stuffed a shirt or two" in out


def test_view_section_number_out_of_range(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "99")
    assert code == 1
    assert "Section 99 is out of range for book ID 1" in out
    assert "gutenbit toc 1" in out


def test_view_rejects_multiple_selectors(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "1", "--section", "CHAPTER 1")
    assert code == 1
    assert "Choose at most one selector" in out


def test_view_rejects_section_path_with_too_many_segments(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "a/b/c/d/e")
    assert code == 1
    assert "Invalid section selector" in out
    assert "max 4" in out


def test_search_rejects_negative_limit(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--limit", "-1")
    assert code == 1
    assert "--limit must be > 0." in out


def test_search_rejects_zero_limit(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--limit", "0")
    assert code == 1
    assert "--limit must be > 0." in out


def test_catalog_rejects_non_positive_limit(tmp_path):
    code, out, _err = _run_cli(
        tmp_path / "any.db", "catalog", "--author", "Dickens", "--limit", "0"
    )
    assert code == 1
    assert "--limit must be > 0." in out


def test_catalog_output_collapses_embedded_newlines(tmp_path, monkeypatch):
    record = BookRecord(
        id=777,
        title="Title Line One\nTitle Line Two",
        authors="Author One\nAuthor Two",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    code, out, _err = _run_cli(
        tmp_path / "any.db", "catalog", "--author", "Author", "--limit", "1"
    )
    assert code == 0
    assert "Author One Author Two" in out
    assert "Title Line One Title Line Two" in out
    assert "Author One\nAuthor Two" not in out
    assert "Title Line One\nTitle Line Two" not in out


def test_catalog_json_collapses_embedded_newlines(tmp_path, monkeypatch):
    record = BookRecord(
        id=777,
        title="Title Line One\nTitle Line Two",
        authors="Author One\nAuthor Two",
        language="en",
        subjects="Subject One\nSubject Two",
        locc="PR\nPS",
        bookshelves="Shelf One\nShelf Two",
        issued="2000-01-01",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    code, out, _err = _run_cli(tmp_path / "any.db", "catalog", "--author", "Author", "--json")
    assert code == 0
    payload = json.loads(out)
    item = payload["data"]["items"][0]
    assert item["title"] == "Title Line One Title Line Two"
    assert item["authors"] == "Author One Author Two"
    assert item["subjects"] == "Subject One Subject Two"
    assert item["locc"] == "PR PS"
    assert item["bookshelves"] == "Shelf One Shelf Two"


def test_catalog_fetch_enforces_english_text_policy_and_canonical_ids(tmp_path, monkeypatch):
    csv_payload = "\n".join(
        [
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves",
            "100,Text,2000-01-01,Duplicate Work,en,Author Example,,,",
            "101,Text,2001-01-01,Duplicate Work,en,Author Example,,,",
            '200,Text,2002-01-01,Bilingual Work,"en, fr",Author Two,,,',
            "300,Text,2003-01-01,French Work,fr,Auteur Trois,,,",
            "400,Sound,2004-01-01,Audio Work,en,Narrator Four,,,",
        ]
    )

    class _FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    compressed = gzip.compress(csv_payload.encode("utf-8"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gutenbit.catalog.httpx.get",
        lambda *_args, **_kwargs: _FakeResponse(compressed),
    )

    catalog = Catalog.fetch()
    assert [book.id for book in catalog.records] == [100, 200]
    assert catalog.fetch_info is not None
    assert catalog.fetch_info.source == "downloaded"
    assert catalog.canonical_id(100) == 100
    assert catalog.canonical_id(101) == 100
    alias = catalog.get(101)
    assert alias is not None
    assert alias.id == 100
    assert catalog.get(300) is None
    assert catalog.get(400) is None


def test_catalog_fetch_uses_fresh_cache_without_network(tmp_path, monkeypatch):
    csv_payload = "\n".join(
        [
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves",
            "100,Text,2000-01-01,Duplicate Work,en,Author Example,,,",
        ]
    )
    compressed = gzip.compress(csv_payload.encode("utf-8"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class _FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    calls: list[dict[str, object]] = []

    def _fake_get(*_args, **kwargs):
        calls.append(kwargs)
        return _FakeResponse(compressed)

    monkeypatch.setattr("gutenbit.catalog.httpx.get", _fake_get)

    first = Catalog.fetch()
    second = Catalog.fetch()

    assert [book.id for book in first.records] == [100]
    assert [book.id for book in second.records] == [100]
    assert second.fetch_info is not None
    assert second.fetch_info.source == "cache"
    assert len(calls) == 1


def test_catalog_fetch_redownloads_when_cache_is_older_than_two_hours(tmp_path, monkeypatch):
    csv_payload = "\n".join(
        [
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves",
            "100,Text,2000-01-01,Duplicate Work,en,Author Example,,,",
        ]
    )
    compressed = gzip.compress(csv_payload.encode("utf-8"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class _FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    calls = {"count": 0}

    def _fake_get(*_args, **_kwargs):
        calls["count"] += 1
        return _FakeResponse(compressed)

    monkeypatch.setattr("gutenbit.catalog.httpx.get", _fake_get)

    initial = Catalog.fetch()
    payload_path = next((tmp_path / "gutenbit").glob("*.csv.gz"))
    stale_timestamp = time.time() - (2 * 60 * 60 + 1)
    os.utime(payload_path, (stale_timestamp, stale_timestamp))
    refreshed = Catalog.fetch()

    assert [book.id for book in initial.records] == [100]
    assert [book.id for book in refreshed.records] == [100]
    assert refreshed.fetch_info is not None
    assert refreshed.fetch_info.source == "downloaded"
    assert calls["count"] == 2


def test_catalog_fetch_falls_back_to_cached_payload_on_network_error(tmp_path, monkeypatch):
    csv_payload = "\n".join(
        [
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves",
            "100,Text,2000-01-01,Duplicate Work,en,Author Example,,,",
        ]
    )
    compressed = gzip.compress(csv_payload.encode("utf-8"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class _FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "gutenbit.catalog.httpx.get",
        lambda *_args, **_kwargs: _FakeResponse(compressed),
    )

    initial = Catalog.fetch()
    payload_path = next((tmp_path / "gutenbit").glob("*.csv.gz"))
    stale_timestamp = time.time() - (2 * 60 * 60 + 1)
    os.utime(payload_path, (stale_timestamp, stale_timestamp))
    monkeypatch.setattr(
        "gutenbit.catalog.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("offline", request=httpx.Request("GET", "https://example.com"))
        ),
    )
    fallback = Catalog.fetch()

    assert [book.id for book in initial.records] == [100]
    assert [book.id for book in fallback.records] == [100]
    assert fallback.fetch_info is not None
    assert fallback.fetch_info.source == "stale_cache"


def test_catalog_fetch_refresh_bypasses_fresh_cache(tmp_path, monkeypatch):
    csv_payload = "\n".join(
        [
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves",
            "100,Text,2000-01-01,Duplicate Work,en,Author Example,,,",
        ]
    )
    compressed = gzip.compress(csv_payload.encode("utf-8"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class _FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    calls = {"count": 0}

    def _fake_get(*_args, **_kwargs):
        calls["count"] += 1
        return _FakeResponse(compressed)

    monkeypatch.setattr("gutenbit.catalog.httpx.get", _fake_get)

    Catalog.fetch()
    refreshed = Catalog.fetch(refresh=True)

    assert refreshed.fetch_info is not None
    assert refreshed.fetch_info.source == "downloaded"
    assert calls["count"] == 2


def test_catalog_cli_uses_project_local_cache_dir_and_reports_cache_hit(tmp_path, monkeypatch):
    record = BookRecord(
        id=777,
        title="Cached Catalog Title",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    seen: dict[str, object] = {}

    def _fake_fetch(*, policy=None, cache_dir=None, refresh=False):
        seen["cache_dir"] = cache_dir
        seen["refresh"] = refresh
        return Catalog(
            [record],
            fetch_info=CatalogFetchInfo(
                source="cache",
                cache_path=(tmp_path / ".gutenbit" / "cache" / "catalog.csv.gz"),
            ),
        )

    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(_fake_fetch))
    monkeypatch.chdir(tmp_path)

    code, out, _err = _run_cli(
        tmp_path / "nested" / "library.db",
        "catalog",
        "--author",
        "Example",
    )
    assert code == 0
    assert "Using cached catalog (English text corpus)." in out
    assert seen["refresh"] is False
    assert str(seen["cache_dir"]) == str(tmp_path / ".gutenbit" / "cache")


def test_catalog_cli_refresh_flag_forces_redownload_message(tmp_path, monkeypatch):
    record = BookRecord(
        id=778,
        title="Fresh Catalog Title",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    seen: dict[str, object] = {}

    def _fake_fetch(*, policy=None, cache_dir=None, refresh=False):
        seen["cache_dir"] = cache_dir
        seen["refresh"] = refresh
        return Catalog(
            [record],
            fetch_info=CatalogFetchInfo(
                source="downloaded",
                cache_path=(tmp_path / ".gutenbit" / "cache" / "catalog.csv.gz"),
            ),
        )

    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(_fake_fetch))
    monkeypatch.chdir(tmp_path)

    code, out, _err = _run_cli(
        tmp_path / "nested" / "library.db",
        "catalog",
        "--author",
        "Example",
        "--refresh",
    )
    assert code == 0
    assert "Refreshed catalog from Project Gutenberg (English text corpus)." in out
    assert seen["refresh"] is True
    assert str(seen["cache_dir"]) == str(tmp_path / ".gutenbit" / "cache")


def test_catalog_policy_dedupes_by_primary_author_and_title():
    canonical = BookRecord(
        id=1342,
        title="Pride and Prejudice",
        authors="Austen, Jane, 1775-1817",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    annotated = BookRecord(
        id=42671,
        title="Pride and Prejudice",
        authors=(
            "Austen, Jane, 1775-1817; Tanner, Tony, 1935-1998 [Introduction]; "
            "Price, Martin, 1919-2010 [Editor]"
        ),
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )

    canonical_books, canonical_id_by_id = apply_catalog_policy([annotated, canonical])
    assert [book.id for book in canonical_books] == [1342]
    assert canonical_id_by_id[1342] == 1342
    assert canonical_id_by_id[42671] == 1342


def test_books_output_collapses_embedded_newlines(tmp_path):
    db = Database(tmp_path / "test.db")
    weird_book = BookRecord(
        id=99,
        title="Book\nTitle",
        authors="Writer\nName",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    html = _make_html("Book Title", "<h2><a id='c1'></a>CHAPTER 1</h2><p>Body.</p>")
    db._store(weird_book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books")
    assert code == 0
    assert "Writer Name" in out
    assert "Book Title" in out
    assert "Writer\nName" not in out
    assert "Book\nTitle" not in out


def test_books_json_collapses_embedded_newlines(tmp_path):
    db = Database(tmp_path / "test.db")
    weird_book = BookRecord(
        id=99,
        title="Book\nTitle",
        authors="Writer\nName",
        language="en",
        subjects="Subject\nLine",
        locc="PR\nPS",
        bookshelves="Shelf\nName",
        issued="",
        type="Text",
    )
    html = _make_html("Book Title", "<h2><a id='c1'></a>CHAPTER 1</h2><p>Body.</p>")
    db._store(weird_book, chunk_html(html))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books", "--json")
    assert code == 0
    payload = json.loads(out)
    item = payload["data"]["items"][0]
    assert item["title"] == "Book Title"
    assert item["authors"] == "Writer Name"
    assert item["subjects"] == "Subject Line"
    assert item["locc"] == "PR PS"
    assert item["bookshelves"] == "Shelf Name"


def test_database_ingest_enforces_catalog_boundaries(tmp_path, monkeypatch):
    english = BookRecord(
        id=10,
        title="Allowed English Text",
        authors="Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    french = BookRecord(
        id=11,
        title="French Text",
        authors="Auteur",
        language="fr",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    audio = BookRecord(
        id=12,
        title="Audio Track",
        authors="Narrator",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Sound",
    )

    html = _make_html("Allowed English Text", "<h2>CHAPTER 1</h2><p>Body text.</p>")
    seen_downloads: list[int] = []

    def _fake_download(book_id: int) -> str:
        seen_downloads.append(book_id)
        return html

    monkeypatch.setattr("gutenbit.db.download_html", _fake_download)

    with Database(tmp_path / "boundaries.db") as db:
        db.ingest([french, audio, english], delay=0)
        stored_ids = [book.id for book in db.books()]
        assert stored_ids == [10]
        assert db.has_text(10) is True
        assert db.has_text(11) is False
        assert db.has_text(12) is False

    assert seen_downloads == [10]


def test_has_current_text_detects_stale_chunker_version(tmp_path):
    db = _make_db(tmp_path)
    db._conn.execute(
        "UPDATE texts SET chunker_version = ? WHERE book_id = ?",
        (CHUNKER_VERSION - 1, 1),
    )
    db._conn.commit()
    assert db.has_text(1) is True
    assert db.has_current_text(1) is False
    db.close()


def test_ingest_reports_skip_before_downloading(tmp_path, monkeypatch):
    record = BookRecord(
        id=888,
        title="Already There",
        authors="Test Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )
    monkeypatch.setattr(
        Database,
        "text_states",
        lambda _self, _book_ids: {888: TextState(has_text=True, has_current_text=True)},
    )

    def _ingest_should_not_run(_self, _book, *, delay, force, state):
        raise AssertionError("_ingest_book() should not run for already-downloaded books")

    monkeypatch.setattr(Database, "_ingest_book", _ingest_should_not_run)

    code, out, _err = _run_cli(tmp_path / "skip.db", "add", "888", "--delay", "0")
    assert code == 0
    assert "skipping 888: Already There (already downloaded)" in out


def test_ingest_reprocesses_stale_chunker_version(tmp_path, monkeypatch):
    record = BookRecord(
        id=889,
        title="Needs Refresh",
        authors="Test Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )
    ingested_ids: list[int] = []
    monkeypatch.setattr(
        Database,
        "text_states",
        lambda _self, _book_ids: {889: TextState(has_text=True, has_current_text=False)},
    )

    def _capture_ingest(_self, book, *, delay, force, state):
        ingested_ids.append(book.id)
        return True

    monkeypatch.setattr(Database, "_ingest_book", _capture_ingest)

    code, out, _err = _run_cli(tmp_path / "stale.db", "add", "889", "--delay", "0")
    assert code == 0
    assert "processing 889: Needs Refresh (chunker updated)" in out
    assert ingested_ids == [889]


def test_ingest_remaps_to_canonical_catalog_id(tmp_path, monkeypatch):
    canonical = BookRecord(
        id=100,
        title="Canonical Work",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    catalog = Catalog([canonical], canonical_id_by_id={100: 100, 101: 100})
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda **_kwargs: catalog))
    ingested_ids: list[int] = []
    monkeypatch.setattr(
        Database,
        "text_states",
        lambda _self, _book_ids: {100: TextState(has_text=False, has_current_text=False)},
    )

    def _capture_ingest(_self, book, *, delay, force, state):
        ingested_ids.append(book.id)
        return True

    monkeypatch.setattr(Database, "_ingest_book", _capture_ingest)

    code, out, _err = _run_cli(tmp_path / "canonical.db", "add", "101", "--delay", "0")
    assert code == 0
    assert "remapped 101 -> 100" in out
    assert ingested_ids == [100]


def test_books_update_rejects_delay_without_update(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books", "--delay", "0")
    assert code == 1
    assert "--delay can only be used with --update." in out


def test_books_update_rejects_force_without_update(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books", "--force")
    assert code == 1
    assert "--force can only be used with --update." in out


def test_books_update_rejects_dry_run_without_update(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books", "--dry-run")
    assert code == 1
    assert "--dry-run can only be used with --update." in out


def test_books_update_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"

    code, out, _err = _run_cli(db_path, "books", "--update")
    assert code == 0
    assert "No books stored yet" in out


def test_books_update_noop_when_all_current(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    def _ingest_should_not_run(_self, _book, *, delay, force, state):
        raise AssertionError("_ingest_book() should not run when all stored books are current")

    monkeypatch.setattr(Database, "_ingest_book", _ingest_should_not_run)

    code, out, _err = _run_cli(db_path, "books", "--update")
    assert code == 0
    assert "All 2 stored book(s) are current." in out


def test_books_update_reprocesses_only_stale_books(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db._conn.execute(
        "UPDATE texts SET chunker_version = ? WHERE book_id = ?",
        (CHUNKER_VERSION - 1, 1),
    )
    db._conn.commit()
    db_path = db.path
    db.close()

    seen_downloads: list[int] = []

    def _fake_download(book_id: int) -> str:
        seen_downloads.append(book_id)
        return {1: _BOOK_HTML, 2: _BOOK2_HTML}[book_id]

    monkeypatch.setattr("gutenbit.db.download_html", _fake_download)

    code, out, _err = _run_cli(db_path, "books", "--update", "--delay", "0")
    assert code == 0
    assert "processing 1: Moby Dick (chunker updated)" in out
    assert "processing 2:" not in out
    assert seen_downloads == [1]


def test_books_update_force_reprocesses_all_books(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    seen_downloads: list[int] = []

    def _fake_download(book_id: int) -> str:
        seen_downloads.append(book_id)
        return {1: _BOOK_HTML, 2: _BOOK2_HTML}[book_id]

    monkeypatch.setattr("gutenbit.db.download_html", _fake_download)

    code, out, _err = _run_cli(db_path, "books", "--update", "--force", "--delay", "0")
    assert code == 0
    assert "processing 1: Moby Dick (forced)" in out
    assert "processing 2: Pride and Prejudice (forced)" in out
    assert seen_downloads == [1, 2]


def test_books_update_dry_run_does_not_ingest(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db._conn.execute(
        "UPDATE texts SET chunker_version = ? WHERE book_id = ?",
        (CHUNKER_VERSION - 1, 1),
    )
    db._conn.commit()
    db_path = db.path
    db.close()

    def _ingest_should_not_run(_self, _book, *, delay, force, state):
        raise AssertionError("_ingest_book() should not run during dry-run")

    monkeypatch.setattr(Database, "_ingest_book", _ingest_should_not_run)

    code, out, _err = _run_cli(db_path, "books", "--update", "--dry-run")
    assert code == 0
    assert "Would reprocess 1 of 2 stored book(s):" in out
    assert "1: Moby Dick" in out


def test_books_update_json_output(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db._conn.execute(
        "UPDATE texts SET chunker_version = ? WHERE book_id = ?",
        (CHUNKER_VERSION - 1, 1),
    )
    db._conn.commit()
    db_path = db.path
    db.close()

    def _fake_download(book_id: int) -> str:
        return {1: _BOOK_HTML, 2: _BOOK2_HTML}[book_id]

    monkeypatch.setattr("gutenbit.db.download_html", _fake_download)

    code, out, _err = _run_cli(db_path, "books", "--update", "--delay", "0", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "books"
    assert payload["data"]["action"] == "update"
    assert payload["data"]["counts"]["stored"] == 2
    assert payload["data"]["counts"]["selected"] == 1
    assert payload["data"]["counts"]["updated"] == 1
    assert payload["data"]["counts"]["skipped_current"] == 1
    assert payload["data"]["counts"]["failed"] == 0
    assert payload["data"]["results"] == [
        {"book_id": 1, "title": "Moby Dick", "status": "reprocessed"}
    ]


# ------------------------------------------------------------------
# Validation edge cases
# ------------------------------------------------------------------


def test_view_non_positive_forward_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "CHAPTER 1", "--forward", "0")
    assert code == 1
    assert "--forward must be > 0." in out


def test_search_negative_radius_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--radius", "-1")
    assert code == 1
    assert "--radius must be >= 0." in out


def test_search_radius_rejected_with_count(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--count", "--radius", "1")
    assert code == 1
    assert "--radius cannot be used with --count." in out


def test_search_rejects_section_path_with_too_many_segments(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--section", "a/b/c/d/e")
    assert code == 1
    assert "Invalid section selector" in out
    assert "max 4" in out


def test_view_negative_radius_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "1", "--radius", "-1")
    assert code == 1
    assert "--radius must be >= 0." in out


def test_view_radius_requires_selector(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--radius", "1")
    assert code == 1
    assert "--radius requires --position or --section." in out


def test_view_radius_rejected_with_forward(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path, "view", "1", "--position", "1", "--forward", "2", "--radius", "1"
    )
    assert code == 1
    assert "Choose one retrieval shape" in out


def test_view_all_rejected_with_position(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "1", "--all")
    assert code == 1
    assert "--all can be used with a book or section, not with --position." in out


def test_view_all_rejected_with_forward(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "1", "--all", "--forward", "2")
    assert code == 1
    assert "Choose one retrieval shape" in out


def test_add_rejects_non_positive_ids(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "add", "0", "-1")
    assert code == 1
    assert "must be positive" in out
    assert "0" in out
    assert "-1" in out


# ------------------------------------------------------------------
# JSON output
# ------------------------------------------------------------------


def test_search_json_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "search"
    assert payload["errors"] == []
    assert payload["warnings"] == []

    data = payload["data"]
    assert data["query"]["raw"] == "Ishmael"
    assert data["order"] == "rank"
    assert data["limit"] == 10
    assert data["filters"]["book_id"] is None
    assert "book" not in data["filters"]
    assert data["filters"]["kind"] == "text"
    assert data["total_results"] >= 1
    assert data["shown_results"] >= 1
    assert len(data["items"]) >= 1

    result = data["items"][0]
    assert result["book_id"] == 1
    assert "book" not in result
    assert result["title"] == "Moby Dick"
    assert result["author"] == "Melville, Herman"
    assert "Ishmael" in result["content"]
    assert result["section_number"] == 1
    assert "position" in result
    assert "section" in result
    assert result["forward"] is None
    assert result["radius"] is None
    assert result["all"] is None
    assert result["kind"] == "text"
    assert "rank" in result
    assert "score" in result


def test_search_cli_rejects_removed_default_order_value(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    removed_order = "rank" + "ed"
    code, out, err = _run_cli(db_path, "search", "Ishmael", "--order", removed_order)
    assert code == 2
    assert out == ""
    assert f"invalid choice: '{removed_order}'" in err


def test_search_json_radius_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--json", "--radius", "2")
    assert code == 0
    payload = json.loads(out)
    data = payload["data"]
    result = data["items"][0]
    assert list(result.keys())[:10] == [
        "book_id",
        "title",
        "author",
        "section",
        "section_number",
        "position",
        "forward",
        "radius",
        "all",
        "content",
    ]
    assert result["forward"] is None
    assert result["radius"] == 2
    assert result["all"] is None
    assert result["kind"] == "text"
    assert result["content"].startswith("CHAPTER 1")
    assert "Call me Ishmael" in result["content"]


def test_search_json_heading_kind_output(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "search",
        "STAVE",
        "--book",
        "3",
        "--kind",
        "heading",
        "--json",
    )
    assert code == 0
    payload = json.loads(out)
    data = payload["data"]
    assert data["filters"]["kind"] == "heading"
    assert data["total_results"] == 1
    assert data["items"][0]["kind"] == "heading"
    assert data["items"][0]["content"] == "STAVE ONE"


def test_search_json_empty(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "xyzzyplugh", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "search"
    assert payload["data"]["filters"]["kind"] == "text"
    assert payload["data"]["total_results"] == 0
    assert payload["data"]["shown_results"] == 0
    assert payload["data"]["items"] == []


def test_books_json_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "books"
    assert payload["errors"] == []
    assert payload["warnings"] == []
    assert payload["data"]["count"] == 2
    items = payload["data"]["items"]
    assert items[0]["id"] == 1
    assert items[0]["title"] == "Moby Dick"
    assert items[1]["id"] == 2
    assert items[1]["title"] == "Pride and Prejudice"


def test_books_json_empty(tmp_path):
    code, out, _err = _run_cli(tmp_path / "empty.db", "books", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "books"
    assert payload["data"]["count"] == 0
    assert payload["data"]["items"] == []


def test_catalog_json_output(tmp_path, monkeypatch):
    record = BookRecord(
        id=777,
        title="Title Line One\nTitle Line Two",
        authors="Author One\nAuthor Two",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    code, out, _err = _run_cli(
        tmp_path / "any.db",
        "catalog",
        "--author",
        "Author",
        "--limit",
        "1",
        "--json",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "catalog"
    assert payload["data"]["total_matches"] == 1
    assert payload["data"]["shown"] == 1
    assert payload["data"]["items"][0]["id"] == 777


def test_add_json_output(tmp_path, monkeypatch):
    canonical = BookRecord(
        id=100,
        title="Canonical Work",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    catalog = Catalog([canonical], canonical_id_by_id={100: 100, 101: 100})
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda **_kwargs: catalog))
    ingested_ids: list[int] = []
    monkeypatch.setattr(
        Database,
        "text_states",
        lambda _self, _book_ids: {100: TextState(has_text=False, has_current_text=False)},
    )

    def _capture_ingest(_self, book, *, delay, force, state):
        ingested_ids.append(book.id)
        return True

    monkeypatch.setattr(Database, "_ingest_book", _capture_ingest)

    code, out, _err = _run_cli(
        tmp_path / "canonical.db",
        "add",
        "101",
        "--delay",
        "0",
        "--json",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "add"
    assert payload["data"]["counts"]["requested"] == 1
    assert payload["data"]["counts"]["canonical"] == 1
    assert payload["data"]["results"][0]["requested_id"] == 101
    assert payload["data"]["results"][0]["canonical_id"] == 100
    assert payload["data"]["results"][0]["status"] == "added"
    assert payload["data"]["results"][0]["add_status"] == "added"
    assert ingested_ids == [100]


def test_add_json_failure_reports_failed_and_stays_parseable(tmp_path, monkeypatch):
    record = BookRecord(
        id=555,
        title="Broken Download",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    def _boom(_book_id):
        raise RuntimeError("boom")

    monkeypatch.setattr("gutenbit.db.download_html", _boom)

    code, out, _err = _run_cli(
        tmp_path / "broken.db",
        "add",
        "555",
        "--delay",
        "0",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "add"
    assert payload["data"]["failed_canonical_ids"] == [555]
    assert payload["data"]["results"][0]["status"] == "failed"
    assert "Failed to add 555: Broken Download" in payload["errors"]


def test_add_non_json_failure_returns_exit_1(tmp_path, monkeypatch):
    record = BookRecord(
        id=555,
        title="Broken Download",
        authors="Example, Author",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    def _boom(_book_id):
        raise RuntimeError("boom")

    monkeypatch.setattr("gutenbit.db.download_html", _boom)

    code, out, _err = _run_cli(
        tmp_path / "broken.db",
        "add",
        "555",
        "--delay",
        "0",
    )
    assert code == 1
    assert "adding 555: Broken Download" in out
    assert "failed 555: Broken Download" in out
    assert "Completed with 1 failure(s)." in out


def test_add_non_json_reports_download_source(tmp_path, monkeypatch):
    record = BookRecord(
        id=15,
        title="Moby Dick",
        authors="Melville, Herman",
        language="en",
        subjects="",
        locc="",
        bookshelves="",
        issued="",
        type="Text",
    )
    monkeypatch.setattr(
        "gutenbit.cli.Catalog.fetch",
        staticmethod(lambda **_kwargs: Catalog([record])),
    )

    class _FakeResponse:
        def __init__(self, *, text: str = "", content: bytes = b"") -> None:
            self.text = text
            self.content = content

        def raise_for_status(self) -> None:
            return None

    def _fake_get(url: str, **_kwargs):
        if url == "https://aleph.pglaf.org/cache/epub/15/pg15-images.html":
            return _FakeResponse(
                text=_make_html("Moby Dick", "<h2>CHAPTER 1</h2><p>Call me Ishmael.</p>")
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    code, out, _err = _run_cli(
        tmp_path / "source.db",
        "add",
        "15",
        "--delay",
        "0",
    )

    assert code == 0
    assert "adding 15: Moby Dick" in out
    assert "finished 15: Moby Dick (official mirror: aleph.pglaf.org)" in out


def test_delete_json_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "delete", "1", "999", "--json")
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "delete"
    assert payload["data"]["deleted_count"] == 1
    assert payload["data"]["missing_count"] == 1
    assert any(
        row["book_id"] == 1 and row["status"] == "deleted" for row in payload["data"]["results"]
    )
    assert any(
        row["book_id"] == 999 and row["status"] == "missing" for row in payload["data"]["results"]
    )


def test_view_section_json_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path, "view", "1", "--section", "CHAPTER 1", "--forward", "1", "--json"
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "view"
    data = payload["data"]
    assert data["book_id"] == 1
    assert "book" not in data
    assert data["title"] == "Moby Dick"
    assert data["author"] == "Melville, Herman"
    assert data["section"] == "CHAPTER 1"
    assert data["section_number"] == 1
    assert data["position"] == 0
    assert data["forward"] == 1
    assert data["radius"] is None
    assert data["all"] is None
    assert data["content"].startswith("CHAPTER 1")
    assert "Call me Ishmael" in data["content"]


def test_view_position_json_radius_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "1", "--radius", "1", "--json")
    assert code == 0
    payload = json.loads(out)
    data = payload["data"]
    assert list(data.keys())[:10] == [
        "book_id",
        "title",
        "author",
        "section",
        "section_number",
        "position",
        "forward",
        "radius",
        "all",
        "content",
    ]
    assert data["forward"] is None
    assert data["radius"] == 1
    assert data["all"] is None
    assert "Call me Ishmael" in data["content"]


def test_view_section_json_radius_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "2", "--radius", "1", "--json")
    assert code == 0
    payload = json.loads(out)
    data = payload["data"]
    assert data["section_number"] == 2
    assert data["radius"] == 1
    assert data["all"] is None
    assert "CHAPTER 2" in data["content"]
    assert "I stuffed a shirt or two" in data["content"]


def test_view_position_json_radius_error_keeps_radius(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--position",
        "999",
        "--radius",
        "2",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["data"]["position"] == 999
    assert payload["data"]["radius"] == 2
    assert payload["data"]["all"] is None


def test_view_section_json_radius_error_keeps_radius(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "999",
        "--radius",
        "2",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["data"]["section"] == "999"
    assert payload["data"]["section_number"] == 999
    assert payload["data"]["radius"] == 2
    assert payload["data"]["all"] is None

def test_view_json_validation_error_uses_envelope(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--position",
        "1",
        "--all",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "view"
    assert payload["errors"] == ["--all can be used with a book or section, not with --position."]


def test_books_has_column_headers(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books")
    assert code == 0
    assert "ID" in out
    assert "AUTHORS" in out
    assert "TITLE" in out
