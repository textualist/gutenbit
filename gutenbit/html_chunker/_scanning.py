"""Document scanning, paragraph extraction, and position helpers."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

from gutenbit.html_chunker._common import (
    _END_DELIMITER_RE,
    _FRONT_MATTER_HEADINGS,
    _HEADING_TAG_SET,
    _NON_ALNUM_RE,
    _START_DELIMITER_RE,
    _clean_heading_text,
    _ContentBounds,
    _extract_heading_text,
    _front_matter_heading_key,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IndexedParagraph:
    """A paragraph tag with its pre-computed position and extracted text."""

    tag: Tag
    position: int
    text: str
    is_toc: bool


@dataclass(frozen=True, slots=True)
class _IndexedHeading:
    """A heading tag with its pre-computed position and cleaned text."""

    tag: Tag
    position: int
    text: str


@dataclass(frozen=True, slots=True)
class _DocumentIndex:
    """Precomputed tag and paragraph indices for one HTML document."""

    tag_positions: dict[int, int]
    subtree_end_positions: dict[int, int]
    paragraphs: list[_IndexedParagraph]
    paragraph_positions: list[int]
    headings: list[_IndexedHeading]
    heading_positions: list[int]
    toc_links: list[Tag]
    anchor_map: dict[str, Tag]
    bounds: _ContentBounds


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------


def _tag_position(tag: Tag, tag_positions: dict[int, int]) -> int | None:
    return tag_positions.get(id(tag))


def _tag_within_bounds(tag: Tag, tag_positions: dict[int, int], bounds: _ContentBounds) -> bool:
    position = _tag_position(tag, tag_positions)
    if position is None:
        return False
    return bounds.contains(position)


def _subtree_end_position(
    tag: Tag,
    tag_positions: dict[int, int],
    subtree_end_positions: dict[int, int],
) -> int | None:
    """Return the last document-order position covered by *tag*'s subtree."""
    return subtree_end_positions.get(id(tag), _tag_position(tag, tag_positions))


# ---------------------------------------------------------------------------
# Document scanning
# ---------------------------------------------------------------------------


