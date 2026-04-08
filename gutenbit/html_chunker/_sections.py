"""Section parsing, refinement, and normalization pipelines.

Layer 3 — depends on ``_common``, ``_headings``, ``_scanning``, and ``_toc``.

Orchestrates the multi-pass pipeline that converts raw heading/TOC data into
a flat list of ``_Section`` objects with correct hierarchy levels.  Major
function groups: TOC parsing, heading-scan fallback, section merging
(subtitle/duplicate/description), hierarchy nesting (broad keywords, rank),
title-page stripping, and collection normalisation.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Sequence

from bs4 import BeautifulSoup, Tag

from gutenbit.html_chunker._common import (
    _BARE_HEADING_NUMBER_RE,
    _BROAD_KEYWORDS,
    _DRAMATIC_BROAD_KEYWORDS,
    _FALLBACK_START_HEADING_RE,
    _HEADING_TAGS,
    _NUMERIC_LINK_TEXT_RE,
    _PLAY_HEADING_PARAGRAPH_RE,
    _REFINEMENT_STOP_HEADING_RE,
    _STANDALONE_STRUCTURAL_RE,
    _clean_heading_text,
    _extract_heading_text,
    _heading_tag_rank,
    _HeadingRow,
    _Section,
)
from gutenbit.html_chunker._headings import (
    _classify_level,
    _deep_rank_bare_numeral_run_indices,
    _heading_key,
    _heading_keyword,
    _heading_text_suggests_play_structure,
    _headings_have_text_between,
    _is_deep_rank_bare_numeral_heading,
    _is_dialogue_speaker_heading,
    _is_editorial_placeholder_heading,
    _is_emphasized_toc_link,
    _is_empty_front_matter_stub_heading,
    _is_front_matter_attribution_heading,
    _is_front_matter_heading,
    _is_ignorable_fallback_heading,
    _is_non_structural_heading_text,
    _is_rank5_subheading_under_nonchapter_section,
    _is_refinement_heading,
    _is_short_uppercase_stage_heading,
    _is_shorter_adjacent_title_repeat,
    _is_single_letter_subheading,
    _is_single_speaker_dialogue_heading,
    _is_title_like_heading,
    _is_title_page_subtitle,
    _is_toc_section_heading,
    _normalized_heading_continuation,
    _rank_relative_level,
    _same_heading_text,
    _split_play_heading_paragraph,
    _toc_link_refines_body_heading,
    _update_dramatic_context_state,
)
from gutenbit.html_chunker._hierarchy import (
    _collapse_degenerate_title_block,
    _respect_heading_rank_nesting,
)
from gutenbit.html_chunker._merging import (
    _merge_bare_heading_pairs,
)
from gutenbit.html_chunker._scanning import (
    _DocumentIndex,
    _IndexedParagraph,
    _tag_position,
    _tag_within_bounds,
)
from gutenbit.html_chunker._toc import (
    _is_structural_toc_link,
    _looks_enumerated_toc_entry,
    _toc_context_text,
    _toc_entry_matches_heading,
)

# ---------------------------------------------------------------------------
# Heading-content patterns used by section merge/filter passes
# ---------------------------------------------------------------------------

_STANDALONE_BYLINE_RE = re.compile(r"^by\.?$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Compiled regex patterns (used only within this module)
# ---------------------------------------------------------------------------

# Anchor IDs that look like page-number references: "page3", "page_47",
# "Page111".  Used to detect TOC links that point at page markers inside
# heading elements rather than at structural anchors.
_PAGE_ANCHOR_ID_RE = re.compile(r"^[Pp]age[_\-]?\d+$")

# Tail-boundary pattern: only clearly apparatus headings, not ambiguous
# singular "NOTE" which can be a narrative epilogue (e.g. Dracula).
_TAIL_BOUNDARY_HEADING_RE = re.compile(
    r"^(?:footnotes?|endnotes?|notes\b|transcriber'?s?\s+notes?|editor'?s?\s+notes?"
    r"|index)\b",
    re.IGNORECASE,
)
_TAIL_SECTION_HEADING_RE = re.compile(
    r"^(?:note\b|note to\b|letter\b|a letter from\b|finale\b|the conclusion\b|"
    r"variation\b|"
    r"author'?s?\s+endnotes?\b|(?:the\s+)?afterthought\b)",
    re.IGNORECASE,
)
# Subset of the stop-heading pattern that matches only "Conclusion"-family
# headings (bare "Conclusion", "Review, and Conclusion", etc.).  Used by
# the peer-chapter exemption to distinguish chapter-title "Conclusion" from
# genuine apparatus boundaries like APPENDIX.
_CONCLUSION_HEADING_RE = re.compile(
    r"^(?:(?:a\s+)?review\s*[,;]?\s*(?:and\s+)?)?conclusion\s*$",
    re.IGNORECASE,
)


# Matches paragraph text that starts with a structural keyword followed by
# a number or Roman numeral — indicating a chapter/section heading encoded
# as a plain <p>, not an <h1>–<h6>.
_PARAGRAPH_SECTION_RE = re.compile(
    r"^(?:CHAPTER|SECTION|BOOK|PART|VOLUME|LECTURE)\s+[IVXLCDM0-9]+\b",
    re.IGNORECASE,
)

# Maximum length (chars) for paragraph-text headings.  Truncated at a word
# boundary to keep div labels reasonable.
_MAX_PARAGRAPH_HEADING_LEN = 120

# Minimum heading-tag rank (h4+) for title-page metadata rows after a "BY"
# byline.  Shallow-rank headings (h2/h3) may be real structural content.
_TITLE_PAGE_METADATA_MIN_RANK = 4

# ---------------------------------------------------------------------------
# TOC parsing
# ---------------------------------------------------------------------------


def _parse_toc_sections(
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Extract section list from TOC ``pginternal`` links."""
    tag_positions = doc_index.tag_positions
    bounds = doc_index.bounds
    toc_links = doc_index.toc_links
    sections: list[_Section] = []
    used_headings: set[int] = set()

    anchor_map = doc_index.anchor_map

    # Pre-scan: identify page-anchor links that resolve to headings and
    # cache the heading parent.  Only enable resolution when ≥ 50% of
    # page-anchor links resolve (PG 492: 7/7 = 100%; PG 786: 14/40 = 35%).
    _page_anchor_headings: dict[str, Tag] = {}  # anchor_id → heading element
    _page_anchor_total = 0
    for link in toc_links:
        href = str(link.get("href", ""))
        if not href.startswith("#"):
            continue
        aid = href[1:]
        raw = _clean_heading_text(" ".join(link.get_text().split()))
        if _PAGE_ANCHOR_ID_RE.match(aid) and _NUMERIC_LINK_TEXT_RE.fullmatch(raw):
            _page_anchor_total += 1
            ba = anchor_map.get(str(aid))
            if ba:
                hp = ba.find_parent(_HEADING_TAGS)
                if hp:
                    _page_anchor_headings[aid] = hp
    _enable_page_anchors = (
        _page_anchor_total > 0 and len(_page_anchor_headings) / _page_anchor_total >= 0.5
    )

    for link in toc_links:
        if not _tag_within_bounds(link, tag_positions, bounds):
            continue
        raw_link_text = _clean_heading_text(" ".join(link.get_text().split()))
        link_text = raw_link_text

        href = str(link.get("href", ""))
        anchor_id = href[1:] if href.startswith("#") else ""

        # Early resolution: when a numeric TOC link targets a page-number
        # anchor inside a heading, resolve through the cached heading
        # parent and use its text.  Runs before the structural-link filter
        # which would otherwise discard the numeric link.
        cached_heading = _page_anchor_headings.get(anchor_id) if _enable_page_anchors else None
        if cached_heading is not None and _tag_within_bounds(
            cached_heading, tag_positions, bounds
        ):
            heading_text = _clean_heading_text(_extract_heading_text(cached_heading))
            if heading_text:
                link_text = heading_text
        elif not _is_structural_toc_link(link, raw_link_text, doc_index=doc_index):
            context_text = _toc_context_text(link)
            if _NUMERIC_LINK_TEXT_RE.fullmatch(raw_link_text) and _looks_enumerated_toc_entry(
                context_text
            ):
                link_text = context_text
            else:
                continue

        if not anchor_id:
            continue
        body_anchor = anchor_map.get(str(anchor_id))
        if not body_anchor or not _tag_within_bounds(body_anchor, tag_positions, bounds):
            continue

        # Skip page-number anchors (e.g. illustrated editions use
        # <span class="pagenum"><a id="page_1">) — these are not sections.
        if body_anchor.find_parent("span", class_="pagenum"):
            heading_parent = body_anchor.find_parent(_HEADING_TAGS)
            if heading_parent and _tag_within_bounds(heading_parent, tag_positions, bounds):
                body_anchor = heading_parent
            else:
                continue

        # Find the associated heading element.
        heading_el = body_anchor.find_parent(_HEADING_TAGS)
        if heading_el and not _tag_within_bounds(heading_el, tag_positions, bounds):
            heading_el = None
        if not heading_el:
            # The anchor may precede an intervening heading (e.g. a repeated
            # book title) that doesn't correspond to this TOC entry.  Search
            # forward through a few candidates to find one that matches.
            skip = set(used_headings)
            heading_el = None
            for _ in range(3):
                candidate = _find_next_heading(
                    body_anchor,
                    skip,
                    doc_index=doc_index,
                )
                if candidate is None:
                    break
                candidate_text = _clean_heading_text(_extract_heading_text(candidate))
                if candidate_text and _toc_entry_matches_heading(link_text, candidate_text):
                    heading_el = candidate
                    break
                skip.add(id(candidate))

        if not heading_el or id(heading_el) in used_headings:
            continue
        used_headings.add(id(heading_el))

        heading_text = _clean_heading_text(_extract_heading_text(heading_el))
        if not heading_text:
            continue
        if _is_non_structural_heading_text(heading_text):
            continue

        is_emphasized = _is_emphasized_toc_link(link)
        heading_rank = _heading_tag_rank(heading_el)
        if heading_rank is None:
            continue
        if not _is_toc_section_heading(
            heading_text,
            link_text=link_text,
            heading_rank=heading_rank,
            is_emphasized=is_emphasized,
        ):
            continue

        heading_level = _classify_level(heading_text, is_emphasized)
        # Apparatus headings (APPENDIX, NOTES ON...) are structurally
        # top-level even when the TOC link isn't emphasised.
        if heading_level > 1 and _REFINEMENT_STOP_HEADING_RE.match(heading_text):
            heading_level = 1
        if _toc_link_refines_body_heading(link_text, heading_text):
            sections.append(
                _Section(anchor_id, heading_text, heading_level, body_anchor, heading_rank)
            )
            sections.append(
                _Section(
                    anchor_id,
                    link_text,
                    _classify_level(link_text, False),
                    body_anchor,
                    heading_rank,
                )
            )
            continue

        sections.append(
            _Section(anchor_id, heading_text, heading_level, body_anchor, heading_rank)
        )

    sections.sort(key=lambda section: tag_positions.get(id(section.body_anchor), float("inf")))

    # Truncate after an apparatus heading (APPENDIX, NOTES ON...): keep the
    # apparatus heading itself but drop everything after it so commentary
    # text stays flat under that one section.  Skip the truncation when a
    # more prominent heading follows the apparatus heading — this indicates
    # additional top-level works in a collected edition rather than trailing
    # commentary (e.g. Henry Esmond's Appendix followed by The English
    # Humourists in PG 29363).
    for trim_idx, section in enumerate(sections):
        if _REFINEMENT_STOP_HEADING_RE.match(section.heading_text):
            apparatus_rank = section.heading_rank
            has_higher_rank_after = False
            is_peer_conclusion = False
            if apparatus_rank is not None:
                remaining = sections[trim_idx + 1 :]
                has_higher_rank_after = any(
                    s.heading_rank is not None and s.heading_rank < apparatus_rank
                    for s in remaining
                )
                # "Conclusion" is commonly a regular chapter title (e.g.
                # PG 205 Walden) rather than an apparatus boundary.  Skip
                # truncation when it shares its heading rank with the
                # majority of preceding sections, indicating it is a peer
                # chapter.  Do not apply this exemption to APPENDIX /
                # NOTES ON which are almost always genuine apparatus
                # headings.  Require at least 3 preceding sections so
                # that very short books where "Conclusion" truly is
                # terminal are not incorrectly exempted.
                if trim_idx >= 3 and _CONCLUSION_HEADING_RE.match(section.heading_text):
                    peer_count = sum(
                        1 for s in sections[:trim_idx] if s.heading_rank == apparatus_rank
                    )
                    is_peer_conclusion = peer_count >= trim_idx // 2
            if not has_higher_rank_after and not is_peer_conclusion:
                sections = sections[: trim_idx + 1]
            break

    # Remove a leading title section whose heading is a prefix of the next section's
    # heading (e.g. "ADVENTURES OF SHERLOCK HOLMES" before "ADVENTURES OF SHERLOCK
    # HOLMES A SCANDAL IN BOHEMIA"). Require a space after the prefix to avoid
    # false matches like "CHAPTER I" / "CHAPTER II".
    if len(sections) >= 2 and sections[1].heading_text.startswith(sections[0].heading_text + " "):
        sections = sections[1:]
    return _respect_heading_rank_nesting(_merge_bare_heading_pairs(sections))


