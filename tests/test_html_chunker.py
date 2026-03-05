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
  <div id="pg-start-separator">*** START ***</div>
</section>
{body}
<section class="pg-boilerplate pgfooter" id="pg-footer">
  <p>End of the Project Gutenberg eBook</p>
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
    paragraphs = [c for c in chunks if c.kind == "paragraph"]

    assert len(headings) == 1
    assert headings[0].content == "CHAPTER I"
    assert headings[0].div2 == "CHAPTER I"
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
    paragraphs = [c for c in chunks if c.kind == "paragraph"]
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
    assert headings[0].div2 == "CHAPTER 1"


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
# PG boilerplate stripping
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


# ------------------------------------------------------------------
# Front matter
# ------------------------------------------------------------------


def test_front_matter_before_first_section():
    html = _make_html("""
    <p>Title Page: A Great Novel by Famous Author.</p>
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content.</p>
    """)
    chunks = chunk_html(html)
    front = [c for c in chunks if c.kind == "front_matter"]
    assert len(front) >= 1
    assert "Famous Author" in front[0].content


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


# ------------------------------------------------------------------
# End matter detection
# ------------------------------------------------------------------


def test_end_matter_detected():
    html = _make_html("""
    <p class="toc"><a href="#ch1" class="pginternal">CHAPTER I</a></p>
    <h2><a id="ch1"></a>CHAPTER I</h2>
    <p>Chapter content.</p>
    <p>FOOTNOTES</p>
    <p>1. This is a footnote.</p>
    """)
    chunks = chunk_html(html)
    end = [c for c in chunks if c.kind == "end_matter"]
    assert len(end) >= 1


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
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "front_matter" in kinds
    assert "end_matter" in kinds
