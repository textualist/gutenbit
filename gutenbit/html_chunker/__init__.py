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

from bs4 import BeautifulSoup, Tag

from gutenbit.html_chunker._common import (
    _HEADING_TAGS,
)
from gutenbit.html_chunker._scanning import (
    _container_residue_cache,  # cleared per-parse (keyed by id())
    _is_toc_paragraph_cache,  # cleared per-parse (keyed by id())
    _paragraphs_in_range,
    _scan_document,
)
from gutenbit.html_chunker._toc import _toc_context_cache  # cleared per-parse (keyed by id())
from gutenbit.html_chunker._sections import (
    _equalize_orphan_level_gap,
    _find_non_structural_boundary_after,
    _flatten_single_work_title_wrapper,
    _merge_adjacent_duplicate_sections,
    _merge_chapter_description_paragraphs,
    _merge_chapter_subtitle_sections,
    _nest_broad_subdivisions,
    _nest_chapters_under_broad_containers,
    _normalize_collection_titles,
    _normalize_toc_heading_ranks,
    _parse_heading_sections,
    _parse_paragraph_sections,
    _parse_toc_sections,
    _promote_more_prominent_heading_runs,
    _refine_toc_sections,
    _respect_heading_rank_nesting,
)

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

HTML_PARSER_BACKEND = "lxml"
CHUNKER_VERSION = 34


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
    # Clear per-parse caches keyed by id() (not valid across soup instances).
    _container_residue_cache.clear()
    _is_toc_paragraph_cache.clear()
    _toc_context_cache.clear()

    soup = BeautifulSoup(html, HTML_PARSER_BACKEND)
    doc_index = _scan_document(soup)
    tag_positions = doc_index.tag_positions

    # Build section list from TOC links and refine with body headings when the
    # TOC is a coarse but valid subsequence of the body structure.
    toc_sections = _parse_toc_sections(doc_index=doc_index)
    # Collect ALL TOC-linked anchor IDs (not just those that pass section
    # parsing) so _merge_chapter_subtitle_sections can preserve TOC entries
    # that become sub-sections through refinement.
    toc_anchor_ids = frozenset(
        str(link.get("href", ""))[1:]
        for link in doc_index.toc_links
        if str(link.get("href", "")).startswith("#")
    )
    heading_sections = _parse_heading_sections(doc_index=doc_index)
    if toc_sections:
        # When the heading scan finds far more structure than the sparse TOC,
        # the TOC links are navigational but not structurally representative
        # (e.g. Dante's Inferno: 2 TOC links vs 37 heading-scan sections).
        # Prefer the richer heading scan in that case.
        if len(heading_sections) > 3 * len(toc_sections) and len(toc_sections) <= 5:
            sections = heading_sections
        else:
            toc_sections = _normalize_toc_heading_ranks(toc_sections)
            sections = _refine_toc_sections(
                toc_sections,
                heading_sections,
                doc_index=doc_index,
            )
    else:
        sections = heading_sections
    if not sections:
        # Try paragraph-text section scan: some editions encode chapter
        # headings as plain <p> elements instead of <h1>–<h6>.
        sections = _parse_paragraph_sections(doc_index=doc_index)
    if not sections:
        # Final fallback: emit all paragraphs as flat unsectioned text.
        if len(doc_index.paragraphs) < 10:
            return []
        chunks: list[Chunk] = []
        for pos_idx, ip in enumerate(doc_index.paragraphs):
            chunks.append(Chunk(pos_idx, "", "", "", "", ip.text, "text"))
        return chunks

    sections = _normalize_collection_titles(sections)
    sections = _nest_broad_subdivisions(sections)
    sections = _nest_chapters_under_broad_containers(sections)
    sections = _promote_more_prominent_heading_runs(sections)
    # Use heading rank (h1→h2→h3) to nest sections under non-keyword
    # parents when the rank gap is exactly 1.  Runs before flatten/orphan
    # passes so that those passes see the rank-informed hierarchy.
    sections = _respect_heading_rank_nesting(sections, infer_from_rank=True)
    # Flatten title wrappers and equalise orphan gaps *before* subtitle
    # merging so that level changes from flattening are visible to the
    # subtitle pass (e.g. note-apparatus headings at the correct level).
    sections = _flatten_single_work_title_wrapper(sections)
    sections = _equalize_orphan_level_gap(sections)
    sections = _merge_chapter_subtitle_sections(sections, toc_anchor_ids=toc_anchor_ids)
    # Merge ALL-CAPS description paragraphs into bare chapter headings so
    # that e.g. "CHAPTER I" followed by a <p>TREATING OF SHOES...</p> becomes
    # "CHAPTER I TREATING OF SHOES...".  The merged <p> tags are excluded
    # from body-text emission below.  Runs before level compaction alongside
    # _merge_chapter_subtitle_sections so all heading-text transforms are
    # grouped together.
    sections, skip_paragraph_ids = _merge_chapter_description_paragraphs(sections)
    _skip_tag_ids = frozenset(skip_paragraph_ids) if skip_paragraph_ids else None
    sections = _merge_adjacent_duplicate_sections(sections)

    # Compact levels so the shallowest level maps to div1.
    # e.g. chapter-only books (min_level=2) shift chapters to div1.
    min_level = min(s.level for s in sections)
    if min_level > 1:
        sections = [s._with_level(s.level - min_level + 1) for s in sections]
    # Cap at 4 levels (div1–div4); deeper nesting is flattened.
    max_div = 4
    sections = [s._with_level(min(max_div, s.level)) for s in sections]

    chunks: list[Chunk] = []
    pos = 0
    divs = ["", "", "", ""]

    # Precompute the heading element (or anchor itself) for each section,
    # avoiding redundant find_parent calls in the loop below.
    def _heading_or_anchor(anchor: Tag) -> Tag:
        return anchor.find_parent(_HEADING_TAGS) or anchor

    section_els = [_heading_or_anchor(s.body_anchor) for s in sections]

    # Opening paragraphs before first section remain unsectioned prose.
    heading_texts = {s.heading_text.lower() for s in sections}
    stop_pos = tag_positions.get(id(section_els[0]))
    if stop_pos is not None:
        for text in _paragraphs_in_range(
            doc_index.paragraphs,
            doc_index.paragraph_positions,
            doc_index.bounds.start_pos,
            stop_pos,
            heading_texts=heading_texts,
            min_length=20,
            skip_tag_ids=_skip_tag_ids,
        ):
            chunks.append(Chunk(pos, "", "", "", "", text, "text"))
            pos += 1

    # Find a tail boundary: the first non-structural heading (e.g. FOOTNOTES,
    # NOTES) that appears after the last section.  This prevents endnotes from
    # being lumped into the last chapter.
    tail_anchor = _find_non_structural_boundary_after(
        sections[-1].body_anchor, doc_index=doc_index
    )
    tail_pos: int | None = None
    if tail_anchor is not None:
        tail_pos = tag_positions.get(id(_heading_or_anchor(tail_anchor)))

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
        start_pos_val = tag_positions.get(id(section_els[i]))

        if i + 1 < len(sections):
            stop_pos_val = tag_positions.get(id(section_els[i + 1]))
        elif tail_pos is not None:
            stop_pos_val = tail_pos
        else:
            stop_pos_val = None

        if start_pos_val is not None:
            for text in _paragraphs_in_range(
                doc_index.paragraphs,
                doc_index.paragraph_positions,
                start_pos_val,
                stop_pos_val,
                skip_tag_ids=_skip_tag_ids,
            ):
                chunks.append(Chunk(pos, divs[0], divs[1], divs[2], divs[3], text, "text"))
                pos += 1

    return chunks
