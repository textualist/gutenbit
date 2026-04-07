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

    # Sections must be div1 — the title is a peer, not a wrapper.
    assert all(h.div2 == "" for h in headings)


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

    assert heading_texts[-1] == "FINALE."
    # "THE END" is a terminal marker, not navigable content — suppressed.
    assert "THE END" not in heading_texts


def test_jane_eyre_keeps_preface_and_note_before_chapter_one():
    heading_texts = [heading.content for heading in _headings(1260)]

    assert heading_texts[:4] == ["PREFACE", "NOTE TO THE THIRD EDITION", "CHAPTER I", "CHAPTER II"]


def test_les_miserables_keeps_preface_and_final_letter():
    headings = _headings(135)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts[:3] == ["LES MISÉRABLES", "PREFACE", "VOLUME I FANTINE"]
    assert heading_texts[-1] == "LETTER TO M. DAELLI"

    # Three-level nesting: Volume > Book > Chapter.
    ch1 = next(h for h in headings if h.content == "CHAPTER I—M. MYRIEL")
    assert ch1.div1 == "VOLUME I FANTINE"
    assert ch1.div2 == "BOOK FIRST—A JUST MAN"
    assert ch1.div3 == "CHAPTER I—M. MYRIEL"


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


def test_tale_of_two_cities_nests_chapters_under_books_with_subtitles():
    headings = _headings(98)

    book_headings = [h for h in headings if not h.div2]
    assert [h.content for h in book_headings] == [
        "Book the First\u2014Recalled to Life",
        "Book the Second\u2014the Golden Thread",
        "Book the Third\u2014the Track of a Storm",
    ]

    book1_chapters = [h for h in headings if "First" in h.div1 and h.div2]
    book2_chapters = [h for h in headings if "Second" in h.div1 and h.div2]
    book3_chapters = [h for h in headings if "Third" in h.div1 and h.div2]
    assert len(book1_chapters) == 6
    assert len(book2_chapters) == 24
    assert len(book3_chapters) == 15

    first_chapter = book1_chapters[0]
    assert first_chapter.div1 == "Book the First\u2014Recalled to Life"
    assert first_chapter.div2 == "CHAPTER I. The Period"
    assert first_chapter.div3 == ""

    last_chapter = book3_chapters[-1]
    assert last_chapter.div1 == "Book the Third\u2014the Track of a Storm"
    assert last_chapter.div2 == "CHAPTER XV. The Footsteps Die Out For Ever"


def test_crime_and_punishment_keeps_epilogue_as_top_level_peer_of_parts():
    headings = _headings(2554)

    part_headings = [heading for heading in headings if heading.content.startswith("PART ")]
    epilogue = next(heading for heading in headings if heading.content == "EPILOGUE")
    epilogue_chapters = [
        heading
        for heading in headings
        if heading.div1 == "EPILOGUE" and heading.content in ("I", "II")
    ]

    assert [heading.content for heading in part_headings] == [
        "PART I",
        "PART II",
        "PART III",
        "PART IV",
        "PART V",
        "PART VI",
    ]
    assert epilogue.div1 == "EPILOGUE"
    assert epilogue.div2 == ""
    assert len(epilogue_chapters) == 2
    assert epilogue_chapters[0].div2 == "I"
    assert epilogue_chapters[1].div2 == "II"


def test_huck_finn_keeps_notice_and_explanatory_text_before_chapter_one():
    chunks = _chunks(76)
    headings = _headings(76)
    heading_texts = [h.content for h in headings]

    # Chapter structure is preserved.
    assert heading_texts[:3] == ["CHAPTER I.", "CHAPTER II.", "CHAPTER III."]
    assert heading_texts[-1] == "CHAPTER THE LAST"
    assert len(headings) == 43

    # NOTICE and EXPLANATORY front matter is not dropped — their prose
    # appears before the first chapter section.
    pre_chapter_text = [
        c.content for c in chunks if c.kind == "text" and c.position < headings[0].position
    ]
    assert any("prosecuted" in t for t in pre_chapter_text)
    assert any("dialects" in t for t in pre_chapter_text)


def test_emma_preserves_volumes_with_chapters_nested_under_them():
    headings = _headings(158)
    heading_texts = [heading.content for heading in headings]

    assert len(headings) == 58
    assert heading_texts[:3] == ["VOLUME I", "CHAPTER I", "CHAPTER II"]
    assert [text for text in heading_texts if text.startswith("VOLUME")] == [
        "VOLUME I",
        "VOLUME II",
        "VOLUME III",
    ]

    vol_i = next(heading for heading in headings if heading.content == "VOLUME I")
    assert vol_i.div1 == "VOLUME I"
    assert vol_i.div2 == ""

    ch_i_under_vol_ii = next(
        heading
        for heading in headings
        if heading.content == "CHAPTER I" and heading.div1 == "VOLUME II"
    )
    assert ch_i_under_vol_ii.div2 == "CHAPTER I"

    vol_iii_chapters = [
        heading
        for heading in headings
        if heading.div1 == "VOLUME III" and heading.div2.startswith("CHAPTER")
    ]
    assert len(vol_iii_chapters) == 19
    assert vol_iii_chapters[-1].content == "CHAPTER XIX"


def test_scarlet_letter_keeps_custom_house_essay_before_narrative_chapters():
    heading_texts = [heading.content for heading in _headings(33)]

    assert heading_texts[:4] == [
        "THE CUSTOM-HOUSE",
        "THE SCARLET LETTER",
        "I. THE PRISON DOOR",
        "II. THE MARKET-PLACE",
    ]
    assert heading_texts[-1] == "XXIV. CONCLUSION"
    assert len(heading_texts) == 26


def test_treasure_island_nests_chapters_under_six_parts():
    headings = _headings(120)

    part_headings = [heading for heading in headings if heading.content.startswith("PART ")]
    assert [heading.content for heading in part_headings] == [
        "PART ONE\u2014The Old Buccaneer",
        "PART TWO\u2014The Sea-cook",
        "PART THREE\u2014My Shore Adventure",
        "PART FOUR\u2014The Stockade",
        "PART FIVE\u2014My Sea Adventure",
        "PART SIX\u2014Captain Silver",
    ]
    assert all(heading.div2 == "" for heading in part_headings)

    chapter_one = next(
        heading
        for heading in headings
        if heading.content == "I The Old Sea-dog at the \u201cAdmiral Benbow\u201d"
    )
    assert chapter_one.div1 == "PART ONE\u2014The Old Buccaneer"
    assert chapter_one.div2 == "I The Old Sea-dog at the \u201cAdmiral Benbow\u201d"

    last_chapter = next(heading for heading in headings if heading.content == "XXXIV And Last")
    assert last_chapter.div1 == "PART SIX\u2014Captain Silver"
    assert last_chapter.div2 == "XXXIV And Last"

    assert headings[0].content == "TREASURE ISLAND"
    assert len(headings) == 41


