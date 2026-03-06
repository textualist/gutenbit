"""Integration tests for chunk storage and FTS5 search."""

from gutenbit.catalog import BookRecord
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


def _make_db(tmp_path):
    """Create a Database with test data (bypassing download)."""
    db = Database(tmp_path / "test.db")
    db._store(_BOOK, chunk_html(_BOOK_HTML))
    db._store(_BOOK2, chunk_html(_BOOK2_HTML))
    return db


# ------------------------------------------------------------------
# Chunk storage
# ------------------------------------------------------------------


def test_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()
    # Book 1: 2 front_matter (TOC) + heading + 2 para + heading + 1 para = 7
    # Book 2: 1 front_matter (TOC) + heading + 1 para = 3
    assert rows["n"] == 10


def test_chunks_have_chapters(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT div2 FROM chunks WHERE book_id = ? AND kind = 'paragraph' ORDER BY position",
        (1,),
    ).fetchall()
    chapters = [r["div2"] for r in rows]
    assert chapters == ["CHAPTER 1", "CHAPTER 1", "CHAPTER 2"]


def test_chunks_have_kinds(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT kind FROM chunks WHERE book_id = ? ORDER BY position", (1,)
    ).fetchall()
    kinds = [r["kind"] for r in rows]
    assert kinds == [
        "front_matter",
        "front_matter",
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
    assert len(chunks) == 7


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
    assert r.div2 == "CHAPTER 1"
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
