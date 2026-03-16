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
        if heading.content == "Scene I. Rossillon. A room in the Countess’s palace."
    )
    assert alls_well_scene.div1 == "ALL’S WELL THAT ENDS WELL"
    assert alls_well_scene.div2 == "ACT I"
    assert alls_well_scene.div3 == "Scene I. Rossillon. A room in the Countess’s palace."

    sonnets = next(heading for heading in headings if heading.content == "THE SONNETS")
    assert sonnets.div1 == "THE SONNETS"
    assert sonnets.div2 == ""


def test_shakespeare_anthology_nests_induction_under_parent_play():
    headings = _headings(100)

    henry_iv_induction = next(
        heading
        for heading in headings
        if heading.content == "INDUCTION"
        and heading.div1 == "THE SECOND PART OF KING HENRY THE FOURTH"
    )
    assert henry_iv_induction.div2 == "INDUCTION"

    taming_induction = next(
        heading
        for heading in headings
        if heading.content == "INDUCTION" and heading.div1 == "THE TAMING OF THE SHREW"
    )
    assert taming_induction.div2 == "INDUCTION"


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
        "Actus Primus.",
        "Scoena Prima.",
        "Scena Secunda.",
        "Scena Tertia",
    ]
    assert "Actus Secundus." in heading_texts
    assert "FINIS" not in heading_texts

    first_scene = next(heading for heading in headings if heading.content == "Scoena Prima.")
    assert first_scene.div1 == "Actus Primus."
    assert first_scene.div2 == "Scoena Prima."


def test_macbeth_uses_paragraph_fallback_for_full_play_structure():
    headings = _headings(1129)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts[:8] == [
        "Actus Primus.",
        "Scoena Prima.",
        "Scena Secunda.",
        "Scena Tertia.",
        "Scena Quarta.",
        "Scena Quinta.",
        "Scena Sexta.",
        "Scena Septima.",
    ]
    assert len(headings) == 28
    assert "Actus Quintus." in heading_texts
    assert "FINIS" not in heading_texts

    act_two_scene_one = next(
        heading
        for heading in headings
        if heading.content == "Scena Prima." and heading.div1 == "Actus Secundus."
    )
    assert act_two_scene_one.div2 == "Scena Prima."


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


def test_theologico_political_treatise_keeps_chapter_sequences_across_parts():
    part_one = _headings(989)
    part_two = _headings(990)
    part_three = _headings(991)

    def chapter_markers(headings: list[Chunk]) -> list[str]:
        markers: list[str] = []
        for heading in headings:
            match = re.match(r"^CHAPTER\s+[IVXLCDM]+", heading.content)
            if match:
                markers.append(match.group(0))
        return markers

    assert part_one[0].content == "PREFACE."
    assert chapter_markers(part_one) == [
        "CHAPTER I",
        "CHAPTER II",
        "CHAPTER III",
        "CHAPTER IV",
        "CHAPTER V",
    ]
    assert part_one[-1].content == "AUTHOR'S ENDNOTES TO THE THEOLOGICO-POLITICAL TREATISE"

    assert chapter_markers(part_two) == [
        "CHAPTER VI",
        "CHAPTER VII",
        "CHAPTER VIII",
        "CHAPTER IX",
        "CHAPTER X",
    ]
    assert part_two[-1].content == "AUTHOR'S ENDNOTES TO THE THEOLOGICO-POLITICAL TREATISE"

    assert chapter_markers(part_three) == [
        "CHAPTER XI",
        "CHAPTER XII",
        "CHAPTER XIII",
        "CHAPTER XIV",
        "CHAPTER XV",
    ]
    assert part_three[-1].content == "AUTHOR'S ENDNOTES TO THE THEOLOGICO-POLITICAL TREATISE"


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