def _normalize_toc_heading_ranks(sections: list[_Section]) -> list[_Section]:
    """Correct anomalous heading ranks within keyword groups.

    Some editions use inconsistent heading tags for the same keyword
    (e.g. most CHAPTERs are ``<h2>`` but one is ``<h3>``).  Normalize
    outlier ranks to the mode so that later rank-based refinement works
    correctly.
    """
    if len(sections) < 3:
        return sections

    rank_counts: dict[str, Counter[int]] = {}
    for section in sections:
        kw = _heading_keyword(section.heading_text)
        if not kw or section.heading_rank is None:
            continue
        if kw not in rank_counts:
            rank_counts[kw] = Counter()
        rank_counts[kw][section.heading_rank] += 1

    mode_ranks = {kw: counts.most_common(1)[0][0] for kw, counts in rank_counts.items()}

    # Single-instance container fix: when one broad keyword has exactly 1
    # instance and another has ≥ 2, the single instance is the container
    # (e.g. "BOOK FIRST" wrapping multiple "PART" entries in the Golden
    # Bowl).  If the container's rank is ≥ the inner keyword's mode rank
    # (inverted), reassign it to be shallower so rank-based nesting works.
    # Guard: only apply to non-dramatic structural keywords to avoid
    # affecting ACT/SCENE/INDUCTION in plays.
    total_counts = {kw: sum(c.values()) for kw, c in rank_counts.items()}
    broad_keywords_present = [
        kw for kw in rank_counts if kw in _BROAD_KEYWORDS and kw not in _DRAMATIC_BROAD_KEYWORDS
    ]
    # Build lookups for the containment check in a single pass.
    single_instance_headings: dict[str, str] = {}
    keyword_positions: dict[str, list[int]] = {}
    for idx, section in enumerate(sections):
        kw = _heading_keyword(section.heading_text)
        if not kw:
            continue
        keyword_positions.setdefault(kw, []).append(idx)
        if total_counts.get(kw) == 1:
            single_instance_headings[kw] = section.heading_text

    if len(broad_keywords_present) >= 2:
        for outer_kw in broad_keywords_present:
            if total_counts[outer_kw] != 1:
                continue
            # Don't promote front-matter headings (e.g. "Volume I Preface")
            # as structural containers.
            outer_text = single_instance_headings.get(outer_kw, "")
            if _is_front_matter_heading(outer_text):
                continue
            outer_rank = mode_ranks[outer_kw]
            outer_pos = keyword_positions.get(outer_kw, [None])[0]
            for inner_kw in broad_keywords_present:
                if inner_kw == outer_kw:
                    continue
                if total_counts[inner_kw] < 2:
                    continue
                # Containment check: the single instance must appear
                # BEFORE all inner instances (it wraps them).  If it
                # appears after any inner instance, it's a trailing
                # peer (e.g. EPILOGUE after PARTs), not a container.
                inner_positions = keyword_positions.get(inner_kw, [])
                if outer_pos is None or (inner_positions and outer_pos >= inner_positions[0]):
                    continue
                inner_rank = mode_ranks[inner_kw]
                if outer_rank >= inner_rank:
                    # Container is at same or deeper rank — promote it.
                    mode_ranks[outer_kw] = max(1, inner_rank - 1)

    changed = False
    new_sections = []
    for section in sections:
        kw = _heading_keyword(section.heading_text)
        if (
            kw
            and section.heading_rank is not None
            and kw in mode_ranks
            and section.heading_rank != mode_ranks[kw]
        ):
            new_sections.append(
                _Section(
                    section.anchor_id,
                    section.heading_text,
                    section.level,
                    section.body_anchor,
                    mode_ranks[kw],
                )
            )
            changed = True
        else:
            new_sections.append(section)

    return new_sections if changed else sections


