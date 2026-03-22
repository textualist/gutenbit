"""Section parsing, refinement, and normalization pipelines."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
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
    _is_bare_keyword_heading,
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
    _next_heading_is_subtitle,
    _normalized_heading_continuation,
    _rank_relative_level,
    _same_heading_text,
    _split_play_heading_paragraph,
    _starts_with_enumerated_heading_prefix,
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
# Thresholds for _merge_chapter_description_paragraphs
# ---------------------------------------------------------------------------

# Longest description paragraph to merge (chars).  Gutenberg chapter
# descriptions are typically 1–2 lines; anything longer is body prose.
_MAX_DESCRIPTION_PARAGRAPH_LEN = 300
# Minimum fraction of alpha chars that must be uppercase for the paragraph
# to be considered an ALL-CAPS description (allows minor OCR artifacts).
_MIN_UPPERCASE_RATIO = 0.9
# Maximum number of sections at min_level before _equalize_orphan_level_gap
# treats them as the primary structure rather than orphan outliers.
_MAX_ORPHAN_LEVEL_COUNT = 2
# Minimum ratio of next-level sections to min-level sections required before
# _equalize_orphan_level_gap will demote the min-level outliers.
_MIN_MAJORITY_RATIO = 3

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
# Headings after the last TOC entry that mark apparatus (appendices,
# notes on the text).  Once one of these is added during refinement,
# further body headings are not promoted to standalone sections.
# NOTE: This is intentionally broad — it may match non-apparatus headings
# like "Notes on the Author" in edge cases.  If that causes mis-truncation,
# add a position-relative guard (e.g. only trigger past the last TOC entry).
# The third alternative uses \b after "conclusion" to allow trailing text
# (e.g. "A Review, and Conclusion of the Whole"); the fourth uses $ to
# match bare "Conclusion" or "Conclusion " without requiring a word after it.
_REFINEMENT_STOP_HEADING_RE = re.compile(
    r"^(?:appendix|notes\s+on\b|(?:a\s+)?review\s*[,;]?\s*(?:and\s+)?conclusion\b|conclusion\s*$)",
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
            if _NUMERIC_LINK_TEXT_RE.fullmatch(raw_link_text) and _looks_enumerated_toc_entry(
                context_text
            ):
                link_text = context_text
            else:
                continue
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
                candidate_text = _clean_heading_text(
                    _extract_heading_text(candidate)
                )
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
            if apparatus_rank is not None:
                has_higher_rank_after = any(
                    s.heading_rank is not None
                    and s.heading_rank < apparatus_rank
                    for s in sections[trim_idx + 1 :]
                )
            else:
                has_higher_rank_after = False
            if not has_higher_rank_after:
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


def _merge_chapter_subtitle_sections(
    sections: list[_Section],
    *,
    toc_anchor_ids: frozenset[str] = frozenset(),
) -> list[_Section]:
    """Merge a chapter heading with an immediately following non-keyword subtitle.

    Handles the pattern where ``<h2>CHAPTER ONE</h2>`` is followed by
    ``<h3>INTRODUCTORY, CONCERNING THE PEDIGREE…</h3>`` — the subtitle should
    be part of the chapter title, not a separate sub-section.

    Merging is skipped when the subtitle has a structural keyword, or when the
    subtitle's anchor appears in *toc_anchor_ids* (it was a deliberate TOC
    entry and should remain a standalone section).

    Complements :func:`_merge_chapter_description_paragraphs`, which merges
    subtitles encoded as ``<p>`` elements rather than heading tags.
    """
    if len(sections) < 2:
        return sections

    merged: list[_Section] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if i + 1 < len(sections):
            nxt = sections[i + 1]
            keyword = _heading_keyword(sec.heading_text)
            if (
                keyword
                and keyword not in _BROAD_KEYWORDS
                and not _heading_keyword(nxt.heading_text)
                and nxt.heading_rank is not None
                and sec.heading_rank is not None
                and nxt.heading_rank == sec.heading_rank + 1
                and nxt.level > sec.level
                and nxt.anchor_id not in toc_anchor_ids
            ):
                combined = f"{sec.heading_text} {nxt.heading_text}"
                merged.append(
                    _Section(
                        sec.anchor_id,
                        combined,
                        sec.level,
                        sec.body_anchor,
                        sec.heading_rank,
                    )
                )
                i += 2
                continue
        merged.append(sec)
        i += 1
    return merged


def _merge_chapter_description_paragraphs(
    sections: list[_Section],
) -> tuple[list[_Section], set[int]]:
    """Merge ALL-CAPS description ``<p>`` elements into bare chapter headings.

    Returns the updated sections and a set of paragraph tag ``id()`` values
    to skip during chunk emission (safe because the same BeautifulSoup parse
    tree is alive for the entire ``chunk_html`` call).

    Handles the common Gutenberg pattern where a chapter heading like
    ``<h2>CHAPTER TWO</h2>`` is followed by a ``<p>WHEREIN CERTAIN
    PERSONS ARE PRESENTED TO THE READER…</p>`` that is really the
    chapter description, not body text.

    Complements :func:`_merge_chapter_subtitle_sections`, which merges
    subtitles encoded as *heading* elements (``<h3>``).  This function
    targets subtitles encoded as *paragraph* elements (``<p>``).
    """
    skip_paragraph_ids: set[int] = set()
    new_sections = list(sections)

    for idx, sec in enumerate(new_sections):
        keyword = _heading_keyword(sec.heading_text)
        if (
            not keyword
            or keyword in _BROAD_KEYWORDS
            or not _is_bare_keyword_heading(sec.heading_text, keyword)
        ):
            continue

        # Find the heading element and then the next <p> sibling.
        anchor = sec.body_anchor
        heading_el = anchor.find_parent(_HEADING_TAGS) or anchor
        next_p: Tag | None = None
        for sibling in heading_el.next_siblings:
            if isinstance(sibling, Tag):
                if sibling.name in _HEADING_TAGS:
                    break  # hit next heading, no description paragraph
                if sibling.name == "p":
                    next_p = sibling
                    break

        if next_p is None:
            continue

        ptext = " ".join(next_p.get_text().split()).strip()
        if not ptext or len(ptext) > _MAX_DESCRIPTION_PARAGRAPH_LEN:
            continue

        # Check if the text is ALL-CAPS (allow minor non-alpha chars).
        alpha_chars = [c for c in ptext if c.isalpha()]
        if not alpha_chars:
            continue
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        if upper_ratio < _MIN_UPPERCASE_RATIO:
            continue

        # Merge description into heading text.
        combined = f"{sec.heading_text} {ptext}"
        new_sections[idx] = _Section(
            sec.anchor_id,
            combined,
            sec.level,
            sec.body_anchor,
            sec.heading_rank,
        )
        skip_paragraph_ids.add(id(next_p))

    return new_sections, skip_paragraph_ids


def _is_valid_rank_parent(
    parent: _Section,
    child: _Section,
    *,
    infer_from_rank: bool,
) -> bool:
    """Return True when *parent* can serve as a rank-based container for *child*.

    In the strict TOC path (*infer_from_rank* False), only refinement headings
    (those with a structural keyword) qualify as parents.

    In the relaxed heading-scan path (*infer_from_rank* True), non-keyword
    headings like ``OUR PARISH`` may parent when the rank gap is exactly 1,
    except front-matter headings which should never act as containers.
    """
    if _is_refinement_heading(parent.heading_text):
        return True
    if not infer_from_rank:
        return False
    if child.heading_rank is None or parent.heading_rank is None:
        return False
    if child.heading_rank - parent.heading_rank != 1:
        return False
    return not _is_front_matter_heading(parent.heading_text)


def _should_skip_same_keyword_nesting(
    parent: _Section,
    child: _Section,
    *,
    infer_from_rank: bool,
) -> bool:
    """Return True when same-keyword parent/child should NOT nest.

    Sequential headings sharing a keyword (e.g. CHAPTER I, CHAPTER II) are
    siblings, not parent-child.  In heading-scan mode, same-keyword nesting
    is allowed when the child rank is strictly deeper and the keyword is not
    a broad container (e.g. ``h3 CHAPTER I → h4 CHAPTER I.``).
    """
    parent_kw = _heading_keyword(parent.heading_text)
    child_kw = _heading_keyword(child.heading_text)
    if not parent_kw or parent_kw != child_kw:
        return False
    if not infer_from_rank:
        return True
    # Broad keywords (BOOK, PART, …) are handled by the separate
    # _nest_chapters_under_broad_containers pass, so always skip here
    # to avoid double-nesting.
    if parent_kw in _BROAD_KEYWORDS:
        return True
    if parent.heading_rank is None or child.heading_rank is None:
        return True
    return child.heading_rank <= parent.heading_rank


def _respect_heading_rank_nesting(
    sections: list[_Section],
    *,
    infer_from_rank: bool = False,
) -> list[_Section]:
    """Raise levels when heading ranks show a section was flattened too far.

    When *infer_from_rank* is True (heading-scan fallback only), non-keyword
    headings may serve as parents when the rank gap is exactly 1 (e.g. h2 "OUR
    PARISH" → h3 "CHAPTER I").  In the TOC path the hierarchy is already
    authoritative, so this relaxation is disabled by default.
    """
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        if section.heading_rank is None:
            continue

        parent_idx: int | None = None
        for prev_idx in range(idx - 1, -1, -1):
            previous = sections[prev_idx]
            if previous.heading_rank is None or previous.heading_rank >= section.heading_rank:
                continue
            if _is_valid_rank_parent(previous, section, infer_from_rank=infer_from_rank):
                parent_idx = prev_idx
                break

        if parent_idx is None:
            continue

        parent = sections[parent_idx]
        # Use the (possibly updated) parent level so that multi-level
        # rank chains (e.g. h2 → h3 → h4) nest correctly.
        effective_parent_level = new_levels[parent_idx]
        if new_levels[idx] > effective_parent_level:
            continue

        if _should_skip_same_keyword_nesting(parent, section, infer_from_rank=infer_from_rank):
            continue

        new_levels[idx] = min(4, effective_parent_level + 1)
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

    sections = _drop_leading_repeated_title_sections(sections)
    sections = _collapse_degenerate_title_block(sections, doc_index=doc_index)
    return _respect_heading_rank_nesting(sections, infer_from_rank=True)


def _collapse_degenerate_title_block(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Collapse title-only heading runs with no content into the last section.

    When heading-scan finds only decorative title-page headings (no structural
    keywords, no front-matter start headings) and no body paragraphs appear
    before the final section, the headings are title fragments — not real
    structure.  Keep only the last section, which owns all content.
    """
    if len(sections) < 2:
        return sections
    # Every section except the last must be a pure title-like heading.
    if not all(
        _is_title_like_heading(s.heading_text)
        and not _FALLBACK_START_HEADING_RE.match(s.heading_text)
        for s in sections[:-1]
    ):
        return sections
    first_pos = _tag_position(sections[0].body_anchor, doc_index.tag_positions)
    last_pos = _tag_position(sections[-1].body_anchor, doc_index.tag_positions)
    if first_pos is None or last_pos is None:
        return sections
    lo = bisect_right(doc_index.paragraph_positions, first_pos)
    hi = bisect_left(doc_index.paragraph_positions, last_pos)
    if lo < hi:
        return sections  # content exists before last section
    return [sections[-1]]


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


def _nest_chapters_under_broad_containers(sections: list[_Section]) -> list[_Section]:
    """Nest chapter-level sections under broad keyword containers at the same level.

    When BOOK and CHAPTER share the same heading rank (both ``<h3>``), the
    broad keyword (BOOK) is a structural container and the chapter keyword
    should sit one level deeper.  This handles works like Les Misérables
    where VOLUME > BOOK > CHAPTER all appear in the TOC.
    """
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        keyword = _heading_keyword(section.heading_text)
        if keyword not in _BROAD_KEYWORDS:
            continue
        # Front-matter headings like "PREFACE TO THE FIRST VOLUME" match a
        # broad keyword but are not structural containers.
        if _is_front_matter_heading(section.heading_text):
            continue

        broad_level = new_levels[idx]

        for inner_idx in range(idx + 1, len(sections)):
            inner_level = new_levels[inner_idx]
            if inner_level < broad_level:
                break
            if inner_level == broad_level:
                inner_kw = _heading_keyword(sections[inner_idx].heading_text)
                if inner_kw and inner_kw in _BROAD_KEYWORDS:
                    break  # next broad container at same level — stop
                # Standalone structural headings and apparatus closures are
                # top-level peers, not children of the preceding broad keyword.
                inner_text = sections[inner_idx].heading_text
                if _STANDALONE_STRUCTURAL_RE.search(inner_text):
                    break
                if _REFINEMENT_STOP_HEADING_RE.match(inner_text):
                    break
                shifted = min(4, inner_level + 1)
                if shifted != inner_level:
                    new_levels[inner_idx] = shifted
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
# Single-work title wrapper flattening
# ---------------------------------------------------------------------------


def _flatten_single_work_title_wrapper(sections: list[_Section]) -> list[_Section]:
    """Flatten title-like headings that wrap structural children.

    When a non-keyword heading (e.g. "Metamorphosis", "THE PRINCE") sits one
    level above enumerated chapters, it is a work title — not a structural
    container.  Promote its children so chapters become peers at min_level.

    Guard: ≥ 2 title-like wrappers at *min_level* → anthology (Shakespeare) → skip.
    """
    if len(sections) < 2:
        return sections

    min_level = min(s.level for s in sections)

    # Identify title-like sections at min_level that have children one level deeper.
    wrapper_indices: list[int] = []
    for idx, section in enumerate(sections):
        if section.level != min_level:
            continue
        if not _is_title_like_heading(section.heading_text):
            continue
        # Indexed headings (e.g. "I. A SCANDAL IN BOHEMIA") are sections
        # within a larger work, not work titles — don't flatten them.
        if _starts_with_enumerated_heading_prefix(section.heading_text.strip()):
            continue
        # Check for at least one direct child at min_level + 1.
        for next_idx in range(idx + 1, len(sections)):
            if sections[next_idx].level <= min_level:
                break
            if sections[next_idx].level == min_level + 1:
                wrapper_indices.append(idx)
                break
        if len(wrapper_indices) >= 2:
            return sections

    if not wrapper_indices:
        return sections

    # Flatten: shift every descendant of the single wrapper up by 1.
    new_levels = [s.level for s in sections]

    for wrapper_idx in wrapper_indices:
        # Find the span of children (up to the next min_level section).
        span_end = len(sections)
        for next_idx in range(wrapper_idx + 1, len(sections)):
            if sections[next_idx].level <= min_level:
                span_end = next_idx
                break

        for i in range(wrapper_idx + 1, span_end):
            new_levels[i] = max(1, new_levels[i] - 1)

    return [s._with_level(new_levels[i]) for i, s in enumerate(sections)]


def _equalize_orphan_level_gap(sections: list[_Section]) -> list[_Section]:
    """Demote orphan min-level sections when the vast majority sit one level deeper.

    In PG 946 (Lady Susan) the TOC puts CONCLUSION at level 1 while the 41
    Roman-numeral letters land at level 2, producing empty div1 slots.  When
    only a tiny minority (≤ 2) of non-keyword sections occupy min_level and the
    next level has ≥ 3× as many sections, flatten the outliers down.
    """
    if len(sections) < 3:
        return sections

    min_level = min(s.level for s in sections)
    at_min = [i for i, s in enumerate(sections) if s.level == min_level]
    at_next = [i for i, s in enumerate(sections) if s.level == min_level + 1]

    if len(at_min) > _MAX_ORPHAN_LEVEL_COUNT or len(at_next) < len(at_min) * _MIN_MAJORITY_RATIO:
        return sections

    # Don't demote structural containers (BOOK, PART, etc.).
    if any(_heading_keyword(sections[i].heading_text) in _BROAD_KEYWORDS for i in at_min):
        return sections

    # Don't demote sections that have children at the next level — they are
    # legitimate wrappers (e.g. a collection title), not orphans.
    for i in at_min:
        for j in range(i + 1, len(sections)):
            if sections[j].level <= min_level:
                break
            if sections[j].level == min_level + 1:
                return sections

    new_levels = [s.level for s in sections]
    for i in at_min:
        new_levels[i] = min_level + 1

    return [s._with_level(new_levels[i]) for i, s in enumerate(sections)]


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


# ---------------------------------------------------------------------------
# Paragraph-text section fallback
# ---------------------------------------------------------------------------

# Matches paragraph text that starts with a structural keyword followed by
# a number or Roman numeral — indicating a chapter/section heading encoded
# as a plain <p>, not an <h1>–<h6>.
_PARAGRAPH_SECTION_RE = re.compile(
    r"^(?:CHAPTER|SECTION|BOOK|PART|VOLUME)\s+[IVXLCDM0-9]+\b",
    re.IGNORECASE,
)


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
        if len(heading_text) > 120:
            last_space = heading_text.rfind(" ", 0, 120)
            heading_text = heading_text[:last_space] if last_space > 0 else heading_text[:120]
        level = _classify_level(heading_text, False)
        anchor_id = ""
        anchor = ip.tag.find("a", id=True)
        if anchor:
            anchor_id = str(anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, ip.tag, None))
    return sections