def test_dubliners_keeps_all_fifteen_story_titles_flat():
    headings = _headings(2814)
    heading_texts = [heading.content for heading in headings]

    assert heading_texts == [
        "THE SISTERS",
        "AN ENCOUNTER",
        "ARABY",
        "EVELINE",
        "AFTER THE RACE",
        "TWO GALLANTS",
        "THE BOARDING HOUSE",
        "A LITTLE CLOUD",
        "COUNTERPARTS",
        "CLAY",
        "A PAINFUL CASE",
        "IVY DAY IN THE COMMITTEE ROOM",
        "A MOTHER",
        "GRACE",
        "THE DEAD",
    ]
    assert all(heading.div2 == "" for heading in headings)


def test_zarathustra_preserves_all_eighty_discourses_and_four_parts():
    headings = _headings(1998)
    heading_texts = [heading.content for heading in headings]

    # Introduction is separate from the main text.
    assert heading_texts[0] == "INTRODUCTION BY MRS FORSTER-NIETZSCHE."

    # All four parts present at div1 level.
    first = next(h for h in headings if h.content == "FIRST PART. ZARATHUSTRA\u2019S DISCOURSES.")
    second = next(h for h in headings if h.content == "THUS SPAKE ZARATHUSTRA. SECOND PART.")
    third = next(h for h in headings if h.content == "THIRD PART.")
    fourth = next(h for h in headings if h.content == "FOURTH AND LAST PART.")
    for part in (first, second, third, fourth):
        assert part.div2 == "", f"{part.content} should be div1, not nested"

    # All 80 discourses present (I through LXXX).
    discourse_headings = [
        h for h in headings if re.match(r"^[IVXLCDM]+\.\s+\S", h.content) and h.div2
    ]
    assert len(discourse_headings) == 80
    assert discourse_headings[0].content == "I. THE THREE METAMORPHOSES."
    assert discourse_headings[-1].content == "LXXX. THE SIGN."

    # Appendix is a single flat section with no subsections at div1 level.
    appendix = next(h for h in headings if h.content == "APPENDIX.")
    assert appendix.div1 == "APPENDIX."
    assert appendix.div2 == ""
    assert heading_texts.index("APPENDIX.") == len(heading_texts) - 1


# ---------------------------------------------------------------------------
# KEI-123 batch: 25 new works
# ---------------------------------------------------------------------------


def test_pride_and_prejudice_keeps_preface_and_sixty_one_chapters():
    headings = _headings(1342)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 62
    assert heading_texts[0] == "PREFACE."
    assert heading_texts[1] == "Chapter I."
    assert heading_texts[-1] == "CHAPTER LXI."
    assert all(h.div2 == "" for h in headings)


def test_anna_karenina_nests_chapters_under_eight_parts():
    headings = _headings(1399)

    parts = [h for h in headings if h.content.startswith("PART ")]
    assert [h.content for h in parts] == [
        "PART ONE",
        "PART TWO",
        "PART THREE",
        "PART FOUR",
        "PART FIVE",
        "PART SIX",
        "PART SEVEN",
        "PART EIGHT",
    ]

    ch1_part1 = next(h for h in headings if h.content == "Chapter 1" and h.div1 == "PART ONE")
    assert ch1_part1.div2 == "Chapter 1"

    ch1_part8 = next(h for h in headings if h.content == "Chapter 1" and h.div1 == "PART EIGHT")
    assert ch1_part8.div2 == "Chapter 1"


def test_resurrection_nests_chapters_under_three_books():
    headings = _headings(1938)
    div1_values = sorted({h.div1 for h in headings if h.div1})

    assert "BOOK I." in div1_values
    assert "BOOK II." in div1_values
    assert "BOOK III." in div1_values

    book1_chapters = [
        h for h in headings if h.div1 == "BOOK I." and h.div2.startswith("CHAPTER")
    ]
    assert len(book1_chapters) == 59

    book2_chapters = [
        h for h in headings if h.div1 == "BOOK II." and h.div2.startswith("CHAPTER")
    ]
    assert len(book2_chapters) == 42

    book3_chapters = [
        h for h in headings if h.div1 == "BOOK III." and h.div2.startswith("CHAPTER")
    ]
    assert len(book3_chapters) == 28


def test_frankenstein_keeps_letters_and_chapters_flat():
    headings = _headings(84)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 28
    assert heading_texts[:5] == ["Letter 1", "Letter 2", "Letter 3", "Letter 4", "Chapter 1"]
    assert heading_texts[-1] == "Chapter 24"
    assert all(h.div2 == "" for h in headings)


def test_wuthering_heights_keeps_thirty_four_flat_chapters():
    headings = _headings(768)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 34
    assert heading_texts[0] == "CHAPTER I"
    assert heading_texts[-1] == "CHAPTER XXXIV"
    assert all(h.div2 == "" for h in headings)


def test_the_devil_keeps_variation_of_conclusion():
    headings = _headings(67224)

    heading_texts = [h.content for h in headings]
    assert heading_texts[0] == "PREFACE"
    assert heading_texts[1] == "I"
    assert heading_texts[-2] == "XXI"
    assert heading_texts[-1] == "VARIATION OF THE CONCLUSION OF THE DEVIL"
    assert len(headings) == 23


def test_poor_folk_keeps_duplicate_date_letters():
    """PG 2302 — epistolary novel: two letters on the same date are distinct."""
    headings = _headings(2302)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 52
    assert heading_texts.count("July 28th.") == 2
    assert heading_texts.count("September 27th.") == 2


def test_dorian_gray_keeps_preface_before_twenty_chapters():
    heading_texts = [h.content for h in _headings(174)]

    assert len(heading_texts) == 21
    assert heading_texts[0] == "THE PREFACE"
    assert heading_texts[1] == "CHAPTER I."
    assert heading_texts[-1] == "CHAPTER XX."


def test_sherlock_holmes_keeps_twelve_stories_with_subsections():
    headings = _headings(1661)

    assert len(headings) == 14
    # First story has 3 subsections (I, II, III).
    assert headings[0].content == "I. A SCANDAL IN BOHEMIA"
    assert headings[1].content == "II."
    assert headings[1].div1 == "I. A SCANDAL IN BOHEMIA"
    assert headings[1].div2 == "II."

    # Remaining 11 stories are flat.
    assert headings[3].content == "II. THE RED-HEADED LEAGUE"
    assert headings[-1].content == "XII. THE ADVENTURE OF THE COPPER BEECHES"


def test_great_expectations_keeps_fifty_nine_flat_chapters():
    heading_texts = [h.content for h in _headings(1400)]

    assert len(heading_texts) == 59
    assert heading_texts[0] == "Chapter I."
    assert heading_texts[-1] == "Chapter LIX."