# ---------------------------------------------------------------------------
# Heading-scan fallback
# ---------------------------------------------------------------------------


def _parse_heading_sections(
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Fallback section extraction directly from body headings.

    Used when TOC links don't point at structural anchors (e.g., page-number
    links only). We start from the first heading that looks structural.
    """
    bounds = doc_index.bounds
    heading_rows: list[_HeadingRow] = []
    for ih in doc_index.headings:
        if not bounds.contains(ih.position):
            continue
        if _is_non_structural_heading_text(ih.text):
            continue
        rank = _heading_tag_rank(ih.tag)
        if rank is None:
            continue
        anchor = ih.tag.find("a", id=True) or ih.tag
        heading_rows.append(_HeadingRow(ih.tag, anchor, ih.text, rank))

    if _should_scan_paragraph_heading_rows(heading_rows, doc_index.paragraphs):
        heading_rows.extend(_paragraph_heading_rows(doc_index))

    if not heading_rows:
        return []

    heading_rows.sort(
        key=lambda row: _tag_position(row.tag, doc_index.tag_positions) or float("inf")
    )
    heading_rows = _filter_fallback_heading_rows(heading_rows)
    if not heading_rows:
        return []

    bare_numeral_run_indices = _deep_rank_bare_numeral_run_indices(heading_rows)

    start_idx = _fallback_start_index(heading_rows)
    if start_idx is None:
        return []

    sections: list[_Section] = []
    dramatic_context_active = False
    previous_kept_heading: str | None = None
    previous_kept_row: _HeadingRow | None = None
    i = start_idx
    while i < len(heading_rows):
        row = heading_rows[i]
        heading_text = row.heading_text
        next_row = heading_rows[i + 1] if i + 1 < len(heading_rows) else None
        following_row = heading_rows[i + 2] if i + 2 < len(heading_rows) else None
        previous_row = heading_rows[i - 1] if i > start_idx else None

        if (
            previous_row is not None
            and _same_heading_text(previous_row.heading_text, heading_text)
            and not _headings_have_text_between(previous_row, row, doc_index=doc_index)
        ):
            i += 1
            continue

        if _is_rank5_subheading_under_nonchapter_section(
            row,
            previous_kept_heading=previous_kept_heading,
            dramatic_context_active=dramatic_context_active,
        ):
            i += 1
            continue

        if _is_single_letter_subheading(
            row,
            previous_kept_heading=previous_kept_heading,
            previous_row=previous_row,
            next_row=next_row,
            doc_index=doc_index,
        ):
            i += 1
            continue

        if _is_deep_rank_bare_numeral_heading(
            row,
            previous_kept_heading=previous_kept_heading,
            previous_row=previous_row,
            next_row=next_row,
            row_index=i,
            bare_numeral_run_indices=bare_numeral_run_indices,
            doc_index=doc_index,
        ):
            i += 1
            continue

        if _is_short_uppercase_stage_heading(
            row,
            previous_kept_heading=previous_kept_heading,
            previous_row=previous_row,
            next_row=next_row,
            dramatic_context_active=dramatic_context_active,
            doc_index=doc_index,
        ):
            i += 1
            continue

        if _is_title_page_subtitle(row, previous_kept_row=previous_kept_row):
            i += 1
            continue

        if next_row is not None and _is_empty_front_matter_stub_heading(
            row,
            next_row,
            doc_index=doc_index,
        ):
            i += 1
            continue

        if next_row is not None and _is_editorial_placeholder_heading(
            row,
            next_row,
            doc_index=doc_index,
        ):
            i += 1
            continue

        if (
            previous_row is previous_kept_row
            and previous_row is not None
            and _is_shorter_adjacent_title_repeat(row, previous_row, doc_index=doc_index)
        ):
            i += 1
            continue

        if _BARE_HEADING_NUMBER_RE.fullmatch(heading_text) and next_row is not None:
            subtitle = _normalized_heading_continuation(
                row,
                next_row,
                following_row=following_row,
                previous_kept_heading=previous_kept_heading,
                dramatic_context_active=dramatic_context_active,
                doc_index=doc_index,
            )
            if subtitle:
                heading_text = f"{heading_text} {subtitle}"
                i += 1

        elif _is_ignorable_fallback_heading(heading_text, heading_rank=row.rank):
            i += 1
            continue

        level = _classify_level(heading_text, False)
        anchor_id = str(row.anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, row.anchor, row.rank))
        previous_kept_heading = heading_text
        previous_kept_row = row
        dramatic_context_active = _update_dramatic_context_state(
            dramatic_context_active,
            heading_text,
        )
        i += 1

    sections = _drop_leading_repeated_title_sections(sections)
    sections = _collapse_degenerate_title_block(sections, doc_index=doc_index)
    return _respect_heading_rank_nesting(sections, infer_from_rank=True)


# ---------------------------------------------------------------------------
# TOC refinement
# ---------------------------------------------------------------------------


def _refine_toc_sections(
    toc_sections: list[_Section],
    heading_sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Supplement a valid TOC with deeper body headings.

    The TOC remains authoritative for matched headings so visually emphasized
    broad divisions keep their TOC-derived levels. Body headings are inserted
    only when they refine that TOC in document order.
    """
    if not toc_sections or not heading_sections:
        return toc_sections

    refined: list[_Section] = []
    added = 0
    heading_idx = 0

    first_toc = toc_sections[0]
    tag_positions = doc_index.tag_positions

    # Positions already covered by TOC sections — skip heading-scan
    # candidates at these positions to avoid duplicates when a TOC link
    # resolves to the same body element under a different heading text.
    toc_positions: set[int] = set()
    for ts in toc_sections:
        tp = _tag_position(ts.body_anchor, tag_positions)
        if tp is not None:
            toc_positions.add(tp)
    first_pos = _tag_position(first_toc.body_anchor, tag_positions)
    if first_pos is not None:
        while heading_idx < len(heading_sections):
            candidate = heading_sections[heading_idx]
            candidate_pos = _tag_position(candidate.body_anchor, tag_positions)
            if candidate_pos is None:
                heading_idx += 1
                continue
            if candidate_pos >= first_pos:
                break
            # Allow front-matter headings (PREFACE, INTRODUCTION, etc.)
            # and broad container headings (VOLUME, BOOK, PART) that sit
            # above the first TOC section in the hierarchy.  Require the
            # container to use a strictly more prominent HTML tag (lower
            # rank number) than the first TOC entry so that leftover
            # CONTENTS-line fragments like "VOLUME I" (same rank as the
            # TOC chapters) are not admitted.
            candidate_kw = _heading_keyword(candidate.heading_text)
            is_front_matter = _FALLBACK_START_HEADING_RE.match(candidate.heading_text)
            is_broad_container = (
                candidate_kw in _BROAD_KEYWORDS
                and candidate.level < first_toc.level
                and candidate.heading_rank is not None
                and first_toc.heading_rank is not None
                and candidate.heading_rank < first_toc.heading_rank
            )
            if is_front_matter or is_broad_container:
                refined.append(candidate._with_level(min(candidate.level, first_toc.level)))
                added += 1
            heading_idx += 1

    for toc_idx, toc_section in enumerate(toc_sections):
        refined.append(toc_section)
        # When the TOC section is an apparatus heading (APPENDIX, NOTES ON...),
        # do not refine it — its content is commentary, not structural sections.
        if _REFINEMENT_STOP_HEADING_RE.match(toc_section.heading_text):
            continue
        start_pos = _tag_position(toc_section.body_anchor, tag_positions)
        if start_pos is None:
            continue

        next_pos: int | None = None
        if toc_idx + 1 < len(toc_sections):
            next_pos = _tag_position(toc_sections[toc_idx + 1].body_anchor, tag_positions)

        # When past the last TOC section, compute a tail boundary from
        # the raw heading index so that sub-headings inside apparatus
        # sections (e.g. essay titles repeated under a NOTES heading)
        # are not promoted as structural sections.
        tail_boundary_pos: int | None = None
        if next_pos is None and start_pos is not None:
            tail_anchor = _find_non_structural_boundary_after(
                toc_section.body_anchor, doc_index=doc_index
            )
            if tail_anchor is not None:
                tail_boundary_pos = tag_positions.get(id(tail_anchor))

        while heading_idx < len(heading_sections):
            candidate_pos = _tag_position(heading_sections[heading_idx].body_anchor, tag_positions)
            if candidate_pos is None or candidate_pos < start_pos:
                heading_idx += 1
                continue
            break

        scan_idx = heading_idx
        while scan_idx < len(heading_sections):
            candidate = heading_sections[scan_idx]
            candidate_pos = _tag_position(candidate.body_anchor, tag_positions)
            if candidate_pos is None:
                scan_idx += 1
                continue
            if next_pos is not None and candidate_pos >= next_pos:
                break
            # Stop at tail boundary (apparatus heading like NOTES) to
            # prevent sub-headings inside apparatus from leaking into
            # the structural section list.
            if tail_boundary_pos is not None and candidate_pos >= tail_boundary_pos:
                break
            if _same_heading_text(candidate.heading_text, toc_section.heading_text):
                scan_idx += 1
                continue
            if candidate_pos in toc_positions:
                scan_idx += 1
                continue
            refined_candidate = _refined_candidate_section(
                candidate,
                toc_section,
                allow_tail_title_like=next_pos is None,
            )
            if refined_candidate is not None:
                refined.append(refined_candidate)
                added += 1
                # After an apparatus heading past the last TOC entry,
                # stop adding further body headings so commentary
                # stays flat under that heading.
                if next_pos is None and _REFINEMENT_STOP_HEADING_RE.match(
                    refined_candidate.heading_text
                ):
                    scan_idx += 1
                    break
            scan_idx += 1

        heading_idx = scan_idx

    return refined if added else toc_sections


