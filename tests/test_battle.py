"""Battle tests: real-book ingestion and CLI against live Project Gutenberg data.

These tests download actual books and validate the full pipeline — chunker
output, database storage, CLI commands, and search.  They are slow and require
network access, so they are gated behind ``-m network``::

    uv run pytest tests/test_battle.py -m network -x -v
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from gutenbit.catalog import BookRecord
from gutenbit.db import Database
from gutenbit.download import download_html
from gutenbit.html_chunker import Chunk, chunk_html

# ---------------------------------------------------------------------------
# Marker: all tests in this file require network access
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.network


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _download_and_chunk(book_id: int) -> list[Chunk]:
    html = download_html(book_id)
    return chunk_html(html)


def _kind_counts(chunks: list[Chunk]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in chunks:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return counts


def _headings(chunks: list[Chunk]) -> list[Chunk]:
    return [c for c in chunks if c.kind == "heading"]


def _run_cli(*args: str, db: str = "test.db") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gutenbit", "--db", db, *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


# ---------------------------------------------------------------------------
# Fake catalog records for books (bypass catalog download during tests)
# ---------------------------------------------------------------------------

_BOOKS: dict[int, BookRecord] = {
    100: BookRecord(
        100,
        "The Complete Works of William Shakespeare",
        "Shakespeare, William, 1564-1616",
        "en",
        "",
        "",
        "",
        "",
        "Text",
    ),
    2600: BookRecord(2600, "War and Peace", "Tolstoy, Leo", "en", "", "", "", "", "Text"),
    2554: BookRecord(
        2554, "Crime and Punishment", "Dostoyevsky, Fyodor", "en", "", "", "", "", "Text"
    ),
    46: BookRecord(46, "A Christmas Carol", "Dickens, Charles", "en", "", "", "", "", "Text"),
    967: BookRecord(967, "Nicholas Nickleby", "Dickens, Charles", "en", "", "", "", "", "Text"),
    730: BookRecord(730, "Oliver Twist", "Dickens, Charles", "en", "", "", "", "", "Text"),
    1342: BookRecord(1342, "Pride and Prejudice", "Austen, Jane", "en", "", "", "", "", "Text"),
    7370: BookRecord(
        7370,
        "Second Treatise of Government",
        "Locke, John",
        "en",
        "",
        "",
        "",
        "",
        "Text",
    ),
    48320: BookRecord(
        48320,
        "The Adventures of Sherlock Holmes",
        "Doyle, Arthur Conan",
        "en",
        "",
        "",
        "",
        "",
        "Text",
    ),
    30802: BookRecord(
        30802,
        "Commentaries on the Laws of England",
        "Blackstone, William",
        "en",
        "",
        "",
        "",
        "",
        "Text",
    ),
    15: BookRecord(15, "Moby Dick", "Melville, Herman", "en", "", "", "", "", "Text"),
    1727: BookRecord(1727, "The Odyssey", "Homer; Butler, Samuel", "en", "", "", "", "", "Text"),
    84: BookRecord(84, "Frankenstein", "Shelley, Mary", "en", "", "", "", "", "Text"),
    345: BookRecord(345, "Dracula", "Stoker, Bram", "en", "", "", "", "", "Text"),
    150: BookRecord(150, "The Republic", "Plato; Jowett, Benjamin", "en", "", "", "", "", "Text"),
    74: BookRecord(
        74, "The Adventures of Tom Sawyer", "Twain, Mark", "en", "", "", "", "", "Text"
    ),
    5200: BookRecord(5200, "Metamorphosis", "Kafka, Franz", "en", "", "", "", "", "Text"),
    132: BookRecord(132, "The Art of War", "Sun Tzu; Giles, Lionel", "en", "", "", "", "", "Text"),
    4300: BookRecord(4300, "Ulysses", "Joyce, James", "en", "", "", "", "", "Text"),
    28054: BookRecord(
        28054, "The Brothers Karamazov", "Dostoyevsky, Fyodor", "en", "", "", "", "", "Text"
    ),
}


# ===================================================================
# CHUNKER TESTS — validate structural parsing of real books
# ===================================================================


class TestWarAndPeace:
    """PG 2600 — Word-ordinal BOOK headings, 15 books + 2 epilogues."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(2600)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 10000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # 17 div1 headings (15 books + 2 epilogues) + ~365 chapters
        assert len(headings) > 350

    def test_div1_books(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        div1_values = sorted({h.div1 for h in headings if h.div1})
        # Must have 15 BOOK entries + 2 EPILOGUE entries
        assert len(div1_values) == 17
        book_headings = [d for d in div1_values if d.startswith("BOOK")]
        assert len(book_headings) == 15
        assert "BOOK ONE: 1805" in div1_values
        assert "BOOK FIFTEEN: 1812 - 13" in div1_values

    def test_epilogues(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        div1_values = {h.div1 for h in headings if h.div1}
        assert "FIRST EPILOGUE: 1813 - 20" in div1_values
        assert "SECOND EPILOGUE" in div1_values

    def test_chapters_under_books(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Chapters under BOOK ONE should have BOOK ONE as div1
        book_one_chapters = [
            h for h in headings if h.div1 == "BOOK ONE: 1805" and h.div2.startswith("CHAPTER")
        ]
        assert len(book_one_chapters) >= 20

    def test_all_kinds_present(self, chunks: list[Chunk]):
        kinds = {c.kind for c in chunks}
        assert "heading" in kinds
        assert "text" in kinds
        # War and Peace has no front matter — TOC links start at BOOK ONE

    def test_positions_sequential(self, chunks: list[Chunk]):
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))

    def test_no_empty_content(self, chunks: list[Chunk]):
        for c in chunks:
            assert c.content.strip(), f"Empty chunk at position {c.position}"


class TestCrimeAndPunishment:
    """PG 2554 — PART + CHAPTER two-level hierarchy."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(2554)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 3000

    def test_part_hierarchy(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        div1_values = {h.div1 for h in headings if h.div1}
        # Should have PART I through PART VI plus top-level headings
        parts = [d for d in div1_values if d.startswith("PART")]
        assert len(parts) >= 6

    def test_chapters_per_part(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        part1_chapters = [
            h for h in headings if h.div1 == "PART I" and h.div2.startswith("CHAPTER")
        ]
        assert len(part1_chapters) == 7

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Total: ~48 headings (translator's preface + title + 6 parts + epilogue + ~41 chapters)
        assert len(headings) >= 40

    def test_no_toc_labels_as_paragraphs(self, chunks: list[Chunk]):
        paragraphs = [c.content.strip() for c in chunks if c.kind == "text"]
        toc_like = [
            text
            for text in paragraphs
            if re.fullmatch(r"(?:PART|CHAPTER)\.?\s+[IVXLCDM0-9]+\.?", text, re.IGNORECASE)
        ]
        assert toc_like == []


class TestChristmasCarol:
    """PG 46 — STAVE headings."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(46)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 500

    def test_five_staves(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 5
        stave_texts = [h.content for h in headings]
        for label in ["ONE", "TWO", "THREE", "FOUR", "FIVE"]:
            assert any(label in s for s in stave_texts), f"Missing STAVE {label}"

    def test_staves_as_div1(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        for h in headings:
            assert h.div1.startswith("STAVE"), f"Expected STAVE in div1, got {h.div1!r}"

    def test_paragraphs_have_content(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        assert len(paragraphs) > 600
        assert any("Marley" in p.content for p in paragraphs)
        assert any("Scrooge" in p.content for p in paragraphs)


class TestShakespeareCompleteWorks:
    """PG 100 — anthology titles should contain acts and scenes cleanly."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(100)

    def test_work_titles_are_top_level(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        for title in [
            "ALL’S WELL THAT ENDS WELL",
            "THE TRAGEDY OF ANTONY AND CLEOPATRA",
            "THE TWO NOBLE KINSMEN",
            "THE WINTER’S TALE",
            "VENUS AND ADONIS",
        ]:
            match = next(h for h in headings if h.content == title)
            assert match.div1 == title
            assert match.div2 == ""

    def test_acts_and_scenes_nest_under_work_titles(self, chunks: list[Chunk]):
        headings = _headings(chunks)

        alls_well_act = next(
            h
            for h in headings
            if h.content == "ACT I" and h.div1 == "ALL’S WELL THAT ENDS WELL"
        )
        assert alls_well_act.div2 == "ACT I"

        alls_well_scene = next(
            h
            for h in headings
            if h.content == "Scene I. Rossillon. A room in the Countess’s palace"
        )
        assert alls_well_scene.div1 == "ALL’S WELL THAT ENDS WELL"
        assert alls_well_scene.div2 == "ACT I"
        assert alls_well_scene.div3 == "Scene I. Rossillon. A room in the Countess’s palace"

        antony_scene = next(
            h
            for h in headings
            if h.content == "Scene I. Alexandria. A Room in Cleopatra’s palace"
        )
        assert antony_scene.div1 == "THE TRAGEDY OF ANTONY AND CLEOPATRA"
        assert antony_scene.div2 == "ACT I"

    def test_scene_headings_match_raw_shakespeare_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert any(h.content == "SCENE II. Rome. Before Titus’s House" for h in headings)
        assert any(h.content == "SCENE III. The country near Athens" for h in headings)
        assert all("Enter " not in h.content for h in headings)

    def test_new_work_titles_are_not_nested_under_previous_act_five(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        for title in [
            "THE TRAGEDY OF ANTONY AND CLEOPATRA",
            "THE TWO NOBLE KINSMEN",
            "THE WINTER’S TALE",
            "VENUS AND ADONIS",
        ]:
            match = next(h for h in headings if h.content == title)
            assert match.div1 == title
            assert match.div2 != "ACT V"

    def test_sonnets_remain_top_level(self, chunks: list[Chunk]):
        heading = next(h for h in _headings(chunks) if h.content == "THE SONNETS")
        assert heading.div1 == "THE SONNETS"
        assert heading.div2 == ""


class TestNicholasNickleby:
    """PG 967 — Multi-chapter with preface."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(967)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 7000

    def test_chapter_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if "CHAPTER" in h.content.upper()]
        # 65 chapters
        assert len(chapter_headings) >= 64

    def test_has_preface(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert any("PREFACE" in h.content.upper() for h in headings)

    def test_no_toc_labels_as_paragraphs(self, chunks: list[Chunk]):
        paragraphs = [c.content.strip() for c in chunks if c.kind == "text"]
        toc_like = [
            text
            for text in paragraphs
            if re.fullmatch(r"CHAPTER\.?\s+[IVXLCDM0-9]+\.?", text, re.IGNORECASE)
        ]
        assert toc_like == []


class TestOliverTwist:
    """PG 730 — Chapters with long descriptive titles."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(730)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 3000

    def test_chapter_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 53

    def test_descriptive_chapter_titles(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Oliver Twist chapters have descriptive titles after the number
        ch1 = headings[0]
        assert "CHAPTER I" in ch1.content
        assert "OLIVER TWIST" in ch1.content.upper()

    def test_chunk_kinds_are_simplified(self, chunks: list[Chunk]):
        # Simplified chunk kinds: heading + paragraph only.
        kinds = _kind_counts(chunks)
        assert set(kinds) <= {"heading", "text"}


class TestPrideAndPrejudice:
    """PG 1342 — Illustrated edition with bracket artifacts."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(1342)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 2000

    def test_chapter_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if "CHAPTER" in h.content.upper()]
        # Should have ~61 chapter headings (some editions have preface/illustrations list)
        assert len(chapter_headings) >= 55

    def test_chunk_kinds_are_simplified(self, chunks: list[Chunk]):
        kinds = _kind_counts(chunks)
        assert set(kinds) <= {"heading", "text"}

    def test_dropcap_letters_preserved(self, chunks: list[Chunk]):
        paragraphs = [c.content for c in chunks if c.kind == "text"]
        matches = [p for p in paragraphs if "BENNET was among the earliest" in p]
        assert matches
        assert any("MR. BENNET" in p.upper() for p in matches)


class TestLockeSecondTreatise:
    """PG 7370 — CHAPTER. I. period-after-keyword format."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(7370)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 300

    def test_chapter_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 19

    def test_period_format_chapters(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Locke uses "CHAPTER. I." format — period after keyword
        chapter_headings = [h for h in headings if h.content.startswith("CHAPTER.")]
        assert len(chapter_headings) >= 15

    def test_chapters_as_div1(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if h.content.startswith("CHAPTER")]
        for h in chapter_headings:
            assert h.div1.startswith("CHAPTER"), f"Expected div1 to be CHAPTER, got {h.div1!r}"


class TestLockeEssayVolume2:
    """PG 10616 — heading-scan fallback should skip contents scaffolding."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(10616)

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 33

    def test_contents_block_is_not_emitted_as_sections(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert [h.content for h in headings[:3]] == [
            "BOOK III OF WORDS",
            "CHAPTER I OF WORDS OR LANGUAGE IN GENERAL",
            "CHAPTER II OF THE SIGNIFICATION OF WORDS",
        ]
        heading_texts = {h.content for h in headings}
        assert "BOOK III. OF WORDS" not in heading_texts
        assert "BOOK IV. OF KNOWLEDGE AND PROBABILITY" not in heading_texts
        assert "CHAP" not in heading_texts

    def test_book_four_heading_drops_editorial_synopsis(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        heading_texts = {h.content for h in headings}
        assert "BOOK IV OF KNOWLEDGE AND PROBABILITY" in heading_texts
        assert "BOOK IV OF KNOWLEDGE AND PROBABILITY SYNOPSIS OF THE FOURTH BOOK" not in (
            heading_texts
        )

    def test_wrong_assent_subheads_are_not_sections(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        heading_texts = {h.content for h in headings}
        assert "CHAPTER XX OF WRONG ASSENT, OR ERROR" in heading_texts
        assert "CHAPTER XXI OF THE DIVISION OF THE SCIENCES" in heading_texts
        assert "CHAPTER XIX. [not in early editions" not in heading_texts
        assert "I. WANT OF PROOFS" not in heading_texts
        assert "II. WANT OF ABILITY TO USE THEM" not in heading_texts
        assert "III. WANT OF WILL TO SEE THEM" not in heading_texts
        assert "IV. WRONG MEASURES OF PROBABILITY" not in heading_texts


class TestSherlockHolmes:
    """PG 48320 — Adventures of Sherlock Holmes, story collection."""

    _STORY_TITLES = [
        "Adventure I A SCANDAL IN BOHEMIA",
        "Adventure II THE RED-HEADED LEAGUE",
        "Adventure III A CASE OF IDENTITY",
        "Adventure IV THE BOSCOMBE VALLEY MYSTERY",
        "Adventure V THE FIVE ORANGE PIPS",
        "Adventure VI THE MAN WITH THE TWISTED LIP",
        "Adventure VII THE ADVENTURE OF THE BLUE CARBUNCLE",
        "Adventure VIII THE ADVENTURE OF THE SPECKLED BAND",
        "Adventure IX THE ADVENTURE OF THE ENGINEER’S THUMB",
        "Adventure X THE ADVENTURE OF THE NOBLE BACHELOR",
        "Adventure XI THE ADVENTURE OF THE BERYL CORONET",
        "Adventure XII THE ADVENTURE OF THE COPPER BEECHES",
    ]

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(48320)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 2400

    def test_has_stories(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == len(self._STORY_TITLES)

    def test_story_titles(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        titles = [h.content for h in headings]
        assert titles == self._STORY_TITLES

    def test_stories_as_div1(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        for h in headings:
            assert h.div1, f"Expected div1 for story heading {h.content!r}"

    def test_chunk_kinds_are_simplified(self, chunks: list[Chunk]):
        kinds = _kind_counts(chunks)
        assert set(kinds) <= {"heading", "text"}

    def test_holmes_watson_present(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs)
        assert "Holmes" in all_text
        assert "Watson" in all_text


class TestBlackstonesCommentaries:
    """PG 30802 — Blackstone's Commentaries on the Laws of England.

    Structurally challenging: the PG edition has 2500+ TOC links (page refs,
    errata, footnote anchors).  The chunker must extract real chapter headings
    from ``<span class="smcap">`` wrapped text inside heading tags.
    """

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(30802)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 2000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # ~25-26 sections: ERRATA, CONTENTS, INTRODUCTION, Sections, Book heading, Chapters
        assert 24 <= len(headings) <= 28

    def test_no_punctuation_only_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        for h in headings:
            assert any(c.isalpha() for c in h.content), (
                f"Punctuation-only heading at pos {h.position}: {h.content!r}"
            )

    def test_chapter_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if h.content.startswith("Chapter the")]
        assert len(chapter_headings) == 18

    def test_introduction_sections(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        sections = [h for h in headings if h.content.startswith("Section the")]
        assert len(sections) == 4

    def test_chunk_kinds_are_simplified(self, chunks: list[Chunk]):
        kinds = _kind_counts(chunks)
        assert set(kinds) <= {"heading", "text"}

    def test_has_paragraphs(self, chunks: list[Chunk]):
        kinds = _kind_counts(chunks)
        assert kinds.get("text", 0) > 1500

    def test_legal_content_present(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs[:200])
        assert "law" in all_text.lower()


class TestMobyDick:
    """PG 15 — 136 chapters, ETYMOLOGY/EXTRACTS as unsectioned opening."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(15)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 2000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 136

    def test_chapters_as_div1(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if "CHAPTER" in h.content.upper()]
        assert len(chapter_headings) >= 134
        for h in chapter_headings:
            assert h.div1.startswith("CHAPTER"), f"Expected div1=CHAPTER, got {h.div1!r}"

    def test_epilogue_present(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert any("EPILOGUE" in h.content.upper() for h in headings)

    def test_unsectioned_opening_has_etymology(self, chunks: list[Chunk]):
        opening = [c for c in chunks if c.kind == "text" and c.div1 == ""]
        assert len(opening) > 50
        all_text = " ".join(c.content for c in opening)
        assert "ETYMOLOGY" in all_text.upper() or "whale" in all_text.lower()

    def test_positions_sequential(self, chunks: list[Chunk]):
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))


class TestOdyssey:
    """PG 1727 — BOOK-based epic with endnotes after the last section."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(1727)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 1000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # PREFACE + THE ODYSSEY + BOOK I..XXIV = 27
        assert len(headings) == 27

    def test_book_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        book_headings = [h for h in headings if h.content.startswith("BOOK")]
        assert len(book_headings) == 24

    def test_endnotes_excluded_from_last_book(self, chunks: list[Chunk]):
        """Endnotes should not be lumped into BOOK XXIV."""
        book_xxiv_paras = [c for c in chunks if c.kind == "text" and "BOOK XXIV" in c.div1]
        # Real text is ~46 paragraphs; without fix it was ~250 (with ~200 footnotes)
        assert len(book_xxiv_paras) < 60

    def test_no_footnote_brackets_in_text(self, chunks: list[Chunk]):
        """Footnote markers like [1] should not appear as chunks."""
        book_xxiv_paras = [c for c in chunks if c.kind == "text" and "BOOK XXIV" in c.div1]
        footnote_like = [c for c in book_xxiv_paras if re.match(r"^\[\d+\]", c.content)]
        assert len(footnote_like) == 0


class TestFrankenstein:
    """PG 84 — Letter + Chapter mixed structure."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(84)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 700

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # 4 letters + 24 chapters = 28
        assert len(headings) == 28

    def test_has_letters(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        letter_headings = [h for h in headings if "LETTER" in h.content.upper()]
        assert len(letter_headings) == 4

    def test_has_chapters(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if "CHAPTER" in h.content.upper()]
        assert len(chapter_headings) == 24

    def test_creature_content(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs)
        assert "Frankenstein" in all_text


class TestDracula:
    """PG 345 — Journal-attributed chapters, singular 'NOTE' epilogue."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(345)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 1900

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 27

    def test_journal_attribution_in_chapter_titles(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        journal_chapters = [h for h in headings if "JOURNAL" in h.content.upper()]
        assert len(journal_chapters) >= 5

    def test_note_epilogue_not_excluded(self, chunks: list[Chunk]):
        """The singular 'NOTE' heading is a narrative epilogue, not apparatus."""
        last_chapter_paras = [c for c in chunks if c.kind == "text" and "CHAPTER XXVII" in c.div1]
        all_text = " ".join(c.content for c in last_chapter_paras)
        # The NOTE epilogue content should be included (within or after Ch XXVII)
        assert "Seven years ago" in all_text or any(
            "Seven years ago" in c.content for c in chunks if c.kind == "text"
        )


class TestRepublic:
    """PG 150 — BOOK + speaker sub-sections."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(150)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 4000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 35

    def test_ten_books(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        div1_values = sorted({h.div1 for h in headings if h.div1})
        book_headings = [d for d in div1_values if d.startswith("BOOK")]
        assert len(book_headings) == 10

    def test_speaker_subsections(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        socrates_headings = [h for h in headings if "SOCRATES" in h.content.upper()]
        assert len(socrates_headings) > 20


class TestTomSawyer:
    """PG 74 — Simple chapters with CONCLUSION."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(74)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 1800

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 35

    def test_chapter_headings(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        chapter_headings = [h for h in headings if "CHAPTER" in h.content.upper()]
        assert len(chapter_headings) >= 33

    def test_all_chapters_present(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert headings[-1].content == "CHAPTER XXXV"

    def test_tom_in_content(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs[:100])
        assert "Tom" in all_text


class TestMetamorphosis:
    """PG 5200 — No TOC links, heading-fallback with front-matter attribution."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(5200)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 90

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Metamorphosis (title) + I + II + III = 4
        assert len(headings) == 4

    def test_no_attribution_headings(self, chunks: list[Chunk]):
        """Front-matter 'by Franz Kafka' and 'Translated by David Wyllie' excluded."""
        headings = _headings(chunks)
        heading_texts = [h.content.lower() for h in headings]
        assert not any(t.startswith("by ") for t in heading_texts)
        assert not any("translated by" in t for t in heading_texts)

    def test_three_parts(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        roman_headings = [h for h in headings if h.content in ("I", "II", "III")]
        assert len(roman_headings) == 3

    def test_gregor_in_content(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs[:20])
        assert "Gregor" in all_text


class TestArtOfWar:
    """PG 132 — Short numbered chapters with prefaces."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(132)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 1000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 22

    def test_has_chapters(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Chapters like "I. LAYING PLANS", "II. WAGING WAR", etc.
        # These are not keyword-prefixed, so count content headings
        assert len(headings) >= 13

    def test_positions_sequential(self, chunks: list[Chunk]):
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))


class TestUlysses:
    """PG 4300 — '— I —' parts with '[ 1' episode numbers."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(4300)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 7000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # 3 parts (— I —, — II —, — III —) + 18 episodes ([ 1 .. [ 18) = 21
        assert len(headings) == 21

    def test_three_parts(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        part_headings = [h for h in headings if h.content.startswith("—")]
        assert len(part_headings) == 3

    def test_eighteen_episodes(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        episode_headings = [h for h in headings if h.content.startswith("[")]
        assert len(episode_headings) == 18

    def test_bloom_in_content(self, chunks: list[Chunk]):
        paragraphs = [c for c in chunks if c.kind == "text"]
        all_text = " ".join(p.content for p in paragraphs[:500])
        assert "Bloom" in all_text or "Mulligan" in all_text


class TestBrothersKaramazov:
    """PG 28054 — PART + Book + Chapter three-level hierarchy with EPILOGUE."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(28054)

    def test_produces_chunks(self, chunks: list[Chunk]):
        assert len(chunks) > 5000

    def test_heading_count(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        assert len(headings) == 113

    def test_four_parts_and_epilogue(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        div1_values = sorted({h.div1 for h in headings if h.div1})
        part_headings = [d for d in div1_values if d.startswith("PART")]
        assert len(part_headings) == 4
        assert any("EPILOGUE" in d for d in div1_values)

    def test_twelve_books(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Book headings are level 1 (broad keyword "book"), compacted to div1
        div1_values = {h.div1 for h in headings if h.div1}
        book_headings = [d for d in div1_values if d.startswith("Book")]
        assert len(book_headings) == 12

    def test_chapters_in_div2(self, chunks: list[Chunk]):
        headings = _headings(chunks)
        # Chapters are level 2, compacted to div2
        chapter_headings = [h for h in headings if h.div2 and "Chapter" in h.div2]
        assert len(chapter_headings) > 80

    def test_endnotes_excluded(self, chunks: list[Chunk]):
        """FOOTNOTES section at end should not leak into the last chapter."""
        last_chapter_paras = [c for c in chunks if c.kind == "text" and "Ilusha" in c.div3]
        footnote_like = [c for c in last_chapter_paras if re.match(r"^\[\d+\]", c.content)]
        assert len(footnote_like) == 0


class TestHardTimes:
    """PG 786 — regression for TOC ordering and delimiter-bounded content."""

    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return _download_and_chunk(786)

    def test_book_second_chapter_iv_precedes_chapter_v(self, chunks: list[Chunk]):
        headings = [
            h.content
            for h in _headings(chunks)
            if h.div1.startswith("BOOK THE SECOND") and h.div2.startswith("CHAPTER")
        ]
        ch4 = [h for h in headings if h.startswith("CHAPTER IV")]
        ch5 = [h for h in headings if h.startswith("CHAPTER V ") or h == "CHAPTER V"]
        assert ch4, "CHAPTER IV not found"
        assert ch5, "CHAPTER V not found"
        assert headings.index(ch4[0]) < headings.index(ch5[0])

    def test_excludes_pg_license_heading(self, chunks: list[Chunk]):
        heading_text = [h.content for h in _headings(chunks)]
        assert not any("PROJECT GUTENBERG" in text.upper() for text in heading_text)

    def test_no_spurious_title_page_section_heading(self, chunks: list[Chunk]):
        heading_text = [h.content for h in _headings(chunks)]
        assert not any("HARD TIMES AND REPRINTED PIECES" in text.upper() for text in heading_text)

    def test_chapter_i_present_in_all_three_books(self, chunks: list[Chunk]):
        chapter_one_books = {
            h.div1
            for h in _headings(chunks)
            if h.content.startswith("CHAPTER I") and h.div1.startswith("BOOK")
        }
        assert len(chapter_one_books) == 3
        assert all(b.startswith("BOOK THE") for b in chapter_one_books)


# ===================================================================
# INGESTION + CLI TESTS — end-to-end pipeline
# ===================================================================


class TestIngestionPipeline:
    """Ingest a subset of books and exercise every CLI command."""

    @pytest.fixture(scope="class")
    def db_path(self, tmp_path_factory) -> str:
        path = str(tmp_path_factory.mktemp("battle") / "battle.db")
        book_ids = [46, 48320]  # Small books for speed
        with Database(path) as db:
            for bid in book_ids:
                html = download_html(bid)
                chunks = chunk_html(html)
                db._store(_BOOKS[bid], chunks)
        return path

    def test_books_stored(self, db_path: str):
        with Database(db_path) as db:
            books = db.books()
        assert len(books) == 2
        ids = {b.id for b in books}
        assert 46 in ids
        assert 48320 in ids

    def test_chunks_stored(self, db_path: str):
        with Database(db_path) as db:
            carol_chunks = db.chunks(46)
            sherlock_chunks = db.chunks(48320)
        assert len(carol_chunks) > 500
        assert len(sherlock_chunks) > 2400

    def test_text_stored(self, db_path: str):
        with Database(db_path) as db:
            text = db.text(46)
        assert text is not None
        assert "Marley" in text
        assert "Scrooge" in text

    def test_search_basic(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("Scrooge")
        assert len(results) >= 1
        assert all(r.book_id == 46 for r in results)

    def test_search_across_books(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("the", limit=50)
        book_ids = {r.book_id for r in results}
        assert 46 in book_ids
        assert 48320 in book_ids

    def test_search_by_author(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("the", author="Doyle")
        assert all(r.book_id == 48320 for r in results)

    def test_search_by_kind(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("STAVE", kind="heading", book_id=46)
        assert len(results) == 5
        assert all(r.kind == "heading" for r in results)

    def test_search_fts5_phrase(self, db_path: str):
        with Database(db_path) as db:
            results = db.search('"Sherlock Holmes"')
        assert len(results) >= 1

    def test_chunks_filter_by_kind(self, db_path: str):
        with Database(db_path) as db:
            headings = db.chunks(46, kinds=["heading"])
            paragraphs = db.chunks(46, kinds=["text"])
        assert len(headings) == 5
        assert all(k == "heading" for _, _, _, _, _, _, k, _ in headings)
        assert len(paragraphs) > 600
        assert all(k == "text" for _, _, _, _, _, _, k, _ in paragraphs)

    def test_char_count_matches(self, db_path: str):
        with Database(db_path) as db:
            chunks = db.chunks(46)
        for _, _, _, _, _, content, _, char_count in chunks:
            assert char_count == len(content)


class TestCLICommands:
    """Exercise CLI subcommands against a real database."""

    @pytest.fixture(scope="class")
    def db_path(self, tmp_path_factory) -> str:
        path = str(tmp_path_factory.mktemp("cli") / "cli.db")
        book_ids = [46, 7370]  # Christmas Carol + Locke
        with Database(path) as db:
            for bid in book_ids:
                html = download_html(bid)
                chunks = chunk_html(html)
                db._store(_BOOKS[bid], chunks)
        return path

    def test_cli_books(self, db_path: str):
        result = _run_cli("books", db=db_path)
        assert result.returncode == 0
        assert "Christmas Carol" in result.stdout
        assert "Second Treatise" in result.stdout
        assert "2 book(s)" in result.stdout

    def test_cli_view_default(self, db_path: str):
        result = _run_cli("view", "46", db=db_path)
        assert result.returncode == 0
        assert "book_id=46" in result.stdout
        assert "Quick actions" in result.stdout
        assert "gutenbit toc 46" in result.stdout
        assert "gutenbit view 46 --all" in result.stdout
        assert "section=" in result.stdout

    def test_cli_toc_default(self, db_path: str):
        result = _run_cli("toc", "46", db=db_path)
        assert result.returncode == 0
        assert "Overview" in result.stdout
        assert "A Christmas Carol" in result.stdout
        assert "STAVE" in result.stdout
        assert "Section" in result.stdout
        assert "Position" in result.stdout
        assert (
            "5 sections · 710 paragraphs · 31,271 words · 156,357 chars · 2h 5m read"
            in result.stdout
        )
        assert "gutenbit view 46 --section 1 --forward 20" in result.stdout

    def test_cli_view_section_header(self, db_path: str):
        result = _run_cli("view", "46", "--section", "STAVE ONE", "--forward", "1", db=db_path)
        assert result.returncode == 0
        assert "author=Dickens, Charles" in result.stdout
        assert "section=STAVE ONE" in result.stdout

    def test_cli_view_section_limit(self, db_path: str):
        result = _run_cli("view", "46", "--section", "STAVE ONE", "--forward", "3", db=db_path)
        assert result.returncode == 0
        assert "Marley was dead" in result.stdout

    def test_cli_view_position(self, db_path: str):
        with Database(db_path) as db:
            row = db._conn.execute(
                "SELECT position FROM chunks "
                "WHERE book_id = ? AND kind = 'heading' "
                "ORDER BY position LIMIT 1",
                (46,),
            ).fetchone()
        assert row is not None
        position = row["position"]

        result = _run_cli("view", "46", "--position", str(position), db=db_path)
        assert result.returncode == 0
        assert f"position={position}" in result.stdout
        assert "section=STAVE ONE" in result.stdout

    def test_cli_search(self, db_path: str):
        result = _run_cli("search", "Scrooge", "--book", "46", db=db_path)
        assert result.returncode == 0
        assert "Scrooge" in result.stdout
        assert "total_results=305  shown_results=10" in result.stdout
        assert "305 results · 10 shown · ranked order" in result.stdout

    def test_cli_search_section(self, db_path: str):
        result = _run_cli("search", "Marley", "--section", "STAVE ONE", "--book", "46", db=db_path)
        assert result.returncode == 0
        assert "STAVE ONE" in result.stdout

    def test_cli_search_no_results(self, db_path: str):
        result = _run_cli("search", "xyzzyplugh", db=db_path)
        assert result.returncode == 0
        assert "No results" in result.stdout

    def test_cli_view_default_locke(self, db_path: str):
        result = _run_cli("view", "7370", db=db_path)
        assert result.returncode == 0
        assert "Quick actions" in result.stdout
        assert "gutenbit toc 7370" in result.stdout

    def test_cli_view_all(self, db_path: str):
        result = _run_cli("view", "46", "--all", db=db_path)
        assert result.returncode == 0
        assert "Marley was dead" in result.stdout
        assert "Scrooge" in result.stdout

    def test_cli_view_all_missing_book(self, db_path: str):
        result = _run_cli("view", "99999", "--all", db=db_path)
        assert result.returncode == 1
        assert "Book ID 99999 is not in the database." in result.stdout

    def test_cli_view_missing_book(self, db_path: str):
        result = _run_cli("view", "99999", db=db_path)
        assert result.returncode == 1
        assert "not in the database" in result.stdout


class TestCLIDeleteCommand:
    """Exercise CLI delete subcommand against a real database."""

    @pytest.fixture(scope="class")
    def db_path(self, tmp_path_factory) -> str:
        path = str(tmp_path_factory.mktemp("cli_delete") / "cli_delete.db")
        book_ids = [46, 7370]  # Christmas Carol + Locke
        with Database(path) as db:
            for bid in book_ids:
                html = download_html(bid)
                chunks = chunk_html(html)
                db._store(_BOOKS[bid], chunks)
        return path

    def test_cli_delete_success(self, db_path: str):
        result = _run_cli("delete", "46", db=db_path)
        assert result.returncode == 0
        assert "Deleted book ID 46" in result.stdout

        books = _run_cli("books", db=db_path)
        assert books.returncode == 0
        assert "Christmas Carol" not in books.stdout
        assert "Second Treatise" in books.stdout

        summary = _run_cli("view", "46", db=db_path)
        assert summary.returncode == 1
        assert "not in the database" in summary.stdout

        all_text = _run_cli("view", "46", "--all", db=db_path)
        assert all_text.returncode == 1
        assert "not in the database" in all_text.stdout

        search = _run_cli("search", "Scrooge", "--book", "46", db=db_path)
        assert search.returncode == 0
        assert "No results" in search.stdout

    def test_cli_delete_missing_book(self, db_path: str):
        result = _run_cli("delete", "99999", db=db_path)
        assert result.returncode == 1
        assert "No book found for book ID 99999." in result.stdout


# ===================================================================
# CROSS-BOOK SEARCH TESTS
# ===================================================================


class TestCrossBookSearch:
    """Search across multiple books to validate multi-book indexing."""

    @pytest.fixture(scope="class")
    def db_path(self, tmp_path_factory) -> str:
        path = str(tmp_path_factory.mktemp("cross") / "cross.db")
        book_ids = [46, 730, 967]  # Christmas Carol, Oliver Twist, Nicholas Nickleby
        with Database(path) as db:
            for bid in book_ids:
                html = download_html(bid)
                chunks = chunk_html(html)
                db._store(_BOOKS[bid], chunks)
        return path

    def test_search_hits_multiple_dickens(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("the", limit=50)
        book_ids = {r.book_id for r in results}
        # All three are Dickens — "the" should appear in all
        assert len(book_ids) >= 2

    def test_search_filter_narrows(self, db_path: str):
        with Database(db_path) as db:
            all_results = db.search("door", limit=100)
            filtered = db.search("door", book_id=46, limit=100)
        assert len(all_results) >= len(filtered)
        assert all(r.book_id == 46 for r in filtered)

    def test_bm25_ranking_meaningful(self, db_path: str):
        with Database(db_path) as db:
            results = db.search("Scrooge", limit=10)
        # Scrooge should only appear in Christmas Carol
        assert all(r.book_id == 46 for r in results)
        # Scores should be positive and descending
        scores = [r.score for r in results]
        assert all(s > 0 for s in scores)
        assert scores == sorted(scores, reverse=True)

    def test_skip_already_ingested(self, db_path: str):
        """Re-ingesting same books should be a no-op."""
        with Database(db_path) as db:
            before = len(db.chunks(46))
            db.ingest([_BOOKS[46]], delay=0)
            after = len(db.chunks(46))
        assert before == after
