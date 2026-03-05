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


# ------------------------------------------------------------------
# Chunk storage
# ------------------------------------------------------------------


def test_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()
    # _TEXT: heading, para, para, heading, para = 5
    # _TEXT2: heading, para = 2
    assert rows["n"] == 7


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
    assert kinds == ["heading", "paragraph", "paragraph", "heading", "paragraph"]


def test_heading_chunks_stored(tmp_path):
    db = _make_db(tmp_path)
    rows = db._conn.execute(
        "SELECT content FROM chunks WHERE book_id = ? AND kind = 'heading' ORDER BY position",
        (1,),
    ).fetchall()
    assert [r["content"] for r in rows] == ["CHAPTER 1", "CHAPTER 2"]


def test_no_short_kind_exists(tmp_path):
    """The 'short' kind should never appear — short text is accumulated."""
    db = _make_db(tmp_path)
    rows = db._conn.execute("SELECT COUNT(*) as n FROM chunks WHERE kind = 'short'").fetchone()
    assert rows["n"] == 0


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
    assert all(k == "paragraph" for _, _, _, _, _, _, k in paragraphs)


def test_chunks_method_reconstruct_text(tmp_path):
    db = _make_db(tmp_path)
    chunks = db.chunks(1)
    reconstructed = "\n\n".join(content for _, _, _, _, _, content, _ in chunks)
    assert "Call me Ishmael" in reconstructed
    assert "CHAPTER 1" in reconstructed


def test_chunks_method_prose_only(tmp_path):
    """Filtering to 'paragraph' gives prose without headings."""
    db = _make_db(tmp_path)
    prose = db.chunks(1, kinds=["paragraph"])
    kinds = {k for _, _, _, _, _, _, k in prose}
    assert kinds == {"paragraph"}
    contents = "\n\n".join(c for _, _, _, _, _, c, _ in prose)
    assert "Call me Ishmael" in contents
    assert "CHAPTER" not in contents


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
    assert r.div1 == ""  # no PART/BOOK heading in test data
    assert r.div2 == "CHAPTER 1"  # CHAPTER is rank-2
    assert r.kind == "paragraph"
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


# ------------------------------------------------------------------
# Dickens integration — realistic multi-chapter text with dialogue
# ------------------------------------------------------------------

_DICKENS_BOOK = BookRecord(
    id=3,
    title="Oliver Twist",
    authors="Dickens, Charles",
    language="en",
    subjects="Orphans; London; Social conditions",
    locc="PR",
    bookshelves="Best Books Ever Listings",
    issued="1996-01-01",
    type="Text",
)

_DICKENS_TEXT = (
    "CHAPTER I\n"
    "\n"
    "Among other public buildings in a certain town, which for many reasons\n"
    "it will be prudent to refrain from mentioning, and to which I will\n"
    "assign no fictitious name, there is one anciently common to most towns,\n"
    "great or small: to wit, a workhouse.\n"
    "\n"
    "'What's your name?'\n"
    "\n"
    "The boy hesitated.\n"
    "\n"
    "'Oliver Twist.'\n"
    "\n"
    "* * *\n"
    "\n"
    "CHAPTER II\n"
    "\n"
    "For the next eight or ten months, Oliver was the victim of a\n"
    "systematic course of treachery and deception. He was brought up by\n"
    "hand. The hungry and destitute situation of the infant orphan was duly\n"
    "reported by the workhouse authorities to the parish authorities.\n"
)


def test_dickens_dialogue_accumulated(tmp_path):
    """Short dialogue lines are accumulated into paragraph chunks, not lost."""
    db = Database(tmp_path / "test.db")
    db._store(_DICKENS_BOOK, _DICKENS_TEXT)

    all_chunks = db.chunks(3)
    reconstructed = "\n\n".join(content for _, _, _, _, _, content, _ in all_chunks)
    assert "Oliver Twist" in reconstructed
    assert "What's your name" in reconstructed
    assert "boy hesitated" in reconstructed


def test_dickens_reconstruct_full_text(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_DICKENS_BOOK, _DICKENS_TEXT)

    all_chunks = db.chunks(3)
    reconstructed = "\n\n".join(content for _, _, _, _, _, content, _ in all_chunks)
    assert "workhouse" in reconstructed
    assert "Oliver Twist" in reconstructed
    assert "* * *" in reconstructed
    assert "CHAPTER II" in reconstructed


def test_dickens_filter_prose_only(tmp_path):
    db = Database(tmp_path / "test.db")
    db._store(_DICKENS_BOOK, _DICKENS_TEXT)

    prose = db.chunks(3, kinds=["paragraph"])
    kinds = {k for _, _, _, _, _, _, k in prose}
    assert kinds == {"paragraph"}
    contents = "\n\n".join(c for _, _, _, _, _, c, _ in prose)
    # Dialogue is inside paragraph chunks (accumulated)
    assert "Oliver Twist" in contents
    # Headings excluded from prose
    assert "CHAPTER" not in contents