def _find_non_structural_boundary_after(
    anchor: Tag,
    *,
    doc_index: _DocumentIndex,
) -> Tag | None:
    """Find the first apparatus heading after *anchor* (e.g. FOOTNOTES, NOTES).

    Returns the heading tag itself so its position can be used as a stop boundary
    for paragraph collection.  Uses a restrictive pattern to avoid false positives
    on narrative headings like a singular "NOTE" epilogue.

    Uses the precomputed heading index for O(log n) lookup instead of
    O(n) DOM traversal via ``find_all_next``.
    """
    anchor_pos = doc_index.tag_positions.get(id(anchor))
    if anchor_pos is None:
        return None

    lo = bisect_right(
        doc_index.heading_positions, anchor_pos
    )  # first heading strictly after anchor_pos
    for ih in doc_index.headings[lo:]:
        if not doc_index.bounds.contains(ih.position):
            continue
        if ih.text and _TAIL_BOUNDARY_HEADING_RE.match(ih.text):
            return ih.tag
    return None


def _find_next_heading(
    anchor: Tag,
    used_headings: set[int] | None = None,
    *,
    doc_index: _DocumentIndex,
) -> Tag | None:
    """Find the next ``<h1>``–``<h3>`` heading after *anchor*.

    Uses the precomputed heading index for O(log n) lookup instead of
    bounded DOM traversal via ``find_all_next``.
    """
    anchor_pos = doc_index.tag_positions.get(id(anchor))
    if anchor_pos is None:
        return None

    lo = bisect_right(doc_index.heading_positions, anchor_pos)
    for ih in doc_index.headings[lo:]:
        if ih.tag.name not in ("h1", "h2", "h3"):
            continue
        if used_headings is not None and id(ih.tag) in used_headings:
            continue
        if not doc_index.bounds.contains(ih.position):
            continue
        return ih.tag
    return None


