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

    assert heading_texts[-2:] == ["FINALE.", "THE END"]


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
