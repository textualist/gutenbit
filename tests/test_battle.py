"""Live Project Gutenberg parser regression corpus.

Bare ``pytest`` excludes this file via the ``network`` marker. Run it
explicitly with:

    uv run pytest -m network

The corpus stays intentionally small. Each retained book covers a distinct
real-world parsing risk that is awkward to model faithfully with tiny fixtures.
"""

from __future__ import annotations

import re
from functools import cache

import pytest

from gutenbit.download import download_html
from gutenbit.html_chunker import Chunk, chunk_html

pytestmark = pytest.mark.network


@cache
def _chunks(book_id: int) -> tuple[Chunk, ...]:
    return tuple(chunk_html(download_html(book_id)))


def _headings(book_id: int) -> list[Chunk]:
    return [chunk for chunk in _chunks(book_id) if chunk.kind == "heading"]


def _paragraphs(book_id: int) -> list[Chunk]:
    return [chunk for chunk in _chunks(book_id) if chunk.kind == "text"]


def test_war_and_peace_keeps_books_and_epilogues():
    headings = _headings(2600)
    div1_values = sorted({heading.div1 for heading in headings if heading.div1})

    assert len(div1_values) == 17
    assert "BOOK ONE: 1805" in div1_values
    assert "BOOK FIFTEEN: 1812 - 13" in div1_values
    assert "FIRST EPILOGUE: 1813 - 20" in div1_values
    assert "SECOND EPILOGUE" in div1_values

    book_one_chapters = [
        heading
        for heading in headings
        if heading.div1 == "BOOK ONE: 1805" and heading.div2.startswith("CHAPTER")
    ]
    assert len(book_one_chapters) >= 20


def test_shakespeare_anthology_nests_acts_and_scenes_under_work_titles():
    headings = _headings(100)

    alls_well_act = next(
        heading
        for heading in headings
        if heading.content == "ACT I" and heading.div1 == "ALL’S WELL THAT ENDS WELL"
    )
    assert alls_well_act.div2 == "ACT I"

    alls_well_scene = next(
        heading
        for heading in headings
        if heading.content == "Scene I. Rossillon. A room in the Countess’s palace"
    )
    assert alls_well_scene.div1 == "ALL’S WELL THAT ENDS WELL"
    assert alls_well_scene.div2 == "ACT I"
    assert alls_well_scene.div3 == "Scene I. Rossillon. A room in the Countess’s palace"

    sonnets = next(heading for heading in headings if heading.content == "THE SONNETS")
    assert sonnets.div1 == "THE SONNETS"
    assert sonnets.div2 == ""


def test_locke_essay_volume_two_skips_contents_scaffolding():
    headings = _headings(10616)
    heading_texts = {heading.content for heading in headings}

    assert len(headings) == 33
    assert "BOOK III OF WORDS" in heading_texts
    assert "BOOK IV OF KNOWLEDGE AND PROBABILITY" in heading_texts
    assert "BOOK III. OF WORDS" not in heading_texts
    assert "BOOK IV. OF KNOWLEDGE AND PROBABILITY" not in heading_texts
    assert "CHAP" not in heading_texts
    assert "CHAPTER XIX. [not in early editions" not in heading_texts
    assert "I. WANT OF PROOFS" not in heading_texts


def test_blackstone_extracts_real_sections_from_a_noisy_toc():
    headings = _headings(30802)
    chapter_headings = [
        heading for heading in headings if heading.content.startswith("Chapter the")
    ]
    sections = [heading for heading in headings if heading.content.startswith("Section the")]

    assert 24 <= len(headings) <= 28
    assert len(chapter_headings) == 18
    assert len(sections) == 4
    assert all(any(char.isalpha() for char in heading.content) for heading in headings)


def test_metamorphosis_uses_heading_scan_and_skips_front_matter_attribution():
    headings = _headings(5200)
    heading_texts = [heading.content for heading in headings]
    lowered = [text.lower() for text in heading_texts]

    assert len(headings) == 4
    assert [text for text in heading_texts if text in ("I", "II", "III")] == ["I", "II", "III"]
    assert not any(text.startswith("by ") for text in lowered)
    assert not any("translated by" in text for text in lowered)