def _refined_candidate_section(
    candidate: _Section,
    toc_section: _Section,
    *,
    allow_tail_title_like: bool,
) -> _Section | None:
    """Return a level-adjusted section if *candidate* refines *toc_section*, else None."""
    if _is_title_like_heading(candidate.heading_text):
        # Structural titles always start with an uppercase letter; a lowercase
        # opener or quotation mark signals descriptive content or dialogue.
        first_char = candidate.heading_text[:1]
        if first_char.islower() or first_char in '"\u201c\u00ab':
            return None
        if candidate.heading_rank is None or toc_section.heading_rank is None:
            return None
        if candidate.heading_rank != toc_section.heading_rank + 1 and not (
            allow_tail_title_like and _TAIL_SECTION_HEADING_RE.match(candidate.heading_text)
        ):
            return None
        return candidate._with_level(_rank_relative_level(candidate, toc_section))

    if not _is_refinement_heading(candidate.heading_text):
        return None
    if _is_title_like_heading(toc_section.heading_text):
        return candidate
    # Allow dramatic parent headings (e.g. ACT) to refine between child
    # headings (e.g. SCENE) even when the candidate level is structurally
    # above the TOC section — the candidate is a parent, not a child.
    # Restrict to known parent→child keyword pairs to avoid promoting
    # unrelated keyword headings (e.g. CHAPTER should not refine between
    # SCENE entries).
    candidate_kw = _heading_keyword(candidate.heading_text)
    toc_kw = _heading_keyword(toc_section.heading_text)
    if (
        candidate.level < toc_section.level
        and candidate_kw
        and toc_kw
        and candidate_kw in _BROAD_KEYWORDS
        and candidate_kw != toc_kw
    ):
        return candidate._with_level(_rank_relative_level(candidate, toc_section))
    return candidate if candidate.level > toc_section.level else None


