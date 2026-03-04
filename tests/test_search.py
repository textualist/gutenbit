"""Integration tests for chunk storage and FTS5 search."""

from gutenbit.catalog import BookRecord
from gutenbit.db import Database, SearchResult

# A small fake book for testing.
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

_TEXT = (
    "CHAPTER 1\n"
    "\n"
    "Call me Ishmael. Some years ago, never mind how long precisely, "
    "having little or no money in my purse, and nothing particular to "
    "interest me on shore, I thought I would sail about a little and "
    "see the watery part of the world.\n"
    "\n"
    "It is a way I have of driving off the spleen and regulating the "
    "circulation. Whenever I find myself growing grim about the mouth; "
    "whenever it is a damp, drizzly November in my soul; I account it "
    "high time to get to sea as soon as I can.\n"
    "\n"
    "CHAPTER 2\n"
    "\n"
    "I stuffed a shirt or two into my old carpet-bag, tucked it under "
    "my arm, and started for Cape Horn and the Pacific. The great "
    "flood-gates of the wonder-world swung open, and in the wild "
    "conceits that swayed me to my purpose, two and twenty of the "
    "pagan world came flooding in.\n"
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

_TEXT2 = (
    "Chapter 1\n"
    "\n"
    "It is a truth universally acknowledged, that a single man in "
    "possession of a good fortune, must be in want of a wife. However "
    "little known the feelings or views of such a man may be on his "
    "first entering a neighbourhood.\n"
)


def _make_db(tmp_path):
    """Create a Database with test data (bypassing download)."""
    db = Database(tmp_path / "test.db")
    db._store(_BOOK, _TEXT)
    db._store(_BOOK2, _TEXT2)
    return db


def test_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()
    assert rows["n"] == 4  # 3 paragraphs from _TEXT + 1 from _TEXT2


def test_chunks_have_chapters(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT chapter FROM chunks WHERE book_id = ? ORDER BY position", (1,)
    ).fetchall()
    chapters = [r["chapter"] for r in rows]
    assert chapters == ["CHAPTER 1", "CHAPTER 1", "CHAPTER 2"]


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
    assert r.chapter == "CHAPTER 1"
    assert r.score > 0


def test_search_filter_by_author(tmp_path):
    db = _make_db(tmp_path)
    # "truth" appears in Austen, not Melville
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
    # Search for text in book 1 but filter to book 2 — should find nothing
    results = db.search("Ishmael", book_id=2)
    assert results == []


def test_search_limit(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("the", limit=1)
    assert len(results) <= 1


def test_search_bm25_ranking(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("Ishmael")
    # The paragraph mentioning Ishmael should rank highest
    assert "Ishmael" in results[0].content


def test_fts_porter_stemming(tmp_path):
    db = _make_db(tmp_path)
    # "sailing" should match "sail" via porter stemmer
    results = db.search("sailing")
    assert len(results) >= 1
    assert any("sail" in r.content for r in results)


def test_search_no_results(tmp_path):
    db = _make_db(tmp_path)
    results = db.search("xyzzyplugh")
    assert results == []