def test_monte_cristo_nests_chapters_under_five_volumes():
    headings = _headings(1184)

    volumes = [h for h in headings if h.content.startswith("VOLUME ")]
    assert len(volumes) == 5

    chapters = [h for h in headings if h.content.startswith("Chapter ")]
    assert len(chapters) == 117

    assert chapters[0].div1 == "VOLUME ONE"
    assert chapters[0].div2 == "Chapter 1. Marseilles\u2014The Arrival"
    assert chapters[-1].div1 == "VOLUME FIVE"
    assert chapters[-1].div2 == "Chapter 117. The Fifth of October"


def test_sense_and_sensibility_keeps_fifty_flat_chapters():
    heading_texts = [h.content for h in _headings(161)]

    assert len(heading_texts) == 50
    assert heading_texts[0] == "CHAPTER I."
    assert heading_texts[-1] == "CHAPTER L."


def test_oliver_twist_keeps_fifty_three_chapters_with_long_titles():
    headings = _headings(730)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 53
    assert "TREATS OF THE PLACE WHERE OLIVER TWIST WAS BORN" in heading_texts[0]
    assert heading_texts[-1] == "CHAPTER LIII. AND LAST"
    assert all(h.div2 == "" for h in headings)


def test_tess_nests_chapters_under_seven_phases():
    headings = _headings(110)

    phases = [h for h in headings if "Phase" in h.content]
    assert len(phases) == 7
    assert phases[0].content == "Phase the First: The Maiden"
    assert phases[-1].content == "Phase the Seventh: Fulfilment"

    ch1 = next(h for h in headings if h.content == "I" and h.div2 == "I")
    assert ch1.div1 == "Phase the First: The Maiden"

    assert len(headings) == 66


def test_alice_in_wonderland_keeps_twelve_chapters():
    heading_texts = [h.content for h in _headings(11)]

    assert len(heading_texts) == 12
    assert heading_texts[0] == "CHAPTER I. Down the Rabbit-Hole"
    assert heading_texts[-1] == "CHAPTER XII. Alice\u2019s Evidence"


def test_little_women_nests_chapters_under_two_parts():
    headings = _headings(514)

    parts = [h for h in headings if h.content.startswith("PART ")]
    assert [h.content for h in parts] == ["PART 1", "PART 2"]

    ch1 = next(h for h in headings if "PLAYING PILGRIMS" in h.content)
    assert ch1.div1 == "PART 1"
    assert ch1.div2 == "CHAPTER ONE PLAYING PILGRIMS"

    assert len(headings) == 49


def test_war_of_the_worlds_nests_chapters_under_two_books():
    headings = _headings(36)

    books = [h for h in headings if h.content.startswith("BOOK ")]
    assert len(books) == 2

    book1_chapters = [h for h in headings if h.div1 == books[0].content and h.div2]
    book2_chapters = [h for h in headings if h.div1 == books[1].content and h.div2]
    assert len(book1_chapters) == 17
    assert len(book2_chapters) == 10


def test_heart_of_darkness_keeps_three_parts():
    headings = _headings(219)
    heading_texts = [h.content for h in headings]

    assert heading_texts == ["I", "II", "III"]
    assert all(h.div2 == "" for h in headings)


def test_jekyll_and_hyde_keeps_ten_named_chapters():
    headings = _headings(42)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 10
    assert heading_texts[0] == "STORY OF THE DOOR"
    assert heading_texts[-1] == "HENRY JEKYLL\u2019S FULL STATEMENT OF THE CASE"
    assert all(h.div2 == "" for h in headings)


def test_great_gatsby_keeps_title_and_nine_chapters():
    headings = _headings(64317)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 10
    assert heading_texts[0] == "The Great Gatsby by F. Scott Fitzgerald"
    assert heading_texts[1:] == ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]

    # Chapters must be div1 — the title is a peer, not a wrapper.
    assert all(h.div2 == "" for h in headings)


def test_the_prince_nests_chapters_under_collection_works():
    headings = _headings(1232)

    # PG 1232 is a collection of three works — chapters nest under each
    # work title as div2, which is correct anthology behaviour.
    the_prince = next(h for h in headings if h.content == "THE PRINCE")
    assert the_prince.div1 == "THE PRINCE"
    assert the_prince.div2 == ""

    ch1 = next(h for h in headings if h.content.startswith("CHAPTER I."))
    assert ch1.div1 == "THE PRINCE"

    chapters = [h for h in headings if h.content.startswith("CHAPTER ") and h.div1 == "THE PRINCE"]
    assert len(chapters) == 26

    assert len(headings) == 36


def test_meditations_keeps_appendix_at_div1_and_excludes_content_subtitle():
    headings = _headings(2680)
    heading_texts = [h.content for h in headings]

    assert heading_texts[1] == "HIS FIRST BOOK"
    assert heading_texts[-2] == "THE TWELFTH BOOK"

    # "concerning HIMSELF:" is a descriptive subtitle, not a structural heading.
    assert "concerning HIMSELF:" not in heading_texts

    # APPENDIX must be a top-level peer of the books, not nested under
    # THE TWELFTH BOOK.
    appendix = next(h for h in headings if h.content == "APPENDIX")
    assert appendix.div1 == "APPENDIX"
    assert appendix.div2 == ""

    assert len(headings) == 14


def test_beyond_good_and_evil_keeps_preface_and_nine_chapters():
    heading_texts = [h.content for h in _headings(4363)]

    assert len(heading_texts) == 11
    assert heading_texts[0] == "PREFACE"
    assert heading_texts[-1] == "FROM THE HEIGHTS"


def test_grimms_fairy_tales_flattens_stories_to_div1():
    headings = _headings(2591)

    assert headings[0].content == "THE BROTHERS GRIMM FAIRY TALES"

    stories = [h for h in headings if h.content != "THE BROTHERS GRIMM FAIRY TALES"]
    assert len(stories) == 62
    assert stories[0].content == "THE GOLDEN BIRD"
    assert stories[-1].content == "SNOW-WHITE AND ROSE-RED"
    assert all(h.div2 == "" for h in stories)


def test_johnsons_journey_flattens_place_name_chapters_to_div1():
    headings = _headings(2064)

    assert headings[0].content == "A JOURNEY TO THE WESTERN ISLANDS OF SCOTLAND"
    places = [h for h in headings if h.content != headings[0].content]
    assert len(places) == 30
    assert places[0].content == "INCH KEITH"
    assert places[-1].content == "INCH KENNETH"
    assert all(h.div2 == "" for h in places)


def test_confessions_of_augustine_keeps_thirteen_books():
    heading_texts = [h.content for h in _headings(3296)]

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
        "BOOK XI",
        "BOOK XII",
        "BOOK XIII",
    ]


def test_robinson_crusoe_keeps_twenty_chapters():
    heading_texts = [h.content for h in _headings(521)]

    assert len(heading_texts) == 20
    assert heading_texts[0] == "CHAPTER I. START IN LIFE"
    assert heading_texts[-1] == "CHAPTER XX. FIGHT BETWEEN FRIDAY AND A BEAR"


def test_dolls_house_keeps_three_acts():
    heading_texts = [h.content for h in _headings(2542)]

    assert heading_texts == ["ACT I", "ACT II", "ACT III"]