# ---------------------------------------------------------------------------
# Hierarchy normalization
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Leading title deduplication
# ---------------------------------------------------------------------------


def _leading_title_cluster_start_index(
    items: Sequence[_Section | _HeadingRow],
    *,
    first_front_matter_idx: int,
) -> int:
    """Return the start index of the title-like cluster preceding front matter."""
    start_idx = first_front_matter_idx
    while start_idx > 0 and _is_title_like_heading(items[start_idx - 1].heading_text):
        start_idx -= 1
    return start_idx


def _post_front_matter_repeat_title_keys(
    items: Sequence[_Section | _HeadingRow],
    *,
    first_front_matter_idx: int,
) -> set[str]:
    """Return immediate post-front-matter titles that repeat a leading title page."""
    repeat_keys: set[str] = set()
    for item in items[first_front_matter_idx + 1 :]:
        heading_text = item.heading_text
        if _is_refinement_heading(heading_text):
            break
        if not _is_title_like_heading(heading_text):
            break
        repeat_keys.add(_heading_key(heading_text))
    return repeat_keys


def _drop_leading_repeated_title_sections(sections: list[_Section]) -> list[_Section]:
    """Drop title-page duplicates when the same title reappears after front matter."""
    first_front_matter_idx = next(
        (
            idx
            for idx, section in enumerate(sections)
            if _FALLBACK_START_HEADING_RE.match(section.heading_text)
        ),
        None,
    )
    if first_front_matter_idx is None:
        return sections

    repeat_title_keys = _post_front_matter_repeat_title_keys(
        sections,
        first_front_matter_idx=first_front_matter_idx,
    )
    if not repeat_title_keys:
        return sections

    return [
        section
        for idx, section in enumerate(sections)
        if not (
            idx < first_front_matter_idx
            and _is_title_like_heading(section.heading_text)
            and _heading_key(section.heading_text) in repeat_title_keys
        )
    ]