def test_canterbury_keeps_troilus_books_and_skips_garbage_headings():
    headings = _headings(2383)
    heading_texts = [heading.content for heading in headings]

    troilus_index = heading_texts.index("TROILUS AND CRESSIDA")
    assert heading_texts[troilus_index : troilus_index + 6] == [
        "TROILUS AND CRESSIDA",
        "THE FIRST BOOK.",
        "THE SECOND BOOK.",
        "THE THIRD BOOK.",
        "THE FOURTH BOOK",
        "THE FIFTH BOOK.",
    ]
    assert "act iv" not in heading_texts
    assert "scene v" not in heading_texts
    assert not any(text in {"C", "D", "I", "L", "M", "V", "X"} for text in heading_texts)


def test_inferno_skips_stray_front_matter_numeral_sections():
    headings = _headings(41537)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts[:7] == [
        "PREFACE.",
        "FLORENCE AND DANTE.",
        "GIOTTO’S PORTRAIT OF DANTE.",
        "CANTO I.",
        "CANTO II.",
        "CANTO III.",
        "CANTO IV.",
    ]
    assert not any(text in {"II", "III", "IV", "V", "VI"} for text in heading_texts[:10])


def test_leviathan_refines_toc_subsections_within_chapters():
    headings = _headings(3207)

    assert len(headings) > 500

    memory = next(heading for heading in headings if heading.content == "Memory")
    dreams = next(heading for heading in headings if heading.content == "Dreams")
    prudence = next(heading for heading in headings if heading.content == "Prudence")

    assert memory.div1 == "PART I. OF MAN"
    assert memory.div2 == "CHAPTER II. OF IMAGINATION"
    assert memory.div3 == "Memory"
    assert dreams.div2 == "CHAPTER II. OF IMAGINATION"
    assert dreams.div3 == "Dreams"
    assert prudence.div2 == "CHAPTER III. OF THE CONSEQUENCE OR TRAYNE OF IMAGINATIONS"
    assert prudence.div3 == "Prudence"


def test_leviathan_keeps_bellarmines_books_nested_within_chapter_xlii():
    headings = _headings(3207)

    first_book = next(heading for heading in headings if heading.content == "The First Book")
    fourth_book = next(heading for heading in headings if heading.content == "The Fourth Book")
    chapter_xliii = next(
        heading
        for heading in headings
        if heading.content
        == "CHAPTER XLIII. OF WHAT IS NECESSARY FOR A MANS RECEPTION INTO THE KINGDOME OF HEAVEN"
    )

    assert first_book.div1 == "PART III. OF A CHRISTIAN COMMON-WEALTH"
    assert first_book.div2 == "CHAPTER XLII. OF POWER ECCLESIASTICALL"
    assert first_book.div3 == "The First Book"
    assert fourth_book.div2 == "CHAPTER XLII. OF POWER ECCLESIASTICALL"
    assert fourth_book.div3 == "The Fourth Book"
    assert chapter_xliii.div1 == "PART III. OF A CHRISTIAN COMMON-WEALTH"
    assert (
        chapter_xliii.div2
        == "CHAPTER XLIII. OF WHAT IS NECESSARY FOR A MANS RECEPTION INTO THE KINGDOME OF HEAVEN"
    )
    assert chapter_xliii.div3 == ""


def test_brothers_karamazov_keeps_books_nested_within_parts():
    headings = _headings(28054)

    book_one = next(
        heading for heading in headings if heading.content == "Book I. The History Of A Family"
    )
    chapter_one = next(
        heading
        for heading in headings
        if heading.content == "Chapter I. Fyodor Pavlovitch Karamazov"
    )
    book_twelve = next(
        heading for heading in headings if heading.content == "Book XII. A Judicial Error"
    )
    epilogue_chapter = next(
        heading
        for heading in headings
        if heading.content == "Chapter III. Ilusha’s Funeral. The Speech At The Stone"
    )

    assert [heading.content for heading in headings if heading.content.startswith("PART ")] == [
        "PART I",
        "PART II",
        "PART III",
        "PART IV",
    ]
    assert book_one.div1 == "PART I"
    assert book_one.div2 == "Book I. The History Of A Family"
    assert chapter_one.div1 == "PART I"
    assert chapter_one.div2 == "Book I. The History Of A Family"
    assert chapter_one.div3 == "Chapter I. Fyodor Pavlovitch Karamazov"
    assert book_twelve.div1 == "PART IV"
    assert book_twelve.div2 == "Book XII. A Judicial Error"
    assert epilogue_chapter.div1 == "EPILOGUE"
    assert epilogue_chapter.div2 == "Chapter III. Ilusha’s Funeral. The Speech At The Stone"
    assert epilogue_chapter.div3 == ""


