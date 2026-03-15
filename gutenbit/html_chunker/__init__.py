"""HTML chunker for Project Gutenberg books.

Uses the table of contents ``<a class="pginternal">`` links as the primary
structural map. When a TOC is present but coarse, body headings can refine it
without replacing TOC-derived hierarchy.

Corpus boundaries are defined by Gutenberg's explicit text delimiters:
``*** START OF (THE|THIS) PROJECT GUTENBERG EBOOK ... ***`` through
``*** END OF (THE|THIS) PROJECT GUTENBERG EBOOK ... ***``.

Each ``<p>`` element becomes its own chunk — no accumulation or merging.
"""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from gutenbit.html_chunker._common import (
    _HEADING_TAGS,
)
from gutenbit.html_chunker._scanning import (
    _paragraphs_in_range,
    _scan_document,
)
from gutenbit.html_chunker._sections import (
    _find_non_structural_boundary_after,
    _merge_adjacent_duplicate_sections,
    _nest_broad_subdivisions,
    _normalize_collection_titles,
    _parse_heading_sections,
    _parse_toc_sections,
    _promote_more_prominent_heading_runs,
    _refine_toc_sections,
)

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

HTML_PARSER_BACKEND = "lxml"
CHUNKER_VERSION = 28


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete block extracted from a book, labelled by kind.

    Structural divisions (div1–div4) are compacted so the shallowest
    heading level always fills div1 first. For a chapter-only book,
    chapters go in div1; for a book with BOOK + CHAPTER, BOOK fills
    div1 and CHAPTER fills div2.

    Kinds: ``"heading"``, ``"text"``
    """

    position: int
    div1: str
    div2: str
    div3: str
    div4: str
    content: str
    kind: str


__all__ = ["CHUNKER_VERSION", "Chunk", "HTML_PARSER_BACKEND", "chunk_html"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_html(html: str) -> list[Chunk]:
    """Split an HTML book into labelled chunks using TOC plus body heading cues.

    Each ``<p>`` element becomes its own chunk. Returns chunks in document order.
    """
    soup = BeautifulSoup(html, HTML_PARSER_BACKEND)
    doc_index = _scan_document(soup)
    bounds = doc_index.bounds
    tag_positions = doc_index.tag_positions

    # Build section list from TOC links and refine with body headings when the
    # TOC is a coarse but valid subsequence of the body structure.
    toc_sections = _parse_toc_sections(doc_index=doc_index, bounds=bounds)
    if toc_sections:
        heading_sections = _parse_heading_sections(
            doc_index=doc_index,
            bounds=bounds,
        )
        sections = _refine_toc_sections(
            toc_sections,
            heading_sections,
            doc_index=doc_index,
        )
    else:
        # Some Gutenberg editions expose page-number TOC links only.
        sections = _parse_heading_sections(doc_index=doc_index, bounds=bounds)
    if not sections:
        return []

    sections = _normalize_collection_titles(sections)
    sections = _nest_broad_subdivisions(sections)
    sections = _promote_more_prominent_heading_runs(sections)
    sections = _merge_adjacent_duplicate_sections(sections)

    # Compact levels so the shallowest level maps to div1.
    # e.g. chapter-only books (min_level=2) shift chapters to div1.
    min_level = min(s.level for s in sections)
    if min_level > 1:
        sections = [s._with_level(s.level - min_level + 1) for s in sections]

    chunks: list[Chunk] = []
    pos = 0
    divs = ["", "", "", ""]

    # Opening paragraphs before first section remain unsectioned prose.
    heading_texts = {s.heading_text.lower() for s in sections}
    first_heading = sections[0].body_anchor.find_parent(_HEADING_TAGS)
    stop_tag = first_heading or sections[0].body_anchor
    stop_pos = tag_positions.get(id(stop_tag))
    if stop_pos is not None:
        for text in _paragraphs_in_range(
            doc_index.paragraphs,
            doc_index.paragraph_positions,
            bounds.start_pos,
            stop_pos,
            heading_texts=heading_texts,
            min_length=20,
        ):
            chunks.append(Chunk(pos, "", "", "", "", text, "text"))
            pos += 1

    # Find a tail boundary: the first non-structural heading (e.g. FOOTNOTES,
    # NOTES) that appears after the last section.  This prevents endnotes from
    # being lumped into the last chapter.
    tail_anchor = _find_non_structural_boundary_after(
        sections[-1].body_anchor, doc_index=doc_index
    )

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

        # Paragraphs until next section (or tail boundary for the last section).
        heading_el = section.body_anchor.find_parent(_HEADING_TAGS)
        start_el = heading_el or section.body_anchor
        start_pos_val = tag_positions.get(id(start_el))

        stop_pos_val: int | None = None
        if i + 1 < len(sections):
            next_heading = sections[i + 1].body_anchor.find_parent(_HEADING_TAGS)
            next_stop = next_heading or sections[i + 1].body_anchor
            stop_pos_val = tag_positions.get(id(next_stop))
        elif tail_anchor is not None:
            tail_heading = tail_anchor.find_parent(_HEADING_TAGS)
            is_heading_tag = tail_heading and tail_heading.name in _HEADING_TAGS
            tail_stop = tail_heading if is_heading_tag else tail_anchor
            stop_pos_val = tag_positions.get(id(tail_stop))

        if start_pos_val is not None:
            for text in _paragraphs_in_range(
                doc_index.paragraphs,
                doc_index.paragraph_positions,
                start_pos_val,
                stop_pos_val,
            ):
                chunks.append(Chunk(pos, divs[0], divs[1], divs[2], divs[3], text, "text"))
                pos += 1

    return chunks
