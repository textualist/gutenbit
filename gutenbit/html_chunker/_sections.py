"""Section parsing, refinement, and normalization pipelines."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence

from bs4 import Tag

from gutenbit.html_chunker._common import (
    _BARE_HEADING_NUMBER_RE,
    _BROAD_KEYWORDS,
    _FALLBACK_START_HEADING_RE,
    _HEADING_TAGS,
    _NUMERIC_LINK_TEXT_RE,
    _PLAY_HEADING_PARAGRAPH_RE,
    _STANDALONE_STRUCTURAL_RE,
    _clean_heading_text,
    _extract_heading_text,
    _heading_tag_rank,
    _HeadingRow,
    _Section,
)
from gutenbit.html_chunker._headings import (
    _broad_heading_with_enumerated_child,
    _broad_nesting_depth,
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
    _next_heading_is_subtitle,
    _normalized_heading_continuation,
    _rank_relative_level,
    _same_heading_text,
    _split_play_heading_paragraph,
    _toc_link_refines_body_heading,
    _update_dramatic_context_state,
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
# Compiled regex patterns (used only within this module)
# ---------------------------------------------------------------------------

# Tail-boundary pattern: only clearly apparatus headings, not ambiguous
# singular "NOTE" which can be a narrative epilogue (e.g. Dracula).
_TAIL_BOUNDARY_HEADING_RE = re.compile(
    r"^(?:footnotes?|endnotes?|notes\b|transcriber'?s?\s+note|editor'?s?\s+note)",
    re.IGNORECASE,
)
_TAIL_SECTION_HEADING_RE = re.compile(
    r"^(?:note\b|note to\b|letter\b|a letter from\b|finale\b|the conclusion\b|"
    r"author'?s?\s+endnotes?\b)",
    re.IGNORECASE,
)

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

    for link in toc_links:
        if not _tag_within_bounds(link, tag_positions, bounds):
            continue
        raw_link_text = _clean_heading_text(" ".join(link.get_text().split()))
        link_text = raw_link_text
        if not _is_structural_toc_link(link, raw_link_text, doc_index=doc_index):
            context_text = _toc_context_text(link)
            if not (
                _NUMERIC_LINK_TEXT_RE.fullmatch(raw_link_text)
                and _looks_enumerated_toc_entry(context_text)
            ):
                continue
            link_text = context_text
        href = str(link.get("href", ""))
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
        used_fallback_heading = False
        if not heading_el:
            used_fallback_heading = True
            heading_el = _find_next_heading(
                body_anchor,
                used_headings,
                doc_index=doc_index,
            )
        if not heading_el or id(heading_el) in used_headings:
            continue
        used_headings.add(id(heading_el))

        heading_text = _clean_heading_text(_extract_heading_text(heading_el))
        if not heading_text:
            continue
        if _is_non_structural_heading_text(heading_text):
            continue
        if used_fallback_heading and not _toc_entry_matches_heading(link_text, heading_text):
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
    # Remove a leading title section whose heading is a prefix of the next section's
    # heading (e.g. "ADVENTURES OF SHERLOCK HOLMES" before "ADVENTURES OF SHERLOCK
    # HOLMES A SCANDAL IN BOHEMIA"). Require a space after the prefix to avoid
    # false matches like "CHAPTER I" / "CHAPTER II".
    if len(sections) >= 2 and sections[1].heading_text.startswith(sections[0].heading_text + " "):
        sections = sections[1:]
    return _respect_heading_rank_nesting(_merge_bare_heading_pairs(sections))


# ---------------------------------------------------------------------------
# Section merging and rank nesting
# ---------------------------------------------------------------------------


def _merge_bare_heading_pairs(sections: list[_Section]) -> list[_Section]:
    """Merge bare chapter-number headings with their immediately following subtitle.

    Detects the pattern ``<h3>CHAPTER I</h3><h5>WHO WILL BE THE NEW BISHOP?</h5>``
    (common in Project Gutenberg editions) and combines them into a single section
    with heading text ``"CHAPTER I WHO WILL BE THE NEW BISHOP?"``.
    """
    if len(sections) < 2:
        return sections

    merged: list[_Section] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if (
            i + 1 < len(sections)
            and _BARE_HEADING_NUMBER_RE.fullmatch(sec.heading_text)
            and not _broad_heading_with_enumerated_child(
                sec.heading_text,
                sections[i + 1].heading_text,
            )
            and _next_heading_is_subtitle(sections[i + 1].heading_text)
        ):
            # Merge: keep anchor and level from the chapter-number heading,
            # combine heading text.
            next_sec = sections[i + 1]
            combined_text = f"{sec.heading_text} {next_sec.heading_text}"
            combined_level = _classify_level(combined_text, False)
            merged.append(
                _Section(
                    sec.anchor_id,
                    combined_text,
                    combined_level,
                    sec.body_anchor,
                    sec.heading_rank,
                )
            )
            i += 2
        else:
            merged.append(sec)
            i += 1
    return merged


def _merge_adjacent_duplicate_sections(sections: list[_Section]) -> list[_Section]:
    """Drop immediately repeated section headings such as duplicate running headers."""
    if len(sections) < 2:
        return sections

    merged = [sections[0]]
    for section in sections[1:]:
        previous = merged[-1]
        if (
            previous.level == section.level
            and previous.heading_rank == section.heading_rank
            and _same_heading_text(previous.heading_text, section.heading_text)
        ):
            continue
        merged.append(section)
    return merged


def _respect_heading_rank_nesting(sections: list[_Section]) -> list[_Section]:
    """Raise levels when heading ranks show a section was flattened too far."""
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        if section.heading_rank is None:
            continue

        parent: _Section | None = None
        for previous in reversed(sections[:idx]):
            if previous.heading_rank is None or previous.heading_rank >= section.heading_rank:
                continue
            if not _is_refinement_heading(previous.heading_text):
                continue
            parent = previous
            break

        if parent is None:
            continue

        if new_levels[idx] > parent.level:
            continue

        if _heading_keyword(section.heading_text) == _heading_keyword(parent.heading_text):
            continue

        new_levels[idx] = min(4, parent.level + 1)
        changed = True

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


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

    return _respect_heading_rank_nesting(_drop_leading_repeated_title_sections(sections))


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
            if _FALLBACK_START_HEADING_RE.match(candidate.heading_text):
                refined.append(candidate._with_level(min(candidate.level, first_toc.level)))
                added += 1
            heading_idx += 1

    for toc_idx, toc_section in enumerate(toc_sections):
        refined.append(toc_section)
        start_pos = _tag_position(toc_section.body_anchor, tag_positions)
        if start_pos is None:
            continue

        next_pos: int | None = None
        if toc_idx + 1 < len(toc_sections):
            next_pos = _tag_position(toc_sections[toc_idx + 1].body_anchor, tag_positions)

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
            if _same_heading_text(candidate.heading_text, toc_section.heading_text):
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
    if _is_title_like_heading(candidate.heading_text):
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
    return candidate if candidate.level > toc_section.level else None


# ---------------------------------------------------------------------------
# Hierarchy normalization
# ---------------------------------------------------------------------------


def _normalize_collection_titles(sections: list[_Section]) -> list[_Section]:
    """Promote repeated title rows that act as anthology/work containers."""
    if len(sections) < 3:
        return sections

    def _is_collection_title(section: _Section) -> bool:
        return _is_title_like_heading(section.heading_text) and (
            section.heading_rank is None or section.heading_rank <= 2
        )

    title_indices_by_level: dict[int, list[int]] = defaultdict(list)
    container_title_indices_by_level: dict[int, list[int]] = defaultdict(list)

    def _has_same_level_collection_title_since_lower_level(title_idx: int, *, level: int) -> bool:
        for previous_idx in range(title_idx - 1, -1, -1):
            previous_section = sections[previous_idx]
            if previous_section.level < level:
                return False
            if previous_section.level == level and _is_collection_title(previous_section):
                return True
        return False

    for idx, section in enumerate(sections):
        if not _is_collection_title(section):
            continue
        title_indices_by_level[section.level].append(idx)

        for next_idx in range(idx + 1, len(sections)):
            next_section = sections[next_idx]
            if _is_collection_title(next_section) and next_section.level == section.level:
                break
            next_depth = _broad_nesting_depth(next_section.heading_text)
            if next_depth is None:
                continue
            if _has_same_level_collection_title_since_lower_level(idx, level=section.level):
                break
            if _heading_keyword(next_section.heading_text) in _BROAD_KEYWORDS:
                container_title_indices_by_level[section.level].append(idx)
                break

    promoted_levels = {
        level
        for level, container_indices in container_title_indices_by_level.items()
        if len(container_indices) >= 2 and len(title_indices_by_level[level]) >= 3
    }
    if not promoted_levels:
        return sections

    new_levels = [section.level for section in sections]
    for level in sorted(promoted_levels):
        title_indices = title_indices_by_level[level]
        for idx in title_indices:
            new_levels[idx] = max(1, sections[idx].level - 1)
        for index_pos, idx in enumerate(title_indices):
            next_idx = (
                title_indices[index_pos + 1]
                if index_pos + 1 < len(title_indices)
                else len(sections)
            )
            for section_idx in range(idx + 1, next_idx):
                new_levels[section_idx] += 1

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _nest_broad_subdivisions(sections: list[_Section]) -> list[_Section]:
    """Nest same-rank broad headings when their keywords imply containment.

    Some Gutenberg editions use the same heading rank for containers like
    ``PART`` and their child ``Book`` headings. Preserve that hierarchy by
    shifting the inner broad run and its descendants one level deeper.
    """
    if len(sections) < 3:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        outer_depth = _broad_nesting_depth(section.heading_text)
        if outer_depth is None:
            continue

        outer_level = new_levels[idx]
        found_nested_broad = False

        for inner_idx in range(idx + 1, len(sections)):
            current_level = new_levels[inner_idx]
            if current_level < outer_level:
                break

            current_depth = _broad_nesting_depth(sections[inner_idx].heading_text)
            if current_level == outer_level:
                if current_depth is None or current_depth <= outer_depth:
                    break
                found_nested_broad = True

            if not found_nested_broad:
                continue

            if current_level == outer_level or current_level > outer_level:
                shifted_level = min(4, current_level + 1)
                if shifted_level != current_level:
                    new_levels[inner_idx] = shifted_level
                    changed = True

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _promote_more_prominent_heading_runs(sections: list[_Section]) -> list[_Section]:
    """Promote runs whose heading rank outranks their assigned parent.

    Some books place opening matter like ``Proem`` in ``h2`` and then start the
    real top-level work structure in ``h1``. If the later run is currently
    nested under that lower-rank opener, lift the run until its first section
    becomes a sibling of the false parent.
    """
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False
    idx = 0

    while idx < len(sections):
        current_level = new_levels[idx]
        current_rank = sections[idx].heading_rank
        if current_level <= 1 or current_rank is None:
            idx += 1
            continue

        parent_idx: int | None = None
        for previous_idx in range(idx - 1, -1, -1):
            if new_levels[previous_idx] < current_level:
                parent_idx = previous_idx
                break

        if parent_idx is None:
            idx += 1
            continue

        parent_rank = sections[parent_idx].heading_rank
        parent_level = new_levels[parent_idx]
        if parent_rank is None or current_rank >= parent_rank:
            idx += 1
            continue

        shift = current_level - parent_level
        run_end = idx
        while run_end < len(sections) and new_levels[run_end] > parent_level:
            promoted_level = max(1, new_levels[run_end] - shift)
            if promoted_level != new_levels[run_end]:
                new_levels[run_end] = promoted_level
                changed = True
            run_end += 1

        idx = run_end

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


# ---------------------------------------------------------------------------
# Leading title deduplication
# ---------------------------------------------------------------------------


def _leading_title_cluster_start_index(
    items: Sequence[_Section | _HeadingRow],
    *,
    first_front_matter_idx: int,
) -> int:
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
            return idx
    return None


def _filter_fallback_heading_rows(heading_rows: list[_HeadingRow]) -> list[_HeadingRow]:
    """Drop heading-scan rows that are clearly non-navigational subheads."""
    filtered: list[_HeadingRow] = []
    for idx, row in enumerate(heading_rows):
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