# ---------------------------------------------------------------------------
# Fallback heading filtering
# ---------------------------------------------------------------------------


def _fallback_start_index(heading_rows: list[_HeadingRow]) -> int | None:
    """Return the first body-structure heading to use for heading-scan fallback."""
    first_front_matter_idx = next(
        (
            idx
            for idx, row in enumerate(heading_rows)
            if _FALLBACK_START_HEADING_RE.match(row.heading_text)
        ),
        None,
    )
    structural_rows = [
        (idx, row)
        for idx, row in enumerate(heading_rows)
        if _heading_keyword(row.heading_text) or _STANDALONE_STRUCTURAL_RE.search(row.heading_text)
    ]
    if first_front_matter_idx is not None and (
        not structural_rows or first_front_matter_idx < structural_rows[0][0]
    ):
        repeat_title_keys = _post_front_matter_repeat_title_keys(
            heading_rows,
            first_front_matter_idx=first_front_matter_idx,
        )
        if not repeat_title_keys:
            return first_front_matter_idx

        start_idx = _leading_title_cluster_start_index(
            heading_rows,
            first_front_matter_idx=first_front_matter_idx,
        )
        leading_title_keys = {
            _heading_key(row.heading_text)
            for row in heading_rows[start_idx:first_front_matter_idx]
            if _is_title_like_heading(row.heading_text)
        }
        if leading_title_keys & repeat_title_keys:
            return start_idx
        return first_front_matter_idx

    start_rows = structural_rows or list(enumerate(heading_rows))
    start_rank = min(row.rank for _, row in start_rows)
    for idx, row in start_rows:
        if row.rank == start_rank:
            # When the first structural heading has peer or slightly-
            # higher-rank headings before it (e.g. h2 DEDICATION before
            # h2 PREFACE, or h2 story titles before h3 "Section C"),
            # extend backwards through a contiguous run of matching-rank
            # headings.  Only extend to ranks in [max(2, start_rank-1),
            # start_rank] — this excludes h1 title headings and stops
            # at any gap (e.g. an intervening h1 or a rank-7 paragraph
            # heading).  The scan stops at the first non-matching rank.
            if start_rank >= 2 and idx > 0:
                min_rank = max(2, start_rank - 1)
                new_start = idx
                for j in range(idx - 1, -1, -1):
                    if min_rank <= heading_rows[j].rank <= start_rank:
                        new_start = j
                    else:
                        break
                if new_start < idx:
                    return new_start
            return idx
    return None


def _filter_fallback_heading_rows(heading_rows: list[_HeadingRow]) -> list[_HeadingRow]:
    """Drop heading-scan rows that are clearly non-navigational subheads."""
    # Pre-pass: detect standalone "by" headings and the author-name heading
    # that immediately follows them (e.g. h3 "BY" + h2 "EDGAR ALLAN POE").
    # This is deliberately loose: the next heading is dropped solely because
    # it follows a "BY" heading and looks title-like.  A false positive would
    # remove a real structural heading.  In practice this is safe because
    # "BY" headings only appear in title blocks, and the next heading there
    # is always the author name, not a chapter/section heading.
    byline_indices: set[int] = set()
    for idx, row in enumerate(heading_rows):
        if _STANDALONE_BYLINE_RE.fullmatch(row.heading_text.strip()):
            byline_indices.add(idx)
            # Drop the author name and any subsequent title-page metadata
            # (publisher names, city addresses, dates) that follow the "BY"
            # heading.  Only extend past the author name into deep-rank (h4+)
            # title-like headings — shallow-rank headings (h2/h3) may be real
            # structural content like "BEFORE THE CURTAIN" (Vanity Fair).
            for j in range(idx + 1, len(heading_rows)):
                nxt = heading_rows[j]
                if _heading_keyword(nxt.heading_text):
                    break
                if _is_front_matter_heading(nxt.heading_text):
                    break
                if nxt.tag.find("a", id=True) is not None:
                    break
                if not _is_title_like_heading(nxt.heading_text):
                    break
                # First row after "BY" is the author name (any rank);
                # subsequent rows must be deep-rank (h4+) to be metadata.
                if j > idx + 1 and nxt.rank < _TITLE_PAGE_METADATA_MIN_RANK:
                    break
                byline_indices.add(j)

    filtered: list[_HeadingRow] = []
    for idx, row in enumerate(heading_rows):
        if idx in byline_indices:
            continue
        previous_row = heading_rows[idx - 1] if idx > 0 else None
        next_row = heading_rows[idx + 1] if idx + 1 < len(heading_rows) else None
        if _is_dialogue_speaker_heading(row.heading_text):
            continue
        if _is_single_speaker_dialogue_heading(
            row.heading_text,
            previous_heading=previous_row.heading_text if previous_row else None,
            next_heading=next_row.heading_text if next_row else None,
        ):
            continue
        if _is_front_matter_attribution_heading(row):
            continue
        filtered.append(row)
    return filtered


