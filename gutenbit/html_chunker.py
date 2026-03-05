"""TOC-driven HTML chunker for Project Gutenberg books.

Uses the table of contents ``<a class="pginternal">`` links as the primary
structural map, rather than regex-based content heuristics.  Each TOC link
points to a body anchor inside an ``<h2>``–``<h6>`` tag, giving us section
boundaries and heading text directly from the markup.

Produces the same ``Chunk`` dataclass as ``chunker.py`` for compatibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

# Re-use the Chunk dataclass from the text chunker for compatibility.
from gutenbit.chunker import Chunk

# ---------------------------------------------------------------------------
# Heading hierarchy helpers
# ---------------------------------------------------------------------------

_BROAD_KEYWORDS = frozenset({"book", "part", "act", "epilogue", "volume"})

# Matches BOOK/PART/CHAPTER etc. headings for level classification fallback.
_HEADING_KEYWORD_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)"
    r"\.?\s",
    re.IGNORECASE,
)

_END_MATTER_RE = re.compile(
    r"^(?:FOOTNOTES?|APPENDIX|GLOSSARY|INDEX|END OF)\b",
    re.IGNORECASE,
)

_MIN_CHUNK_LEN = 50


@dataclass(frozen=True, slots=True)
class _Section:
    """A section parsed from the TOC."""

    anchor_id: str
    heading_text: str
    level: int  # 1 = broad (BOOK/PART), 2 = chapter, 3 = sub-chapter
    body_anchor: Tag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_html(html: str) -> list[Chunk]:
    """Split an HTML book into labelled chunks using the TOC as structural map.

    Returns chunks in document order, compatible with ``chunker.chunk_text``.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Strip PG boilerplate
    for section_id in ("pg-header", "pg-footer"):
        el = soup.find(id=section_id)
        if el:
            el.decompose()

    # 2. Build section list from TOC links
    sections = _parse_toc_sections(soup)
    if not sections:
        return []

    # 3. Build chunks
    chunks: list[Chunk] = []
    position = 0
    divs = ["", "", "", ""]  # [div1, div2, div3, div4]

    # --- Front matter: everything before first section anchor ---
    front_paragraphs = _paragraphs_before(soup, sections[0].body_anchor)
    for text in front_paragraphs:
        chunks.append(
            Chunk(
                position=position,
                div1="",
                div2="",
                div3="",
                div4="",
                content=text,
                kind="front_matter",
            )
        )
        position += 1

    # --- Body sections ---
    for i, section in enumerate(sections):
        # Update division tracking
        rank = section.level
        divs[rank - 1] = section.heading_text
        for lvl in range(rank, 4):
            divs[lvl] = ""

        # Heading chunk
        chunks.append(
            Chunk(
                position=position,
                div1=divs[0],
                div2=divs[1],
                div3=divs[2],
                div4=divs[3],
                content=section.heading_text,
                kind="heading",
            )
        )
        position += 1

        # Paragraphs until next section (or end of document)
        next_anchor = sections[i + 1].body_anchor if i + 1 < len(sections) else None
        paragraphs = _paragraphs_between(section.body_anchor, next_anchor)
        position = _emit_paragraphs(paragraphs, divs, chunks, position)

    return chunks


def _emit_paragraphs(
    paragraphs: list[str],
    divs: list[str],
    chunks: list[Chunk],
    position: int,
) -> int:
    """Accumulate paragraph texts into chunks, detecting end matter.

    Returns the updated *position* counter.
    """
    in_end_matter = False
    buffer: list[str] = []

    def flush() -> int:
        nonlocal in_end_matter
        nonlocal position
        if not buffer:
            return position
        content = "\n\n".join(buffer)
        kind = "end_matter" if in_end_matter else "paragraph"
        chunks.append(
            Chunk(
                position=position,
                div1=divs[0],
                div2=divs[1],
                div3=divs[2],
                div4=divs[3],
                content=content,
                kind=kind,
            )
        )
        position += 1
        buffer.clear()
        return position

    for text in paragraphs:
        if not in_end_matter and _END_MATTER_RE.match(text):
            position = flush()
            in_end_matter = True

        buffer.append(text)
        if in_end_matter or sum(len(b) for b in buffer) >= _MIN_CHUNK_LEN:
            position = flush()

    position = flush()
    return position


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_toc_sections(soup: BeautifulSoup) -> list[_Section]:
    """Extract section list from TOC ``pginternal`` links."""
    toc_links = soup.select("a.pginternal")
    sections: list[_Section] = []
    used_headings: set[int] = set()

    for link in toc_links:
        href = link.get("href", "")
        if not href.startswith("#"):
            continue
        anchor_id = href[1:]
        body_anchor = soup.find("a", id=anchor_id)
        if not body_anchor:
            continue

        # Find the associated heading element.  Two patterns exist:
        #   1. Anchor inside the heading:  <h2><a id="...">TEXT</a></h2>
        #   2. Anchor before the heading:  <p><a id="..."></a></p> ... <h2>TEXT</h2>
        heading_el = body_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
        if not heading_el:
            heading_el = _find_next_heading(body_anchor, used_headings)
        if not heading_el:
            continue
        if id(heading_el) in used_headings:
            continue
        used_headings.add(id(heading_el))

        heading_text = _extract_heading_text(heading_el)
        if not heading_text:
            continue

        # Determine hierarchy level
        is_bold = link.find("b") is not None
        level = _classify_level(heading_text, is_bold)

        sections.append(_Section(anchor_id, heading_text, level, body_anchor))

    return sections


