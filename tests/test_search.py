"""Integration tests for chunk storage and FTS5 search."""

import contextlib
import gzip
import io
import json

from gutenbit.catalog import BookRecord, Catalog
from gutenbit.cli import main as cli_main
from gutenbit.db import Database, SearchResult
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


def test_search_mode_first_orders_by_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", mode="first", limit=2)
    assert [r.position for r in results] == [0, 3]


def test_search_mode_last_orders_reverse_position(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("CHAPTER", book_id=1, kind="heading", mode="last", limit=2)
    assert [r.position for r in results] == [3, 0]


def test_search_help_documents_mode_ordering(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "search", "-h")
    assert code == 0
    assert "ranked: BM25 rank, then book_id, then position" in out
    assert "first:  book_id ascending, then position ascending" in out
    assert "last:   book_id descending, then position descending" in out


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
    assert "gutenbit view 1 --section 1 -n 20" in out
    assert "gutenbit view 1 -n 0" in out
    assert "position=" not in out
    assert "section=" not in out


def test_toc_default_shows_structure(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "toc", "1")
    assert code == 0
    assert "Moby Dick" in out
    assert "CHAPTER 1" in out
    assert "Sections" in out
    assert "Section #" in out
    assert "Section" in out and "Position" in out
    assert "Paras" in out
    assert "Chars" in out
    assert "Est words" in out
    assert "Est read" in out
    assert "Opening" in out
    assert "--position" in out


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
    assert data["mode"] == "opening"
    assert data["opening_chunk_count"] == 3
    assert data["n"] == 3
    assert data["count"] == 3
    assert data["full"] is True
    assert data["meta"] is False
    assert data["chunks"][0] == "CHAPTER 1"
    assert data["action_hints"]["toc"] == "gutenbit toc 1"
    assert data["action_hints"]["view_first_section"] == "gutenbit view 1 --section 1 -n 20"


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
        "position",
        "paras",
        "chars",
        "est_words",
        "est_read",
        "opening_line",
    ]
    assert summary["sections"][0]["est_words"] > 0
    assert summary["sections"][0]["opening_line"].endswith("…")
    assert len(summary["sections"][0]["opening_line"]) <= 141
    assert summary["quick_actions"]["search"] == "gutenbit search <query> --book-id 1"
    assert summary["quick_actions"]["view_first_section"] == "gutenbit view 1 --section 1 -n 20"
    assert summary["quick_actions"]["view_first_position"].startswith(
        "gutenbit view 1 --position "
    )
    assert summary["quick_actions"]["view_from_position"].startswith("gutenbit view 1 --position ")
    assert summary["quick_actions"]["view_full"] == "gutenbit view 1 -n 0"


def test_view_json_full_with_n_zero(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--json", "-n", "0")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "view"
    assert payload["data"]["mode"] == "full"
    assert payload["data"]["n"] == 0
    assert payload["data"]["book_id"] == 1
    assert payload["data"]["chars"] > 0
    assert "Call me Ishmael" in payload["data"]["content"]


def test_view_full_with_n_zero_and_missing_book(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    ok_code, ok_out, _ok_err = _run_cli(db_path, "view", "1", "-n", "0")
    assert ok_code == 0
    assert "Call me Ishmael" in ok_out

    miss_code, miss_out, _miss_err = _run_cli(db_path, "view", "999", "-n", "0")
    assert miss_code == 1
    assert "No text found" in miss_out


def test_view_position_with_n(tmp_path):
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

    code, out, _err = _run_cli(db_path, "view", "1", "--position", str(position), "-n", "2")
    assert code == 0
    assert "Call me Ishmael" in out
    assert "It is a way I have of driving off the spleen" in out
    assert "position=" not in out


def test_view_section_with_n_and_meta(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "CHAPTER 1",
        "-n",
        "1",
        "--meta",
    )
    assert code == 0
    assert "section='CHAPTER 1'" in out
    assert "kind=heading" in out
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
        "-n",
        "1",
    )
    assert code == 0
    assert "CHAPTER I.The Beginning" in out


def test_view_section_accepts_section_number(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "2", "-n", "2")
    assert code == 0
    assert "I stuffed a shirt or two" in out


def test_view_section_number_out_of_range(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "99")
    assert code == 1
    assert "Section 99 is out of range for book 1" in out
    assert "gutenbit toc 1" in out


def test_view_rejects_multiple_selectors(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--position", "1", "--section", "CHAPTER 1")
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))
    monkeypatch.setattr(Database, "has_current_text", lambda _self, _book_id: True)
    monkeypatch.setattr(Database, "has_text", lambda _self, _book_id: True)

    def _ingest_should_not_run(_self, _books, *, delay=1.0):
        raise AssertionError("ingest() should not run for already-downloaded books")

    monkeypatch.setattr(Database, "ingest", _ingest_should_not_run)

    code, out, _err = _run_cli(tmp_path / "skip.db", "ingest", "888", "--delay", "0")
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))
    monkeypatch.setattr(Database, "has_current_text", lambda _self, _book_id: False)
    monkeypatch.setattr(Database, "has_text", lambda _self, _book_id: True)

    ingested_ids: list[int] = []

    def _capture_ingest(_self, books, *, delay=1.0):
        ingested_ids.extend(book.id for book in books)

    monkeypatch.setattr(Database, "ingest", _capture_ingest)

    code, out, _err = _run_cli(tmp_path / "stale.db", "ingest", "889", "--delay", "0")
    assert code == 0
    assert "reprocessing 889: Needs Refresh (chunker updated)" in out
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: catalog))
    monkeypatch.setattr(Database, "has_current_text", lambda _self, _book_id: False)
    monkeypatch.setattr(Database, "has_text", lambda _self, _book_id: False)

    ingested_ids: list[int] = []

    def _capture_ingest(_self, books, *, delay=1.0):
        ingested_ids.extend(book.id for book in books)

    monkeypatch.setattr(Database, "ingest", _capture_ingest)

    code, out, _err = _run_cli(tmp_path / "canonical.db", "ingest", "101", "--delay", "0")
    assert code == 0
    assert "remapped 101 -> 100" in out
    assert ingested_ids == [100]


