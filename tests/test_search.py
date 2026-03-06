"""Integration tests for chunk storage and FTS5 search."""

import contextlib
import gzip
import io
import json

from gutenbit.catalog import BookRecord, Catalog
from gutenbit.cli import main as cli_main
from gutenbit.db import Database, SearchResult
from gutenbit.html_chunker import chunk_html

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
</section>
{body}
<section class="pg-boilerplate pgfooter" id="pg-footer">
  <p>End of the Project Gutenberg eBook</p>
</section>
</body>
</html>
"""


def _make_html(title: str, body: str) -> str:
    return _PG_TEMPLATE.format(title=title, body=body)


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
        "SELECT div1 FROM chunks WHERE book_id = ? AND kind = 'paragraph' ORDER BY position",
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
        "paragraph",
        "paragraph",
        "heading",
        "paragraph",
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
    paragraphs = db.chunks(1, kinds=["paragraph"])
    assert len(paragraphs) == 3
    assert all(k == "paragraph" for _, _, _, _, _, _, k, _ in paragraphs)


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
    prose = db.chunks(1, kinds=["paragraph"])
    kinds = {k for _, _, _, _, _, _, k, _ in prose}
    assert kinds == {"paragraph"}
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
    assert r.kind == "paragraph"
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


def test_search_mode_first_orders_by_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", mode="first", limit=2)
    assert [r.position for r in results] == [0, 3]


def test_search_mode_last_orders_reverse_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", mode="last", limit=2)
    assert [r.position for r in results] == [3, 0]


# ------------------------------------------------------------------
# CLI view command
# ------------------------------------------------------------------


def test_view_default_shows_structure(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1")
    assert code == 0
    assert "Moby Dick" in out
    assert "CHAPTER 1" in out
    assert "Sections" in out
    assert "Section" in out
    assert "Paras" in out
    assert "Chars" in out
    assert "Est words" in out
    assert "Est read" in out
    assert "Position" in out
    assert "Opening" in out
    assert "--position" in out


def test_view_default_json(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--json")
    assert code == 0

    payload = json.loads(out)
    assert payload["book"]["id"] == 1
    assert payload["book"]["title"] == "Moby Dick"
    assert payload["book"]["authors"] == "Melville, Herman"
    assert payload["overview"]["sections_total"] == 2
    assert payload["overview"]["chunk_counts"]["heading"] == 2
    assert payload["sections"][0]["section"] == "CHAPTER 1"
    assert list(payload["sections"][0].keys()) == [
        "position",
        "section",
        "paras",
        "chars",
        "est_words",
        "est_read",
        "opening_line",
    ]
    assert payload["sections"][0]["est_words"] > 0
    assert payload["sections"][0]["opening_line"].endswith("…")
    assert len(payload["sections"][0]["opening_line"]) <= 141
    assert payload["quick_actions"]["search"] == (
        "gutenbit search <query> --book-id 1 --kind paragraph"
    )
    assert payload["quick_actions"]["view_first_position"].startswith(
        "gutenbit view 1 --position "
    )
    assert payload["quick_actions"]["view_first_position_around"].startswith(
        "gutenbit view 1 --position "
    )


def test_view_json_rejects_selectors(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--json", "--all")
    assert code == 1
    assert "--json can only be used with the default summary view." in out


def test_view_all_and_missing_book(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    ok_code, ok_out, _ok_err = _run_cli(db_path, "view", "1", "--all")
    assert ok_code == 0
    assert "Call me Ishmael" in ok_out

    miss_code, miss_out, _miss_err = _run_cli(db_path, "view", "999", "--all")
    assert miss_code == 1
    assert "No text found" in miss_out


def test_view_position_with_neighbors(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    row = db._conn.execute(
        "SELECT position FROM chunks WHERE book_id = ? AND kind = 'paragraph' ORDER BY position LIMIT 1",
        (1,),
    ).fetchone()
    assert row is not None
    position = row["position"]
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", str(position), "--around", "1")
    assert code == 0
    assert f"position={position}" in out
    assert "section=CHAPTER 1" in out


def test_view_section_with_filters_and_limit(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "CHAPTER 1",
        "--kind",
        "paragraph",
        "-n",
        "1",
    )
    assert code == 0
    assert "section='CHAPTER 1'" in out
    assert "kind=paragraph" in out
    assert "1 chunk(s)" in out


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
    assert "No chunks found for book 1 under section 'BOOK THIRTEEN: 1812 / CHAPTER XII'." in out
    assert "Available sections include:" in out
    assert "CHAPTER 1" in out
    assert "CHAPTER 2" in out
    assert "Tip: run `gutenbit view 1` to list all sections." in out


def test_chunks_by_div_ignores_trailing_punctuation(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_BOOK3, chunk_html(_BOOK3_HTML))
    rows = db.chunks_by_div(3, "STAVE ONE", kinds=["heading"])
    db.close()

    assert len(rows) == 1
    assert rows[0].div1 == "STAVE ONE"


def test_view_rejects_multiple_selectors(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--all", "--section", "CHAPTER 1")
    assert code == 1
    assert "Choose at most one selector" in out


def test_search_rejects_negative_limit(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "-n", "-1")
    assert code == 1
    assert "--limit must be >= 0." in out


def test_search_rejects_unknown_kind_choice(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, _out, err = _run_cli(db_path, "search", "Ishmael", "--kind", "typo_kind")
    assert code == 2
    assert "invalid choice" in err


def test_catalog_rejects_non_positive_limit(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "catalog", "--author", "Dickens", "-n", "0")
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))

    code, out, _err = _run_cli(tmp_path / "any.db", "catalog", "--author", "Author", "-n", "1")
    assert code == 0
    assert "Author One Author Two" in out
    assert "Title Line One Title Line Two" in out
    assert "Author One\nAuthor Two" not in out
    assert "Title Line One\nTitle Line Two" not in out


def test_catalog_fetch_enforces_english_text_policy_and_canonical_ids(monkeypatch):
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
    monkeypatch.setattr(
        "gutenbit.catalog.httpx.get",
        lambda *_args, **_kwargs: _FakeResponse(compressed),
    )

    catalog = Catalog.fetch()
    assert [book.id for book in catalog.records] == [100, 200]
    assert catalog.canonical_id(100) == 100
    assert catalog.canonical_id(101) == 100
    alias = catalog.get(101)
    assert alias is not None
    assert alias.id == 100
    assert catalog.get(300) is None
    assert catalog.get(400) is None


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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))
    monkeypatch.setattr(Database, "has_text", lambda _self, _book_id: True)

    def _ingest_should_not_run(_self, _books, *, delay=1.0):
        raise AssertionError("ingest() should not run for already-downloaded books")

    monkeypatch.setattr(Database, "ingest", _ingest_should_not_run)

    code, out, _err = _run_cli(tmp_path / "skip.db", "ingest", "888", "--delay", "0")
    assert code == 0
    assert "skipping 888: Already There (already downloaded)" in out


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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: catalog))
    monkeypatch.setattr(Database, "has_text", lambda _self, _book_id: False)

    ingested_ids: list[int] = []

    def _capture_ingest(_self, books, *, delay=1.0):
        ingested_ids.extend(book.id for book in books)

    monkeypatch.setattr(Database, "ingest", _capture_ingest)

    code, out, _err = _run_cli(tmp_path / "canonical.db", "ingest", "101", "--delay", "0")
    assert code == 0
    assert "remapped 101 -> 100" in out
    assert ingested_ids == [100]