def test_decameron_does_not_nest_days_under_proem():
    headings = _headings(23700)

    proem = next(heading for heading in headings if heading.content == "Proem")
    day_one = next(heading for heading in headings if heading.content == "Day the First")
    first_story = next(heading for heading in headings if heading.content == "THE FIRST STORY")
    conclusion = next(
        heading for heading in headings if heading.content == "Conclusion of the Author"
    )

    assert proem.div1 == "Proem"
    assert proem.div2 == ""
    assert day_one.div1 == "Day the First"
    assert day_one.div2 == ""
    assert first_story.div1 == "Day the First"
    assert first_story.div2 == "THE FIRST STORY"
    assert first_story.div3 == ""
    assert conclusion.div1 == "Conclusion of the Author"
    assert conclusion.div2 == ""


def test_leaves_of_grass_keeps_poems_nested_within_books():
    headings = _headings(1322)
    paragraphs = _paragraphs(1322)

    book_one = next(heading for heading in headings if heading.content == "BOOK I. INSCRIPTIONS")
    one_self = next(heading for heading in headings if heading.content == "One’s-Self I Sing")
    book_two = next(heading for heading in headings if heading.content == "BOOK II")
    book_two_text = next(
        paragraph
        for paragraph in paragraphs
        if paragraph.div1 == "BOOK II"
        and "Starting from fish-shape Paumanok where I was born" in paragraph.content
    )

    assert book_one.div1 == "BOOK I. INSCRIPTIONS"
    assert book_one.div2 == ""
    assert one_self.div1 == "BOOK I. INSCRIPTIONS"
    assert one_self.div2 == "One’s-Self I Sing"
    assert book_two.div1 == "BOOK II"
    assert book_two.div2 == ""
    assert book_two_text.div1 == "BOOK II"
    assert book_two_text.div2 == ""


def test_souls_of_black_folk_keeps_numbered_chapters():
    headings = _headings(408)

    chapter_one = next(
        heading for heading in headings if heading.content == "I. Of Our Spiritual Strivings"
    )
    chapter_fourteen = next(
        heading for heading in headings if heading.content == "XIV. Of the Sorrow Songs"
    )
    afterthought = next(heading for heading in headings if heading.content == "The Afterthought")

    assert chapter_one.div1 == "I. Of Our Spiritual Strivings"
    assert chapter_one.div2 == ""
    assert chapter_fourteen.div1 == "XIV. Of the Sorrow Songs"
    assert chapter_fourteen.div2 == ""
    assert afterthought.div1 == "The Afterthought"
    assert afterthought.div2 == ""


def test_moby_dick_keeps_etymology_and_extracts_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(15)]

    assert heading_texts[:4] == ["ETYMOLOGY.", "EXTRACTS.", "CHAPTER I.", "CHAPTER II."]


def test_dracula_keeps_the_final_note_section():
    heading_texts = [heading.content for heading in _headings(345)]

    assert heading_texts[-2:] == ["CHAPTER XXVII MINA HARKER’S JOURNAL", "NOTE"]


def test_middlemarch_keeps_the_finale_section():
    heading_texts = [heading.content for heading in _headings(145)]

    assert heading_texts[-2:] == ["FINALE.", "THE END"]


def test_jane_eyre_keeps_preface_and_note_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(1260)]

    assert heading_texts[:4] == ["PREFACE", "NOTE TO THE THIRD EDITION", "CHAPTER I", "CHAPTER II"]


def test_les_miserables_keeps_preface_and_final_letter():
    heading_texts = [heading.content for heading in _headings(135)]

    assert heading_texts[:3] == ["LES MISÉRABLES", "PREFACE", "VOLUME I FANTINE"]
    assert heading_texts[-1] == "LETTER TO M. DAELLI"