# ------------------------------------------------------------------
# Validation edge cases
# ------------------------------------------------------------------


def test_view_preview_without_selector_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--preview")
    assert code == 1
    assert "--preview can only be used with --position or --section." in out


def test_view_chars_requires_preview(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--chars", "80")
    assert code == 1
    assert "--chars can only be used with --preview." in out


def test_search_preview_chars_zero_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--preview-chars", "0")
    assert code == 1
    assert "--preview-chars must be > 0" in out


def test_search_preview_chars_negative_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "Ishmael", "--preview-chars", "-5")
    assert code == 1
    assert "--preview-chars must be > 0" in out


def test_view_chars_zero_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path, "view", "1", "--section", "CHAPTER 1", "--preview", "--chars", "0"
    )
    assert code == 1
    assert "--chars must be > 0" in out


def test_view_negative_n_rejected(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "CHAPTER 1", "-n", "-1")
    assert code == 1
    assert "-n must be >= 0." in out


def test_ingest_rejects_non_positive_ids(tmp_path):
    code, out, _err = _run_cli(tmp_path / "any.db", "ingest", "0", "-1")
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
    assert data["mode"] == "ranked"
    assert data["count"] >= 1
    assert len(data["items"]) >= 1

    result = data["items"][0]
    assert result["book_id"] == 1
    assert result["title"] == "Moby Dick"
    assert "Ishmael" in result["content"]
    assert "rank" in result
    assert "position" in result
    assert "section" in result
    assert "score" in result
    assert "kind" in result
    assert "char_count" in result


def test_search_json_empty(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "search", "xyzzyplugh", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "search"
    assert payload["data"]["count"] == 0
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))

    code, out, _err = _run_cli(
        tmp_path / "any.db",
        "catalog",
        "--author",
        "Author",
        "-n",
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


def test_ingest_json_output(tmp_path, monkeypatch):
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
    current_ids: set[int] = set()

    def _has_current(_self, book_id):
        return book_id in current_ids

    def _capture_ingest(_self, books, *, delay=1.0):
        for book in books:
            ingested_ids.append(book.id)
            current_ids.add(book.id)

    monkeypatch.setattr(Database, "has_current_text", _has_current)
    monkeypatch.setattr(Database, "ingest", _capture_ingest)

    code, out, _err = _run_cli(
        tmp_path / "canonical.db",
        "ingest",
        "101",
        "--delay",
        "0",
        "--json",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "ingest"
    assert payload["data"]["counts"]["requested"] == 1
    assert payload["data"]["counts"]["canonical"] == 1
    assert payload["data"]["results"][0]["requested_id"] == 101
    assert payload["data"]["results"][0]["canonical_id"] == 100
    assert payload["data"]["results"][0]["status"] == "ingested"
    assert ingested_ids == [100]


def test_ingest_json_failure_reports_failed_and_stays_parseable(tmp_path, monkeypatch):
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
    monkeypatch.setattr("gutenbit.cli.Catalog.fetch", staticmethod(lambda: Catalog([record])))

    def _boom(_book_id):
        raise RuntimeError("boom")

    monkeypatch.setattr("gutenbit.db.download_html", _boom)

    code, out, _err = _run_cli(
        tmp_path / "broken.db",
        "ingest",
        "555",
        "--delay",
        "0",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "ingest"
    assert payload["data"]["failed_canonical_ids"] == [555]
    assert payload["data"]["results"][0]["status"] == "failed"
    assert "Failed to ingest 555: Broken Download" in payload["errors"]


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

    code, out, _err = _run_cli(db_path, "view", "1", "--section", "CHAPTER 1", "-n", "1", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["command"] == "view"
    assert payload["data"]["mode"] == "section"
    assert payload["data"]["section"] == "CHAPTER 1"
    assert payload["data"]["n"] == 1
    assert payload["data"]["meta"] is False
    assert payload["data"]["count"] == 1
    assert payload["data"]["chunks"][0] == "CHAPTER 1"


def test_view_section_json_meta_output(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path, "view", "1", "--section", "CHAPTER 1", "-n", "1", "--meta", "--json"
    )
    assert code == 0
    payload = json.loads(out)
    chunk = payload["data"]["chunks"][0]
    assert payload["data"]["meta"] is True
    assert chunk["section"] == "CHAPTER 1"
    assert chunk["position"] == 0


def test_view_json_validation_error_uses_envelope(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(
        db_path,
        "view",
        "1",
        "--section",
        "CHAPTER 1",
        "--preview",
        "--chars",
        "0",
        "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "view"
    assert payload["errors"] == ["--chars must be > 0."]


def test_books_has_column_headers(tmp_path):
    db = _make_db(tmp_path)
    db_path = db.path
    db.close()

    code, out, _err = _run_cli(db_path, "books")
    assert code == 0
    assert "ID" in out
    assert "AUTHORS" in out
    assert "TITLE" in out
