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
        "Scene I. Hall",
        "SCENE II. Garden",
    ]
    assert headings[1].div1 == "ACT I"
    assert headings[2].div1 == "ACT I"
    assert headings[2].div2 == "Scene I. Hall"
    assert paragraphs[0].div1 == "ACT I"
    assert paragraphs[0].div2 == "Scene I. Hall"


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
        "Scene I. Hall",
        "PLAY TWO",
        "ACT I",
        "Scene I. Garden",
        "POEM THREE",
    ]
    assert headings[0].div1 == "PLAY ONE"
    assert headings[1].div1 == "PLAY ONE"
    assert headings[1].div2 == "ACT I"
    assert headings[2].div1 == "PLAY ONE"
    assert headings[2].div2 == "ACT I"
    assert headings[2].div3 == "Scene I. Hall"
    assert headings[3].div1 == "PLAY TWO"
    assert headings[4].div1 == "PLAY TWO"
    assert headings[4].div2 == "ACT I"
    assert headings[6].div1 == "POEM THREE"
    assert paragraphs[2].div1 == "POEM THREE"


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
    assert headings[0].content == "STAVE ONE"


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
    assert headings[0].content == "CHAPTER I"


def test_heading_from_img_alt():
    html = _make_html("""
    <p><a href="#ch1" class="pginternal">Chapter I</a></p>
    <h2><a id="ch1"></a><img alt="CHAPTER I." src="ch1.jpg"></h2>
    <p>Content paragraph.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]
    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I"


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
        "CHAPTER I OF WORDS OR LANGUAGE IN GENERAL",
    ]
    assert headings[0].div1 == "BOOK III OF WORDS"
    assert headings[1].div1 == "BOOK III OF WORDS"
    assert headings[1].div2 == "CHAPTER I OF WORDS OR LANGUAGE IN GENERAL"


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
        "CHAPTER I",
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


def test_heading_scan_keeps_dialogue_subheadings_after_book_start():
    html = _make_html("""
    <h2>BOOK I</h2>
    <h4>SOCRATES - GLAUCON</h4>
    <p>Dialogue paragraph one.</p>
    <h5>SOCRATES - THRASYMACHUS</h5>
    <p>Dialogue paragraph two.</p>
    """)
    chunks = chunk_html(html)
    headings = [c for c in chunks if c.kind == "heading"]

    assert [h.content for h in headings] == [
        "BOOK I SOCRATES - GLAUCON",
        "SOCRATES - THRASYMACHUS",
    ]


def test_heading_scan_keeps_repeated_dialogue_speakers_nested_under_each_book():
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
    headings = [c for c in chunk_html(html) if c.kind == "heading"]

    book_headings = [h for h in headings if h.content.startswith("BOOK")]
    assert [h.div1 for h in book_headings] == [
        "BOOK I SOCRATES - GLAUCON",
        "BOOK II SOCRATES - GLAUCON",
        "BOOK III SOCRATES - GLAUCON",
        "BOOK IV SOCRATES - GLAUCON",
    ]
    assert all(not h.div2 for h in book_headings)


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
