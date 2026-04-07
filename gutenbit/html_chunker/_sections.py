"""Section parsing, refinement, and normalization pipelines."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence

from bs4 import BeautifulSoup, Tag

from gutenbit.html_chunker._common import (
    _BARE_HEADING_NUMBER_RE,
    _BROAD_KEYWORDS,
    _FALLBACK_START_HEADING_RE,
    _HEADING_TAGS,
    _NUMERIC_LINK_TEXT_RE,
    _PLAY_HEADING_PARAGRAPH_RE,
    _STANDALONE_STRUCTURAL_RE,
    _TERMINAL_MARKER_RE,
    _clean_heading_text,
    _extract_heading_text,
    _heading_tag_rank,
    _HeadingRow,
    _Section,
)
from gutenbit.html_chunker._headings import (
    _DRAMATIC_CONTEXT_HEADING_RE,
    _PLAIN_NUMBER_HEADING_RE,
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
    _paragraphs_in_range,
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
# Thresholds for _merge_chapter_description_paragraphs
# ---------------------------------------------------------------------------

# Longest description paragraph to merge (chars).  Gutenberg chapter
# descriptions are typically 1–2 lines; anything longer is body prose.
_MAX_DESCRIPTION_PARAGRAPH_LEN = 300
# Minimum fraction of alpha chars that must be uppercase for the paragraph
# to be considered an ALL-CAPS description (allows minor OCR artifacts).
_MIN_UPPERCASE_RATIO = 0.9
# Maximum word count between duplicate heading pairs that qualifies as an
# epigraph/introductory poem rather than a full section body.  When the
# content between two same-text headings is at most this many words, the
# first heading is treated as an epigraph wrapper and merged with the second.
# Observed ceiling: PG 75942 MARY MOODY EMERSON at 353 words.  Cannot
# raise above 400 without false-positiving on PG 2302 (Poor Folk), an
# epistolary novel with short same-date letters that hit the ≥ 3 pairs guard.
_MAX_EPIGRAPH_WORDS = 400
# Maximum number of sections at min_level before _equalize_orphan_level_gap
# treats them as the primary structure rather than orphan outliers.
_MAX_ORPHAN_LEVEL_COUNT = 2
# Minimum ratio of next-level sections to min-level sections required before
# _equalize_orphan_level_gap will demote the min-level outliers.
_MIN_MAJORITY_RATIO = 3

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

# Subset of the stop-heading pattern that matches only "Conclusion"-family
# headings (bare "Conclusion", "Review, and Conclusion", etc.).  Used by
# the peer-chapter exemption to distinguish chapter-title "Conclusion" from
# genuine apparatus boundaries like APPENDIX.
_CONCLUSION_HEADING_RE = re.compile(
    r"^(?:(?:a\s+)?review\s*[,;]?\s*(?:and\s+)?)?conclusion\s*$",
    re.IGNORECASE,
)

# Trailing isolated page number on a heading (e.g. "THE WILL TO BELIEVE 1").
# Matches a space-separated bare integer at the end of the heading text.
# Used to detect printed-TOC entries that leak into the heading sequence.
_TRAILING_PAGE_NUMBER_RE = re.compile(r"\s+\d{1,4}\s*$")

# Minimum length for a printed-TOC run before it is suppressed.  Three entries
# keeps the filter far from real chapter titles that happen to end with a
# number (e.g. a standalone "ACT 2" or "HENRY V, PART 2").
_PRINTED_TOC_RUN_MIN = 3

# Minimum word count (excluding the trailing number) required for a heading
# to be treated as a printed-TOC entry.  Printed-TOC rows for essay
# collections are multi-word titles like "THE WILL TO BELIEVE" or "THE
# SENTIMENT OF RATIONALITY"; shorter forms like "Letter 1" or "Scene 2"
# are bare enumeration labels that must not be suppressed.
_PRINTED_TOC_MIN_BODY_WORDS = 3

# Publisher/copyright metadata paragraph patterns used by title-page
# stripping to determine whether the paragraphs between a leading title
# heading and the first real section are imprint noise rather than prose.
# These patterns are intentionally tight: each alternative must anchor to
# the start or end of the (short) paragraph line.
_PUBLISHER_METADATA_PARA_RE = re.compile(
    r"^(?:"
    r"by\s+[A-Z][^.]*$"  # "BY HENRY JAMES"
    r"|by\.?\s*$"  # standalone "BY" or "BY." (name on next line)
    r"|(?:translated|edited|illustrated|selected)"
    r"\s+(?:and\s+\w+\s+)?by\b"
    r"|(?:first\s+|originally\s+)?"
    r"(?:published|printed|issued|reprinted)\b"
    r"|copyright\b"
    r"|all\s+rights\s+reserved\b"
    r"|printed\s+in\b"
    r"|new\s+york\s*$"  # standalone city
    r"|london\s*$"
    r"|\d{4}[.,]?$"  # bare year "1921." or "1921"
    r"|[\w\s,.-]+\s+\d{4}\.?$"  # "London, 1921"
    r"|.{1,60}\b(?:&|and)\s+co\.?"
    r"(?:,\s*(?:ltd|inc|limited)\.?)?\s*$"
    r"|.{1,60}\bco\.?,?\s*(?:ltd|inc|limited)\.?\s*$"
    r"|.{1,60}\b(?:ltd|inc|limited|sons|press|"
    r"printers?|publishers?)\.?\s*$"
    r"|(?:volume|vol\.?|part)\s+[IVXLCDM0-9]+\.?\s*$"
    # Address line: "ST. MARTIN'S STREET, LONDON".  The trailing
    # comma + short city name + end-of-string acts as the practical
    # anchor (imprint lines end "STREET, LONDON", not mid-sentence).
    r"|[A-Z][\w'.-]*(?:\s+[\w'.-]+){0,4}\s*"
    r"(?:street|road|avenue|square|place|lane|"
    r"court|row|boulevard),\s*\S+\s*$"
    r")",
    re.IGNORECASE,
)

# Very short all-uppercase lines on a title page are likely author names
# or publisher fragments that fell on their own `<p>` element (e.g.
# "HENRY JAMES", "BOSTON").  Only matched in the publisher-metadata
# zone, NOT as a standalone classifier.
_SHORT_ALLCAPS_LINE_MAX_WORDS = 3

# Cap on paragraph count between a title heading and the first real
# section to qualify as a publisher-metadata-only title zone.  Keeps the
# check off substantive opening content like prologues or framing prose.
_MAX_TITLE_ZONE_METADATA_PARAS = 8

# Maximum word count per paragraph for the publisher-metadata check.
# Real prose paragraphs routinely exceed this; imprint lines are short.
_MAX_METADATA_PARA_WORDS = 12

# Broad keywords that are dramatic (plays) and should NOT participate in
# the single-instance container heuristic in _normalize_toc_heading_ranks.
# ACT/INDUCTION are peer keywords in plays, not structural containers.
_DRAMATIC_BROAD_KEYWORDS = frozenset({"act", "induction"})

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
        _page_anchor_total > 0
        and len(_page_anchor_headings) / _page_anchor_total >= 0.5
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
        cached_heading = (
            _page_anchor_headings.get(anchor_id) if _enable_page_anchors else None
        )
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
                if (
                    trim_idx >= 3
                    and _CONCLUSION_HEADING_RE.match(section.heading_text)
                ):
                    peer_count = sum(
                        1
                        for s in sections[:trim_idx]
                        if s.heading_rank == apparatus_rank
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
# Section merging and rank nesting
# ---------------------------------------------------------------------------


def _merge_bare_heading_pairs(sections: list[_Section]) -> list[_Section]:
    """Merge bare chapter-number headings with their immediately following subtitle.

    Detects the pattern ``<h3>CHAPTER I</h3><h5>WHO WILL BE THE NEW BISHOP?</h5>``
    (common in Project Gutenberg editions) and combines them into a single section
    with heading text ``"CHAPTER I WHO WILL BE THE NEW BISHOP?"``.

    Only merges when the subtitle is at a deeper level than the bare heading
    number — i.e., the subtitle is a child, not a sibling section.
    Separate TOC entries that happen to be adjacent at the same level (e.g.
    "CHAPTER 25" followed by a different work "LIGEIA") are never merged.
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
            and sections[i + 1].level > sec.level
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