def test_beowulf_merges_canto_pairs_and_captures_verse_content():
    headings = _headings(16328)
    heading_texts = [h.content for h in headings]
    paragraphs = _paragraphs(16328)

    # Frontmatter preserved.
    assert heading_texts[:3] == ["PREFACE.", "THE STORY.", "ABBREVIATIONS USED IN THE NOTES."]

    # Roman numerals merge with descriptive titles (43 cantos).
    assert heading_texts[7] == "I. THE LIFE AND DEATH OF SCYLD"
    assert heading_texts[-2] == "XLIII. THE BURNING OF BEOWULF"
    assert heading_texts[-1] == "ADDENDA."
    assert len(headings) == 51

    # Verse content (in <div class="l"> tags) is captured.
    canto_i_text = [p for p in paragraphs if "I. THE LIFE AND DEATH OF SCYLD" in p.div1]
    assert len(canto_i_text) > 50
    assert any("Spear-Dane" in p.content for p in canto_i_text)


def test_leviathan_refines_ch_xlvii_subsections_despite_same_rank():
    """PG 3207 — Ch XLVII is h3 (not h2) but its subsections should still refine."""
    headings = _headings(3207)

    ch47_subs = [h for h in headings if "XLVII" in h.div2 and h.div3]
    assert len(ch47_subs) >= 15

    first_sub = ch47_subs[0]
    assert first_sub.div1 == "PART IV. OF THE KINDOME OF DARKNESSE"
    assert "XLVII" in first_sub.div2
    assert first_sub.div3 != ""


def test_leviathan_review_and_conclusion_at_div1():
    """PG 3207 — 'A REVIEW, AND CONCLUSION' is a top-level structural closure."""
    headings = _headings(3207)

    review = next(h for h in headings if "REVIEW" in h.content)
    assert review.div1 == "A REVIEW, AND CONCLUSION"
    assert review.div2 == ""


def test_kjv_psalms_excludes_verse_reference_headings():
    """PG 30 — Bible verse references (19:070:001...) are not structural headings."""
    headings = _headings(30)
    heading_texts = [h.content for h in headings]

    assert "Book 19 Psalms" in heading_texts
    # No verse references should leak as sections.
    assert not any("19:070:001" in t or "19:092:001" in t for t in heading_texts)
    # Psalms should be a single section (no subsections).
    psalms_subs = [h for h in headings if h.div2 and "Psalm" in h.div1]
    assert len(psalms_subs) == 0


def test_divine_comedy_inferno_captures_all_cantos():
    """PG 1995 — Sparse TOC (2 links) yields to heading scan for all 34 cantos."""
    headings = _headings(1995)
    heading_texts = [h.content for h in headings]

    assert "INTRODUCTION." in heading_texts
    assert "CANTO I." in heading_texts
    assert "CANTO XXXIV." in heading_texts

    cantos = [h for h in heading_texts if h.startswith("CANTO ")]
    assert len(cantos) == 34


def test_chinese_classics_extracts_chapters_from_paragraph_text():
    """PG 3100 — No <h1>-<h6> tags; chapter structure recovered from <p> text."""
    headings = _headings(3100)
    paragraphs = _paragraphs(3100)
    heading_texts = [h.content for h in headings]

    # Chapters recovered from paragraph text containing structural keywords.
    assert any("CHAPTER I" in t for t in heading_texts)
    assert any("CHAPTER V" in t for t in heading_texts)
    chapters = [h for h in heading_texts if "CHAPTER" in h]
    assert len(chapters) == 6

    # Sections nested under chapters.
    sections = [h for h in heading_texts if h.startswith("SECTION")]
    assert len(sections) >= 10

    # Content is accessible.
    assert len(paragraphs) > 600
    assert any("Confucius" in p.content for p in paragraphs)


def test_medical_essays_excludes_publication_metadata():
    """PG 2700 — 'Printed in 1843; reprinted...' is metadata, not structure."""
    headings = _headings(2700)
    heading_texts = [h.content for h in headings]

    assert "THE CONTAGIOUSNESS OF PUERPERAL FEVER" in heading_texts
    assert not any("Printed" in t for t in heading_texts)
    assert all(h.div2 == "" for h in headings)


def test_dolly_dialogues_excludes_dialogue_subheadings():
    """PG 1203 — h3 dialogue lines should not leak into the TOC as subsections."""
    headings = _headings(1203)
    heading_texts = [h.content for h in headings]

    # All sections are flat chapter titles — no subsections.
    assert len(headings) == 20
    assert all(h.div2 == "" for h in headings)

    # Verify the previously leaking dialogue headings are excluded.
    assert not any('"' in t for t in heading_texts)
    assert "A REMINISCENCE" in heading_texts
    assert "A QUICK CHANGE" in heading_texts


def test_peter_rabbit_collapses_title_block_into_single_section():
    """PG 14304 — title split across multiple h2 tags with no chapter structure."""
    headings = _headings(14304)
    paragraphs = _paragraphs(14304)

    # Single-section work: title fragments collapse to one section.
    assert len(headings) == 1
    assert headings[0].div1 == "Peter Rabbit"

    # All content captured under the single section.
    assert len(paragraphs) > 40
    assert any("four little rabbits" in p.content for p in paragraphs)


# ---------------------------------------------------------------------------
# Thackeray & George Eliot corpus
# ---------------------------------------------------------------------------


def test_thackeray_biography_chapter_i_not_dropped():
    """PG 18645 — Thackeray by Trollope must include CHAPTER I.

    The TOC uses <ul class="toc"> with <li> entries.  The first <li>
    has a ``<span class="tocright">PAGE</span>`` residue that caused
    _is_toc_context_link to reject the CHAPTER I link.
    """
    headings = _headings(18645)
    heading_texts = [h.content for h in headings]

    assert any("CHAPTER I" in t for t in heading_texts), (
        f"CHAPTER I missing from headings: {heading_texts[:5]}"
    )
    # All nine chapters present.
    for n in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"):
        assert any(f"CHAPTER {n}." in t or f"CHAPTER {n} " in t for t in heading_texts), (
            f"CHAPTER {n} missing"
        )


def test_eye_for_an_eye_volume_i_nests_chapters():
    """PG 16804 — An Eye for an Eye must include Volume I as a container.

    The TOC links only reference chapters (not volumes).  Volume I appears
    before the first TOC section and uses h2 while chapters are h3.  The
    pre-TOC refinement scan must admit broad container headings when they
    are strictly more prominent than the TOC entries.
    """
    headings = _headings(16804)

    # Volume I should be present
    vol1 = [h for h in headings if "Volume I" in h.content]
    assert vol1, "Volume I. missing"

    # Volume I chapters should nest (div1 = Volume I.)
    vol1_chapters = [h for h in headings if h.div1 == "Volume I." and "Chapter" in h.div2]
    assert len(vol1_chapters) >= 12, (
        f"Expected >=12 chapters under Volume I., got {len(vol1_chapters)}"
    )

    # Volume II should also be present with nested chapters
    vol2_chapters = [h for h in headings if h.div1 == "Volume II." and "Chapter" in h.div2]
    assert len(vol2_chapters) >= 10, (
        f"Expected >=10 chapters under Volume II., got {len(vol2_chapters)}"
    )