def _scan_document(soup: BeautifulSoup) -> _DocumentIndex:
    """Single-pass DFS that builds all document indices at once.

    Replaces separate calls to ``_build_tag_and_subtree_positions``,
    ``_find_gutenberg_bounds``, ``_build_paragraph_index``, and
    ``_build_heading_index`` — avoiding 5+ redundant DOM traversals.
    """
    tag_positions: dict[int, int] = {}
    end_positions: dict[int, int] = {}
    all_heading_tags: list[Tag] = []
    toc_links: list[Tag] = []
    anchor_map: dict[str, Tag] = {}
    blocks: list[Tag] = []
    paragraphs_with_pagenum: set[int] = set()
    paragraphs_with_img: set[int] = set()
    paragraphs_with_pginternal: set[int] = set()
    start_marker_parent: Tag | None = None
    end_marker_parent: Tag | None = None
    pg_header: Tag | None = None
    pg_footer: Tag | None = None

    counter = 0
    in_body = False
    block_stack: list[Tag] = []

    stack: list[tuple[BeautifulSoup | Tag, bool]] = [(soup, False)]
    while stack:
        node, visited = stack.pop()
        if not visited:
            if isinstance(node, Tag) and node is not soup:
                tag_positions[id(node)] = counter
                counter += 1

                name = node.name

                if name == "body":
                    in_body = True

                if name in _HEADING_TAG_SET:
                    all_heading_tags.append(node)

                if name == "a":
                    aid = node.get("id")
                    if aid is not None:
                        anchor_map[str(aid)] = node
                    if "pginternal" in (node.get("class") or []):
                        toc_links.append(node)

                if in_body and name in ("p", "pre"):
                    blocks.append(node)
                    block_stack.append(node)

                if in_body and block_stack:
                    current_block = block_stack[-1]
                    if name == "span" and "pagenum" in (node.get("class") or []):
                        paragraphs_with_pagenum.add(id(current_block))
                    elif name == "img":
                        paragraphs_with_img.add(id(current_block))
                    elif (
                        name == "a"
                        and "pginternal" in (node.get("class") or [])
                        and current_block.name == "p"
                    ):
                        paragraphs_with_pginternal.add(id(current_block))

                nid = node.get("id")
                if nid == "pg-header":
                    pg_header = node
                elif nid == "pg-footer":
                    pg_footer = node

            stack.append((node, True))
            tag_children: list[Tag] = []
            for child in node.contents:
                if isinstance(child, Tag):
                    tag_children.append(child)
                elif isinstance(child, NavigableString):
                    if start_marker_parent is None or end_marker_parent is None:
                        text = str(child)
                        if start_marker_parent is None and _START_DELIMITER_RE.search(text):
                            p = child.parent
                            if isinstance(p, Tag):
                                start_marker_parent = p
                        if end_marker_parent is None and _END_DELIMITER_RE.search(text):
                            p = child.parent
                            if isinstance(p, Tag):
                                end_marker_parent = p
            for child in reversed(tag_children):
                stack.append((child, False))
            continue

        # Post-order visit.
        if isinstance(node, Tag) and node is not soup:
            if node.name == "body":
                in_body = False
            if in_body and node.name in ("p", "pre") and block_stack and block_stack[-1] is node:
                block_stack.pop()

        end_pos = tag_positions.get(id(node))
        for child in node.contents:
            if not isinstance(child, Tag):
                continue
            child_end = end_positions.get(id(child))
            if child_end is not None and (end_pos is None or child_end > end_pos):
                end_pos = child_end
        if end_pos is not None:
            end_positions[id(node)] = end_pos

    # Compute Gutenberg content bounds.
    start_pos = tag_positions.get(id(start_marker_parent)) if start_marker_parent else None
    end_pos_val = tag_positions.get(id(end_marker_parent)) if end_marker_parent else None

    def _subtree_end(tag: Tag | None) -> int | None:
        if tag is None:
            return None
        return end_positions.get(id(tag), tag_positions.get(id(tag)))

    header_end_pos = _subtree_end(pg_header)
    footer_start_pos = tag_positions.get(id(pg_footer)) if pg_footer else None

    if start_pos is None:
        start_pos = header_end_pos
    if end_pos_val is None:
        end_pos_val = footer_start_pos
    if start_pos is not None and end_pos_val is not None and end_pos_val <= start_pos:
        if footer_start_pos is not None and footer_start_pos > start_pos:
            end_pos_val = footer_start_pos
        else:
            end_pos_val = None

    bounds = _ContentBounds(start_pos=start_pos, end_pos=end_pos_val)

    # Build paragraph index (needs bounds for filtering).
    paragraphs: list[_IndexedParagraph] = []
    paragraph_positions: list[int] = []
    for block in blocks:
        pos = tag_positions.get(id(block))
        if pos is None or not bounds.contains(pos):
            continue
        if block.name == "pre":
            text = _extract_preformatted_text(block)
            is_toc = False
        else:
            has_pagenum = id(block) in paragraphs_with_pagenum
            has_img = id(block) in paragraphs_with_img
            text = _extract_paragraph_text(block, has_pagenum=has_pagenum, has_img=has_img)
            is_toc = _is_toc_paragraph(
                block, has_pginternal=id(block) in paragraphs_with_pginternal
            )
        if text:
            paragraphs.append(_IndexedParagraph(block, pos, text, is_toc))
            paragraph_positions.append(pos)

    # Build heading index.
    headings: list[_IndexedHeading] = []
    heading_positions: list[int] = []
    for heading in all_heading_tags:
        pos = tag_positions.get(id(heading))
        if pos is None:
            continue
        htext = _clean_heading_text(_extract_heading_text(heading))
        if htext:
            headings.append(_IndexedHeading(heading, pos, htext))
            heading_positions.append(pos)

    return _DocumentIndex(
        tag_positions=tag_positions,
        subtree_end_positions=end_positions,
        paragraphs=paragraphs,
        paragraph_positions=paragraph_positions,
        headings=headings,
        heading_positions=heading_positions,
        toc_links=toc_links,
        anchor_map=anchor_map,
        bounds=bounds,
    )