def _merge_adjacent_duplicate_sections(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop immediately repeated section headings such as duplicate running headers.

    When three or more consecutive sections share the same text, level, and
    rank they are treated as deliberate structural repetition (e.g. four
    "LEGENDS OF THE PROVINCE HOUSE" headings each introducing a different
    story) and are all kept.  Runs of exactly two are collapsed to one —
    those are typically redundant heading tags in the source HTML.

    Guard: when *doc_index* is available and the positional gap between two
    same-text siblings is large enough to contain real content (> 8 document
    positions), the pair is kept — they are genuine distinct sections (e.g.
    two letters dated "July 28th." in an epistolary novel).

    Epigraph merge: when many same-text heading pairs (≥ 3) bracket short
    content (≤ _MAX_EPIGRAPH_WORDS words), the first heading of each pair is
    an epigraph wrapper and the pair is collapsed to keep only the second
    (body) heading.  This handles Emerson-style essays where each essay has
    an introductory poem bracketed by duplicate heading tags.
    """
    if len(sections) < 2:
        return sections

    tag_positions = doc_index.tag_positions

    # Pre-compute same-text run lengths so we can distinguish genuine
    # structural runs (≥3) from HTML-duplicate pairs (exactly 2).
    # NOTE: The threshold of 2 can false-positive on genuine 2-item
    # anthologies where both entries share the same series title.  This is
    # acceptable because true HTML duplicates (same heading emitted twice
    # by the source) are far more common than 2-item anthology runs.
    n = len(sections)
    run_length = [1] * n
    i = 0
    while i < n:
        j = i + 1
        while (
            j < n
            and sections[j].level == sections[i].level
            and sections[j].heading_rank == sections[i].heading_rank
            and _same_heading_text(sections[j].heading_text, sections[i].heading_text)
        ):
            j += 1
        length = j - i
        for k in range(i, j):
            run_length[k] = length
        i = j

    # Pass 1: identify epigraph pairs — same-text heading pairs (run of 2)
    # with a large-enough positional gap but short content between them.
    # The run_length invariant already guarantees that consecutive entries
    # with run_length == 2 share the same level, rank, and heading text.
    epigraph_pair_indices: set[int] = set()  # indices of the *first* section in a pair
    for idx in range(n - 1):
        if run_length[idx] != 2 or run_length[idx + 1] != 2:
            continue
        a, b = sections[idx], sections[idx + 1]
        a_pos = _tag_position(a.body_anchor, tag_positions)
        b_pos = _tag_position(b.body_anchor, tag_positions)
        if a_pos is None or b_pos is None or b_pos - a_pos <= 8:
            continue  # close gap — will be collapsed by the normal dedup
        between_words = sum(
            len(t.split())
            for t in _paragraphs_in_range(
                doc_index.paragraphs,
                doc_index.paragraph_positions,
                a_pos,
                b_pos,
            )
        )
        if between_words <= _MAX_EPIGRAPH_WORDS:
            epigraph_pair_indices.add(idx)

    # Only apply epigraph merge when the pattern is widespread (≥ 3 pairs),
    # preventing false positives on one-off date-heading duplicates in
    # epistolary novels.
    apply_epigraph_merge = len(epigraph_pair_indices) >= 3

    # Pass 2: build merged list.
    merged = [sections[0]]
    for idx, section in enumerate(sections[1:], start=1):
        previous = merged[-1]
        if (
            previous.level == section.level
            and previous.heading_rank == section.heading_rank
            and _same_heading_text(previous.heading_text, section.heading_text)
            and run_length[idx] <= 2
        ):
            prev_pos = _tag_position(previous.body_anchor, tag_positions)
            curr_pos = _tag_position(section.body_anchor, tag_positions)
            if prev_pos is not None and curr_pos is not None and curr_pos - prev_pos > 8:
                # Epigraph merge: replace the first heading with the second.
                if apply_epigraph_merge and (idx - 1) in epigraph_pair_indices:
                    merged[-1] = section
                    continue
                merged.append(section)
                continue
            # Small gap — normal HTML duplicate, drop the second.
            continue
        merged.append(section)
    return merged


def _strip_printed_toc_page_runs(sections: list[_Section]) -> list[_Section]:
    """Drop runs of non-keyword headings that end with a trailing page number.

    Some Gutenberg editions embed a printed table-of-contents page as
    heading tags whose text ends with an isolated page number (e.g.
    ``<h2>THE WILL TO BELIEVE 1</h2>``, ``<h2>THE SENTIMENT OF
    RATIONALITY 63</h2>``). These printed-TOC entries leak into the section
    list and duplicate the real essay headings that appear later.

    A run of at least :data:`_PRINTED_TOC_RUN_MIN` consecutive non-keyword
    headings with trailing page numbers is treated as a printed TOC and
    dropped.  Requiring ≥3 entries keeps the filter from touching isolated
    chapter titles that end in a number (e.g. a single ``ACT 2`` heading);
    requiring no structural keyword keeps it off runs like
    ``CHAPTER 1 / CHAPTER 2 / CHAPTER 3``.
    """
    if len(sections) < _PRINTED_TOC_RUN_MIN:
        return sections

    def _looks_printed_toc(section: _Section) -> bool:
        if _heading_keyword(section.heading_text):
            return False
        if _TRAILING_PAGE_NUMBER_RE.search(section.heading_text) is None:
            return False
        # Require the text before the trailing number to have enough words
        # to look like an essay title; bare enumeration labels like
        # "Letter 1" / "Scene 2" must survive.
        body = _TRAILING_PAGE_NUMBER_RE.sub("", section.heading_text).strip()
        return len(body.split()) >= _PRINTED_TOC_MIN_BODY_WORDS

    drop = [False] * len(sections)
    i = 0
    while i < len(sections):
        if not _looks_printed_toc(sections[i]):
            i += 1
            continue
        j = i + 1
        while j < len(sections) and _looks_printed_toc(sections[j]):
            j += 1
        if j - i >= _PRINTED_TOC_RUN_MIN:
            for k in range(i, j):
                drop[k] = True
        i = j

    if not any(drop):
        return sections
    return [section for section, dropped in zip(sections, drop, strict=True) if not dropped]


def _drop_empty_interior_title_repeats(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop interior title-like headings that duplicate an earlier title and own no content.

    Some editions repeat the work title as a decorative heading *after* the
    front matter (e.g. between the preface and ``BOOK FIRST``, or nested as
    an empty sibling inside a book container).  Those ghost headings add
    empty TOC entries with no content beneath them.

    A repeat is dropped when:

    1. Its text matches an earlier kept section's text (case-insensitive,
       punctuation-insensitive).
    2. No body paragraphs appear between it and the next section — i.e. it
       owns no content of its own.
    3. It is title-like (no structural keyword and not front/back matter),
       so deliberate ``PART I`` / ``BOOK I`` containers are unaffected.

    Deliberate anthology repetitions like *Twice-Told Tales* keep their
    copies because each one introduces body paragraphs of the next tale.
    """
    if len(sections) < 2:
        return sections

    seen_keys: set[str] = set()
    keep = [True] * len(sections)
    for idx, section in enumerate(sections):
        key = _heading_key(section.heading_text)
        if not _is_title_like_heading(section.heading_text):
            seen_keys.add(key)
            continue
        if key not in seen_keys:
            seen_keys.add(key)
            continue
        if idx + 1 >= len(sections):
            continue
        if _has_paragraphs_between(section, sections[idx + 1], doc_index=doc_index):
            continue
        keep[idx] = False

    if all(keep):
        return sections
    return [section for section, kept in zip(sections, keep, strict=True) if kept]


def _merge_chapter_subtitle_sections(
    sections: list[_Section],
    *,
    toc_anchor_ids: frozenset[str] = frozenset(),
) -> list[_Section]:
    """Merge a chapter heading with an immediately following non-keyword subtitle.

    Handles the pattern where ``<h2>CHAPTER ONE</h2>`` is followed by
    ``<h3>INTRODUCTORY, CONCERNING THE PEDIGREE…</h3>`` — the subtitle should
    be part of the chapter title, not a separate sub-section.

    Also handles title-like headings (no keyword) followed by a deeper-rank
    title-like subtitle — e.g. ``<h2>KING PEST</h2>`` followed by
    ``<h3>A Tale Containing an Allegory.</h3>``.

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
            shared_merge_conditions = (
                not _heading_keyword(nxt.heading_text)
                and nxt.heading_rank is not None
                and sec.heading_rank is not None
                and nxt.heading_rank == sec.heading_rank + 1
                and nxt.anchor_id not in toc_anchor_ids
            )
            # Merge keyword headings (CHAPTER, SCENE, etc.) — not BROAD.
            # Also merge title-like headings with their rank+1 subtitle.
            # For keyword headings, require nxt.level > sec.level to avoid
            # merging peers.  For title-like headings, level may have been
            # equalised by earlier passes, so rank alone is sufficient.
            # For title-like pairs, only merge when the subtitle has no
            # same-rank peers — if the next-next section is at the same rank,
            # we have sibling stories, not a title + subtitle.  Bare numbers
            # (Roman numerals, digits) on either side are never title + subtitle.
            nxt_is_lone_subtitle = False
            if not keyword:
                sec_stripped = sec.heading_text.strip()
                nxt_stripped = nxt.heading_text.strip()
                nxt_is_lone_subtitle = (
                    _is_title_like_heading(sec_stripped)
                    and not _PLAIN_NUMBER_HEADING_RE.fullmatch(sec_stripped)
                    and not _PLAIN_NUMBER_HEADING_RE.fullmatch(nxt_stripped)
                    and _next_heading_is_subtitle(nxt_stripped)
                    and not _DRAMATIC_CONTEXT_HEADING_RE.match(nxt_stripped)
                    and not _TERMINAL_MARKER_RE.fullmatch(nxt_stripped)
                    # Skip merge when nxt is the last section — a document-end
                    # heading is more likely a standalone closing entry (e.g.
                    # "THE END") than a subtitle of the previous section.
                    and i + 2 < len(sections)
                    and sections[i + 2].heading_rank != nxt.heading_rank
                )
            if shared_merge_conditions and (
                (keyword and keyword not in _BROAD_KEYWORDS and nxt.level > sec.level)
                or nxt_is_lone_subtitle
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
    parent_is_refinement = _is_refinement_heading(parent.heading_text)
    if parent_is_refinement:
        # Broad-keyword children (PART, BOOK, …) should only nest under
        # parents that themselves carry a keyword — never under a bare
        # numeral like "VIII" that happens to sit at a shallower rank due
        # to inconsistent source HTML tags.
        child_kw = _heading_keyword(child.heading_text)
        if child_kw and child_kw in _BROAD_KEYWORDS:
            return _heading_keyword(parent.heading_text) is not None
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

    # Pre-index keyword occurrences by (keyword, heading_rank) for the
    # keyword-peer reset check below.
    keyword_rank_seen: set[tuple[str, int]] = set()
    for s in sections:
        kw = _heading_keyword(s.heading_text)
        if kw and s.heading_rank is not None:
            keyword_rank_seen.add((kw, s.heading_rank))

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

        # Keyword-peer reset: when a keyword child (e.g. CHAPTER XVIII
        # at h5) finds a NON-keyword parent (e.g. "THE ECCENTRIC
        # PROJECTION" at h4) but the same keyword already appeared at
        # the same rank earlier (CHAPTER XVII at h5), the child may be
        # a sibling of that peer — not a child of the intervening non-
        # keyword subsection heading.
        #
        # Guard: only fire when the candidate parent is a subsection
        # heading (deeper rank than the peer's container), not a new
        # top-level section.  Find the container of the earlier peer
        # (the first heading before it with a shallower rank); if the
        # candidate parent's rank is at or shallower than that
        # container, it signals a structural reset (e.g. "SCENES" at
        # h2 replacing "OUR PARISH" at h2) and the nesting is correct.
        child_kw = _heading_keyword(section.heading_text)
        parent_kw = _heading_keyword(parent.heading_text)
        if child_kw and not parent_kw and (child_kw, section.heading_rank) in keyword_rank_seen:
            latest_peer_idx = -1
            for i in range(idx - 1, -1, -1):
                if (
                    _heading_keyword(sections[i].heading_text) == child_kw
                    and sections[i].heading_rank == section.heading_rank
                ):
                    latest_peer_idx = i
                    break
            if latest_peer_idx >= 0:
                if parent_idx > latest_peer_idx:
                    # Parent sits between the peer and the child.  Check
                    # the container rank of the peer: only reset when the
                    # parent is deeper (a subsection, not a new section).
                    peer_container_rank: int | None = None
                    peer_rank = sections[latest_peer_idx].heading_rank
                    if peer_rank is not None:
                        for i in range(latest_peer_idx - 1, -1, -1):
                            r = sections[i].heading_rank
                            if r is not None and r < peer_rank:
                                peer_container_rank = r
                                break
                    if peer_container_rank is None or (
                        parent.heading_rank is not None
                        and parent.heading_rank > peer_container_rank
                    ):
                        continue
                elif new_levels[latest_peer_idx] <= new_levels[parent_idx]:
                    # The peer comes after the parent but is at the same
                    # or shallower level — it already "reset" past the
                    # parent.  The current child should follow suit.
                    continue

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


def _paragraph_range_between(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> tuple[int, int] | None:
    """Return ``(lo, hi)`` paragraph-position indices between two sections.

    Returns ``None`` when either section's position is unknown.
    """
    pos_a = _tag_position(section_a.body_anchor, doc_index.tag_positions)
    pos_b = _tag_position(section_b.body_anchor, doc_index.tag_positions)
    if pos_a is None or pos_b is None:
        return None
    return (
        bisect_right(doc_index.paragraph_positions, pos_a),
        bisect_left(doc_index.paragraph_positions, pos_b),
    )


def _has_paragraphs_between(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True if any indexed paragraphs exist between two sections."""
    rng = _paragraph_range_between(section_a, section_b, doc_index=doc_index)
    if rng is None:
        return True  # assume content exists when positions are unknown
    return rng[0] < rng[1]


def _paragraphs_between_are_metadata_only(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True when every paragraph between *a* and *b* is imprint metadata.

    Recognises byline, publisher, city/year, copyright, and "VOLUME N" lines
    that sit on a title page between the book title and the first structural
    section.  Requires the paragraph count to be small and each paragraph to
    be short enough that real prose cannot slip through.
    """
    rng = _paragraph_range_between(section_a, section_b, doc_index=doc_index)
    if rng is None:
        return False
    lo, hi = rng
    if lo >= hi:
        return False  # no paragraphs — caller handles the empty case
    if hi - lo > _MAX_TITLE_ZONE_METADATA_PARAS:
        return False
    for ip in doc_index.paragraphs[lo:hi]:
        text = " ".join(ip.text.split()).strip(" .,:;")
        if not text:
            continue
        words = text.split()
        if len(words) > _MAX_METADATA_PARA_WORDS:
            return False
        if _PUBLISHER_METADATA_PARA_RE.match(text) is not None:
            continue
        # Short all-caps lines (≤ 3 words) like "HENRY JAMES" or
        # "BOSTON" are author names / publisher city fragments that
        # landed on their own <p>.  A bare Roman numeral like "IV"
        # would also pass this check, but that's harmless: Roman-
        # numeral chapter headings are <h*> tags, not <p> elements,
        # so they never appear in the title-page paragraph zone.
        if (
            len(words) <= _SHORT_ALLCAPS_LINE_MAX_WORDS
            and any(c.isalpha() for c in text)
            and all(c.isupper() or not c.isalpha() for c in text)
        ):
            continue
        return False
    return True


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
    if not all(
        _is_title_like_heading(s.heading_text)
        and not _FALLBACK_START_HEADING_RE.match(s.heading_text)
        for s in sections[:-1]
    ):
        return sections
    if _has_paragraphs_between(sections[0], sections[-1], doc_index=doc_index):
        return sections
    return [sections[-1]]


def _is_title_page_candidate(
    section: _Section,
    next_section: _Section | None,
    *,
    skip_children_guard: bool = False,
) -> bool:
    """Return True if *section* looks like a title-page heading to strip."""
    if _heading_keyword(section.heading_text):
        return False
    if _FALLBACK_START_HEADING_RE.match(section.heading_text):
        return False
    if not _is_title_like_heading(section.heading_text):
        return False
    # Bare enumeration labels (e.g. "I", "II", "1") and enumerated titles
    # (e.g. "I. FROM MISS AURORA CHURCH...") are chapter entries, not
    # title-page candidates.
    if _PLAIN_NUMBER_HEADING_RE.fullmatch(section.heading_text):
        return False
    if _starts_with_enumerated_heading_prefix(section.heading_text.strip()):
        return False
    if section.anchor_id:
        return False
    # Title-like sections with children at a deeper level are structural
    # containers (e.g. "OUR PARISH" nesting chapters), not title pages.
    if not skip_children_guard and next_section is not None and next_section.level > section.level:
        return False
    return True


def _strip_leading_title_page_sections(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop title-like sections at the start that precede all real structure.

    A leading prefix of sections where each is ``_is_title_like_heading`` and
    has no structural keyword or front-matter keyword is a title-page cluster.
    Drop it when no content paragraphs exist between the cluster and the first
    structural section (prevents false positives on legitimate title headings
    with body text like *The Metamorphosis*).

    Publisher-metadata bypass: when the paragraphs between the leading cluster
    and the first real section are ALL short imprint lines (byline, publisher
    name, city/year, copyright), treat the zone as empty and strip the cluster.
    The children-depth guard is also bypassed in this case, since real chapter
    containers never have only imprint metadata between their title and first
    child.
    """
    if len(sections) < 2:
        return sections

    def _first_non_candidate(*, skip_children_guard: bool = False) -> int | None:
        return next(
            (
                idx
                for idx, section in enumerate(sections)
                if not _is_title_page_candidate(
                    section,
                    sections[idx + 1] if idx + 1 < len(sections) else None,
                    skip_children_guard=skip_children_guard,
                )
            ),
            None,
        )

    # First pass: strict guards (no children, no paragraphs).
    first_real = _first_non_candidate()
    if (
        first_real is not None
        and first_real > 0
        and not _has_paragraphs_between(sections[0], sections[first_real], doc_index=doc_index)
    ):
        return sections[first_real:]

    # Second pass: publisher-metadata bypass.
    first_real_relaxed = _first_non_candidate(skip_children_guard=True)
    if (
        first_real_relaxed is not None
        and first_real_relaxed > 0
        and _paragraphs_between_are_metadata_only(
            sections[0], sections[first_real_relaxed], doc_index=doc_index
        )
    ):
        return sections[first_real_relaxed:]

    return sections


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


def _normalize_collection_titles(sections: list[_Section]) -> list[_Section]:
    """Promote repeated title rows that act as anthology/work containers."""
    if len(sections) < 3:
        return sections

    def _is_collection_title(section: _Section) -> bool:
        if _is_front_matter_heading(section.heading_text):
            return False
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
            if _heading_keyword(next_section.heading_text) in _BROAD_KEYWORDS:
                # Guard: when the broad keyword immediately follows this
                # title (no non-collection sections between) and another
                # collection title at the same level preceded us, skip.
                # This prevents poetry collections (PG 1322 Leaves of
                # Grass) from being falsely promoted.  When intermediate
                # non-collection sections exist (e.g. front-matter like
                # Dedication/Preface in PG 29363), the guard is bypassed
                # so the work title can still be detected as a container.
                non_collection_between = sum(
                    1
                    for j in range(idx + 1, next_idx)
                    if not _is_collection_title(sections[j])
                )
                if (
                    non_collection_between == 0
                    and _has_same_level_collection_title_since_lower_level(
                        idx, level=section.level
                    )
                ):
                    break
                container_title_indices_by_level[section.level].append(idx)
                break

    # Two-tier threshold: ≥2 containers always qualifies; a single
    # container qualifies only when the title count is small (≤10),
    # indicating a real collected edition rather than a chapter-rich
    # book (e.g. PG 1998 Zarathustra has 82 discourse titles at the
    # same level as INTRODUCTION, but only 1 container — that's not a
    # collected edition).
    _MAX_TITLES_FOR_SINGLE_CONTAINER = 10
    promoted_levels = {
        level
        for level, container_indices in container_title_indices_by_level.items()
        if len(title_indices_by_level[level]) >= 3
        and (
            len(container_indices) >= 2
            or (
                len(container_indices) >= 1
                and len(title_indices_by_level[level]) <= _MAX_TITLES_FOR_SINGLE_CONTAINER
            )
        )
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


def _broad_keywords_at_modal_rank(
    sections: list[_Section],
) -> frozenset[str]:
    """Return broad keywords whose modal heading rank matches the overall mode.

    Only fires for heading-scan sections (no TOC anchors), exactly one
    broad keyword type present, and ≥ 80% of sections sharing the same
    rank — i.e. the HTML used a single tag for everything and the
    keyword-based level-1 assignment is misleading.
    """
    # TOC-driven sections have anchor_ids → hierarchy is authoritative.
    if any(s.anchor_id for s in sections):
        return frozenset()

    all_ranks: Counter[int] = Counter()
    broad_kw_ranks: dict[str, Counter[int]] = {}
    for section in sections:
        if section.heading_rank is None:
            continue
        all_ranks[section.heading_rank] += 1
        kw = _heading_keyword(section.heading_text)
        if kw and kw in _BROAD_KEYWORDS and kw not in _DRAMATIC_BROAD_KEYWORDS:
            broad_kw_ranks.setdefault(kw, Counter())[section.heading_rank] += 1

    if not all_ranks or len(broad_kw_ranks) != 1:
        return frozenset()

    overall_mode = all_ranks.most_common(1)[0][0]
    mode_count = all_ranks[overall_mode]
    total_ranked = sum(all_ranks.values())
    if mode_count < total_ranked * 0.8:
        return frozenset()

    return frozenset(
        kw for kw, ranks in broad_kw_ranks.items() if ranks.most_common(1)[0][0] == overall_mode
    )


def _demote_same_rank_broad_keywords(
    sections: list[_Section],
    *,
    demote_keywords: frozenset[str],
) -> list[_Section]:
    """Demote broad keywords to chapter level when they share the modal rank.

    *demote_keywords* is the output of :func:`_broad_keywords_at_modal_rank`,
    computed once in the pipeline and reused here to avoid a redundant
    iteration over all sections.
    """
    if len(sections) < 3 or not demote_keywords:
        return sections

    new_sections = []
    changed = False
    for section in sections:
        kw = _heading_keyword(section.heading_text)
        if kw and kw in demote_keywords and section.level == 1:
            new_sections.append(section._with_level(2))
            changed = True
        else:
            new_sections.append(section)

    return new_sections if changed else sections


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


def _nest_chapters_under_broad_containers(
    sections: list[_Section],
    *,
    skip_keywords: frozenset[str] = frozenset(),
) -> list[_Section]:
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
        # Skip keywords that were demoted by _demote_same_rank_broad_keywords.
        if keyword in skip_keywords:
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
    Guard: when the wrapper has title-like peers at *min_level* AND its
    direct children are all bare enumeration labels (``I``, ``II``, ``III``),
    the wrapper is one essay among many in a collection and its bare-numeral
    children are sub-sections — skip flattening to preserve that nesting.
    """
    if len(sections) < 2:
        return sections

    min_level = min(s.level for s in sections)

    # Precompute direct children at min_level + 1 for each min_level
    # section in a single forward pass.  Used both for the peer-essay
    # check and for the wrapper-identification loop below.
    children_of: dict[int, list[_Section]] = {}
    for idx in range(len(sections)):
        if sections[idx].level != min_level:
            continue
        children: list[_Section] = []
        for next_idx in range(idx + 1, len(sections)):
            if sections[next_idx].level <= min_level:
                break
            if sections[next_idx].level == min_level + 1:
                children.append(sections[next_idx])
        if children:
            children_of[idx] = children

    has_peer_essay = any(
        s.level == min_level
        and _is_title_like_heading(s.heading_text)
        and not _is_front_matter_heading(s.heading_text)
        and not _starts_with_enumerated_heading_prefix(s.heading_text.strip())
        and idx not in children_of
        for idx, s in enumerate(sections)
    )

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
        direct_children = children_of.get(idx)
        if not direct_children:
            continue
        # Essay-collection bare-numeral sub-section guard.
        if has_peer_essay and all(
            _PLAIN_NUMBER_HEADING_RE.fullmatch(child.heading_text) is not None
            for child in direct_children
        ):
            return sections
        wrapper_indices.append(idx)
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
                if j > idx + 1 and nxt.rank < 4:
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

# Matches paragraph text that starts with a structural keyword followed by
# a number or Roman numeral — indicating a chapter/section heading encoded
# as a plain <p>, not an <h1>–<h6>.
_PARAGRAPH_SECTION_RE = re.compile(
    r"^(?:CHAPTER|SECTION|BOOK|PART|VOLUME|LECTURE)\s+[IVXLCDM0-9]+\b",
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

    sections.sort(
        key=lambda s: tag_positions.get(id(s.body_anchor), float("inf"))
    )
    return sections