def test_henry_esmond_collected_preserves_all_three_works():
    """PG 29363 — Collected edition must not truncate after Appendix.

    The Appendix belongs to Henry Esmond, but two more works follow:
    The English Humourists and The Four Georges.  The apparatus-heading
    truncation must skip when more prominent headings come after it.
    Heading rank nesting (h1→h2→h3) must be respected so that lectures
    nest under The English Humourists, and Georges nest under The Georges.
    """
    headings = _headings(29363)
    div1_values = sorted({h.div1 for h in headings if h.div1})

    # Henry Esmond: Books I-III with chapters, plus Appendix.
    # Books nest under "The History Of Henry Esmond" as div2.
    henry_books = [h for h in headings if "Book I" in h.div2 or "Book II" in h.div2 or "Book III" in h.div2]
    assert len({h.div2 for h in henry_books}) == 3

    # Chapters nest as div3 under their Books.
    book1_chapters = [h for h in headings if "Book I." in h.div2 and "Chapter" in h.div3]
    assert len(book1_chapters) >= 14

    # Appendix is kept (under Henry Esmond).
    assert any("Appendix" in h.content for h in headings)

    # The English Humourists — a separate div1 work.
    assert "The English Humourists Of The Eighteenth Century" in div1_values
    # Lectures nest under Humourists as div2.
    humourist_lectures = [
        h for h in headings
        if "Lecture The" in h.content
        and "Humourists" in h.div1
    ]
    assert len(humourist_lectures) == 6

    # The Four Georges — a separate div1 work.
    assert "The Georges" in div1_values
    # George sections nest under The Georges.
    george_sections = [
        h for h in headings
        if "George The" in h.content
        and "Georges" in h.div1
    ]
    assert len(george_sections) >= 4


# ---------------------------------------------------------------------------
# Hawthorne / Poe battle-test issue families
# ---------------------------------------------------------------------------


def test_byline_headings_excluded_from_heading_scan():
    """Standalone "BY" headings and the author name that follows them must not
    appear as structural headings (Family 1: title-block author-name leakage).

    PG 932 (Fall of the House of Usher) and PG 1063 (Cask of Amontillado) both
    have h3 "BY" + h2 author-name heading blocks that the fallback heading scan
    should suppress.
    """
    for pg_id in (932, 1063):
        headings = _headings(pg_id)
        heading_texts_upper = [h.content.strip().upper() for h in headings]
        assert "BY" not in heading_texts_upper, f"PG {pg_id}: standalone BY leaked"
        assert "BY." not in heading_texts_upper, f"PG {pg_id}: standalone BY. leaked"
        # No author-only headings (all-caps name without keyword).
        assert "EDGAR ALLAN POE" not in heading_texts_upper, (
            f"PG {pg_id}: author name leaked as heading"
        )


def test_bare_heading_pairs_not_merged_across_toc_entries():
    """Bare chapter numbers must not merge with the next story title when they
    are separate TOC entries at the same level (Family 2: h2/h3 split-title
    cross-merge).

    PG 2149 has "CHAPTER 25" followed by "LIGEIA" — separate works, not a
    chapter + subtitle pair.
    """
    headings = _headings(2149)
    heading_texts = [h.content for h in headings]
    assert "CHAPTER 25" in heading_texts
    assert "LIGEIA" in heading_texts
    # They must NOT be merged into a single heading.
    merged = [h for h in heading_texts if "CHAPTER 25" in h and "LIGEIA" in h]
    assert merged == []


def test_volume_heading_not_merged_with_child_sections():
    """VOLUME headings must not merge with peer-rank child sections
    (Family 3: volume title merging).

    PG 7877 has "VOLUME II" followed by multiple same-rank sections
    (LONDON…, REFORM-CLUB…). The volume heading must remain standalone.
    """
    headings = _headings(7877)
    vol2 = [h for h in headings if h.content.startswith("VOLUME II")]
    assert len(vol2) == 1
    assert vol2[0].content == "VOLUME II"
    # Child sections must exist as separate headings.
    london = [h for h in headings if "LONDON" in h.content]
    assert len(london) >= 1


def test_repeated_series_headings_preserved_as_separate_sections():
    """Three or more consecutive same-text headings that each introduce
    different content must all be kept (Family 4: identical heading text
    merging).

    PG 508 (Twice-Told Tales) has four "LEGENDS OF THE PROVINCE HOUSE"
    headings, each introducing a different story in the series.
    """
    headings = _headings(508)
    legends = [h for h in headings if "LEGENDS OF THE PROVINCE HOUSE" in h.content]
    assert len(legends) == 4


def test_anchorless_act_headings_refined_into_play_structure():
    """ACT headings without anchor IDs must be recovered via TOC refinement
    and nest scenes correctly under each act.

    PG 100 (Complete Works of Shakespeare): Henry IV Part 1 and Richard II
    have anchorless h2 ACT headings while every other play has anchored ones.
    The TOC links scenes directly, skipping acts.
    """
    headings = _headings(100)

    # Henry IV Part 1: all 5 acts present and nesting scenes
    h4p1 = [h for h in headings if h.div1 == "THE FIRST PART OF KING HENRY THE FOURTH"]
    h4p1_acts = [h for h in h4p1 if h.content.startswith("ACT")]
    assert sorted(h.content for h in h4p1_acts) == [
        "ACT I", "ACT II", "ACT III", "ACT IV", "ACT V",
    ]
    # Scenes nest under their respective acts, not all under ACT I
    act2_scenes = [h for h in h4p1 if h.div2 == "ACT II" and h.content.startswith("SCENE")]
    assert len(act2_scenes) == 4

    # Richard II: same pattern
    r2 = [h for h in headings if h.div1 == "THE LIFE AND DEATH OF KING RICHARD THE SECOND"]
    r2_acts = [h for h in r2 if h.content.startswith("ACT")]
    assert sorted(h.content for h in r2_acts) == [
        "ACT I", "ACT II", "ACT III", "ACT IV", "ACT V",
    ]
    act5_scenes = [h for h in r2 if h.div2 == "ACT V" and h.content.startswith("SCENE")]
    assert len(act5_scenes) == 6