def test_christmas_carol_keeps_preface_before_stave_one():
    heading_texts = [heading.content for heading in _headings(46)]

    assert heading_texts[:3] == ["PREFACE", "STAVE ONE.", "STAVE TWO."]


def test_tom_sawyer_keeps_preface_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(74)]

    assert heading_texts[:3] == ["PREFACE", "CHAPTER I", "CHAPTER II"]


def test_gulliver_keeps_both_prefatory_sections_before_part_one():
    heading_texts = [heading.content for heading in _headings(829)]

    assert heading_texts[:3] == [
        "THE PUBLISHER TO THE READER.",
        "A LETTER FROM CAPTAIN GULLIVER TO HIS COUSIN SYMPSON.",
        "PART I. A VOYAGE TO LILLIPUT.",
    ]


def test_don_quixote_keeps_preface_and_commendatory_verses():
    heading_texts = [heading.content for heading in _headings(996)]

    assert heading_texts[:8] == [
        "INTRODUCTION",
        "PREFARATORY",
        "CERVANTES",
        "‘DON QUIXOTE’",
        "THE AUTHOR’S PREFACE",
        "SOME COMMENDATORY VERSES",
        "URGANDA THE UNKNOWN",
        "AMADIS OF GAUL",
    ]


def test_bleak_house_keeps_preface_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(1023)]

    assert heading_texts[:3] == ["PREFACE", "CHAPTER I In Chancery", "CHAPTER II In Fashion"]


def test_vanity_fair_keeps_before_the_curtain_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(599)]

    assert heading_texts[:3] == [
        "BEFORE THE CURTAIN",
        "CHAPTER I Chiswick Mall",
        "CHAPTER II In Which Miss Sharp and Miss Sedley Prepare to Open the Campaign",
    ]


def test_black_beauty_keeps_part_headings_as_independent_sections():
    heading_texts = [heading.content for heading in _headings(271)]

    assert heading_texts[:4] == ["Black Beauty", "Part I", "01 My Early Home", "02 The Hunt"]
    assert [heading for heading in heading_texts if heading.startswith("Part ")] == [
        "Part I",
        "Part II",
        "Part III",
        "Part IV",
    ]
    assert heading_texts[heading_texts.index("Part II") + 1] == "22 Earlshall"


def test_descent_of_man_preserves_terminal_heading_punctuation():
    heading_texts = [heading.content for heading in _headings(2300)]

    assert "ORDER, DIPTERA (FLIES)." in heading_texts
    assert "ORDER, HEMIPTERA (FIELD-BUGS)." in heading_texts
    assert "ORDER, ORTHOPTERA (CRICKETS AND GRASSHOPPERS)." in heading_texts
    assert "ORDER, COLEOPTERA (BEETLES)." in heading_texts
    assert "ORDER, DIPTERA (FLIES" not in heading_texts
    assert "ORDER, COLEOPTERA (BEETLES" not in heading_texts


def test_koran_preserves_terminal_bracket_punctuation():
    heading_texts = [heading.content for heading in _headings(2800)]

    assert "SURA LXXIV.-THE ENWRAPPED1 [II.]" in heading_texts
    assert "SURA LXXIII. THE ENFOLDED1 [III.]" in heading_texts
    assert "SURA XCIII.1-THE BRIGHTNESS [IV.]" in heading_texts
    assert "SURA I.1 [VIII.]" in heading_texts
    assert "SURA LXXIV.-THE ENWRAPPED1 [II" not in heading_texts
    assert "SURA I.1 [VIII" not in heading_texts


def test_candide_keeps_front_matter_headings_and_skips_attribution_noise():
    heading_texts = [heading.content for heading in _headings(19942)]

    assert heading_texts[:5] == [
        "THE MODERN LIBRARY",
        "CANDIDE BY VOLTAIRE",
        "INTRODUCTION",
        "CANDIDE",
        "I",
    ]
    assert "INTRODUCTION BY PHILIP LITTELL" not in heading_texts
