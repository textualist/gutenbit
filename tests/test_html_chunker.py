"""Tests for HTML chunker: TOC-driven structural parsing."""

from gutenbit.html_chunker import chunk_html

# ------------------------------------------------------------------
# Helper to build minimal PG-style HTML
# ------------------------------------------------------------------

_PG_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><title>Test Book</title></head>
<body>
<section class="pg-boilerplate pgheader" id="pg-header">
  <h2 id="pg-header-heading">The Project Gutenberg eBook of Test</h2>
  <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
</section>
{body}
<section class="pg-boilerplate pgfooter" id="pg-footer">
  <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
</section>
</body>
</html>
"""


def _make_html(body: str) -> str:
    return _PG_TEMPLATE.format(body=body)


# ------------------------------------------------------------------
# Basic structure
# ------------------------------------------------------------------


def test_empty_html():
    html = _make_html("")
    assert chunk_html(html) == []


def test_no_toc_links():
    html = _make_html("<p>Just a paragraph with no table of contents links.</p>")
    assert chunk_html(html) == []


def test_single_chapter():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>It was the best of times, it was the worst of times.</p>
    <p>Another paragraph follows here.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I"
    assert headings[0].div1 == "CHAPTER I"
    assert len(paragraphs) == 2


def test_multiple_chapters():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p class="toc"><a href="#ch2" class="pginternal">CHAPTER II</a></p>
    <p class="toc"><a href="#ch3" class="pginternal">CHAPTER III</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>First chapter content.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Second chapter content.</p>
    <h2><a id="ch3"></a>CHAPTER III</h2>
    <p>Third chapter content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 3
    assert [h.content for h in headings] == ["CHAPTER I", "CHAPTER II", "CHAPTER III"]


def test_structural_headings_preserve_terminal_punctuation():
    html = _make_html("""
    <p class="toc"><a href="#part2" class="pginternal">PART II. SEXUAL SELECTION</a></p>
    <p class="toc"><a href="#ch10" class="pginternal">
      CHAPTER X. SECONDARY SEXUAL CHARACTERS OF INSECTS
    </a></p>
    <p class="toc"><a href="#diptera" class="pginternal">ORDER, DIPTERA (FLIES).</a></p>
    <p class="toc"><a href="#appendix" class="pginternal">CHAPTER XI. APPENDIX [II.]</a></p>
    <h2><a id="part2"></a>PART II. SEXUAL SELECTION</h2>
    <h3><a id="ch10"></a>CHAPTER X. SECONDARY SEXUAL CHARACTERS OF INSECTS</h3>
    <h4><a id="diptera"></a>ORDER, DIPTERA (FLIES).</h4>
    <p>Diptera paragraph.</p>
    <h3><a id="appendix"></a>CHAPTER XI. APPENDIX [II.]</h3>
    <p>Appendix paragraph.</p>
    """)
    headings = [chunk for chunk in chunk_html(html) if chunk.kind == "heading"]

    diptera = next(heading for heading in headings if heading.content.startswith("ORDER, DIPTERA"))
    appendix = next(heading for heading in headings if heading.content.startswith("CHAPTER XI"))

    assert diptera.content == "ORDER, DIPTERA (FLIES)."
    assert diptera.div1 == "PART II. SEXUAL SELECTION"
    assert diptera.div2 == "CHAPTER X. SECONDARY SEXUAL CHARACTERS OF INSECTS"
    assert diptera.div3 == "ORDER, DIPTERA (FLIES)."
    assert appendix.content == "CHAPTER XI. APPENDIX [II.]"
    assert appendix.div1 == "PART II. SEXUAL SELECTION"
    assert appendix.div2 == "CHAPTER XI. APPENDIX [II.]"


def test_positions_are_sequential():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>First paragraph.</p>
    <p>Second paragraph.</p>
    """)
    chunks = chunk_html(html)
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_each_paragraph_is_own_chunk():
    """Each <p> element becomes its own chunk — no accumulation."""
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>"Yes?"</p>
    <p>"No."</p>
    <p>"Maybe."</p>
    <p>"Perhaps."</p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]
    assert len(paragraphs) == 4
    assert paragraphs[0].content == '"Yes?"'
    assert paragraphs[1].content == '"No."'
    assert paragraphs[2].content == '"Maybe."'
    assert paragraphs[3].content == '"Perhaps."'


# ------------------------------------------------------------------
# Hierarchy detection
# ------------------------------------------------------------------