def test_shakespeare_complete_works_full_structure():
    """PG 100 (The Complete Works of William Shakespeare) must parse as
    44 top-level works: 37 plays (each with 5 acts and correctly nested
    scenes), THE SONNETS, A LOVER'S COMPLAINT, THE PASSIONATE PILGRIM,
    THE PHOENIX AND THE TURTLE, THE RAPE OF LUCRECE, and VENUS AND ADONIS.
    Also verifies THE TWO NOBLE KINSMEN (sometimes disputed).

    This is a cornerstone corpus work — regressions here indicate broad
    structural damage to the parser.
    """
    headings = _headings(100)

    # ---- 44 top-level works ----
    works = []
    seen = set()
    for h in headings:
        if h.div1 and h.div1 not in seen:
            seen.add(h.div1)
            works.append(h.div1)
    assert len(works) == 44

    # ---- Expected plays (37) — each must have exactly 5 acts ----
    expected_plays = [
        "ALL\u2019S WELL THAT ENDS WELL",
        "THE TRAGEDY OF ANTONY AND CLEOPATRA",
        "AS YOU LIKE IT",
        "THE COMEDY OF ERRORS",
        "THE TRAGEDY OF CORIOLANUS",
        "CYMBELINE",
        "THE TRAGEDY OF HAMLET, PRINCE OF DENMARK",
        "THE FIRST PART OF KING HENRY THE FOURTH",
        "THE SECOND PART OF KING HENRY THE FOURTH",
        "THE LIFE OF KING HENRY THE FIFTH",
        "THE FIRST PART OF HENRY THE SIXTH",
        "THE SECOND PART OF KING HENRY THE SIXTH",
        "THE THIRD PART OF KING HENRY THE SIXTH",
        "KING HENRY THE EIGHTH",
        "THE LIFE AND DEATH OF KING JOHN",
        "THE TRAGEDY OF JULIUS CAESAR",
        "THE TRAGEDY OF KING LEAR",
        "LOVE\u2019S LABOUR\u2019S LOST",
        "THE TRAGEDY OF MACBETH",
        "MEASURE FOR MEASURE",
        "THE MERCHANT OF VENICE",
        "THE MERRY WIVES OF WINDSOR",
        "A MIDSUMMER NIGHT\u2019S DREAM",
        "THE TRAGEDY OF OTHELLO, THE MOOR OF VENICE",
        "PERICLES, PRINCE OF TYRE",
        "THE LIFE AND DEATH OF KING RICHARD THE SECOND",
        "KING RICHARD THE THIRD",
        "THE TRAGEDY OF ROMEO AND JULIET",
        "THE TAMING OF THE SHREW",
        "THE TEMPEST",
        "THE LIFE OF TIMON OF ATHENS",
        "THE TRAGEDY OF TITUS ANDRONICUS",
        "TROILUS AND CRESSIDA",
        "TWELFTH NIGHT; OR, WHAT YOU WILL",
        "THE TWO GENTLEMEN OF VERONA",
        "THE TWO NOBLE KINSMEN",
        "THE WINTER\u2019S TALE",
    ]
    # Much Ado has a known title contamination (Dramatis Personæ merged
    # into h2 title due to anchorless intervening h2 Contents heading in
    # source HTML) — match it with a prefix.
    much_ado_title = [w for w in works if w.startswith("MUCH ADO ABOUT NOTHING")]
    assert len(much_ado_title) == 1

    for play in expected_plays:
        play_headings = [h for h in headings if h.div1 == play]
        assert play_headings, f"Play missing from top-level works: {play}"

        acts = [h for h in play_headings if h.content.startswith("ACT")]
        act_names = sorted(h.content.rstrip(".") for h in acts)
        assert act_names == ["ACT I", "ACT II", "ACT III", "ACT IV", "ACT V"], (
            f"{play}: expected 5 acts, got {act_names}"
        )

    # ---- Non-play works (7 including Much Ado) ----
    expected_non_plays = [
        "THE SONNETS",
        "A LOVER\u2019S COMPLAINT",
        "THE PASSIONATE PILGRIM",
        "THE PHOENIX AND THE TURTLE",
        "THE RAPE OF LUCRECE",
        "VENUS AND ADONIS",
    ]
    for title in expected_non_plays:
        assert title in works, f"Non-play work missing: {title}"

    # ---- Scene nesting: spot-check plays with known parsing history ----

    # Hamlet: 5 acts, ACT III has 4 scenes
    hamlet = [h for h in headings if h.div1 == "THE TRAGEDY OF HAMLET, PRINCE OF DENMARK"]
    hamlet_act3_scenes = [
        h for h in hamlet
        if h.div2 == "ACT III" and h.content.upper().startswith("SCENE")
    ]
    assert len(hamlet_act3_scenes) == 4

    # Macbeth: ACT V has 8 scenes (the most of any act in Shakespeare)
    macbeth = [h for h in headings if h.div1 == "THE TRAGEDY OF MACBETH"]
    macbeth_act5_scenes = [
        h for h in macbeth
        if h.div2 == "ACT V" and h.content.upper().startswith("SCENE")
    ]
    assert len(macbeth_act5_scenes) == 8

    # Romeo and Juliet: ACT II has 6 scenes
    romeo = [h for h in headings if h.div1 == "THE TRAGEDY OF ROMEO AND JULIET"]
    romeo_act2_scenes = [
        h for h in romeo
        if h.div2 == "ACT II" and h.content.upper().startswith("SCENE")
    ]
    assert len(romeo_act2_scenes) == 6

    # Henry V: has PROLOGUE and EPILOGUE at act level alongside ACTs
    henry5 = [h for h in headings if h.div1 == "THE LIFE OF KING HENRY THE FIFTH"]
    henry5_act_level = [
        h for h in henry5
        if h.content in ("PROLOGUE.", "EPILOGUE.")
        or h.content.startswith("ACT")
    ]
    henry5_labels = [h.content for h in henry5_act_level]
    assert "PROLOGUE." in henry5_labels
    assert "EPILOGUE." in henry5_labels

    # ---- Total heading count sanity ----
    # With 37 plays × ~25 headings + poetry works, expect 1000+ headings
    assert len(headings) > 1000


# ---------------------------------------------------------------------------
# Emerson corpus (issue #179)
# ---------------------------------------------------------------------------


def test_emerson_conduct_of_life_resolves_paragraph_toc_anchors():
    """PG 39827: TOC links point to <p id="fate"> instead of <a id="...">."""
    headings = _headings(39827)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 9
    assert heading_texts == [
        "Fate",
        "Power",
        "Wealth",
        "Culture",
        "Behavior",
        "Worship",
        "Considerations by the Way",
        "Beauty",
        "Illusions",
    ]


def test_emerson_nature_prefers_paragraph_chapters_over_sparse_heading():
    """PG 29433: single <h1>NATURE</h1> vs 8 <p>-encoded chapters."""
    headings = _headings(29433)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 8
    assert heading_texts[0] == "CHAPTER I."
    assert heading_texts[-1] == "CHAPTER VIII. PROSPECTS."


def test_emerson_essays_first_series_keeps_twelve_essays():
    headings = _headings(2944)
    assert len(headings) == 12
    assert headings[0].content == "I. HISTORY"
    assert headings[-1].content == "XII. ART"


def test_emerson_representative_men_keeps_seven_lectures():
    headings = _headings(6312)
    assert len(headings) == 7
    assert headings[0].content == "I. USES OF GREAT MEN."
    assert "SHAKSPEARE" in headings[4].content