# ---------------------------------------------------------------------------
# Paragraph extraction
# ---------------------------------------------------------------------------


def _paragraphs_in_range(
    p_index: list[_IndexedParagraph],
    p_positions: list[int],
    start_pos: int | None,
    stop_pos: int | None,
    *,
    heading_texts: set[str] | None = None,
    min_length: int = 0,
) -> list[str]:
    """Return paragraph texts within (start_pos, stop_pos) using bisect.

    *start_pos* is exclusive (paragraphs must be strictly after it).
    *stop_pos* is exclusive (paragraphs must be strictly before it).
    """
    lo = bisect_right(p_positions, start_pos) if start_pos is not None else 0
    hi = bisect_left(p_positions, stop_pos) if stop_pos is not None else len(p_index)

    _heading_texts = heading_texts or set()
    paragraphs: list[str] = []
    for ip in p_index[lo:hi]:
        if ip.is_toc:
            continue
        if min_length and len(ip.text) < min_length:
            continue
        if _heading_texts:
            lowered = ip.text.lower()
            if lowered in _heading_texts or (
                _front_matter_heading_key(ip.text) in _FRONT_MATTER_HEADINGS
            ):
                continue
        paragraphs.append(ip.text)
    return paragraphs


def _extract_paragraph_text(
    paragraph: Tag,
    *,
    has_pagenum: bool | None = None,
    has_img: bool | None = None,
) -> str:
    """Get clean paragraph text, preserving drop-cap img ``alt`` text.

    Strips ``<span class="pagenum">`` page-number markers and replaces
    ``<img>`` tags with their ``alt`` text.

    When *has_pagenum* / *has_img* are provided, skip per-paragraph find()
    calls (already pre-indexed by the caller).
    """
    # Fast path: most paragraphs have no pagenum spans or images.
    if has_pagenum is None:
        has_pagenum = paragraph.find("span", class_="pagenum") is not None
    if has_img is None:
        has_img = paragraph.find("img") is not None

    if not has_pagenum and not has_img:
        return " ".join(paragraph.get_text().split()).strip()

    parts: list[str] = []

    def _append_text(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            if child.name == "span" and "pagenum" in {
                str(cls).lower() for cls in (child.get("class") or [])
            }:
                continue
            if child.name == "img":
                alt_value = child.get("alt")
                alt_text = " ".join(str(alt_value or "").split()).strip()
                if alt_text:
                    parts.append(alt_text)
                continue
            _append_text(child)

    _append_text(paragraph)
    return " ".join("".join(parts).split()).strip()


def _extract_preformatted_text(pre: Tag) -> str:
    """Return trimmed preformatted text while preserving line breaks."""
    lines = [line.rstrip() for line in pre.get_text("\n").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# TOC detection
# ---------------------------------------------------------------------------


def _container_residue_without_link_text(container: Tag) -> str:
    """Return container text after removing each internal-link label once."""
    residue = container.get_text()
    for link in container.find_all("a", class_="pginternal"):
        link_text = link.get_text()
        if link_text:
            residue = residue.replace(link_text, "", 1)
    return " ".join(residue.split()).strip()


def _is_toc_paragraph(paragraph: Tag, *, has_pginternal: bool | None = None) -> bool:
    """Return True for TOC/navigation paragraphs."""
    if has_pginternal is not None and not has_pginternal:
        return False
    links = paragraph.find_all("a", class_="pginternal")
    if not links:
        return False

    classes = {str(c).lower() for c in (paragraph.get("class") or [])}
    if "toc" in classes:
        return True

    # Check if removing pginternal link text leaves only punctuation/whitespace,
    # without re-parsing the paragraph.
    residue = _container_residue_without_link_text(paragraph)
    return _NON_ALNUM_RE.sub("", residue) == ""


def _is_dense_chapter_index_paragraph(paragraph: Tag) -> bool:
    """Return True for single-line chapter indexes like ``Chapter: I., II., ...``."""
    links = paragraph.find_all("a", class_="pginternal")
    if len(links) < 3:
        return False
    text = " ".join(paragraph.get_text(" ", strip=True).split()).lower()
    return "chapter:" in text or "chapters:" in text
