"""HTML chunker for Project Gutenberg books.

Uses the table of contents ``<a class="pginternal">`` links as the structural
map.  Each TOC link points to a body anchor inside an ``<h2>``–``<h3>`` tag,
giving section boundaries and heading text directly from the markup.

Each ``<p>`` element becomes its own chunk — no accumulation or merging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete block extracted from a book, labelled by kind.

    Structural divisions (div1–div4):
    - div1 — broadest: BOOK, PART, ACT, VOLUME
    - div2 — chapter-level: CHAPTER, STAVE, SCENE
    - div3 — sub-chapter: SECTION
    - div4 — reserved for deeper nesting

    Kinds: ``"front_matter"``, ``"heading"``, ``"paragraph"``, ``"end_matter"``
    """

    position: int
    div1: str
    div2: str
    div3: str
    div4: str
    content: str
    kind: str


# ---------------------------------------------------------------------------
# Heading hierarchy helpers
# ---------------------------------------------------------------------------

_BROAD_KEYWORDS = frozenset({"book", "part", "act", "epilogue", "volume"})

_HEADING_KEYWORD_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)\.?\s",
    re.IGNORECASE,
)

_END_MATTER_RE = re.compile(
    r"^(?:FOOTNOTES?|APPENDIX|GLOSSARY|INDEX|END OF)\b",
    re.IGNORECASE,
)


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

    Each ``<p>`` element becomes its own chunk.  Returns chunks in document order.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strip PG boilerplate
    for section_id in ("pg-header", "pg-footer"):
        el = soup.find(id=section_id)
        if el:
            el.decompose()

    # Build section list from TOC links
    sections = _parse_toc_sections(soup)
    if not sections:
        return []

    chunks: list[Chunk] = []
    pos = 0
    divs = ["", "", "", ""]

    # Front matter: everything before first section anchor
    for text in _paragraphs_before(soup, sections[0].body_anchor):
        chunks.append(Chunk(pos, "", "", "", "", text, "front_matter"))
        pos += 1

    # Body sections
    for i, section in enumerate(sections):
        # Update division tracking
        divs[section.level - 1] = section.heading_text
        for lvl in range(section.level, 4):
            divs[lvl] = ""

        # Heading chunk
        chunks.append(
            Chunk(pos, divs[0], divs[1], divs[2], divs[3], section.heading_text, "heading")
        )
        pos += 1

        # Paragraphs until next section
        next_anchor = sections[i + 1].body_anchor if i + 1 < len(sections) else None
        in_end_matter = False
        for text in _paragraphs_between(section.body_anchor, next_anchor):
            if not in_end_matter and _END_MATTER_RE.match(text):
                in_end_matter = True
            kind = "end_matter" if in_end_matter else "paragraph"
            chunks.append(Chunk(pos, divs[0], divs[1], divs[2], divs[3], text, kind))
            pos += 1

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_toc_sections(soup: BeautifulSoup) -> list[_Section]:
    """Extract section list from TOC ``pginternal`` links."""
    toc_links = soup.select("a.pginternal")
    sections: list[_Section] = []
    used_headings: set[int] = set()

    # Index all anchors by id to avoid O(n) soup.find per TOC link.
    anchor_map: dict[str, Tag] = {str(a["id"]): a for a in soup.find_all("a", id=True)}

    for link in toc_links:
        href = link.get("href", "")
        if not href.startswith("#"):
            continue
        anchor_id = href[1:]
        body_anchor = anchor_map.get(str(anchor_id))
        if not body_anchor:
            continue

        # Skip page-number anchors (e.g. illustrated editions use
        # <span class="pagenum"><a id="page_1">) — these are not sections.
        if body_anchor.find_parent("span", class_="pagenum"):
            continue

        # Find the associated heading element.
        heading_el = body_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
        if not heading_el:
            heading_el = _find_next_heading(body_anchor, used_headings)
        if not heading_el or id(heading_el) in used_headings:
            continue
        used_headings.add(id(heading_el))

        heading_text = _extract_heading_text(heading_el)
        if not heading_text:
            continue

        is_bold = link.find("b") is not None
        level = _classify_level(heading_text, is_bold)
        sections.append(_Section(anchor_id, heading_text, level, body_anchor))

    return sections


def _find_next_heading(anchor: Tag, used_headings: set[int] | None = None) -> Tag | None:
    """Find the next ``<h1>``–``<h3>`` heading after *anchor*."""
    for el in anchor.find_all_next(limit=10):
        if isinstance(el, Tag) and el.name in ("h1", "h2", "h3"):
            if used_headings is not None and id(el) in used_headings:
                continue
            return el
    return None


def _extract_heading_text(heading_el: Tag) -> str:
    """Get clean heading text from a heading tag.

    Handles: direct text, ``<a>`` anchors, ``<img alt="...">``,
    and strips ``<span class="pagenum">`` elements.
    """
    heading_copy = BeautifulSoup(str(heading_el), "html.parser")
    for pagenum in heading_copy.select("span.pagenum"):
        pagenum.decompose()

    root = heading_copy.find(["h1", "h2", "h3", "h4", "h5", "h6"]) or heading_copy

    # Try direct text nodes first
    direct_text = "".join(child for child in root.children if isinstance(child, str))
    cleaned = " ".join(direct_text.split()).strip()
    if cleaned:
        return cleaned

    # Try img alt text (illustrated editions)
    img = root.find("img", alt=True)
    if img:
        alt = " ".join(img["alt"].split()).strip()
        if alt:
            return alt

    # Fall back to full text content
    return " ".join(root.get_text().split()).strip()


def _classify_level(heading_text: str, is_bold_in_toc: bool) -> int:
    """Determine structural level: 1=broad (BOOK/PART), 2=chapter, 3=sub-chapter."""
    if is_bold_in_toc:
        return 1
    m = _HEADING_KEYWORD_RE.match(heading_text)
    if m:
        keyword = heading_text.split()[0].rstrip(".,:]").lower()
        if keyword in _BROAD_KEYWORDS:
            return 1
        if keyword == "section":
            return 3
        return 2
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
    """Collect paragraph text between two body anchors."""
    heading_el = start_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
    start_el = heading_el or start_anchor
    stop_heading = (
        stop_anchor.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"]) if stop_anchor else None
    )

    paragraphs: list[str] = []
    for el in start_el.find_all_next():
        if stop_anchor and (el is stop_anchor or el is stop_anchor.parent):
            break
        if stop_heading and isinstance(el, Tag) and el is stop_heading:
            break
        if isinstance(el, Tag) and el.name == "p":
            text = " ".join(el.get_text().split()).strip()
            if text:
                paragraphs.append(text)
    return paragraphs


def _is_ancestor_of(potential_ancestor: object, target: Tag) -> bool:
    parent = target.parent
    while parent:
        if parent is potential_ancestor:
            return True
        parent = parent.parent
    return False