def test_emerson_poems_nests_poems_under_six_parts():
    headings = _headings(12843)
    div1_values = sorted({h.div1 for h in headings if h.div1})
    assert "I — POEMS" in div1_values
    assert "II — MAY-DAY AND OTHER PIECES" in div1_values
    assert "V — APPENDIX" in div1_values
    assert "VI — POEMS OF YOUTH AND EARLY MANHOOD" in div1_values
    # Poems nested under parts
    good_bye = next(h for h in headings if h.content == "GOOD-BYE")
    assert good_bye.div1 == "I — POEMS"


def test_emerson_english_traits_keeps_nineteen_chapters():
    headings = _headings(39862)
    assert len(headings) == 19
    assert headings[0].content == "CHAPTER I.--FIRST VISIT TO ENGLAND."
    assert headings[-1].content == "CHAPTER XIX.--SPEECH AT MANCHESTER."


# ---------------------------------------------------------------------------
# Thoreau corpus (issue #179)
# ---------------------------------------------------------------------------


def test_thoreau_walden_keeps_title_chapters_and_civil_disobedience():
    """PG 205: 'Conclusion' must not truncate the companion essay."""
    headings = _headings(205)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 20
    assert heading_texts[0] == "WALDEN"
    assert heading_texts[1] == "Economy"
    assert heading_texts[18] == "Conclusion"
    assert heading_texts[19] == "ON THE DUTY OF CIVIL DISOBEDIENCE"


def test_thoreau_week_on_concord_keeps_eight_days():
    headings = _headings(4232)
    assert len(headings) == 8
    assert headings[0].content == "CONCORD RIVER"
    assert headings[-1].content == "FRIDAY"


def test_thoreau_cape_cod_keeps_eleven_chapters():
    headings = _headings(34392)
    assert len(headings) == 11
    assert headings[0].content == "INTRODUCTION"
    assert headings[-1].content == "X PROVINCETOWN"


def test_thoreau_journal_01_keeps_introduction_and_year_sections():
    headings = _headings(57393)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 11
    assert heading_texts[0] == "INTRODUCTION"


# ---------------------------------------------------------------------------
# Melville corpus (issue #179)
# ---------------------------------------------------------------------------


def test_melville_typee_keeps_preface_and_note():
    headings = _headings(1900)
    heading_texts = [h.content for h in headings]
    assert heading_texts[0] == "PREFACE"
    assert heading_texts[-1] == "NOTE."
    assert len(headings) == 39


def test_melville_pierre_nests_chapters_under_twentysix_books():
    headings = _headings(34970)
    assert len(headings) == 26
    assert headings[0].content.startswith("BOOK I.")
    assert headings[-1].content.startswith("BOOK XXVI.")


def test_melville_piazza_tales_keeps_six_stories():
    headings = _headings(15859)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 16
    assert heading_texts[0] == "THE PIAZZA."
    assert "BARTLEBY." in heading_texts
    assert heading_texts[-1] == "THE BELL-TOWER."


def test_melville_confidence_man_keeps_fortyfive_chapters():
    headings = _headings(21816)
    assert len(headings) == 45
    assert headings[0].content.startswith("CHAPTER I.")
    assert headings[-1].content.startswith("CHAPTER XLV.")


# ---------------------------------------------------------------------------
# Stevenson corpus (issue #179)
# ---------------------------------------------------------------------------


def test_stevenson_kidnapped_keeps_preface_and_thirty_chapters():
    headings = _headings(421)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 32
    assert "PREFACE TO THE BIOGRAPHICAL EDITION" in heading_texts[0]
    assert headings[-1].content.startswith("CHAPTER XXX")


def test_stevenson_new_arabian_nights_nests_stories():
    headings = _headings(839)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 28
    assert "THE SUICIDE CLUB" in heading_texts


def test_stevenson_in_south_seas_nests_chapters_under_four_parts():
    headings = _headings(464)
    div1_values = sorted({h.div1 for h in headings if h.div1})
    assert len(div1_values) == 4
    assert "PART 1: THE MARQUESAS" in div1_values
    assert "PART IV: THE GILBERTS—APEMAMA" in div1_values


def test_stevenson_catriona_nests_chapters_under_two_parts():
    headings = _headings(589)
    div1_values = sorted({h.div1 for h in headings if h.div1})
    assert "PART I. THE LORD ADVOCATE" in div1_values
    assert "PART II. FATHER AND DAUGHTER" in div1_values


def test_stevenson_childs_garden_of_verses_keeps_two_collections():
    headings = _headings(136)
    heading_texts = [h.content for h in headings]
    assert len(headings) == 2
    assert heading_texts[0] == "THE CHILD ALONE"
    assert heading_texts[1] == "ENVOYS"


# ---------------------------------------------------------------------------
# Issue #185: PG 16643 — Mid-heading footnote citations stripped;
# NOTES sub-headings not promoted as sections.
# ---------------------------------------------------------------------------


def test_emerson_essays_merrill_no_notes_sections():
    """PG 16643: inline citations like [525] stripped from headings;
    scholarly NOTES sub-headings do not leak into sections."""
    headings = _headings(16643)
    heading_texts = [h.content for h in headings]

    assert "SHAKSPEARE; OR, THE POET" in heading_texts
    # No duplicate essay titles from the NOTES section
    assert heading_texts.count("THE AMERICAN SCHOLAR") <= 1
    assert heading_texts.count("COMPENSATION") <= 1
    assert len(headings) == 14


# ---------------------------------------------------------------------------
# Issue #184: PG 492 — Page-number anchor TOC links resolved to headings.
# ---------------------------------------------------------------------------


def test_stevenson_art_of_writing_page_anchor_toc():
    """PG 492: TOC links target page-number anchors inside headings.
    All 7 essays should be parsed as sections."""
    headings = _headings(492)
    heading_texts = [h.content for h in headings]

    assert len(headings) == 7
    assert heading_texts[0] == "ON SOME TECHNICAL ELEMENTS OF STYLE IN LITERATURE"
    assert "THE MORALITY OF THE PROFESSION OF LETTERS" in heading_texts
    assert any("PREFACE TO" in t and "MASTER OF BALLANTRAE" in t for t in heading_texts)


# ---------------------------------------------------------------------------
# Issue #181: PG 75942 — Epigraph/body heading duplication merged.
# ---------------------------------------------------------------------------


def test_emerson_lectures_epigraph_merge():
    """PG 75942: duplicate heading pairs bracketing epigraphs collapsed
    into single sections."""
    headings = _headings(75942)
    heading_texts = [h.content for h in headings]

    # Each essay appears exactly once (not twice)
    assert heading_texts.count("DEMONOLOGY.") == 1
    assert heading_texts.count("ARISTOCRACY.") == 1
    assert heading_texts.count("PLUTARCH.") == 1
    assert heading_texts.count("THOREAU.") == 1
    assert len(headings) == 19


# ---------------------------------------------------------------------------
# Issue #182: PG 438 — Poetry collection headings preserved.
# ---------------------------------------------------------------------------