def _paragraph_heading_rows(
    doc_index: _DocumentIndex,
) -> list[_HeadingRow]:
    """Return strict paragraph-based structural rows for heading-scan fallback."""
    rows: list[_HeadingRow] = []
    for paragraph in doc_index.paragraphs:
        if paragraph.is_toc:
            continue
        for heading_text in _split_play_heading_paragraph(paragraph.text):
            rows.append(_HeadingRow(paragraph.tag, paragraph.tag, heading_text, 7))
    return rows


def _should_scan_paragraph_heading_rows(
    heading_rows: list[_HeadingRow],
    paragraphs: list[_IndexedParagraph],
) -> bool:
    """Return True when paragraph-based play headings are worth scanning."""
    if not paragraphs:
        return False
    if any(_heading_text_suggests_play_structure(row.heading_text) for row in heading_rows):
        return True

    non_toc_scanned = 0
    for paragraph in paragraphs:
        if paragraph.is_toc:
            continue
        if _PLAY_HEADING_PARAGRAPH_RE.fullmatch(paragraph.text):
            return True
        non_toc_scanned += 1
        if non_toc_scanned >= 400:
            break
    return False


# ---------------------------------------------------------------------------
# Paragraph-text section fallback
# ---------------------------------------------------------------------------


def _parse_paragraph_sections(
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Extract sections from paragraph text when no heading tags exist.

    Some Gutenberg editions encode chapter headings as plain ``<p>`` elements
    (e.g. ``<p class="xhtml_p_align">CHAPTER I. TITLE</p>``).  When neither
    the TOC nor the heading scan finds structure, this function scans
    paragraphs for structural keyword patterns and creates sections from them.
    """
    sections: list[_Section] = []
    for ip in doc_index.paragraphs:
        match = _PARAGRAPH_SECTION_RE.match(ip.text)
        if not match:
            continue
        heading_text = _clean_heading_text(ip.text)
        if not heading_text:
            continue
        # Paragraph text can be much longer than a real heading tag;
        # truncate at a word boundary to keep div labels reasonable.
        if len(heading_text) > _MAX_PARAGRAPH_HEADING_LEN:
            last_space = heading_text.rfind(" ", 0, _MAX_PARAGRAPH_HEADING_LEN)
            heading_text = (
                heading_text[:last_space]
                if last_space > 0
                else heading_text[:_MAX_PARAGRAPH_HEADING_LEN]
            )
        level = _classify_level(heading_text, False)
        anchor_id = ""
        anchor = ip.tag.find("a", id=True)
        if anchor:
            anchor_id = str(anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, ip.tag, None))
    return sections


def _parse_toc_paragraph_sections(
    soup: BeautifulSoup,
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Build sections from TOC links whose targets are non-<a> elements.

    Some Gutenberg editions set ``id`` directly on ``<p>`` or ``<div>``
    elements instead of child ``<a>`` anchors (e.g. PG 39827 uses
    ``<p id="fate">``).  The normal scanner only collects ``<a>`` IDs, so
    these targets are invisible to :func:`_parse_toc_sections`.

    This function resolves the missing targets with ``soup.find(id=...)``
    and creates sections from the TOC link text.  It is called only when
    all other parsing strategies have failed.

    Unlike :func:`_parse_toc_sections`, this function does not handle the
    numeric-link-text / enumerated-TOC-entry fallback — books that reach
    this path have no resolved heading anchors at all, so the simpler
    structural-link filter is sufficient.

    .. note::

       Synthetic positions are written into ``doc_index.tag_positions``
       for newly-resolved targets.  This is safe because the function
       only runs as the last fallback, after all other passes have
       completed.
    """
    tag_positions = doc_index.tag_positions
    bounds = doc_index.bounds
    sections: list[_Section] = []
    used_ids: set[str] = set()
    # Monotonic counter for synthetic positions assigned to newly-resolved
    # targets.  Starts above the existing maximum so new entries sort after
    # all scanner-assigned positions without an O(n) max() per iteration.
    _next_pos = max(tag_positions.values(), default=0) + 1

    for link in doc_index.toc_links:
        if not _tag_within_bounds(link, tag_positions, bounds):
            continue
        link_text = _clean_heading_text(" ".join(link.get_text().split()))
        if not link_text:
            continue
        if not _is_structural_toc_link(link, link_text, doc_index=doc_index):
            continue
        href = str(link.get("href", ""))
        if not href.startswith("#"):
            continue
        anchor_id = href[1:]
        if anchor_id in used_ids:
            continue
        # Already resolved by the normal scanner — skip.
        if anchor_id in doc_index.anchor_map:
            continue
        target = soup.find(id=anchor_id)
        if target is None or not isinstance(target, Tag):
            continue
        # Assign a synthetic position for downstream processing.
        if id(target) not in tag_positions:
            tag_positions[id(target)] = _next_pos
            _next_pos += 1
        if not bounds.contains(tag_positions[id(target)]):
            continue
        used_ids.add(anchor_id)
        # link_text is already cleaned above; skip non-structural labels.
        if _is_non_structural_heading_text(link_text):
            continue
        level = _classify_level(link_text, _is_emphasized_toc_link(link))
        sections.append(_Section(anchor_id, link_text, level, target, 2))

    sections.sort(key=lambda s: tag_positions.get(id(s.body_anchor), float("inf")))
    return sections