def test_odyssey_endnotes_do_not_leak_into_book_twenty_four():
    book_xxiv_paragraphs = [
        paragraph for paragraph in _paragraphs(1727) if "BOOK XXIV" in paragraph.div1
    ]

    assert len(book_xxiv_paragraphs) < 60
    assert not any(re.match(r"^\[\d+\]", paragraph.content) for paragraph in book_xxiv_paragraphs)


def test_hard_times_preserves_book_two_order_and_excludes_scaffolding():
    headings = _headings(786)
    book_two_chapters = [
        heading.content
        for heading in headings
        if heading.div1.startswith("BOOK THE SECOND") and heading.div2.startswith("CHAPTER")
    ]
    chapter_iv = next(content for content in book_two_chapters if content.startswith("CHAPTER IV"))
    chapter_v = next(
        content
        for content in book_two_chapters
        if content.startswith("CHAPTER V ") or content == "CHAPTER V"
    )
    heading_texts = [heading.content.upper() for heading in headings]

    assert book_two_chapters.index(chapter_iv) < book_two_chapters.index(chapter_v)
    assert not any("PROJECT GUTENBERG" in text for text in heading_texts)
    assert not any("HARD TIMES AND REPRINTED PIECES" in text for text in heading_texts)


def test_ulysses_preserves_bracketed_episode_labels():
    episode_labels = [
        heading.content for heading in _headings(4300) if heading.content.startswith("[")
    ]

    assert len(episode_labels) == 18
    assert episode_labels[:3] == ["[ 1 ]", "[ 2 ]", "[ 3 ]"]
    assert episode_labels[-1] == "[ 18 ]"


def test_hamlet_uses_paragraph_fallback_for_act_and_scene_structure():
    headings = _headings(1122)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts[:4] == [
        "Actus Primus",
        "Scoena Prima",
        "Scena Secunda",
        "Scena Tertia",
    ]
    assert "Actus Secundus" in heading_texts
    assert "FINIS" not in heading_texts

    first_scene = next(heading for heading in headings if heading.content == "Scoena Prima")
    assert first_scene.div1 == "Actus Primus"
    assert first_scene.div2 == "Scoena Prima"


def test_macbeth_uses_paragraph_fallback_for_full_play_structure():
    headings = _headings(1129)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts[:8] == [
        "Actus Primus",
        "Scoena Prima",
        "Scena Secunda",
        "Scena Tertia",
        "Scena Quarta",
        "Scena Quinta",
        "Scena Sexta",
        "Scena Septima",
    ]
    assert len(headings) == 28
    assert "Actus Quintus" in heading_texts
    assert "FINIS" not in heading_texts

    act_two_scene_one = next(
        heading
        for heading in headings
        if heading.content == "Scena Prima" and heading.div1 == "Actus Secundus"
    )
    assert act_two_scene_one.div2 == "Scena Prima"


def test_republic_preserves_book_headings_over_dialogue_speakers():
    headings = _headings(150)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts == [
        "BOOK I",
        "BOOK II",
        "BOOK III",
        "BOOK IV",
        "BOOK V",
        "BOOK VI",
        "BOOK VII",
        "BOOK VIII",
        "BOOK IX",
        "BOOK X",
    ]
    assert all("-" not in heading.content for heading in headings)
    assert all(heading.div2 == "" for heading in headings)


def test_faust_keeps_only_top_level_dramatic_sections():
    headings = _headings(3023)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts == [
        "PROLOGUE FOR THE THEATRE",
        "PROLOGUE IN HEAVEN",
        "THE TRAGEDY OF FAUST",
        "PART I",
    ]
    excluded = {"MANAGER", "POET", "MERRYMAN", "NIGHT", "FAUST"}
    assert all(text not in excluded for text in heading_texts)