def test_stevenson_underwoods_full_poem_list():
    """PG 438: all 58 poem headings preserved via heading-scan when
    TOC is sparse (10 vs 58 sections, 5.8:1 ratio)."""
    headings = _headings(438)
    heading_texts = [h.content for h in headings]

    # Should have all poems, not just the 10 from the sparse TOC
    assert len(headings) >= 55
    assert "NOTE" in heading_texts
    assert "BOOK I.\u2014In English" in heading_texts or any("BOOK I" in t for t in heading_texts)
    assert any("ENVOY" in t for t in heading_texts)


# ---------------------------------------------------------------------------
# Sampled corpus regression guards (√n ≈ 8 of the 68 battle-tested works)
#
# Each test represents a distinct structural pattern from the PR #177
# (James brothers) and PR #186 (Emerson/Thoreau/Melville/Stevenson)
# battle-tested corpora.  One work per pattern category.
# ---------------------------------------------------------------------------


def test_james_princess_casamassima_large_multi_level():
    """PG 64599 — 53 sections with BOOK containers + nested chapters."""
    h = _headings(64599)
    assert len(h) == 53
    assert any("BOOK FIRST" in hx.content for hx in h)


def test_james_portrait_of_a_lady_vol2_split_volume():
    """PG 2834 — Split volume starting at CHAPTER XXVIII."""
    h = _headings(2834)
    assert len(h) == 28
    assert h[0].content == "CHAPTER XXVIII"


def test_james_the_american_scene_many_flat():
    """PG 68717 — 74 flat title-like sections (travel writing)."""
    h = _headings(68717)
    assert len(h) == 74
    assert h[0].content == "PREFACE"


def test_james_letters_vol1_large_correspondence():
    """PG 38776 — 180 sections (letters/correspondence, largest James work)."""
    h = _headings(38776)
    assert len(h) == 180
    assert h[0].content == "INTRODUCTION"


def test_james_italian_hours_date_filtering():
    """PG 6354 — 61 sections after date-heading filtering (was 78 pre-fix)."""
    h = _headings(6354)
    assert len(h) == 61
    assert h[0].content == "PREFACE"


def test_james_the_ivory_tower_colophon_fix():
    """PG 62979 — 18 sections after colophon/publisher noise removal."""
    h = _headings(62979)
    assert len(h) == 18
    assert h[0].content == "PREFACE"


def test_james_the_real_thing_collection_with_note():
    """PG 2715 — 21-section story collection with prefatory NOTE."""
    h = _headings(2715)
    assert len(h) == 21
    assert h[0].content == "NOTE."


def test_stevenson_new_poems_sparse_toc_override():
    """PG 441 — 144 sections via sparse-TOC heading-scan override."""
    h = _headings(441)
    assert len(h) == 144
    assert h[0].content == "PREFACE"


def test_pluralistic_universe_lectures_visible_index_suppressed():
    """PG 11984 — 8 LECTURE headings must be top-level; index entries suppressed.

    Root cause: "LECTURE" was not recognised as a structural keyword, so the
    heading-scan start index skipped to APPENDIX B and the entire lecture body
    was invisible.  Index entries (ALL-CAPS names with page references) were
    promoted as structural headings.
    """
    h = _headings(11984)

    # All 8 distinct body lectures should appear as div1 entries.
    lecture_div1s = sorted(
        {c.div1 for c in h if c.div1.startswith("LECTURE") and c.div1 == c.content}
    )
    assert len(lecture_div1s) == 8
    assert lecture_div1s[0] == "LECTURE I"
    assert lecture_div1s[-1] == "LECTURE VIII"

    # Lecture subtitles should nest as div2 under their lecture.
    types_heading = next(c for c in h if c.content == "THE TYPES OF PHILOSOPHIC THINKING")
    assert types_heading.div1 == "LECTURE I"

    conclusions = next(c for c in h if c.content == "CONCLUSIONS" and c.div1 == "LECTURE VIII")
    assert conclusions.div2 == "CONCLUSIONS"

    # Index entries must NOT appear as headings.
    all_text = " ".join(c.content for c in h)
    assert "ARISTIDES" not in all_text
    assert "BAILEY" not in all_text
    assert "SOCRATES" not in all_text


def test_memories_and_studies_editor_credit_not_promoted():
    """PG 20768 — editor credit 'HENRY JAMES, JR.' must not be div1 parent.

    h5 author/editor name headings on the title page (WILLIAM JAMES,
    HENRY JAMES, JR.) must be stripped as title-page subtitles, not
    promoted to structural containers that nest every essay beneath them.
    """
    h = _headings(20768)
    all_div1 = {c.div1 for c in h}
    # Editor/author names must NOT be structural parents.
    assert "HENRY JAMES, JR." not in all_div1
    assert "WILLIAM JAMES" not in all_div1
    assert "MEMORIES AND STUDIES" not in all_div1
    # First real section should be PREFATORY NOTE.
    assert h[0].content == "PREFATORY NOTE"
    # Essay headings visible at div1 level.
    assert any(c.content == "I" and c.div1 == "I" for c in h)


def test_will_to_believe_edition_history_not_structural():
    """PG 26659 — copyright and edition-history headings must not be sections.

    'Copyright, 1896 BY WILLIAM JAMES' and 'First Edition. February, 1897...'
    are publication metadata and must be suppressed.  The printed TOC entries
    (essay titles with trailing page numbers) must also be stripped.
    """
    h = _headings(26659)
    all_text = " ".join(c.content for c in h)
    # Publication metadata must not appear.
    assert "Copyright" not in all_text
    assert "First Edition" not in all_text
    # Real essay headings must survive.
    assert any("THE WILL TO BELIEVE" in c.content and "1" not in c.content for c in h)
    assert any("IS LIFE WORTH LIVING" in c.content for c in h)


def test_thackeray_collected_edition_work_titles_equalized():
    """PG 29363 — all three h1 work titles must be at div1 depth.

    Root cause: 'Henry Esmond' was at div2 while 'English Humourists' and
    'The Georges' were at div1.  The collection-title promotion wasn't
    firing because front-matter headings (Dedication, Preface) at the same
    level were blocking the broad-keyword-child scan.
    """
    h = _headings(29363)
    all_div1 = {c.div1 for c in h if c.div1}

    # All three work titles must be at div1.
    assert "The History Of Henry Esmond, Esq." in all_div1
    assert "The English Humourists Of The Eighteenth Century" in all_div1
    assert "The Georges" in all_div1

    # Books must nest under Henry Esmond at div2.
    book1 = next(c for c in h if c.content.startswith("Book I."))
    assert book1.div1 == "The History Of Henry Esmond, Esq."

    # Chapters must nest under a Book at div3.
    ch1 = next(c for c in h if c.content.startswith("Chapter I."))
    assert ch1.div1 == "The History Of Henry Esmond, Esq."
    assert ch1.div3.startswith("Chapter I.")