def _find_next_heading(anchor: Tag, used_headings: set[int] | None = None) -> Tag | None:
    """Find the next major heading element after *anchor*, within a few elements.

    Only matches ``<h1>``–``<h3>`` tags (not ``<h4>``–``<h6>`` which are often
    used for illustration captions).  Skips headings already claimed by another
    anchor (tracked via *used_headings* set of element ids).
    """
    for el in anchor.find_all_next(limit=10):
        if isinstance(el, Tag) and el.name in ("h1", "h2", "h3"):
            if used_headings is not None and id(el) in used_headings:
                continue
            return el
    return None


def _extract_heading_text(heading_el: Tag) -> str:
    """Get clean heading text from an ``<h2>`` tag.

    Handles several Project Gutenberg HTML patterns:
    - Direct text nodes in the heading (most common)
    - Text inside ``<a>`` anchors (e.g. Christmas Carol ``<a id="...">STAVE ONE.</a>``)
    - Illustrated editions where heading text is in ``<img alt="...">``
    - Page-number spans (``<span class="pagenum">``) that should be ignored
    """
    # Remove page-number spans before extracting text
    heading_copy = BeautifulSoup(str(heading_el), "html.parser")
    for pagenum in heading_copy.select("span.pagenum"):
        pagenum.decompose()

    # Try direct text nodes first (handles P&P's image-caption h2 elements)
    root = heading_copy.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if not root:
        root = heading_copy

    direct_text = ""
    for child in root.children:
        if isinstance(child, str):
            direct_text += child

    cleaned = " ".join(direct_text.split()).strip()
    if cleaned:
        return cleaned

    # Try img alt text (illustrated editions use images for heading text)
    img = root.find("img", alt=True)
    if img:
        alt = " ".join(img["alt"].split()).strip()
        if alt:
            return alt

    # Fall back to full text content (headings with text inside child elements)
    return " ".join(root.get_text().split()).strip()


def _classify_level(heading_text: str, is_bold_in_toc: bool) -> int:
    """Determine structural level: 1=broad (BOOK/PART), 2=chapter, 3=sub-chapter."""
    # Bold in TOC reliably signals broader divisions
    if is_bold_in_toc:
        return 1

    # Fall back to keyword-based classification
    m = _HEADING_KEYWORD_RE.match(heading_text)
    if m:
        keyword = heading_text.split()[0].rstrip(".,:]").lower()
        if keyword in _BROAD_KEYWORDS:
            return 1
        if keyword == "section":
            return 3
        return 2  # CHAPTER, STAVE, SCENE

    # Default to chapter level
    return 2


def _paragraphs_before(soup: BeautifulSoup, stop_anchor: Tag) -> list[str]:
    """Collect paragraph text from body start up to *stop_anchor*."""
    body = soup.find("body")
    if not body:
        return []

    paragraphs: list[str] = []
    for el in body.descendants:
        if el is stop_anchor or _is_ancestor_of(el, stop_anchor):
            break
        if isinstance(el, Tag) and el.name == "p":
            text = " ".join(el.get_text().split()).strip()
            if text:
                paragraphs.append(text)

    return paragraphs


def _paragraphs_between(start_anchor: Tag, stop_anchor: Tag | None) -> list[str]:
    """Collect paragraph text between two body anchors.

    Walks siblings/parents from *start_anchor*'s heading element forward,
    collecting ``<p>`` text until *stop_anchor* is reached.
    """
    # Start from the heading element containing the anchor
    heading_el = start_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
    start_el = heading_el if heading_el else start_anchor

    paragraphs: list[str] = []
    for el in start_el.find_all_next():
        if stop_anchor and (el is stop_anchor or el is stop_anchor.parent):
            break
        # Stop if we hit a heading that contains the stop anchor
        if stop_anchor and isinstance(el, Tag):
            stop_heading = stop_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
            if stop_heading and el is stop_heading:
                break

        if isinstance(el, Tag) and el.name == "p":
            text = " ".join(el.get_text().split()).strip()
            if text:
                paragraphs.append(text)

    return paragraphs


def _is_ancestor_of(potential_ancestor: object, target: Tag) -> bool:
    """Check if *potential_ancestor* is an ancestor of *target*."""
    parent = target.parent
    while parent:
        if parent is potential_ancestor:
            return True
        parent = parent.parent
    return False
