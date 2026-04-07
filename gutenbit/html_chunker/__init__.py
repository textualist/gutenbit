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
from gutenbit.html_chunker._sections import (
    _broad_keywords_at_modal_rank,
    _demote_same_rank_broad_keywords,
    _drop_empty_interior_title_repeats,
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
    _parse_toc_paragraph_sections,
    _parse_toc_sections,
    _promote_more_prominent_heading_runs,
    _refine_toc_sections,
    _respect_heading_rank_nesting,
    _strip_leading_title_page_sections,
    _strip_printed_toc_page_runs,
)
from gutenbit.html_chunker._toc import _toc_context_cache  # cleared per-parse (keyed by id())

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

HTML_PARSER_BACKEND = "lxml"
CHUNKER_VERSION = 41

# ---------------------------------------------------------------------------
# Sparse-TOC override thresholds
# ---------------------------------------------------------------------------
# When the heading scan finds far more structure than the TOC, the TOC is
# navigational but not structurally representative.  Three tiers control
# when to prefer the heading scan over TOC-based refinement.

# Minimum heading-to-TOC ratio for any override to fire.
_SPARSE_TOC_MIN_RATIO = 3
# Maximum TOC entry count for the low-ratio (3:1) tier.
_SPARSE_TOC_MAX_LOW_TIER = 5
# Maximum TOC entry count for the mid-ratio (5:1) tier.
# Handles poetry collections (e.g. PG 438: 10 TOC, 58 headings at 5.8:1).
_SPARSE_TOC_MAX_MID_TIER = 10
# Minimum heading-to-TOC ratio for the mid tier to fire.
_SPARSE_TOC_MID_RATIO = 5
# Minimum heading-to-TOC ratio for the extreme override (any TOC count).
_SPARSE_TOC_EXTREME_RATIO = 10


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
        # Prefer the richer heading scan when the TOC is navigational
        # but not structurally representative.  See constants above for
        # tier definitions and the PG IDs that motivated each threshold.
        n_toc = len(toc_sections)
        n_head = len(heading_sections)
        if n_head > _SPARSE_TOC_MIN_RATIO * n_toc and (
            n_toc <= _SPARSE_TOC_MAX_LOW_TIER
            or (n_toc <= _SPARSE_TOC_MAX_MID_TIER and n_head > _SPARSE_TOC_MID_RATIO * n_toc)
            or n_head > _SPARSE_TOC_EXTREME_RATIO * n_toc
        ):
            # Rank normalization runs on heading-scan sections too (not
            # just TOC sections) so the single-instance container
            # heuristic can fix inverted BOOK/PART ranks in books like
            # the Golden Bowl (PG 4262) that lack a pginternal TOC.
            sections = _normalize_toc_heading_ranks(heading_sections)
        else:
            toc_sections = _normalize_toc_heading_ranks(toc_sections)
            sections = _refine_toc_sections(
                toc_sections,
                heading_sections,
                doc_index=doc_index,
            )
    else:
        # Heading-scan fallback: normalise ranks here so the
        # single-instance container heuristic fires before any
        # hierarchy pass.  See _normalize_toc_heading_ranks docstring.
        sections = _normalize_toc_heading_ranks(heading_sections)
    # When heading scan is very sparse and paragraph scan finds richer
    # structure, prefer paragraph sections.  This handles editions where
    # a single title <h1> exists but chapter headings are <p> elements
    # (e.g. PG 29433 "Nature" has <h1>NATURE</h1> + 8 <p>-encoded chapters).
    # Cache the result so the same scan isn't repeated in the fallback below.
    para_sections: list | None = None
    if sections and len(sections) <= 2:
        para_sections = _parse_paragraph_sections(doc_index=doc_index)
        if len(para_sections) > 3 * len(sections):
            sections = para_sections
    if not sections:
        # Try paragraph-text section scan: some editions encode chapter
        # headings as plain <p> elements instead of <h1>–<h6>.
        if para_sections is None:
            para_sections = _parse_paragraph_sections(doc_index=doc_index)
        sections = para_sections
    if not sections and doc_index.toc_links:
        # TOC-paragraph fallback: some editions set anchor IDs directly on
        # <p> or <div> elements instead of <a> tags (e.g. PG 39827).
        # The scanner only collects <a> IDs, so the normal TOC path finds
        # nothing.  Resolve missing targets with soup.find(id=...) and
        # create sections from the TOC link text.
        sections = _parse_toc_paragraph_sections(soup, doc_index=doc_index)
    if not sections:
        # Final fallback: emit all paragraphs as flat unsectioned text.
        if len(doc_index.paragraphs) < 10:
            return []
        chunks: list[Chunk] = []
        for pos_idx, ip in enumerate(doc_index.paragraphs):
            chunks.append(Chunk(pos_idx, "", "", "", "", ip.text, "text"))
        return chunks

    # Strip printed-TOC runs (heading tags whose text ends with a trailing
    # page number) before any hierarchy-normalisation pass runs: those
    # entries are source-HTML artefacts, not real sections, and they skew
    # the rank and level statistics used by the nesting passes below.
    sections = _strip_printed_toc_page_runs(sections)
    sections = _normalize_collection_titles(sections)
    sections = _nest_broad_subdivisions(sections)
    # Detect broad keywords that share the overall modal heading rank
    # (single-tag documents like all-h4).  These are peers, not parents.
    # _broad_keywords_at_modal_rank runs first (detection) so that
    # _nest_chapters_under_broad_containers can skip demoted keywords,
    # then _demote_same_rank_broad_keywords applies the level change.
    _skip_broad = _broad_keywords_at_modal_rank(sections)
    sections = _nest_chapters_under_broad_containers(sections, skip_keywords=_skip_broad)
    sections = _demote_same_rank_broad_keywords(sections, demote_keywords=_skip_broad)
    sections = _promote_more_prominent_heading_runs(sections)
    # Use heading rank (h1→h2→h3) to nest sections under non-keyword
    # parents when the rank gap is exactly 1.  Runs before flatten/orphan
    # passes so that those passes see the rank-informed hierarchy.
    sections = _respect_heading_rank_nesting(sections, infer_from_rank=True)
    # Flatten title wrappers and equalise orphan gaps *before* subtitle
    # merging so that level changes from flattening are visible to the
    # subtitle pass (e.g. note-apparatus headings at the correct level).
    # Strip leading title-page clusters.  Runs *after* rank nesting so the
    # children-depth guard sees rank-adjusted levels, and *before* flatten
    # so that removed title wrappers don't confuse the flatten heuristic.
    sections = _strip_leading_title_page_sections(sections, doc_index=doc_index)
    sections = _flatten_single_work_title_wrapper(sections)
    # Re-run keyword nesting after title-wrapper flattening: when a single
    # work title (e.g. "RESURRECTION") wraps BOOK + CHAPTER at the same
    # rank, rank nesting pushes both under the title, then flattening
    # promotes them back to the same level.  This second pass restores the
    # BOOK → CHAPTER hierarchy that was lost in the flatten step.
    sections = _nest_chapters_under_broad_containers(sections, skip_keywords=_skip_broad)
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
    sections = _merge_adjacent_duplicate_sections(sections, doc_index=doc_index)
    # Drop interior title-like headings that repeat an earlier title and
    # own no content (decorative "ghost" headings that reappear after the
    # front matter or nest as empty siblings inside a book container).
    sections = _drop_empty_interior_title_repeats(sections, doc_index=doc_index)

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
