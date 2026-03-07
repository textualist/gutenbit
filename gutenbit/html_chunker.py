"""HTML chunker for Project Gutenberg books.

Uses the table of contents ``<a class="pginternal">`` links as the structural
map. Each TOC link points to a body anchor inside an ``<h2>``–``<h3>`` tag,
giving section boundaries and heading text directly from the markup.

Corpus boundaries are defined by Gutenberg's explicit text delimiters:
``*** START OF THE PROJECT GUTENBERG EBOOK ... ***`` through
``*** END OF THE PROJECT GUTENBERG EBOOK ... ***``.

Each ``<p>`` element becomes its own chunk — no accumulation or merging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete block extracted from a book, labelled by kind.

    Structural divisions (div1–div4) are compacted so the shallowest
    heading level always fills div1 first. For a chapter-only book,
    chapters go in div1; for a book with BOOK + CHAPTER, BOOK fills
    div1 and CHAPTER fills div2.

    Kinds: ``"heading"``, ``"paragraph"``
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
CHUNKER_VERSION = 3

_HEADING_KEYWORD_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)\.?\s",
    re.IGNORECASE,
)
_START_DELIMITER_RE = re.compile(
    r"\*\*\*\s*START OF THE PROJECT GUTENBERG EBOOK\b",
    re.IGNORECASE,
)
_END_DELIMITER_RE = re.compile(
    r"\*\*\*\s*END OF THE PROJECT GUTENBERG EBOOK\b",
    re.IGNORECASE,
)
_HEADING_CITATION_SUFFIX_RE = re.compile(r"\s*\[\d+\]\s*$")
_NUMERIC_LINK_TEXT_RE = re.compile(r"^\[?\d+\]?$")
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(frozen=True, slots=True)
class _Section:
    """A section parsed from the TOC."""

    anchor_id: str
    heading_text: str
    level: int  # 1 = broad (BOOK/PART), 2 = chapter, 3 = sub-chapter
    body_anchor: Tag


@dataclass(frozen=True, slots=True)
class _ContentBounds:
    """Document-order bounds for in-book content."""

    start_pos: int | None = None
    end_pos: int | None = None

    def contains(self, position: int) -> bool:
        """Return True when *position* lies within in-book boundaries."""
        if self.start_pos is not None and position <= self.start_pos:
            return False
        return not (self.end_pos is not None and position >= self.end_pos)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_html(html: str) -> list[Chunk]:
    """Split an HTML book into labelled chunks using the TOC as structural map.

    Each ``<p>`` element becomes its own chunk. Returns chunks in document order.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag_positions = _tag_positions(soup)
    bounds = _find_gutenberg_bounds(soup, tag_positions)

    # Build section list from TOC links.
    sections = _parse_toc_sections(soup, tag_positions=tag_positions, bounds=bounds)
    if not sections:
        # Some Gutenberg editions expose page-number TOC links only.
        sections = _parse_heading_sections(soup, tag_positions=tag_positions, bounds=bounds)
    if not sections:
        return []

    # Compact levels so the shallowest level maps to div1.
    # e.g. chapter-only books (min_level=2) shift chapters to div1.
    min_level = min(s.level for s in sections)
    if min_level > 1:
        sections = [
            _Section(s.anchor_id, s.heading_text, s.level - min_level + 1, s.body_anchor)
            for s in sections
        ]

    chunks: list[Chunk] = []
    pos = 0
    divs = ["", "", "", ""]

    # Opening paragraphs before first section remain unsectioned prose.
    for text in _paragraphs_before(
        soup,
        sections[0].body_anchor,
        tag_positions=tag_positions,
        bounds=bounds,
    ):
        chunks.append(Chunk(pos, "", "", "", "", text, "paragraph"))
        pos += 1

    # Body sections.
    for i, section in enumerate(sections):
        # Update division tracking.
        divs[section.level - 1] = section.heading_text
        for lvl in range(section.level, 4):
            divs[lvl] = ""

        # Heading chunk.
        chunks.append(
            Chunk(pos, divs[0], divs[1], divs[2], divs[3], section.heading_text, "heading")
        )
        pos += 1

        # Paragraphs until next section.
        next_anchor = sections[i + 1].body_anchor if i + 1 < len(sections) else None
        for text in _paragraphs_between(
            section.body_anchor,
            next_anchor,
            tag_positions=tag_positions,
            bounds=bounds,
        ):
            chunks.append(Chunk(pos, divs[0], divs[1], divs[2], divs[3], text, "paragraph"))
            pos += 1

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_toc_sections(
    soup: BeautifulSoup,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> list[_Section]:
    """Extract section list from TOC ``pginternal`` links."""
    toc_links = soup.select("a.pginternal")
    sections: list[_Section] = []
    used_headings: set[int] = set()

    # Index all anchors by id to avoid O(n) soup.find per TOC link.
    anchor_map: dict[str, Tag] = {str(a["id"]): a for a in soup.find_all("a", id=True)}

    for link in toc_links:
        if not _tag_within_bounds(link, tag_positions, bounds):
            continue
        if not _is_structural_toc_link(link):
            continue
        href = link.get("href", "")
        if not href.startswith("#"):
            continue
        anchor_id = href[1:]
        body_anchor = anchor_map.get(str(anchor_id))
        if not body_anchor or not _tag_within_bounds(body_anchor, tag_positions, bounds):
            continue

        # Skip page-number anchors (e.g. illustrated editions use
        # <span class="pagenum"><a id="page_1">) — these are not sections.
        if body_anchor.find_parent("span", class_="pagenum"):
            continue

        # Find the associated heading element.
        heading_el = body_anchor.find_parent(_HEADING_TAGS)
        if heading_el and not _tag_within_bounds(heading_el, tag_positions, bounds):
            heading_el = None
        if not heading_el:
            heading_el = _find_next_heading(
                body_anchor,
                used_headings,
                tag_positions=tag_positions,
                bounds=bounds,
            )
        if not heading_el or id(heading_el) in used_headings:
            continue
        used_headings.add(id(heading_el))

        heading_text = _clean_heading_text(_extract_heading_text(heading_el))
        if not heading_text:
            continue

        is_bold = link.find("b") is not None
        level = _classify_level(heading_text, is_bold)
        sections.append(_Section(anchor_id, heading_text, level, body_anchor))

    sections.sort(key=lambda section: tag_positions.get(id(section.body_anchor), float("inf")))
    return sections


def _parse_heading_sections(
    soup: BeautifulSoup,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> list[_Section]:
    """Fallback section extraction directly from body headings.

    Used when TOC links don't point at structural anchors (e.g., page-number
    links only). We start from the first heading that looks structural.
    """
    heading_rows: list[tuple[Tag, str]] = []
    for heading in soup.find_all(_HEADING_TAGS):
        if not _tag_within_bounds(heading, tag_positions, bounds):
            continue
        heading_text = _clean_heading_text(_extract_heading_text(heading))
        if not heading_text:
            continue
        heading_rows.append((heading, heading_text))

    if not heading_rows:
        return []

    start_idx = 0
    for idx, (_heading, heading_text) in enumerate(heading_rows):
        if _HEADING_KEYWORD_RE.match(heading_text):
            start_idx = idx
            break

    sections: list[_Section] = []
    for heading, heading_text in heading_rows[start_idx:]:
        level = _classify_level(heading_text, False)
        anchor = heading.find("a", id=True) or heading
        anchor_id = str(anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, anchor))

    return sections


def _find_next_heading(
    anchor: Tag,
    used_headings: set[int] | None = None,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> Tag | None:
    """Find the next ``<h1>``–``<h3>`` heading after *anchor*."""
    for el in anchor.find_all_next(limit=10):
        if isinstance(el, Tag) and el.name in ("h1", "h2", "h3"):
            if used_headings is not None and id(el) in used_headings:
                continue
            if not _tag_within_bounds(el, tag_positions, bounds):
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

    root = heading_copy.find(_HEADING_TAGS) or heading_copy

    # Try direct text nodes first (only accept if it has real words,
    # not just stray punctuation between <span> elements).
    direct_text = "".join(child for child in root.children if isinstance(child, str))
    cleaned = " ".join(direct_text.split()).strip()
    if cleaned and re.search(r"[A-Za-z0-9]", cleaned):
        return cleaned

    # Try img alt text (illustrated editions).
    img = root.find("img", alt=True)
    if img:
        alt = " ".join(img["alt"].split()).strip()
        if alt:
            return alt

    # Fall back to full text content.
    return " ".join(root.get_text().split()).strip()


def _clean_heading_text(heading_text: str) -> str:
    """Normalize heading text and strip trailing citation counters."""
    text = " ".join(heading_text.split()).strip()
    text = _HEADING_CITATION_SUFFIX_RE.sub("", text)
    return text.rstrip(" .,;:])")


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


def _paragraphs_before(
    soup: BeautifulSoup,
    stop_anchor: Tag,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> list[str]:
    """Collect paragraph text from body start up to *stop_anchor*."""
    body = soup.find("body")
    if not body:
        return []
    stop_heading = stop_anchor.find_parent(_HEADING_TAGS)
    stop_tag = stop_heading or stop_anchor
    stop_pos = _tag_position(stop_tag, tag_positions)
    if stop_pos is None:
        return []

    paragraphs: list[str] = []
    for paragraph in body.find_all("p"):
        paragraph_pos = _tag_position(paragraph, tag_positions)
        if paragraph_pos is None:
            continue
        if paragraph_pos >= stop_pos:
            break
        if not bounds.contains(paragraph_pos):
            continue
        if _is_toc_paragraph(paragraph):
            continue
        text = _extract_paragraph_text(paragraph)
        if text:
            paragraphs.append(text)
    return paragraphs


def _paragraphs_between(
    start_anchor: Tag,
    stop_anchor: Tag | None,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> list[str]:
    """Collect paragraph text between two body anchors."""
    heading_el = start_anchor.find_parent(_HEADING_TAGS)
    start_el = heading_el or start_anchor
    start_pos = _tag_position(start_el, tag_positions)
    if start_pos is None:
        return []

    stop_pos: int | None = None
    if stop_anchor:
        stop_heading = stop_anchor.find_parent(_HEADING_TAGS)
        stop_tag = stop_heading or stop_anchor
        stop_pos = _tag_position(stop_tag, tag_positions)

    paragraphs: list[str] = []
    for paragraph in start_el.find_all_next("p"):
        paragraph_pos = _tag_position(paragraph, tag_positions)
        if paragraph_pos is None:
            continue
        if paragraph_pos <= start_pos:
            continue
        if stop_pos is not None and paragraph_pos >= stop_pos:
            break
        if bounds.end_pos is not None and paragraph_pos >= bounds.end_pos:
            break
        if not bounds.contains(paragraph_pos):
            continue
        if _is_toc_paragraph(paragraph):
            continue
        text = _extract_paragraph_text(paragraph)
        if text:
            paragraphs.append(text)
    return paragraphs


def _extract_paragraph_text(paragraph: Tag) -> str:
    """Get clean paragraph text, preserving drop-cap img ``alt`` text."""
    paragraph_copy = BeautifulSoup(str(paragraph), "html.parser").find("p")
    if paragraph_copy is None:
        return ""

    for img in paragraph_copy.find_all("img"):
        alt_value = img.get("alt")
        alt_text = " ".join(str(alt_value or "").split()).strip()
        if alt_text:
            img.replace_with(NavigableString(alt_text))
        else:
            img.decompose()

    return " ".join(paragraph_copy.get_text().split()).strip()


def _is_toc_paragraph(paragraph: Tag) -> bool:
    """Return True for TOC/navigation paragraphs."""
    if paragraph.find("a", class_="pginternal") is None:
        return False

    classes = {str(c).lower() for c in (paragraph.get("class") or [])}
    if "toc" in classes:
        return True

    paragraph_copy = BeautifulSoup(str(paragraph), "html.parser").find("p")
    if paragraph_copy is None:
        return False
    for anchor in paragraph_copy.find_all("a", class_="pginternal"):
        anchor.decompose()

    residue = " ".join(paragraph_copy.get_text().split()).strip()
    return re.sub(r"[^A-Za-z0-9]+", "", residue) == ""


def _is_structural_toc_link(link: Tag) -> bool:
    """Return True for TOC links that can map to actual section headings."""
    link_classes = {str(cls).lower() for cls in (link.get("class") or [])}
    if "citation" in link_classes:
        return False

    href = str(link.get("href", ""))
    if href.startswith("#"):
        target_id = href[1:].lower()
        if target_id.startswith(("page", "footnote", "citation")):
            return False

    if link.find_parent("span", class_="indexpageno"):
        return False
    if link.find_parent("span", class_="pagenum"):
        return False

    link_text = " ".join(link.get_text().split()).strip()
    if not link_text:
        return False
    return not _NUMERIC_LINK_TEXT_RE.fullmatch(link_text)


def _tag_positions(soup: BeautifulSoup) -> dict[int, int]:
    """Return document-order index for each HTML tag in the document."""
    return {id(tag): idx for idx, tag in enumerate(soup.find_all(True))}


def _tag_position(tag: Tag, tag_positions: dict[int, int]) -> int | None:
    return tag_positions.get(id(tag))


def _tag_within_bounds(tag: Tag, tag_positions: dict[int, int], bounds: _ContentBounds) -> bool:
    position = _tag_position(tag, tag_positions)
    if position is None:
        return False
    return bounds.contains(position)


def _find_gutenberg_bounds(soup: BeautifulSoup, tag_positions: dict[int, int]) -> _ContentBounds:
    """Locate START/END delimiter bounds in document order."""

    def _find_marker_parent(marker_re: re.Pattern[str]) -> Tag | None:
        marker_text = soup.find(
            string=lambda text: isinstance(text, str)
            and marker_re.search(" ".join(text.split())) is not None
        )
        if marker_text is None:
            return None
        return marker_text.parent if isinstance(marker_text.parent, Tag) else None

    start_parent = _find_marker_parent(_START_DELIMITER_RE)
    end_parent = _find_marker_parent(_END_DELIMITER_RE)
    start_pos = _tag_position(start_parent, tag_positions) if start_parent else None
    end_pos = _tag_position(end_parent, tag_positions) if end_parent else None

    if start_pos is not None and end_pos is not None and end_pos <= start_pos:
        return _ContentBounds()
    return _ContentBounds(start_pos=start_pos, end_pos=end_pos)