def test_bold_toc_link_is_div1():
    """Bold text in TOC links signals broader divisions (BOOK, PART)."""
    html = _make_html("""
    <p><a href="#b1" class="pginternal"><b>BOOK ONE: 1805</b></a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="b1"></a>BOOK ONE: 1805</h2>
    <p>Book introduction paragraph.</p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 2
    assert headings[0].div1 == "BOOK ONE: 1805"
    assert headings[0].div2 == ""
    assert headings[1].div1 == "BOOK ONE: 1805"
    assert headings[1].div2 == "CHAPTER I"


def test_font_size_toc_link_is_div1():
    html = _make_html("""
    <p><a href="#ot" class="pginternal"><span style="font-size:150%;">OLD TESTAMENT</span></a></p>
    <p><a href="#gen" class="pginternal">GENESIS</a></p>
    <h2><a id="ot"></a>OLD TESTAMENT</h2>
    <p>Testament introduction paragraph.</p>
    <h2><a id="gen"></a>GENESIS</h2>
    <p>Book content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 2
    assert headings[0].div1 == "OLD TESTAMENT"
    assert headings[0].div2 == ""
    assert headings[1].div1 == "OLD TESTAMENT"
    assert headings[1].div2 == "GENESIS"


def test_keyword_based_hierarchy_without_bold():
    html = _make_html("""
    <p><a href="#p1" class="pginternal">PART I</a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="p1"></a>PART I</h2>
    <p>Part introduction.</p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert headings[0].div1 == "PART I"
    assert headings[1].div1 == "PART I"
    assert headings[1].div2 == "CHAPTER I"


def test_div_reset_on_new_broad_heading():
    html = _make_html("""
    <p><a href="#b1" class="pginternal"><b>BOOK ONE</b></a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p><a href="#b2" class="pginternal"><b>BOOK TWO</b></a></p>
    <p><a href="#ch2" class="pginternal">CHAPTER I</a></p>
    <h2><a id="b1"></a>BOOK ONE</h2>
    <p>Text.</p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter one content.</p>
    <h2><a id="b2"></a>BOOK TWO</h2>
    <p>Text.</p>
    <h2><a id="ch2"></a>CHAPTER I</h2>
    <p>New chapter one content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert headings[2].div1 == "BOOK TWO"
    assert headings[2].div2 == ""
    assert headings[3].div1 == "BOOK TWO"
    assert headings[3].div2 == "CHAPTER I"


def test_same_rank_part_and_book_headings_stay_nested():
    html = _make_html("""
    <p><a href="#part1" class="pginternal">PART I</a></p>
    <p><a href="#book1" class="pginternal">Book I. The History Of A Family</a></p>
    <p><a href="#chap1" class="pginternal">Chapter I. Fyodor Pavlovitch Karamazov</a></p>
    <p><a href="#book2" class="pginternal">Book II. An Unfortunate Gathering</a></p>
    <p><a href="#chap2" class="pginternal">Chapter I. They Arrive At The Monastery</a></p>
    <p><a href="#part2" class="pginternal">PART II</a></p>
    <p><a href="#book3" class="pginternal">Book III. The Sensualists</a></p>
    <p><a href="#chap3" class="pginternal">Chapter I. In The Servants’ Quarters</a></p>
    <h2><a id="part1"></a>PART I</h2>
    <h2><a id="book1"></a>Book I. The History Of A Family</h2>
    <h2><a id="chap1"></a>Chapter I. Fyodor Pavlovitch Karamazov</h2>
    <p>Family history.</p>
    <h2><a id="book2"></a>Book II. An Unfortunate Gathering</h2>
    <h2><a id="chap2"></a>Chapter I. They Arrive At The Monastery</h2>
    <p>Monastery chapter.</p>
    <h2><a id="part2"></a>PART II</h2>
    <h2><a id="book3"></a>Book III. The Sensualists</h2>
    <h2><a id="chap3"></a>Chapter I. In The Servants’ Quarters</h2>
    <p>Servants chapter.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == [
        "PART I",
        "Book I. The History Of A Family",
        "Chapter I. Fyodor Pavlovitch Karamazov",
        "Book II. An Unfortunate Gathering",
        "Chapter I. They Arrive At The Monastery",
        "PART II",
        "Book III. The Sensualists",
        "Chapter I. In The Servants’ Quarters",
    ]
    assert headings[1].div1 == "PART I"
    assert headings[1].div2 == "Book I. The History Of A Family"
    assert headings[2].div1 == "PART I"
    assert headings[2].div2 == "Book I. The History Of A Family"
    assert headings[2].div3 == "Chapter I. Fyodor Pavlovitch Karamazov"
    assert headings[3].div1 == "PART I"
    assert headings[3].div2 == "Book II. An Unfortunate Gathering"
    assert headings[5].div1 == "PART II"
    assert headings[5].div2 == ""
    assert headings[6].div1 == "PART II"
    assert headings[6].div2 == "Book III. The Sensualists"
    assert headings[7].div1 == "PART II"
    assert headings[7].div2 == "Book III. The Sensualists"
    assert headings[7].div3 == "Chapter I. In The Servants’ Quarters"


def test_more_prominent_heading_run_is_not_nested_under_proem():
    html = _make_html("""
    <p><a href="#proem" class="pginternal"><b>PROEM.</b></a></p>
    <p><b><a href="#day1" class="pginternal">DAY THE FIRST</a></b></p>
    <p><a href="#story1" class="pginternal">THE FIRST STORY</a></p>
    <p><b><a href="#conclusion" class="pginternal">CONCLUSION OF THE AUTHOR</a></b></p>
    <h2><a id="proem"></a>Proem</h2>
    <p>Proem text.</p>
    <h1><a id="day1"></a>Day the First</h1>
    <p>Day introduction.</p>
    <h2><a id="story1"></a>THE FIRST STORY</h2>
    <p>Story text.</p>
    <h1><a id="conclusion"></a>Conclusion of the Author</h1>
    <p>Conclusion text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == [
        "Proem",
        "Day the First",
        "THE FIRST STORY",
        "Conclusion of the Author",
    ]
    assert headings[0].div1 == "Proem"
    assert headings[0].div2 == ""
    assert headings[1].div1 == "Day the First"
    assert headings[1].div2 == ""
    assert headings[2].div1 != "Proem"
    assert headings[3].div1 == "Conclusion of the Author"
    assert headings[3].div2 == ""


def test_body_headings_refine_partial_toc():
    html = _make_html("""
    <table><tbody>
      <tr><td><a href="#p1" class="pginternal">PART ONE</a></td></tr>
      <tr><td><a href="#p2" class="pginternal">PART TWO</a></td></tr>
    </tbody></table>
    <div class="chapter">
      <h2><a id="p1"></a>PART ONE</h2>
      <h3>Chapter 1</h3>
      <p>Part one, chapter one paragraph.</p>
      <h3>Chapter 2</h3>
      <p>Part one, chapter two paragraph.</p>
    </div>
    <div class="chapter">
      <h2><a id="p2"></a>PART TWO</h2>
      <h3>Chapter 1</h3>
      <p>Part two, chapter one paragraph.</p>
    </div>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "PART ONE",
        "Chapter 1",
        "Chapter 2",
        "PART TWO",
        "Chapter 1",
    ]
    assert headings[1].div1 == "PART ONE"
    assert headings[1].div2 == "Chapter 1"
    assert headings[2].div1 == "PART ONE"
    assert headings[2].div2 == "Chapter 2"
    assert headings[4].div1 == "PART TWO"
    assert headings[4].div2 == "Chapter 1"
    assert paragraphs[0].div1 == "PART ONE"
    assert paragraphs[0].div2 == "Chapter 1"
    assert paragraphs[1].div1 == "PART ONE"
    assert paragraphs[1].div2 == "Chapter 2"
    assert paragraphs[2].div1 == "PART TWO"
    assert paragraphs[2].div2 == "Chapter 1"


def test_title_like_toc_section_still_allows_body_refinement():
    html = _make_html("""
    <p><a href="#work" class="pginternal">Collected Play</a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="work"></a>Collected Play</h2>
    <h2>BOOK I</h2>
    <p>Book introduction paragraph.</p>
    <h3><a id="ch1"></a>CHAPTER I</h3>
    <p>First chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == ["Collected Play", "BOOK I", "CHAPTER I"]
    assert headings[0].div1 == ""
    assert headings[0].div2 == "Collected Play"
    assert headings[1].div1 == "BOOK I"
    assert headings[1].div2 == ""
    assert headings[2].div1 == "BOOK I"
    assert headings[2].div2 == "CHAPTER I"
    assert paragraphs[0].div1 == "BOOK I"


# ------------------------------------------------------------------
# Anchor patterns
# ------------------------------------------------------------------


def test_anchor_before_heading_pattern():
    html = _make_html("""
    <p class="toc"><a href="#link2HCH0001" class="pginternal">CHAPTER 1</a></p>
    <p><a id="link2HCH0001"><!--  H2 anchor --></a></p>
    <div style="height: 4em;"><br></div>
    <h2>CHAPTER 1</h2>
    <p>Content of chapter one.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 1
    assert headings[0].content == "CHAPTER 1"
    assert headings[0].div1 == "CHAPTER 1"


def test_scene_one_toc_link_pointing_to_act_anchor_emits_both_levels():
    html = _make_html("""
    <p><a href="#play" class="pginternal">PLAY TITLE</a></p>
    <p><a href="#scene1" class="pginternal">Scene I. Hall.</a></p>
    <p><a href="#scene2" class="pginternal">Scene II. Garden.</a></p>
    <h2><a id="play"></a>PLAY TITLE</h2>
    <h2><a id="scene1"></a>ACT I</h2>
    <h3>SCENE I. Hall.</h3>
    <p>Scene one text.</p>
    <h3><a id="scene2"></a>SCENE II. Garden.</h3>
    <p>Scene two text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "PLAY TITLE",
        "ACT I",
        "Scene I. Hall.",
        "SCENE II. Garden.",
    ]
    assert headings[1].div1 == "ACT I"
    assert headings[2].div1 == "ACT I"
    assert headings[2].div2 == "Scene I. Hall."
    assert paragraphs[0].div1 == "ACT I"
    assert paragraphs[0].div2 == "Scene I. Hall."


def test_collection_titles_promote_to_top_level_when_repeated():
    html = _make_html("""
    <p><a href="#play1" class="pginternal">PLAY ONE</a></p>
    <p><a href="#play1_scene1" class="pginternal">Scene I. Hall.</a></p>
    <p><a href="#play2" class="pginternal">PLAY TWO</a></p>
    <p><a href="#play2_scene1" class="pginternal">Scene I. Garden.</a></p>
    <p><a href="#poem" class="pginternal">POEM THREE</a></p>
    <h2><a id="play1"></a>PLAY ONE</h2>
    <h2><a id="play1_scene1"></a>ACT I</h2>
    <h3>SCENE I. Hall.</h3>
    <p>Play one text.</p>
    <h2><a id="play2"></a>PLAY TWO</h2>
    <h2><a id="play2_scene1"></a>ACT I</h2>
    <h3>SCENE I. Garden.</h3>
    <p>Play two text.</p>
    <h2><a id="poem"></a>POEM THREE</h2>
    <p>Poem text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "PLAY ONE",
        "ACT I",
        "Scene I. Hall.",
        "PLAY TWO",
        "ACT I",
        "Scene I. Garden.",
        "POEM THREE",
    ]
    assert headings[0].div1 == "PLAY ONE"
    assert headings[1].div1 == "PLAY ONE"
    assert headings[1].div2 == "ACT I"
    assert headings[2].div1 == "PLAY ONE"
    assert headings[2].div2 == "ACT I"
    assert headings[2].div3 == "Scene I. Hall."
    assert headings[3].div1 == "PLAY TWO"
    assert headings[4].div1 == "PLAY TWO"
    assert headings[4].div2 == "ACT I"
    assert headings[6].div1 == "POEM THREE"
    assert paragraphs[2].div1 == "POEM THREE"


def test_title_like_poems_stay_nested_within_books():
    html = _make_html("""
    <p><a href="#book1" class="pginternal"><b>BOOK I. INSCRIPTIONS</b></a></p>
    <p><a href="#poem1" class="pginternal">One’s-Self I Sing</a></p>
    <p><a href="#poem2" class="pginternal">As I Ponder’d in Silence</a></p>
    <p><a href="#book2" class="pginternal"><b>BOOK II</b></a></p>
    <h2><a id="book1"></a>BOOK I. INSCRIPTIONS</h2>
    <h2><a id="poem1"></a>One’s-Self I Sing</h2>
    <p>Poem one text.</p>
    <h2><a id="poem2"></a>As I Ponder’d in Silence</h2>
    <p>Poem two text.</p>
    <h2><a id="book2"></a>BOOK II</h2>
    <p>Starting from Paumanok</p>
    <pre>
  Starting from fish-shape Paumanok where I was born,
  Well-begotten, and rais’d by a perfect mother,
    </pre>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "BOOK I. INSCRIPTIONS",
        "One’s-Self I Sing",
        "As I Ponder’d in Silence",
        "BOOK II",
    ]
    assert headings[0].div1 == "BOOK I. INSCRIPTIONS"
    assert headings[1].div1 == "BOOK I. INSCRIPTIONS"
    assert headings[1].div2 == "One’s-Self I Sing"
    assert headings[2].div1 == "BOOK I. INSCRIPTIONS"
    assert headings[2].div2 == "As I Ponder’d in Silence"
    assert headings[3].div1 == "BOOK II"
    assert headings[3].div2 == ""

    book_two_paragraphs = [paragraph for paragraph in paragraphs if paragraph.div1 == "BOOK II"]
    assert [paragraph.content for paragraph in book_two_paragraphs] == [
        "Starting from Paumanok",
        (
            "Starting from fish-shape Paumanok where I was born,\n"
            "  Well-begotten, and rais’d by a perfect mother,"
        ),
    ]


def test_pre_blocks_are_collected_as_text_chunks():
    html = _make_html("""
    <p><a href="#book2" class="pginternal"><b>BOOK II</b></a></p>
    <h2><a id="book2"></a>BOOK II</h2>
    <p>Starting from Paumanok</p>
    <pre>
  Starting from fish-shape Paumanok where I was born,
  Well-begotten, and rais’d by a perfect mother,
    </pre>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [paragraph.div1 for paragraph in paragraphs] == ["BOOK II", "BOOK II"]
    assert paragraphs[0].content == "Starting from Paumanok"
    assert (
        paragraphs[1].content == "Starting from fish-shape Paumanok where I was born,\n"
        "  Well-begotten, and rais’d by a perfect mother,"
    )


def test_enumerated_h3_headings_are_kept_as_sections():
    html = _make_html("""
    <h3><a id="chap1"></a>I.<br>Of Our Spiritual Strivings</h3>
    <p>First chapter text.</p>
    <h3><a id="chap2"></a>II.<br>Of the Dawn of Freedom</h3>
    <p>Second chapter text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [heading.content for heading in headings] == [
        "I. Of Our Spiritual Strivings",
        "II. Of the Dawn of Freedom",
    ]
    assert headings[0].div1 == "I. Of Our Spiritual Strivings"
    assert headings[1].div1 == "II. Of the Dawn of Freedom"
    assert paragraphs[0].div1 == "I. Of Our Spiritual Strivings"
    assert paragraphs[1].div1 == "II. Of the Dawn of Freedom"


def test_single_work_title_is_not_promoted_above_parts():
    html = _make_html("""
    <p><a href="#title" class="pginternal">THE BOOK</a></p>
    <p><a href="#p1" class="pginternal">PART I</a></p>
    <p><a href="#c1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="title"></a>THE BOOK</h2>
    <h2><a id="p1"></a>PART I</h2>
    <p>Part introduction.</p>
    <h3><a id="c1"></a>CHAPTER I</h3>
    <p>Chapter text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == ["THE BOOK", "PART I", "CHAPTER I"]
    assert headings[0].div1 == ""
    assert headings[0].div2 == "THE BOOK"
    assert headings[1].div1 == "PART I"
    assert headings[1].div2 == ""
    assert headings[2].div1 == "PART I"
    assert headings[2].div2 == "CHAPTER I"


def test_illustration_links_ignored():
    html = _make_html("""
    <p><a href="#stave1" class="pginternal">MARLEY'S GHOST</a></p>
    <p><a href="#illust1" class="pginternal">Marley's Ghost Illustration</a></p>
    <h2><a id="stave1"></a>STAVE ONE.</h2>
    <p>Marley was dead.</p>
    <p><a id="illust1"></a></p>
    <h4><i>Marley's Ghost Illustration</i></h4>
    <p>More text after illustration.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 1
    assert headings[0].content == "STAVE ONE."


def test_page_number_links_ignored():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p><a href="#page_42" class="pginternal">42</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content paragraph.</p>
    <p><a id="page_42"></a>More text at page forty-two.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I"


# ------------------------------------------------------------------
# Gutenberg delimiter bounds
# ------------------------------------------------------------------


def test_pg_header_stripped():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content that should appear.</p>
    """)
    chunks = chunk_html(html)
    all_text = " ".join(c.content for c in chunks)
    assert "Project Gutenberg" not in all_text


def test_pg_legacy_this_delimiters_bound_content():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content that should appear.</p>
    """)
    html = html.replace(
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***",
        "*** START OF THIS PROJECT GUTENBERG EBOOK TEST BOOK ***",
    ).replace(
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>",
        "*** END OF THIS PROJECT GUTENBERG EBOOK TEST BOOK ***</div>"
        "<p>Project Gutenberg License content.</p>",
    )

    chunks = chunk_html(html)
    all_text = " ".join(c.content for c in chunks)
    assert "Content that should appear." in all_text
    assert "Project Gutenberg License content." not in all_text


def test_pg_header_fallback_when_start_delimiter_missing():
    html = """\
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Test Book</title></head>
    <body>
    <section class="pg-boilerplate pgheader" id="pg-header">
      <h2>The Project Gutenberg eBook of Test</h2>
      <p><a href="#junk" class="pginternal">JUNK HEADING</a></p>
      <h2><a id="junk"></a>JUNK HEADING</h2>
    </section>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content that should appear.</p>
    <section class="pg-boilerplate pgfooter" id="pg-footer">
      <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
    </section>
    </body>
    </html>
    """
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    all_text = " ".join(c.content for c in chunks)

    assert headings == ["CHAPTER I"]
    assert "JUNK HEADING" not in all_text


def test_pg_footer_fallback_when_end_delimiter_missing():
    html = """\
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Test Book</title></head>
    <body>
    <section class="pg-boilerplate pgheader" id="pg-header">
      <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
    </section>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content that should appear.</p>
    <section class="pg-boilerplate pgfooter" id="pg-footer">
      <h2><a id="license"></a>PROJECT GUTENBERG LICENSE</h2>
      <p><a href="#license" class="pginternal">PROJECT GUTENBERG LICENSE</a></p>
    </section>
    </body>
    </html>
    """
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    all_text = " ".join(c.content for c in chunks)

    assert headings == ["CHAPTER I"]
    assert "PROJECT GUTENBERG LICENSE" not in all_text


# ------------------------------------------------------------------
# Opening prose before first heading
# ------------------------------------------------------------------


def test_opening_prose_before_first_section_is_paragraph():
    html = _make_html("""
    <p>Title Page: A Great Novel by Famous Author.</p>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content.</p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]
    assert len(paragraphs) >= 2
    assert paragraphs[0].content == "Title Page: A Great Novel by Famous Author."
    assert paragraphs[0].div1 == ""


def test_toc_paragraphs_not_emitted_as_content():
    html = _make_html("""
    <p>Title Page: A Great Novel by Famous Author.</p>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p class="toc"><a href="#ch2" class="pginternal">CHAPTER II</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter one content.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Chapter two content.</p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c.content for c in chunks if c.kind == "text"]
    assert "CHAPTER I" not in paragraphs
    assert "CHAPTER II" not in paragraphs
    assert "Title Page: A Great Novel by Famous Author." in paragraphs


def test_inline_pginternal_links_not_toc():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>
      Body text with inline reference
      <a href="#fn1" class="pginternal">[1]</a>
      remains content.
    </p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c.content for c in chunks if c.kind == "text"]
    assert paragraphs == ["Body text with inline reference [1] remains content."]


def test_single_link_chapter_toc_paragraph_with_subtitle_is_toc():
    html = _make_html("""
    <p><a href="#ch11" class="pginternal">CHAPTER XI</a> - Of Prophecy.</p>
    <p><a href="#ch12" class="pginternal">CHAPTER XII</a> - Of Miracles.</p>
    <p><a href="#endnotes" class="pginternal">Author's Endnotes to the Treatise.</a></p>
    <h2><a id="ch11"></a>CHAPTER XI - Of Prophecy.</h2>
    <p>Chapter eleven paragraph.</p>
    <h2><a id="ch12"></a>CHAPTER XII - Of Miracles.</h2>
    <p>Chapter twelve paragraph.</p>
    <h2><a id="endnotes"></a>Author's Endnotes to the Treatise.</h2>
    <p>Endnotes paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    paragraphs = [c.content for c in chunks if c.kind == "text"]

    assert headings == [
        "CHAPTER XI - Of Prophecy.",
        "CHAPTER XII - Of Miracles.",
        "Author's Endnotes to the Treatise.",
    ]
    assert paragraphs == [
        "Chapter eleven paragraph.",
        "Chapter twelve paragraph.",
        "Endnotes paragraph.",
    ]


# ------------------------------------------------------------------
# Heading text extraction
# ------------------------------------------------------------------


def test_heading_with_pagenum_span():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a><span class="pagenum"><a id="page_1">{1}</a></span>
    CHAPTER I.</h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I."


def test_heading_from_img_alt():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">Chapter I</a></p>
    <h2><a id="ch1"></a><img alt="CHAPTER I." src="ch1.jpg"></h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I."


def test_heading_text_preferred_over_img_alt_caption():
    html = _make_html("""
    <p><a href="#ch3" class="pginternal">CHAPTER III</a></p>
    <h2><a id="ch3"></a><img alt="He rode a black horse." src="plate.jpg">CHAPTER III</h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER III"


def test_heading_keeps_structural_suffix_when_caption_precedes_it():
    html = _make_html("""
    <p><a href="#ch27" class="pginternal">On the Stairs. CHAPTERXXVII</a></p>
    <h2><a id="ch27"></a>On the Stairs. CHAPTERXXVII</h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER XXVII"


def test_heading_cleanup_does_not_treat_part_inside_word_as_keyword():
    html = _make_html("""
    <p><a href="#ch37" class="pginternal">His parting obeisance. CHAPTER XXXVII</a></p>
    <h2><a id="ch37"></a>His parting obeisance. CHAPTER XXXVII</h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER XXXVII"


def test_bracketed_numeric_heading_keeps_closing_bracket():
    html = _make_html("""
    <p class="toc"><a href="#part01" class="pginternal"><b>— I —</b></a></p>
    <p class="toc"><a href="#chap01" class="pginternal">[ 1 ]</a></p>
    <h2><a id="part01"></a>— I —</h2>
    <h3><a id="chap01"></a>[ 1 ]</h3>
    <p>Stately, plump Buck Mulligan came from the stairhead.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == ["— I —", "[ 1 ]"]
    assert headings[1].div1 == "— I —"
    assert headings[1].div2 == "[ 1 ]"


def test_paragraph_from_img_alt_drop_cap():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p><img alt="M" src="dropcap.jpg">r. Bennet was among the earliest.</p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]
    assert len(paragraphs) == 1
    assert paragraphs[0].content.startswith("Mr. Bennet")


def test_apparatus_toc_links_do_not_become_sections():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p><a href="#notes" class="pginternal">NOTES</a></p>
    <p><a href="#page1" class="pginternal">Page 1</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content paragraph.</p>
    <h2><a id="notes"></a>NOTES</h2>
    <p>Editorial note.</p>
    <h2><a id="page1"></a>Page 1</h2>
    <p>Page marker text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    assert headings == ["CHAPTER I"]


def test_heading_scan_skips_notes_and_page_markers():
    html = """\
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Test Book</title></head>
    <body>
    <section class="pg-boilerplate pgheader" id="pg-header">
      <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
    </section>
    <table><tbody>
      <tr><td>CHAPTER I</td><td><span class="indexpageno">
        <a href="#page1" class="pginternal">1</a></span></td></tr>
    </tbody></table>
    <h2><a id="page1"></a>CHAPTER I</h2>
    <p>First chapter paragraph.</p>
    <h2>NOTES</h2>
    <p>Editorial note paragraph.</p>
    <h2>Page 2</h2>
    <p>Page marker paragraph.</p>
    <section class="pg-boilerplate pgfooter" id="pg-footer">
      <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK TEST BOOK ***</div>
    </section>
    </body>
    </html>
    """
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    assert headings == ["CHAPTER I"]


def test_heading_scan_skips_punctuated_contents_heading():
    html = _make_html("""
    <h2>PREFACE.</h2>
    <p>Preface paragraph.</p>
    <h2>CONTENTS.</h2>
    <p>Contents paragraph.</p>
    <h2>CHAPTER I.</h2>
    <p>Chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["PREFACE.", "CHAPTER I."]


def test_heading_scan_skips_front_contents_cluster_and_merges_split_headings():
    html = _make_html("""
    <p>CONTENTS OF THE SECOND VOLUME</p>
    <h4>BOOK III. OF WORDS</h4>
    <h5>CHAP.</h5>
    <h5>I. OF WORDS OR LANGUAGE IN GENERAL II. OF THE SIGNIFICATION OF WORDS</h5>
    <h2>BOOK III</h2>
    <h5>OF WORDS</h5>
    <h4>CHAPTER I.</h4>
    <h5>OF WORDS OR LANGUAGE IN GENERAL</h5>
    <p>Actual first chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == [
        "BOOK III OF WORDS",
        "CHAPTER I. OF WORDS OR LANGUAGE IN GENERAL",
    ]
    assert headings[0].div1 == "BOOK III OF WORDS"
    assert headings[1].div1 == "BOOK III OF WORDS"
    assert headings[1].div2 == "CHAPTER I. OF WORDS OR LANGUAGE IN GENERAL"


def test_heading_scan_keeps_part_headings_separate_from_numbered_child_sections():
    html = _make_html("""
    <h1>Black Beauty</h1>
    <h2>Part I</h2>
    <h3>01 My Early Home</h3>
    <p>First chapter paragraph.</p>
    <h3>02 The Hunt</h3>
    <p>Second chapter paragraph.</p>
    <h2>Part II</h2>
    <h3>22 Earlshall</h3>
    <p>Third chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == [
        "Part I",
        "01 My Early Home",
        "02 The Hunt",
        "Part II",
        "22 Earlshall",
    ]
    assert headings[0].div1 == "Part I"
    assert headings[1].div1 == "Part I"
    assert headings[1].div2 == "01 My Early Home"
    assert headings[4].div1 == "Part II"
    assert headings[4].div2 == "22 Earlshall"


def test_heading_scan_keeps_leading_title_page_headings_and_skips_attributions():
    html = _make_html("""
    <h3>THE MODERN LIBRARY</h3>
    <h4>OF THE WORLD'S BEST BOOKS</h4>
    <h3>CANDIDE BY VOLTAIRE</h3>
    <h1>CANDIDE</h1>
    <h4>INTRODUCTION BY PHILIP LITTELL</h4>
    <h5>BONI AND LIVERIGHT, INC. PUBLISHERS NEW YORK</h5>
    <h2>INTRODUCTION</h2>
    <p>Intro paragraph.</p>
    <h2>CANDIDE</h2>
    <h2>I</h2>
    <h3>HOW CANDIDE WAS BROUGHT UP IN A MAGNIFICENT CASTLE</h3>
    <p>First chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    heading_texts = [h.content for h in headings]

    assert heading_texts[:5] == [
        "THE MODERN LIBRARY",
        "CANDIDE BY VOLTAIRE",
        "INTRODUCTION",
        "CANDIDE",
        "I",
    ]
    assert "INTRODUCTION BY PHILIP LITTELL" not in heading_texts
    assert "BONI AND LIVERIGHT, INC. PUBLISHERS NEW YORK" not in heading_texts


def test_heading_scan_starts_at_front_matter_without_immediate_title_repeat():
    html = _make_html("""
    <h2>BLEAK HOUSE</h2>
    <h3>by</h3>
    <h3>Charles Dickens</h3>
    <h2>PREFACE</h2>
    <p>Preface paragraph.</p>
    <h2>CHAPTER I</h2>
    <p>Chapter one paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["PREFACE", "CHAPTER I"]


def test_heading_scan_does_not_drop_title_that_only_repeats_much_later():
    html = _make_html("""
    <h2>VOLUME II</h2>
    <h2>INTRODUCTION</h2>
    <h3>PREFARATORY</h3>
    <h3>CERVANTES</h3>
    <h3>‘DON QUIXOTE’</h3>
    <h3>THE AUTHOR’S PREFACE</h3>
    <h2>SOME COMMENDATORY VERSES</h2>
    <h3>URGANDA THE UNKNOWN</h3>
    <h3>AMADIS OF GAUL</h3>
    <h2>PART I</h2>
    <h3>THE AUTHOR’S PREFACE</h3>
    <p>Body paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings[:6] == [
        "VOLUME II INTRODUCTION",
        "PREFARATORY",
        "CERVANTES",
        "‘DON QUIXOTE’",
        "THE AUTHOR’S PREFACE",
        "SOME COMMENDATORY VERSES",
    ]


def test_heading_scan_strips_synopsis_from_book_heading():
    html = _make_html("""
    <h2>BOOK IV</h2>
    <h5>OF KNOWLEDGE AND PROBABILITY SYNOPSIS OF THE FOURTH BOOK.</h5>
    <p>Book-level synopsis paragraph.</p>
    <h2>CHAPTER I.</h2>
    <p>First chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "BOOK IV OF KNOWLEDGE AND PROBABILITY",
        "CHAPTER I.",
    ]
    assert paragraphs[0].div1 == "BOOK IV OF KNOWLEDGE AND PROBABILITY"
    assert paragraphs[0].content == "Book-level synopsis paragraph."


def test_heading_scan_ignores_internal_non_structural_subheads():
    html = _make_html("""
    <h2>CHAPTER XX</h2>
    <h5>OF WRONG ASSENT, OR ERROR</h5>
    <h5>I. WANT OF PROOFS.</h5>
    <h5>II. WANT OF ABILITY TO USE THEM.</h5>
    <p>Actual chapter text paragraph.</p>
    <h2>CHAPTER XXI</h2>
    <p>Next chapter paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "CHAPTER XX OF WRONG ASSENT, OR ERROR",
        "CHAPTER XXI",
    ]
    assert paragraphs[0].div1 == "CHAPTER XX OF WRONG ASSENT, OR ERROR"
    assert paragraphs[0].content == "Actual chapter text paragraph."


def test_heading_scan_skips_editorial_placeholder_heading():
    html = _make_html("""
    <h2>BOOK IV</h2>
    <h5>OF KNOWLEDGE AND PROBABILITY</h5>
    <h2>CHAPTER XIX. [not in early editions]</h2>
    <h2>CHAPTER XX</h2>
    <h5>OF WRONG ASSENT, OR ERROR</h5>
    <p>Actual chapter text paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == [
        "BOOK IV OF KNOWLEDGE AND PROBABILITY",
        "CHAPTER XX OF WRONG ASSENT, OR ERROR",
    ]


def test_heading_scan_skips_dialogue_subheadings_after_book_start():
    html = _make_html("""
    <h2>BOOK I</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph one.</p>
    <h5>SOCRATES - THRASYMACHUS</h5>
    <p>Dialogue paragraph two.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == ["BOOK I"]
    assert all(paragraph.div1 == "BOOK I" for paragraph in paragraphs)
    assert all(not paragraph.div2 for paragraph in paragraphs)


def test_heading_scan_keeps_repeated_books_without_dialogue_speakers():
    html = _make_html("""
    <h2>BOOK I</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph one.</p>
    <h5>SOCRATES - THRASYMACHUS</h5>
    <p>Dialogue paragraph two.</p>
    <h2>BOOK II</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph three.</p>
    <h5>SOCRATES - THRASYMACHUS</h5>
    <p>Dialogue paragraph four.</p>
    <h2>BOOK III</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph five.</p>
    <h5>SOCRATES - THRASYMACHUS</h5>
    <p>Dialogue paragraph six.</p>
    <h2>BOOK IV</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph seven.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == ["BOOK I", "BOOK II", "BOOK III", "BOOK IV"]
    assert all(not h.div2 for h in headings)
    assert [paragraph.div1 for paragraph in paragraphs] == [
        "BOOK I",
        "BOOK I",
        "BOOK II",
        "BOOK II",
        "BOOK III",
        "BOOK III",
        "BOOK IV",
    ]
    assert all(not paragraph.div2 for paragraph in paragraphs)


def test_heading_scan_uses_non_keyword_headings_when_no_structural_keywords_exist():
    html = _make_html("""
    <h1>Metamorphosis</h1>
    <p>Front paragraph.</p>
    <h2>I</h2>
    <p>Gregor awoke one morning.</p>
    <h2>II</h2>
    <p>Another paragraph.</p>
    <h2>III</h2>
    <p>Final paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["Metamorphosis", "I", "II", "III"]


# ------------------------------------------------------------------
# Hard Times regression shape: delimiter-bounded + sorted TOC anchors
# ------------------------------------------------------------------


def test_out_of_order_toc_and_out_of_bounds_links_are_handled():
    html = """\
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Hard Times</title></head>
    <body>
    <h2><a id="front"></a>Hard Times and Reprinted Pieces</h2>
    <p>This heading is outside START and must be excluded.</p>
    <section class="pg-boilerplate pgheader" id="pg-header">
      <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK HARD TIMES ***</div>
      <p><a href="#front" class="pginternal">Hard Times and Reprinted Pieces</a></p>
    </section>
    <p><a href="#ch5" class="pginternal">CHAPTER V</a></p>
    <p><a href="#ch4" class="pginternal">CHAPTER IV</a></p>
    <h2><a id="ch4"></a>CHAPTER IV</h2>
    <p>Chapter four paragraph.</p>
    <h2><a id="ch5"></a>CHAPTER V</h2>
    <p>Chapter five paragraph.</p>
    <section class="pg-boilerplate pgfooter" id="pg-footer">
      <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK HARD TIMES ***</div>
      <p><a href="#license" class="pginternal">THE FULL PROJECT GUTENBERG LICENSE</a></p>
      <h2><a id="license"></a>THE FULL PROJECT GUTENBERG LICENSE</h2>
    </section>
    <p>Outside END and must be excluded.</p>
    </body>
    </html>
    """
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    paragraphs = [c.content for c in chunks if c.kind == "text"]

    assert headings == ["CHAPTER IV", "CHAPTER V"]
    assert "Hard Times and Reprinted Pieces" not in headings
    assert "THE FULL PROJECT GUTENBERG LICENSE" not in headings
    assert "Outside END and must be excluded." not in paragraphs


def test_page_number_toc_links_fall_back_to_heading_scan():
    html = """\
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Hard Times</title></head>
    <body>
    <section class="pg-boilerplate pgheader" id="pg-header">
      <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK HARD TIMES ***</div>
    </section>

    <h1>
      Hard Times and Reprinted Pieces
      <a class="citation pginternal" href="#footnote0">[0]</a>
    </h1>
    <h2>CONTENTS</h2>
    <table><tbody>
      <tr><td>CHAPTER I</td><td><span class="indexpageno">
        <a href="#page3" class="pginternal">3</a></span></td></tr>
      <tr><td>CHAPTER II</td><td><span class="indexpageno">
        <a href="#page4" class="pginternal">4</a></span></td></tr>
    </tbody></table>

    <h2><a id="page3"></a>BOOK THE FIRST</h2>
    <h3>CHAPTER I</h3>
    <p>First chapter paragraph.</p>
    <h3>CHAPTER II</h3>
    <p><a id="page4"></a>Second chapter paragraph.</p>

    <section class="pg-boilerplate pgfooter" id="pg-footer">
      <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK HARD TIMES ***</div>
    </section>
    </body>
    </html>
    """
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["BOOK THE FIRST", "CHAPTER I", "CHAPTER II"]
    assert "Hard Times and Reprinted Pieces [0" not in headings


def test_numeric_toc_links_to_heading_keep_story_sections_and_ignore_inline_links():
    html = _make_html("""
    <h2>CONTENTS</h2>
    <table><tbody>
      <tr><td>I.</td><td>—A SCANDAL IN BOHEMIA</td><td>
        <a href="#i" class="pginternal">3</a></td></tr>
      <tr><td>II.</td><td>—THE RED-HEADED LEAGUE</td><td>
        <a href="#ii" class="pginternal">29</a></td></tr>
    </tbody></table>
    <h2>ADVENTURES OF SHERLOCK HOLMES<br><a id="i"></a>
      <span class="ornate">Adventure I</span><br>A SCANDAL IN BOHEMIA</h2>
    <p>Story one text with an inline glossary link
      <a href="#term" class="pginternal">parallel</a>.</p>
    <p><a id="term"></a>Glossary marker.</p>
    <h2><a id="ii"></a><span class="ornate">Adventure II</span><br>THE RED-HEADED LEAGUE</h2>
    <p>Story two text.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == [
        "Adventure I A SCANDAL IN BOHEMIA",
        "Adventure II THE RED-HEADED LEAGUE",
    ]


def test_dense_chapter_index_paragraph_falls_back_to_heading_scan():
    html = _make_html("""
    <p>
      <a href="#preface" class="pginternal">PREFACE.</a>
      Chapter:
      <a href="#ch1" class="pginternal">I.,</a>
      <a href="#ch2" class="pginternal">II.,</a>
      <a href="#ch3" class="pginternal">III.</a>
    </p>
    <h2><a id="preface"></a>PREFACE</h2>
    <p>Preface paragraph.</p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter one paragraph.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Chapter two paragraph.</p>
    <h2><a id="ch3"></a>CHAPTER III</h2>
    <p>Chapter three paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["PREFACE", "CHAPTER I", "CHAPTER II", "CHAPTER III"]


def test_toc_refines_title_like_h3_subheads_inside_chapters():
    html = _make_html("""
    <table><tbody>
      <tr><td><a href="#intro" class="pginternal">THE INTRODUCTION</a></td></tr>
      <tr><td><a href="#part1" class="pginternal"><b>PART I. OF MAN</b></a></td></tr>
      <tr><td><a href="#ch1" class="pginternal">CHAPTER I. OF SENSE</a></td></tr>
      <tr><td><a href="#memory" class="pginternal">Memory</a></td></tr>
      <tr><td><a href="#dreams" class="pginternal">Dreams</a></td></tr>
      <tr><td><a href="#ch2" class="pginternal">CHAPTER II. OF IMAGINATION</a></td></tr>
    </tbody></table>
    <h2><a id="intro"></a>THE INTRODUCTION</h2>
    <p>Intro paragraph.</p>
    <h2><a id="part1"></a>PART I. OF MAN</h2>
    <h2><a id="ch1"></a>CHAPTER I. OF SENSE</h2>
    <p>Chapter one paragraph.</p>
    <h3><a id="memory"></a>Memory</h3>
    <p>Memory paragraph.</p>
    <h3><a id="dreams"></a>Dreams</h3>
    <p>Dreams paragraph.</p>
    <h2><a id="ch2"></a>CHAPTER II. OF IMAGINATION</h2>
    <p>Chapter two paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "THE INTRODUCTION",
        "PART I. OF MAN",
        "CHAPTER I. OF SENSE",
        "Memory",
        "Dreams",
        "CHAPTER II. OF IMAGINATION",
    ]

    memory_heading = next(h for h in headings if h.content == "Memory")
    dreams_heading = next(h for h in headings if h.content == "Dreams")
    assert memory_heading.div1 == "PART I. OF MAN"
    assert memory_heading.div2 == "CHAPTER I. OF SENSE"
    assert memory_heading.div3 == "Memory"
    assert dreams_heading.div1 == "PART I. OF MAN"
    assert dreams_heading.div2 == "CHAPTER I. OF SENSE"
    assert dreams_heading.div3 == "Dreams"

    memory_paragraph = next(p for p in paragraphs if p.content == "Memory paragraph.")
    dreams_paragraph = next(p for p in paragraphs if p.content == "Dreams paragraph.")
    assert memory_paragraph.div1 == "PART I. OF MAN"
    assert memory_paragraph.div2 == "CHAPTER I. OF SENSE"
    assert memory_paragraph.div3 == "Memory"
    assert dreams_paragraph.div1 == "PART I. OF MAN"
    assert dreams_paragraph.div2 == "CHAPTER I. OF SENSE"
    assert dreams_paragraph.div3 == "Dreams"


# ------------------------------------------------------------------
# Paragraph heading fallback
# ------------------------------------------------------------------


def test_paragraph_play_headings_split_act_and_scene_and_ignore_finis():
    html = _make_html("""
    <h1>The Tragedie of Hamlet</h1>
    <p>Actus Primus. Scoena Prima.</p>
    <p>Enter Barnardo and Francisco two Centinels.</p>
    <p>Scena Secunda.</p>
    <p>Enter Claudius, King of Denmarke.</p>
    <h4>FINIS.</h4>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == ["Actus Primus.", "Scoena Prima.", "Scena Secunda."]
    assert headings[0].div1 == "Actus Primus."
    assert headings[1].div1 == "Actus Primus."
    assert headings[1].div2 == "Scoena Prima."
    assert headings[2].div1 == "Actus Primus."
    assert headings[2].div2 == "Scena Secunda."
    assert all(h.content != "FINIS" for h in headings)
    assert paragraphs[0].div1 == "Actus Primus."
    assert paragraphs[0].div2 == "Scoena Prima."
    assert paragraphs[1].div1 == "Actus Primus."
    assert paragraphs[1].div2 == "Scena Secunda."


def test_paragraph_play_headings_reset_scene_hierarchy_on_new_act():
    html = _make_html("""
    <h1>The Tragedie of Macbeth</h1>
    <p>Actus Primus. Scoena Prima.</p>
    <p>Thunder and Lightning. Enter three Witches.</p>
    <p>Scena Secunda.</p>
    <p>Alarum within.</p>
    <p>Actus Secundus. Scena Prima.</p>
    <p>Enter Banquo, and Fleance with a Torch before him.</p>
    <h4>FINIS.</h4>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "Actus Primus.",
        "Scoena Prima.",
        "Scena Secunda.",
        "Actus Secundus.",
        "Scena Prima.",
    ]
    assert headings[3].div1 == "Actus Secundus."
    assert headings[3].div2 == ""
    assert headings[4].div1 == "Actus Secundus."
    assert headings[4].div2 == "Scena Prima."
    assert paragraphs[2].div1 == "Actus Secundus."
    assert paragraphs[2].div2 == "Scena Prima."


def test_paragraph_play_headings_do_not_extract_act_scene_from_prose():
    html = _make_html("""
    <h2>THE ASSEMBLY OF FOWLS</h2>
    <p>
      Quoted in Terence, "Eunuchus," act iv. scene v., but this is body prose,
      not a structural heading.
    </p>
    <h2>TROILUS AND CRESSIDA</h2>
    <p>Story paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["THE ASSEMBLY OF FOWLS", "TROILUS AND CRESSIDA"]


def test_title_like_toc_sections_keep_trailing_book_headings_and_skip_letter_markers():
    html = _make_html("""
    <table><tbody>
      <tr><td><a href="#assembly" class="pginternal">THE ASSEMBLY OF FOWLS</a></td></tr>
      <tr><td><a href="#troilus" class="pginternal">TROILUS AND CRESSIDA</a></td></tr>
      <tr><td><a href="#abc" class="pginternal">CHAUCER'S A. B. C.</a></td></tr>
      <tr><td><a href="#ballad" class="pginternal">A GOODLY BALLAD OF CHAUCER</a></td></tr>
    </tbody></table>
    <h2><a id="assembly"></a>THE ASSEMBLY OF FOWLS</h2>
    <p>Assembly paragraph.</p>
    <h2><a id="troilus"></a>TROILUS AND CRESSIDA</h2>
    <h3>THE FIRST BOOK.</h3>
    <p>First book paragraph.</p>
    <h3>THE SECOND BOOK.</h3>
    <p>Second book paragraph.</p>
    <h2><a id="abc"></a>CHAUCER'S A. B. C.</h2>
    <h4>C.</h4>
    <p>C paragraph.</p>
    <h4>D.</h4>
    <p>D paragraph.</p>
    <h2><a id="ballad"></a>A GOODLY BALLAD OF CHAUCER</h2>
    <p>Ballad paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == [
        "THE ASSEMBLY OF FOWLS",
        "TROILUS AND CRESSIDA",
        "THE FIRST BOOK.",
        "THE SECOND BOOK.",
        "CHAUCER'S A. B. C.",
        "A GOODLY BALLAD OF CHAUCER",
    ]


def test_heading_scan_skips_deep_rank_bare_numeral_subheads():
    html = _make_html("""
    <h2>PREFACE.</h2>
    <p>Preface paragraph.</p>
    <h2>FLORENCE AND DANTE.</h2>
    <p>Essay paragraph.</p>
    <h4>II.</h4>
    <h4>III.</h4>
    <h4>IV.</h4>
    <h4>VI.</h4>
    <h2>GIOTTO'S PORTRAIT OF DANTE.</h2>
    <p>Portrait paragraph.</p>
    <h2>CANTO I.</h2>
    <p>Canto paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == [
        "PREFACE.",
        "FLORENCE AND DANTE.",
        "GIOTTO'S PORTRAIT OF DANTE.",
        "CANTO I.",
    ]


def test_heading_scan_keeps_deep_rank_bare_numerals_when_they_are_real_sections():
    html = _make_html("""
    <h4>I</h4>
    <p>First section paragraph.</p>
    <h4>II</h4>
    <p>Second section paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["I", "II"]


def test_heading_scan_keeps_deep_rank_single_letter_sections_when_they_are_real():
    html = _make_html("""
    <h2>APPENDIX</h2>
    <p>Appendix opening paragraph.</p>
    <h4>A</h4>
    <p>Appendix A paragraph.</p>
    <h4>B</h4>
    <p>Appendix B paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["APPENDIX", "A", "B"]


def test_dialogue_speaker_headings_do_not_replace_book_structure():
    html = _make_html("""
    <h1>BOOK I</h1>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Book one opening paragraph.</p>
    <h5>GLAUCON</h5>
    <p>Another book one paragraph.</p>
    <h2>BOOK II</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <h5>ADEIMANTUS</h5>
    <p>Book two opening paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == ["BOOK I", "BOOK II"]
    assert all("SOCRATES" not in h.content for h in headings)
    assert all("GLAUCON" not in h.content for h in headings)
    assert paragraphs[0].div1 == "BOOK I"
    assert paragraphs[0].div2 == ""
    assert paragraphs[1].div1 == "BOOK I"
    assert paragraphs[1].div2 == ""
    assert paragraphs[2].div1 == "BOOK II"
    assert paragraphs[2].div2 == ""


def test_heading_scan_keeps_hyphenated_chapter_headings():
    html = _make_html("""
    <h1>A Theologico-Political Treatise</h1>
    <h3>CHAPTER VI. - OF MIRACLES.</h3>
    <p>Chapter six paragraph.</p>
    <h3>CHAPTER VII. - OF THE INTERPRETATION OF SCRIPTURE.</h3>
    <p>Chapter seven paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == [
        "CHAPTER VI. - OF MIRACLES.",
        "CHAPTER VII. - OF THE INTERPRETATION OF SCRIPTURE.",
    ]


def test_heading_scan_starts_from_prologues_and_skips_short_dramatic_cues():
    html = _make_html("""
    <h5>INTRODUCTORY NOTE</h5>
    <h1>PROLOGUE FOR THE THEATRE</h1>
    <h5>MANAGER</h5>
    <p>Manager paragraph.</p>
    <h1>PROLOGUE IN HEAVEN</h1>
    <h5>RAPHAEL</h5>
    <p>Raphael paragraph.</p>
    <h1>THE TRAGEDY OF FAUST</h1>
    <h5>DRAMATIS PERSONAE</h5>
    <h1>PART I</h1>
    <h5>NIGHT</h5>
    <h5>FAUST</h5>
    <p>Faust paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [h.content for h in headings] == [
        "PROLOGUE FOR THE THEATRE",
        "PROLOGUE IN HEAVEN",
        "THE TRAGEDY OF FAUST",
        "PART I",
    ]
    excluded = {"MANAGER", "RAPHAEL", "DRAMATIS PERSONAE", "NIGHT", "FAUST"}
    assert all(h.content not in excluded for h in headings)
    assert paragraphs[-1].div1 == "PART I"
    assert paragraphs[-1].div2 == ""


def test_heading_scan_resets_dramatic_context_after_non_dramatic_sections():
    html = _make_html("""
    <h2>ACT I</h2>
    <h3>SCENE I</h3>
    <p>Opening speech.</p>
    <h2>CHAPTER I</h2>
    <p>Chapter opening paragraph.</p>
    <h5>MEMORY</h5>
    <p>Memory paragraph.</p>
    <h5>DREAMS</h5>
    <p>Dreams paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["ACT I", "SCENE I", "CHAPTER I", "MEMORY", "DREAMS"]


def test_heading_scan_keeps_short_uppercase_prose_sections_outside_dramatic_context():
    html = _make_html("""
    <h2>CHAPTER I</h2>
    <p>Chapter opening paragraph.</p>
    <h5>MEMORY</h5>
    <p>Memory paragraph.</p>
    <h5>DREAMS</h5>
    <p>Dreams paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]

    assert headings == ["CHAPTER I", "MEMORY", "DREAMS"]


def test_heading_scan_uses_paragraph_play_headings_after_generic_title():
    html = _make_html("""
    <h2>HAMLET</h2>
    <p>ACT I</p>
    <p>SCENE I</p>
    <p>Opening speech.</p>
    <p>SCENE II</p>
    <p>Another speech.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert [heading.content for heading in headings] == ["ACT I", "SCENE I", "SCENE II"]
    assert paragraphs[0].div1 == "ACT I"
    assert paragraphs[0].div2 == "SCENE I"
    assert paragraphs[1].div1 == "ACT I"
    assert paragraphs[1].div2 == "SCENE II"


def test_heading_scan_starts_from_front_matter_before_shallower_chapters():
    html = _make_html("""
    <h3>ETYMOLOGY.</h3>
    <h3>ETYMOLOGY</h3>
    <p>Etymology paragraph.</p>
    <h3>EXTRACTS.</h3>
    <h3>EXTRACTS.</h3>
    <p>Extracts paragraph.</p>
    <h2>CHAPTER I.</h2>
    <p>Call me Ishmael.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert headings == ["ETYMOLOGY.", "EXTRACTS.", "CHAPTER I."]
    assert paragraphs[0].div1 == "ETYMOLOGY."
    assert paragraphs[1].div1 == "EXTRACTS."
    assert paragraphs[2].div1 == "CHAPTER I."


def test_singular_note_heading_is_preserved_as_a_section():
    html = _make_html("""
    <h2>CHAPTER I</h2>
    <p>Chapter paragraph.</p>
    <h2>NOTE</h2>
    <p>Closing note paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    note_paragraph = next(
        c for c in chunks if c.kind == "text" and c.content == "Closing note paragraph."
    )

    assert headings == ["CHAPTER I", "NOTE"]
    assert note_paragraph.div1 == "NOTE"


def test_toc_refinement_keeps_terminal_note_after_last_chapter():
    html = _make_html("""
    <table><tbody>
      <tr><td><a href="#ch1" class="pginternal">CHAPTER I</a></td></tr>
      <tr><td><a href="#ch2" class="pginternal">CHAPTER II</a></td></tr>
    </tbody></table>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter one paragraph.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Chapter two paragraph.</p>
    <h2>NOTE</h2>
    <p>Closing note paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    note_paragraph = next(
        c for c in chunks if c.kind == "text" and c.content == "Closing note paragraph."
    )

    assert headings == ["CHAPTER I", "CHAPTER II", "NOTE"]
    assert note_paragraph.div1 == "NOTE"


def test_toc_refinement_keeps_terminal_authors_endnotes_after_same_rank_chapters():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">CHAPTER I</a> - Of Prophecy.</p>
    <p><a href="#ch2" class="pginternal">CHAPTER II</a> - Of Miracles.</p>
    <p><a href="#endnotes" class="pginternal">Author's Endnotes to the Treatise.</a></p>
    <h3><a id="ch1"></a>CHAPTER I - Of Prophecy.</h3>
    <p>Chapter one paragraph.</p>
    <h3><a id="ch2"></a>CHAPTER II - Of Miracles.</h3>
    <p>Chapter two paragraph.</p>
    <h3><a id="endnotes"></a>Author's Endnotes to the Treatise.</h3>
    <p>Endnotes paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    endnotes_paragraph = next(
        c for c in chunks if c.kind == "text" and c.content == "Endnotes paragraph."
    )

    assert headings == [
        "CHAPTER I - Of Prophecy.",
        "CHAPTER II - Of Miracles.",
        "Author's Endnotes to the Treatise.",
    ]
    assert endnotes_paragraph.div1 == "Author's Endnotes to the Treatise."


def test_toc_refinement_keeps_leading_preface_before_first_toc_section():
    html = _make_html("""
    <h3>PREFACE</h3>
    <p>Preface paragraph.</p>
    <table><tbody>
      <tr><td><a href="#ch1" class="pginternal">CHAPTER I</a></td></tr>
      <tr><td><a href="#ch2" class="pginternal">CHAPTER II</a></td></tr>
    </tbody></table>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter one paragraph.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Chapter two paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c.content for c in chunks if c.kind == "heading"]
    preface_paragraph = next(
        c for c in chunks if c.kind == "text" and c.content == "Preface paragraph."
    )

    assert headings == ["PREFACE", "CHAPTER I", "CHAPTER II"]
    assert preface_paragraph.div1 == "PREFACE"


# ------------------------------------------------------------------
# Chunk kind coverage
# ------------------------------------------------------------------


def test_chunk_kinds():
    html = _make_html("""
    <p>Title page text for front matter.</p>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Content paragraph.</p>
    <p>FOOTNOTES</p>
    <p>1. A footnote.</p>
    """)
    chunks = chunk_html(html)
    kinds = {c.kind for c in chunks}
    assert kinds == {"heading", "text"}


# ------------------------------------------------------------------
# Leaf-div verse blocks (cf. PG 16328, Beowulf)
# ------------------------------------------------------------------


def test_leaf_div_verse_lines_captured_as_paragraphs():
    """Verse-line divs (<div class="l">) should be treated as paragraphs."""
    html = _make_html("""
    <p class="toc"><a href="#canto1" class="pginternal">I. THE LIFE AND DEATH OF SCYLD</a></p>
    <h2><a id="canto1"></a>I. THE LIFE AND DEATH OF SCYLD</h2>
    <div class="l">Lo! the Spear-Danes' glory through splendid achievements</div>
    <div class="l">The folk-Loss of former days, far and wide we have heard,</div>
    <p>A prose paragraph between verses.</p>
    <div class="l">How Scyld Scefing seized many mead-benches.</div>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert len(paragraphs) == 4
    assert any("Spear-Dane" in p.content for p in paragraphs)
    assert any("prose paragraph" in p.content for p in paragraphs)


def test_leaf_div_with_block_children_not_treated_as_paragraph():
    """Divs containing block children (p, div, etc.) are not leaf blocks."""
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <div><p>Nested paragraph inside a div.</p></div>
    <p>Standalone paragraph.</p>
    """)
    chunks = chunk_html(html)
    paragraphs = [c for c in chunks if c.kind == "text"]

    # Only the <p> tags should be captured, not the wrapper div.
    assert len(paragraphs) == 2
    assert paragraphs[0].content == "Nested paragraph inside a div."
    assert paragraphs[1].content == "Standalone paragraph."


# ------------------------------------------------------------------
# Bare Roman numeral heading merge (cf. PG 16328, Beowulf cantos)
# ------------------------------------------------------------------


def test_bare_roman_numeral_with_period_merges_with_subtitle():
    """Standalone 'I.' merges with the following descriptive title."""
    html = _make_html("""
    <h2><a id="c1"></a>I.</h2>
    <h2>THE LIFE AND DEATH OF SCYLD</h2>
    <p>First canto content.</p>
    <h2><a id="c2"></a>II.</h2>
    <h2>SCYLD'S BURIAL</h2>
    <p>Second canto content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 2
    assert headings[0].content == "I. THE LIFE AND DEATH OF SCYLD"
    assert headings[1].content == "II. SCYLD'S BURIAL"


# ------------------------------------------------------------------
# Degenerate title-block collapse (cf. PG 14304, Peter Rabbit)
# ------------------------------------------------------------------


def test_title_block_collapse_when_no_content_between_headings():
    """Multiple decorative title-page headings collapse to the last one."""
    html = _make_html("""
    <h2><a id="t1"></a>THE TALE OF</h2>
    <h2><a id="t2"></a>PETER RABBIT</h2>
    <p>Once upon a time there were four little rabbits.</p>
    <p>Their names were Flopsy, Mopsy, Cotton-tail, and Peter.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]

    assert len(headings) == 1
    assert headings[0].content == "PETER RABBIT"
    assert len(paragraphs) == 2


def test_title_block_not_collapsed_when_content_between():
    """Title headings with content paragraphs between them are real sections."""
    html = _make_html("""
    <h2><a id="t1"></a>INTRODUCTION</h2>
    <p>Some introductory text here.</p>
    <h2><a id="t2"></a>THE TALE OF PETER RABBIT</h2>
    <p>Once upon a time there were four little rabbits.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 2


# ------------------------------------------------------------------
# TOC heading rank normalization (cf. PG 3207, Leviathan Ch XLVII)
# ------------------------------------------------------------------


def test_toc_heading_rank_normalization_for_outlier():
    """One chapter at a different heading rank should be normalized to the mode.

    Modelled on PG 3207 (Leviathan) where Ch XLVII uses <h3> while all
    other chapters use <h2>.  Without normalization the outlier chapter
    would be incorrectly nested under its predecessor.
    """
    html = _make_html("""
    <p><a href="#ch1" class="pginternal"><b>CHAPTER I</b></a></p>
    <p><a href="#ch2" class="pginternal"><b>CHAPTER II</b></a></p>
    <p><a href="#ch3" class="pginternal"><b>CHAPTER III</b></a></p>
    <p><a href="#ch4" class="pginternal"><b>CHAPTER IV</b></a></p>

    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>First chapter.</p>
    <h2><a id="ch2"></a>CHAPTER II</h2>
    <p>Second chapter.</p>
    <h3><a id="ch3"></a>CHAPTER III</h3>
    <p>Third chapter — outlier heading rank.</p>
    <h2><a id="ch4"></a>CHAPTER IV</h2>
    <p>Fourth chapter.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    chapters = [h for h in headings if h.content.startswith("CHAPTER")]
    assert len(chapters) == 4
    # All chapters at div1 — the outlier h3 is normalized to match the h2 mode.
    assert all(h.div2 == "" for h in chapters)


# ------------------------------------------------------------------
# Chapter nesting under broad containers (cf. PG 135, Les Misérables)
# ------------------------------------------------------------------


def test_chapters_nested_under_broad_containers_at_same_rank():
    """When BOOK and CHAPTER share the same heading rank, chapters should
    nest one level deeper under the BOOK container.

    Modelled on PG 135 (Les Misérables) where VOLUME > BOOK > CHAPTER
    all appear in the TOC at the same <h3> rank.
    """
    html = _make_html("""
    <p><a href="#v1" class="pginternal"><b>VOLUME I</b></a></p>
    <p><a href="#b1" class="pginternal"><b>BOOK FIRST</b></a></p>
    <p><a href="#ch1" class="pginternal"><b>CHAPTER I</b></a></p>
    <p><a href="#ch2" class="pginternal"><b>CHAPTER II</b></a></p>
    <p><a href="#v2" class="pginternal"><b>VOLUME II</b></a></p>
    <p><a href="#b2" class="pginternal"><b>BOOK THIRD</b></a></p>
    <p><a href="#ch3" class="pginternal"><b>CHAPTER I</b></a></p>

    <h3><a id="v1"></a>VOLUME I</h3>
    <h3><a id="b1"></a>BOOK FIRST</h3>
    <h3><a id="ch1"></a>CHAPTER I</h3>
    <p>Chapter one content.</p>
    <h3><a id="ch2"></a>CHAPTER II</h3>
    <p>Chapter two content.</p>
    <h3><a id="v2"></a>VOLUME II</h3>
    <h3><a id="b2"></a>BOOK THIRD</h3>
    <h3><a id="ch3"></a>CHAPTER I</h3>
    <p>Volume two chapter one content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    ch1 = next(h for h in headings if h.content == "CHAPTER I" and h.div2 == "BOOK FIRST")
    assert ch1.div1 == "VOLUME I"
    assert ch1.div3 == "CHAPTER I"

    ch3 = next(h for h in headings if h.content == "CHAPTER I" and h.div2 == "BOOK THIRD")
    assert ch3.div1 == "VOLUME II"
    assert ch3.div3 == "CHAPTER I"


def test_broad_nesting_stops_at_standalone_structural_heading():
    """Standalone structural headings (EPILOGUE, etc.) are peers, not children."""
    html = _make_html("""
    <p><a href="#b1" class="pginternal"><b>BOOK ONE</b></a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p><a href="#ep" class="pginternal"><b>EPILOGUE</b></a></p>

    <h2><a id="b1"></a>BOOK ONE</h2>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content.</p>
    <h2><a id="ep"></a>EPILOGUE</h2>
    <p>Epilogue content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    epilogue = next(h for h in headings if h.content == "EPILOGUE")
    assert epilogue.div1 == "EPILOGUE"
    assert epilogue.div2 == ""


# ------------------------------------------------------------------
# Dialogue heading rejection (cf. PG 1203, The Dolly Dialogues)
# ------------------------------------------------------------------


def test_double_quote_heading_rejected_as_refinement_candidate():
    """Headings starting with quotation marks are dialogue, not structure."""
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">A REMINISCENCE</a></p>
    <p class="toc"><a href="#ch2" class="pginternal">A QUICK CHANGE</a></p>

    <h2><a id="ch1"></a>A REMINISCENCE</h2>
    <h3>\u201cCarter is a very good name.\u201d</h3>
    <p>First chapter content.</p>
    <h3>\u201cYes, it is,\u201d said Lady Doris.</h3>
    <p>More dialogue content.</p>
    <h2><a id="ch2"></a>A QUICK CHANGE</h2>
    <p>Second chapter content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    heading_texts = [h.content for h in headings]

    assert heading_texts == ["A REMINISCENCE", "A QUICK CHANGE"]
    assert all(h.div2 == "" for h in headings)


# ------------------------------------------------------------------
# Publication metadata exclusion (cf. PG 2700, Medical Essays)
# ------------------------------------------------------------------


def test_publication_metadata_headings_excluded():
    """'Printed in...', 'Published...', 'Reprinted...' are metadata, not structure."""
    html = _make_html("""
    <p class="toc"><a href="#e1" class="pginternal">PUERPERAL FEVER</a></p>
    <p class="toc"><a href="#e2" class="pginternal">COUNTER-CURRENTS</a></p>

    <h2><a id="e1"></a>PUERPERAL FEVER</h2>
    <h4>Printed in 1843; reprinted in 1855.</h4>
    <p>Essay content here.</p>
    <h2><a id="e2"></a>COUNTER-CURRENTS</h2>
    <h4>Published in 1861.</h4>
    <p>Second essay content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    heading_texts = [h.content for h in headings]

    assert heading_texts == ["PUERPERAL FEVER", "COUNTER-CURRENTS"]
    assert not any("Printed" in t or "Published" in t for t in heading_texts)


# ------------------------------------------------------------------
# Verse reference heading exclusion (cf. PG 30, KJV Psalms)
# ------------------------------------------------------------------


def test_verse_reference_headings_excluded():
    """Bible-style verse references (N:N:N) are not structural headings."""
    html = _make_html("""
    <p class="toc"><a href="#psalms" class="pginternal"><b>Book 19 Psalms</b></a></p>

    <h2><a id="psalms"></a>Book 19 Psalms</h2>
    <h3>19:070:001</h3>
    <p>Make haste, O God, to deliver me.</p>
    <h3>19:070:002</h3>
    <p>Let them be ashamed and confounded.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    heading_texts = [h.content for h in headings]

    assert heading_texts == ["Book 19 Psalms"]
    assert not any("19:070" in t for t in heading_texts)


# ------------------------------------------------------------------
# Sparse TOC bypass (cf. PG 1995, Dante's Inferno)
# ------------------------------------------------------------------


def test_sparse_toc_bypassed_for_richer_heading_scan():
    """When heading scan finds >3x more structure than a sparse TOC (<=5),
    prefer the heading scan."""
    toc_links = "".join(
        f'<p class="toc"><a href="#s{i}" class="pginternal">Section {i}</a></p>'
        for i in range(1, 3)  # only 2 TOC links
    )
    body_headings = "".join(
        f'<h2><a id="c{i}"></a>CANTO {i}.</h2>\n<p>Canto {i} content.</p>\n'
        for i in range(1, 11)  # 10 heading-scan sections
    )
    anchors = (
        '<h2><a id="s1"></a>Section 1</h2><p>Text.</p>'
        '<h2><a id="s2"></a>Section 2</h2><p>Text.</p>'
    )
    html = _make_html(f"{toc_links}\n{anchors}\n{body_headings}")
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    cantos = [h for h in headings if h.content.startswith("CANTO")]
    assert len(cantos) == 10


# ------------------------------------------------------------------
# Paragraph-text section fallback (cf. PG 3100, Chinese Classics)
# ------------------------------------------------------------------


def test_paragraph_section_fallback_extracts_chapters():
    """When no <h1>-<h6> headings exist, recover structure from <p> text."""
    html = _make_html("""
    <p>CHAPTER I. OF THE CHINESE CLASSICS GENERALLY.</p>
    <p>Introduction to the classics.</p>
    <p>More introductory text.</p>
    <p>SECTION I. BOOKS INCLUDED.</p>
    <p>Description of the books.</p>
    <p>CHAPTER II. OF THE CONFUCIAN ANALECTS.</p>
    <p>Analysis of the Analects.</p>
    <p>SECTION I. FORMATION OF THE TEXT.</p>
    <p>Text formation details.</p>
    <p>SECTION II. AUTHORSHIP AND PLAN.</p>
    <p>Authorship discussion.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    paragraphs = [c for c in chunks if c.kind == "text"]
    heading_texts = [h.content for h in headings]

    assert "CHAPTER I. OF THE CHINESE CLASSICS GENERALLY" in heading_texts[0]
    assert "CHAPTER II. OF THE CONFUCIAN ANALECTS" in heading_texts[2]

    chapters = [h for h in heading_texts if "CHAPTER" in h]
    sections = [h for h in heading_texts if h.startswith("SECTION")]
    assert len(chapters) == 2
    assert len(sections) == 3

    assert len(paragraphs) >= 5


def test_paragraph_section_fallback_not_used_when_headings_exist():
    """Paragraph fallback should not activate when real heading tags exist."""
    html = _make_html("""
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>CHAPTER II. This paragraph looks like a chapter but isn't.</p>
    <p>Regular content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I"


# ------------------------------------------------------------------
# Flat paragraph fallback (cf. PG 3100, Chinese Classics — no structure)
# ------------------------------------------------------------------


def test_flat_paragraph_fallback_when_no_structure_detected():
    """Documents with >=10 paragraphs but no structure emit flat text chunks."""
    paragraphs = "".join(
        f"<p>Paragraph {i} with enough text to not be filtered.</p>\n" for i in range(1, 15)
    )
    html = _make_html(paragraphs)
    chunks = chunk_html(html)

    assert len(chunks) >= 10
    assert all(c.kind == "text" for c in chunks)
    assert all(c.div1 == "" for c in chunks)


def test_flat_paragraph_fallback_not_triggered_for_few_paragraphs():
    """Documents with fewer than 10 paragraphs and no structure return empty."""
    paragraphs = "".join(f"<p>Short paragraph {i}.</p>\n" for i in range(1, 5))
    html = _make_html(paragraphs)
    chunks = chunk_html(html)

    assert chunks == []


# ------------------------------------------------------------------
# Paragraph heading truncation (cf. PG 3100, Chinese Classics — long headings)
# ------------------------------------------------------------------


def test_paragraph_section_heading_truncated_at_word_boundary():
    """Paragraph-derived headings exceeding 120 chars are truncated at a word boundary."""
    long_title = "CHAPTER I. " + "WORD " * 30  # ~160 chars
    html = _make_html(f"""
    <p>{long_title}</p>
    <p>Chapter content follows.</p>
    <p>CHAPTER II. SHORT TITLE</p>
    <p>Second chapter content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert len(headings) == 2
    assert len(headings[0].content) <= 120
    assert headings[0].content.startswith("CHAPTER I.")
    # Should not break mid-word.
    assert not headings[0].content.endswith("WOR")
    assert headings[1].content == "CHAPTER II. SHORT TITLE"


# ------------------------------------------------------------------
# Level cap at div4 (prevents overflow)
# ------------------------------------------------------------------


def test_section_levels_capped_at_four():
    """Deeply nested sections are capped at div4, not overflowing."""
    html = _make_html("""
    <p><a href="#v1" class="pginternal"><b>VOLUME I</b></a></p>
    <p><a href="#b1" class="pginternal"><b>BOOK I</b></a></p>
    <p><a href="#p1" class="pginternal">PART I</a></p>
    <p><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <p><a href="#s1" class="pginternal">Section 1</a></p>

    <h1><a id="v1"></a>VOLUME I</h1>
    <h2><a id="b1"></a>BOOK I</h2>
    <h3><a id="p1"></a>PART I</h3>
    <h4><a id="ch1"></a>CHAPTER I</h4>
    <h5><a id="s1"></a>Section 1</h5>
    <p>Deeply nested content.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    # div4 is the deepest allowed level — no IndexError.
    assert len(headings) >= 4
    deepest = headings[-1]
    assert deepest.div4 != "" or deepest.div3 != ""
