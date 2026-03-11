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

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
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

    Kinds: ``"heading"``, ``"text"``
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
_STRUCTURAL_KEYWORD_ALIASES = {
    "actus": "act",
    "scena": "scene",
    "scoena": "scene",
}
CHUNKER_VERSION = 17

# Bare chapter-number headings: "CHAPTER I", "CHAPTER IV.", "BOOK 2" etc.
# with no subtitle text — used to merge consecutive number + title headings.
_BARE_HEADING_NUMBER_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)"
    r"\.?\s+[IVXLCDM0-9]+\.?$",
    re.IGNORECASE,
)

_HEADING_KEYWORD_RE = re.compile(
    r"^(?:BOOK|PART|ACT|ACTUS|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SCENA|SCOENA|SECTION|ADVENTURE)\.?\s",
    re.IGNORECASE,
)
_START_DELIMITER_RE = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b",
    re.IGNORECASE,
)
_END_DELIMITER_RE = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b",
    re.IGNORECASE,
)
_HEADING_CITATION_SUFFIX_RE = re.compile(r"\s*\[\d+\]\s*$")
_STRUCTURAL_HEADING_SPACING_RE = re.compile(
    r"\b(BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)(\.?)\s*([IVXLCDM0-9]+)\b",
    re.IGNORECASE,
)
_STRUCTURAL_HEADING_TRAILER_RE = re.compile(
    r"(\b(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)\.?\s*[IVXLCDM0-9]+\b.*)$",
    re.IGNORECASE,
)
_BRACKETED_NUMERIC_HEADING_RE = re.compile(r"^\[\s*\d+\s*\]$")
_NUMERIC_LINK_TEXT_RE = re.compile(r"^\[?\d+\]?$")
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
_PLAIN_NUMBER_HEADING_RE = re.compile(r"^(?:[IVXLCDM]+|[0-9]+)\.?$", re.IGNORECASE)
_STRUCTURAL_INDEX_TOKEN_RE = re.compile(
    r"^(?:[IVXLCDM]+|[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|"
    r"sixteenth|seventeenth|eighteenth|nineteenth|twentieth|"
    r"primus|prima|secundus|secunda|tertius|tertia|quartus|quarta|"
    r"quintus|quinta|sextus|sexta|septimus|septima|octavus|octava|"
    r"nonus|nona|decimus|decima)$",
    re.IGNORECASE,
)
_PAGE_HEADING_RE = re.compile(r"^(?:page|p\.)\s+\d+\b", re.IGNORECASE)
_NON_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:notes?|footnotes?|endnotes?|transcriber's note|transcribers note|"
    r"editor's note|editors note|finis)\b",
    re.IGNORECASE,
)
_FRONT_MATTER_ATTRIBUTION_RE = re.compile(
    r"^(?:by|translated\s+by|edited\s+by|illustrated\s+by)\s",
    re.IGNORECASE,
)
_FRONT_MATTER_HEADINGS = frozenset(
    {
        "contents",
        "illustrations",
        "table of contents",
        "list of illustrations",
    }
)

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_PLAY_HEADING_PARAGRAPH_RE = re.compile(
    r"^(?:(?P<act>(?:ACTUS|ACT)\s+[A-Z0-9IVXLCDM]+\.?)"
    r"(?:\s+(?P<scene>(?:SC(?:OE|E)NA|SCENE)\s+[A-Z0-9IVXLCDM]+\.?))?"
    r"|(?P<scene_only>(?:SC(?:OE|E)NA|SCENE)\s+[A-Z0-9IVXLCDM]+\.?))$",
    re.IGNORECASE,
)
_TRAILING_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:THE\s+)?(?P<index>[A-Z0-9]+)\s+"
    r"(?P<keyword>BOOK|PART|ACT|ACTUS|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SCENA|"
    r"SCOENA|SECTION|ADVENTURE)\.?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Section:
    """A section parsed from the TOC."""

    anchor_id: str
    heading_text: str
    level: int  # 1 = broad (BOOK/PART), 2 = chapter, 3 = sub-chapter
    body_anchor: Tag
    heading_rank: int | None


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
    """Split an HTML book into labelled chunks using TOC plus body heading cues.

    Each ``<p>`` element becomes its own chunk. Returns chunks in document order.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag_positions = _tag_positions(soup)
    subtree_end_positions = _build_subtree_end_positions(soup, tag_positions)
    bounds = _find_gutenberg_bounds(soup, tag_positions, subtree_end_positions)
    paragraphs, paragraph_positions = _build_paragraph_index(soup, tag_positions, bounds)
    doc_index = _DocumentIndex(
        tag_positions=tag_positions,
        subtree_end_positions=subtree_end_positions,
        paragraphs=paragraphs,
        paragraph_positions=paragraph_positions,
    )

    # Build section list from TOC links and refine with body headings when the
    # TOC is a coarse but valid subsequence of the body structure.
    toc_sections = _parse_toc_sections(soup, tag_positions=tag_positions, bounds=bounds)
    if toc_sections:
        heading_sections = _parse_heading_sections(
            soup,
            doc_index=doc_index,
            bounds=bounds,
        )
        sections = _refine_toc_sections(
            toc_sections,
            heading_sections,
            tag_positions=tag_positions,
        )
    else:
        # Some Gutenberg editions expose page-number TOC links only.
        sections = _parse_heading_sections(soup, doc_index=doc_index, bounds=bounds)
    if not sections:
        return []

    sections = _normalize_collection_titles(sections)

    # Compact levels so the shallowest level maps to div1.
    # e.g. chapter-only books (min_level=2) shift chapters to div1.
    min_level = min(s.level for s in sections)
    if min_level > 1:
        sections = [
            _Section(
                s.anchor_id,
                s.heading_text,
                s.level - min_level + 1,
                s.body_anchor,
                s.heading_rank,
            )
            for s in sections
        ]

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
        sections[-1].body_anchor, tag_positions=tag_positions, bounds=bounds
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
        raw_link_text = _clean_heading_text(" ".join(link.get_text().split()))
        link_text = raw_link_text
        if not _is_structural_toc_link(link, raw_link_text):
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
                tag_positions=tag_positions,
                bounds=bounds,
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
    return _merge_bare_heading_pairs(sections)


# Matches a structural heading pattern anywhere in text (keyword + number).
# Used to reject subtitles that contain embedded headings like "... CHAPTER II".
_EMBEDDED_HEADING_RE = re.compile(
    r"(?:BOOK|PART|ACT|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)"
    r"\.?\s+[IVXLCDM0-9]+",
    re.IGNORECASE,
)

# Keywords that are almost exclusively structural even without a trailing number.
_STANDALONE_STRUCTURAL_RE = re.compile(
    r"\bEPILOGUE\b|\bPROLOGUE\b|\bAPPENDIX\b",
    re.IGNORECASE,
)
_NON_SUBTITLE_HEADING_RE = re.compile(r"^(?:chap(?:ters?)?)\.?$", re.IGNORECASE)
_SYNOPSIS_SUFFIX_RE = re.compile(r"\s+SYNOPSIS OF\b.*$", re.IGNORECASE)
_EDITORIAL_PLACEHOLDER_HEADING_RE = re.compile(
    r"(?:\[\s*(?:not\b|omitted\b|wanting\b)|\bnot in early editions\b)",
    re.IGNORECASE,
)
_ENUMERATED_SUBHEADING_RE = re.compile(r"^(?:[IVXLCDM]+|[0-9]+)\.\s+\S", re.IGNORECASE)
_LIST_ITEM_MARKER_RE = re.compile(r"(?:^|\s)(?:[IVXLCDM]+|[0-9]+)\.\s+\S", re.IGNORECASE)
_STANDALONE_APPARATUS_HEADING_RE = re.compile(r"^SYNOPSIS OF\b", re.IGNORECASE)
_FONT_SIZE_STYLE_RE = re.compile(
    r"font-size\s*:\s*([0-9.]+)\s*(%|em|rem|px)",
    re.IGNORECASE,
)
_FALLBACK_START_HEADING_RE = re.compile(
    r"^(?:preface|introduction|introductory note|prelude|prologue\b|"
    r"note\b|note to\b|letter\b|a letter from\b|the publisher to the reader\b|"
    r"before the curtain\b|etymology\b|extracts\b|some commendatory verses\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _HeadingRow:
    """One cleaned section candidate used by heading-scan fallback."""

    tag: Tag
    anchor: Tag
    heading_text: str
    rank: int


def _next_heading_is_subtitle(heading_text: str) -> bool:
    """Return True when a heading looks like a chapter subtitle, not a structural division."""
    if _NON_SUBTITLE_HEADING_RE.fullmatch(heading_text):
        return False
    if _HEADING_KEYWORD_RE.match(heading_text):
        return False
    if _is_dialogue_speaker_heading(heading_text):
        return False
    # Reject if the text contains an embedded structural heading pattern
    # (e.g. "I hope Mr. Bingley will like it. CHAPTER II"), but allow
    # incidental uses of keywords as ordinary words (e.g. "ACT OF PARLIAMENT",
    # "A LOVE SCENE", "THE DEAN AND CHAPTER TAKE COUNSEL").
    if _EMBEDDED_HEADING_RE.search(heading_text):
        return False
    # EPILOGUE, PROLOGUE, APPENDIX are almost always structural divisions.
    return not _STANDALONE_STRUCTURAL_RE.search(heading_text)


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


def _parse_heading_sections(
    soup: BeautifulSoup,
    *,
    doc_index: _DocumentIndex,
    bounds: _ContentBounds,
) -> list[_Section]:
    """Fallback section extraction directly from body headings.

    Used when TOC links don't point at structural anchors (e.g., page-number
    links only). We start from the first heading that looks structural.
    """
    heading_rows: list[_HeadingRow] = []
    for heading in soup.find_all(_HEADING_TAGS):
        if not _tag_within_bounds(heading, doc_index.tag_positions, bounds):
            continue
        heading_text = _clean_heading_text(_extract_heading_text(heading))
        if not heading_text:
            continue
        if _is_non_structural_heading_text(heading_text):
            continue
        rank = _heading_tag_rank(heading)
        if rank is None:
            continue
        anchor = heading.find("a", id=True) or heading
        heading_rows.append(_HeadingRow(heading, anchor, heading_text, rank))

    heading_rows.extend(
        _paragraph_heading_rows(
            soup,
            doc_index=doc_index,
            bounds=bounds,
        )
    )

    if not heading_rows:
        return []

    heading_rows.sort(
        key=lambda row: _tag_position(row.tag, doc_index.tag_positions) or float("inf")
    )
    heading_rows = _filter_fallback_heading_rows(heading_rows)
    if not heading_rows:
        return []

    start_idx = _fallback_start_index(heading_rows)
    if start_idx is None:
        return []

    sections: list[_Section] = []
    i = start_idx
    while i < len(heading_rows):
        row = heading_rows[i]
        heading_text = row.heading_text
        next_row = heading_rows[i + 1] if i + 1 < len(heading_rows) else None

        if _is_rank5_subheading_under_nonchapter_section(
            row,
            previous_kept_heading=sections[-1].heading_text if sections else None,
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

        if _BARE_HEADING_NUMBER_RE.fullmatch(heading_text) and next_row is not None:
            subtitle = _normalized_heading_continuation(
                row,
                next_row,
                doc_index=doc_index,
            )
            if subtitle:
                heading_text = f"{heading_text} {subtitle}"
                i += 1

        elif _is_ignorable_fallback_heading(heading_text):
            i += 1
            continue

        level = _classify_level(heading_text, False)
        anchor_id = str(row.anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, row.anchor, row.rank))
        i += 1

    return sections


def _refine_toc_sections(
    toc_sections: list[_Section],
    heading_sections: list[_Section],
    *,
    tag_positions: dict[int, int],
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
            if not _is_refinement_heading(candidate.heading_text):
                scan_idx += 1
                continue
            if _candidate_refines_toc(candidate, toc_section):
                refined.append(candidate)
                added += 1
            scan_idx += 1

        heading_idx = scan_idx

    return refined if added else toc_sections


# Tail-boundary pattern: only clearly apparatus headings, not ambiguous
# singular "NOTE" which can be a narrative epilogue (e.g. Dracula).
_TAIL_BOUNDARY_HEADING_RE = re.compile(
    r"^(?:footnotes?|endnotes?|notes\b|transcriber'?s?\s+note|editor'?s?\s+note)",
    re.IGNORECASE,
)


def _find_non_structural_boundary_after(
    anchor: Tag,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> Tag | None:
    """Find the first apparatus heading after *anchor* (e.g. FOOTNOTES, NOTES).

    Returns the heading tag itself so its position can be used as a stop boundary
    for paragraph collection.  Uses a restrictive pattern to avoid false positives
    on narrative headings like a singular "NOTE" epilogue.
    """
    for el in anchor.find_all_next(_HEADING_TAGS):
        if not isinstance(el, Tag):
            continue
        if not _tag_within_bounds(el, tag_positions, bounds):
            continue
        heading_text = _clean_heading_text(_extract_heading_text(el))
        if heading_text and _TAIL_BOUNDARY_HEADING_RE.match(heading_text):
            return el
    return None


def _find_next_heading(
    anchor: Tag,
    used_headings: set[int] | None = None,
    *,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> Tag | None:
    """Find the next ``<h1>``–``<h3>`` heading after *anchor*."""
    for el in anchor.find_all_next(limit=25):
        if isinstance(el, Tag) and el.name in ("h1", "h2", "h3"):
            if used_headings is not None and id(el) in used_headings:
                continue
            if not _tag_within_bounds(el, tag_positions, bounds):
                continue
            return el
    return None


def _extract_heading_text(heading_el: Tag) -> str:
    """Get clean heading text from a heading tag.

    Handles: ``<br>`` line breaks, inline formatting (``<i>``, ``<b>``, etc.),
    ``<img alt="...">`` fallback, and strips ``<span class="pagenum">`` elements.
    """
    has_pagenum = heading_el.find("span", class_="pagenum") is not None
    has_br = heading_el.find("br") is not None
    has_img = heading_el.find("img") is not None

    # Fast path: no special elements to strip or replace.
    if not has_pagenum and not has_br:
        text = " ".join(heading_el.get_text().split()).strip()
        if text:
            return text
        if has_img:
            img = heading_el.find("img", alt=True)
            if img:
                return " ".join(str(img["alt"]).split()).strip()
        return ""

    # Slow path: re-parse only when we need to modify the tree.
    heading_copy = BeautifulSoup(str(heading_el), "html.parser")
    for pagenum in heading_copy.select("span.pagenum"):
        pagenum.decompose()

    root = heading_copy.find(_HEADING_TAGS) or heading_copy

    for br in root.find_all("br"):
        br.replace_with(" ")

    text = " ".join(root.get_text().split()).strip()
    if text:
        return text

    img = root.find("img", alt=True)
    if img:
        return " ".join(str(img["alt"]).split()).strip()
    return ""


def _clean_heading_text(heading_text: str) -> str:
    """Normalize heading text and strip trailing citation counters."""
    text = " ".join(heading_text.split()).strip()
    text = _HEADING_CITATION_SUFFIX_RE.sub("", text)
    text = _STRUCTURAL_HEADING_SPACING_RE.sub(r"\1\2 \3", text)
    if _BRACKETED_NUMERIC_HEADING_RE.fullmatch(text):
        return text
    text = text.rstrip(" .,;:])")
    trailer_match = _STRUCTURAL_HEADING_TRAILER_RE.search(text)
    if trailer_match:
        prefix = text[: trailer_match.start()].strip(" .,:;!?'\"-")
        if prefix:
            text = trailer_match.group(1).strip()
    return text


def _is_non_structural_heading_text(heading_text: str) -> bool:
    """Return True for apparatus headings that should not become sections."""
    text = " ".join(heading_text.split()).strip()
    lowered = text.lower()
    if lowered in _FRONT_MATTER_HEADINGS:
        return True
    if _PAGE_HEADING_RE.match(text):
        return True
    if _FRONT_MATTER_ATTRIBUTION_RE.match(text):
        return True
    return _NON_STRUCTURAL_HEADING_RE.match(text) is not None


def _heading_keyword(heading_text: str) -> str:
    match = _HEADING_KEYWORD_RE.match(heading_text)
    if match:
        keyword = heading_text.split()[0].rstrip(".,:]").lower()
        canonical = _STRUCTURAL_KEYWORD_ALIASES.get(keyword, keyword)

        remainder = heading_text[len(heading_text.split()[0]) :].lstrip(" .,:;!?-—–")
        tokens = [token.lower() for token in re.split(r"[^A-Za-z0-9]+", remainder) if token]
        if not tokens:
            return canonical
        index_token = tokens[1] if len(tokens) > 1 and tokens[0] == "the" else tokens[0]
        if _STRUCTURAL_INDEX_TOKEN_RE.fullmatch(index_token):
            return canonical
        return ""

    trailing_match = _TRAILING_STRUCTURAL_HEADING_RE.fullmatch(heading_text)
    if not trailing_match:
        return ""

    index_token = trailing_match.group("index").lower()
    if not _STRUCTURAL_INDEX_TOKEN_RE.fullmatch(index_token):
        return ""

    keyword = trailing_match.group("keyword").lower()
    return _STRUCTURAL_KEYWORD_ALIASES.get(keyword, keyword)


def _heading_key(heading_text: str) -> str:
    return _NON_ALNUM_RE.sub("", heading_text.lower())


def _same_heading_text(left: str, right: str) -> bool:
    return _heading_key(left) == _heading_key(right)


def _is_title_like_heading(heading_text: str) -> bool:
    if _heading_keyword(heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return False
    if _is_dialogue_speaker_heading(heading_text):
        return False
    return not _is_non_structural_heading_text(heading_text)


def _toc_link_refines_body_heading(link_text: str, heading_text: str) -> bool:
    if not link_text or _same_heading_text(link_text, heading_text):
        return False
    if _NUMERIC_LINK_TEXT_RE.fullmatch(link_text):
        return False
    if not _is_refinement_heading(link_text):
        return False
    if not _is_refinement_heading(heading_text):
        return False
    return _classify_level(link_text, False) > _classify_level(heading_text, False)


def _classify_level(heading_text: str, is_emphasized_in_toc: bool) -> int:
    """Determine structural level: 1=broad (BOOK/PART), 2=chapter, 3=sub-chapter."""
    if is_emphasized_in_toc:
        return 1
    keyword = _heading_keyword(heading_text)
    if keyword:
        if keyword in _BROAD_KEYWORDS:
            return 1
        if keyword == "section":
            return 3
        return 2
    return 2


def _candidate_refines_toc(candidate: _Section, toc_section: _Section) -> bool:
    if _is_title_like_heading(toc_section.heading_text):
        return True
    return candidate.level > toc_section.level


def _normalize_collection_titles(sections: list[_Section]) -> list[_Section]:
    """Promote repeated title rows that act as anthology/work containers."""
    if len(sections) < 3:
        return sections

    title_indices_by_level: dict[int, list[int]] = defaultdict(list)
    container_title_indices_by_level: dict[int, list[int]] = defaultdict(list)

    for idx, section in enumerate(sections):
        if not _is_title_like_heading(section.heading_text):
            continue
        title_indices_by_level[section.level].append(idx)

        for next_idx in range(idx + 1, len(sections)):
            next_section = sections[next_idx]
            if (
                _is_title_like_heading(next_section.heading_text)
                and next_section.level == section.level
            ):
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
            next_idx = title_indices[index_pos + 1] if index_pos + 1 < len(title_indices) else len(
                sections
            )
            for section_idx in range(idx + 1, next_idx):
                new_levels[section_idx] += 1

    return [
        _Section(
            section.anchor_id,
            section.heading_text,
            new_levels[idx],
            section.body_anchor,
            section.heading_rank,
        )
        for idx, section in enumerate(sections)
    ]


@dataclass(frozen=True, slots=True)
class _IndexedParagraph:
    """A paragraph tag with its pre-computed position and extracted text."""

    position: int
    text: str
    is_toc: bool


@dataclass(frozen=True, slots=True)
class _DocumentIndex:
    """Precomputed tag and paragraph indices for one HTML document."""

    tag_positions: dict[int, int]
    subtree_end_positions: dict[int, int]
    paragraphs: list[_IndexedParagraph]
    paragraph_positions: list[int]


def _build_paragraph_index(
    soup: BeautifulSoup,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> tuple[list[_IndexedParagraph], list[int]]:
    """Pre-collect all usable paragraphs with positions and text.

    Builds a sorted list once so section-range queries use bisect instead of
    repeated ``find_all_next("p")`` traversals.
    """
    body = soup.find("body")
    if not body:
        return [], []
    result: list[_IndexedParagraph] = []
    positions: list[int] = []
    for p in body.find_all("p"):
        pos = tag_positions.get(id(p))
        if pos is None or not bounds.contains(pos):
            continue
        text = _extract_paragraph_text(p)
        if text:
            result.append(_IndexedParagraph(pos, text, _is_toc_paragraph(p)))
            positions.append(pos)
    return result, positions


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
            if lowered in _heading_texts or lowered in _FRONT_MATTER_HEADINGS:
                continue
        paragraphs.append(ip.text)
    return paragraphs


def _extract_paragraph_text(paragraph: Tag) -> str:
    """Get clean paragraph text, preserving drop-cap img ``alt`` text.

    Strips ``<span class="pagenum">`` page-number markers and replaces
    ``<img>`` tags with their ``alt`` text.
    """
    # Fast path: most paragraphs have no pagenum spans or images.
    has_pagenum = paragraph.find("span", class_="pagenum") is not None
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


_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _container_residue_without_link_text(container: Tag) -> str:
    """Return container text after removing each internal-link label once."""
    residue = container.get_text()
    for link in container.find_all("a", class_="pginternal"):
        link_text = link.get_text()
        if link_text:
            residue = residue.replace(link_text, "", 1)
    return " ".join(residue.split()).strip()


def _is_toc_paragraph(paragraph: Tag) -> bool:
    """Return True for TOC/navigation paragraphs."""
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


def _is_toc_context_link(link: Tag) -> bool:
    """Return True when *link* sits in a TOC-like container."""
    if link.find_parent("tr") is not None:
        return True

    for name in ("p", "li", "div"):
        container = link.find_parent(name)
        if container is None:
            continue
        if container.name == "p" and _is_toc_paragraph(container):
            return True

        classes = {str(c).lower() for c in (container.get("class") or [])}
        if "toc" in classes or "contents" in classes:
            return True

        residue = _container_residue_without_link_text(container)
        if _NON_ALNUM_RE.sub("", residue) == "":
            return True
    return False


def _toc_context_text(link: Tag) -> str:
    """Return nearby non-link TOC text for a link, if any."""
    for name in ("tr", "p", "li", "div"):
        container = link.find_parent(name)
        if container is None:
            continue
        text = _clean_heading_text(_container_residue_without_link_text(container))
        if text:
            return text
    return ""


def _looks_enumerated_toc_entry(text: str) -> bool:
    """Return True for entries like ``I. Title`` or ``12. Title``."""
    if not text:
        return False
    first_token = text.split(maxsplit=1)[0].rstrip(".)")
    return _ROMAN_NUMERAL_RE.fullmatch(first_token) is not None or first_token.isdigit()


def _previous_heading_text(link: Tag) -> str:
    """Return the nearest preceding heading text, if any."""
    for heading in link.find_all_previous(_HEADING_TAGS):
        if not isinstance(heading, Tag):
            continue
        text = _clean_heading_text(_extract_heading_text(heading))
        if text:
            return text
    return ""


def _is_structural_toc_link(link: Tag, link_text: str | None = None) -> bool:
    """Return True for TOC links that can map to actual section headings."""
    if not _is_toc_context_link(link):
        return False

    previous_heading = _previous_heading_text(link).lower()
    if previous_heading in _FRONT_MATTER_HEADINGS and previous_heading not in {
        "contents",
        "table of contents",
    }:
        return False

    link_classes = {str(cls).lower() for cls in (link.get("class") or [])}
    if "citation" in link_classes:
        return False

    paragraph = link.find_parent("p")
    if paragraph is not None and _is_dense_chapter_index_paragraph(paragraph):
        cleaned_link_text = _clean_heading_text(" ".join(link.get_text().split()))
        chapter_marker = cleaned_link_text.rstrip(".,;:)")
        if cleaned_link_text.lower().startswith("chapter") or _ROMAN_NUMERAL_RE.fullmatch(
            chapter_marker
        ):
            return False

    href = str(link.get("href", ""))
    if href.startswith("#"):
        target_id = href[1:].lower()
        if target_id.startswith(("footnote", "citation")):
            return False

    if link.find_parent("span", class_="indexpageno"):
        pass
    if link.find_parent("span", class_="pagenum"):
        return False

    if link_text is None:
        link_text = _clean_heading_text(" ".join(link.get_text().split()))
    if not link_text:
        return False
    if _NUMERIC_LINK_TEXT_RE.fullmatch(link_text):
        return False
    # Filter front-matter headings (CONTENTS, ILLUSTRATIONS, etc.)
    if _is_non_structural_heading_text(link_text):
        return False
    # Filter bare roman numerals (I, II, III — sub-section markers, not chapters)
    return not _ROMAN_NUMERAL_RE.fullmatch(link_text)


def _toc_entry_matches_heading(entry_text: str, heading_text: str) -> bool:
    """Return True when a TOC entry label clearly aligns with a heading."""
    if not entry_text:
        return False
    if _same_heading_text(entry_text, heading_text):
        return True

    entry_key = _heading_key(entry_text)
    heading_key = _heading_key(heading_text)
    if len(entry_key) < 6:
        return False
    return entry_key in heading_key or heading_key in entry_key


def _heading_tag_rank(tag: Tag) -> int | None:
    if tag.name and len(tag.name) == 2 and tag.name.startswith("h") and tag.name[1].isdigit():
        return int(tag.name[1])
    return None


def _fallback_start_index(heading_rows: list[_HeadingRow]) -> int | None:
    """Return the first body-structure heading to use for heading-scan fallback."""
    structural_rows = [
        (idx, row)
        for idx, row in enumerate(heading_rows)
        if _is_fallback_start_heading_text(row.heading_text)
    ]
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
        if _is_single_letter_subheading(row):
            continue
        if _is_deep_rank_bare_numeral_heading(row):
            continue
        if _is_short_uppercase_stage_heading(row):
            continue
        filtered.append(row)
    return filtered


def _paragraph_heading_rows(
    soup: BeautifulSoup,
    *,
    doc_index: _DocumentIndex,
    bounds: _ContentBounds,
) -> list[_HeadingRow]:
    """Return strict paragraph-based structural rows for heading-scan fallback."""
    rows: list[_HeadingRow] = []
    for paragraph in soup.find_all("p"):
        if not _tag_within_bounds(paragraph, doc_index.tag_positions, bounds):
            continue
        if _is_toc_paragraph(paragraph):
            continue
        for heading_text in _split_play_heading_paragraph(_extract_paragraph_text(paragraph)):
            rows.append(_HeadingRow(paragraph, paragraph, heading_text, 7))
    return rows


def _split_play_heading_paragraph(paragraph_text: str) -> list[str]:
    """Split strict play-heading paragraphs into act/scene section labels."""
    text = " ".join(paragraph_text.split()).strip()
    match = _PLAY_HEADING_PARAGRAPH_RE.fullmatch(text)
    if not match:
        return []

    parts: list[str] = []
    act = match.group("act")
    if act:
        parts.append(_clean_heading_text(act))

    scene = match.group("scene") or match.group("scene_only")
    if scene:
        parts.append(_clean_heading_text(scene))
    return parts


def _is_fallback_start_heading_text(heading_text: str) -> bool:
    """Return True when a heading is strong enough to start fallback scanning."""
    if _heading_keyword(heading_text):
        return True
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return True
    return _FALLBACK_START_HEADING_RE.match(heading_text) is not None


def _build_subtree_end_positions(
    soup: BeautifulSoup, tag_positions: dict[int, int]
) -> dict[int, int]:
    """Return the last document-order position covered by each tag subtree."""
    end_positions: dict[int, int] = {}
    stack: list[tuple[BeautifulSoup | Tag, bool]] = [(soup, False)]
    while stack:
        node, visited = stack.pop()
        if not visited:
            stack.append((node, True))
            for child in reversed(node.contents):
                if isinstance(child, Tag):
                    stack.append((child, False))
            continue

        end_pos = tag_positions.get(id(node))
        for child in node.contents:
            if not isinstance(child, Tag):
                continue
            child_end = end_positions.get(id(child))
            if child_end is not None and (end_pos is None or child_end > end_pos):
                end_pos = child_end
        if end_pos is not None:
            end_positions[id(node)] = end_pos
    return end_positions


def _subtree_end_position(
    tag: Tag,
    tag_positions: dict[int, int],
    subtree_end_positions: dict[int, int],
) -> int | None:
    """Return the last document-order position covered by *tag*'s subtree."""
    return subtree_end_positions.get(id(tag), _tag_position(tag, tag_positions))


def _headings_have_text_between(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True when body paragraphs intervene between two heading rows."""
    start_pos = _subtree_end_position(
        current.tag,
        doc_index.tag_positions,
        doc_index.subtree_end_positions,
    )
    stop_pos = _tag_position(next_row.tag, doc_index.tag_positions)
    if start_pos is None or stop_pos is None or stop_pos <= start_pos:
        return True
    lo = bisect_right(doc_index.paragraph_positions, start_pos)
    hi = bisect_left(doc_index.paragraph_positions, stop_pos)
    return lo < hi


def _normalize_heading_subtitle(heading_text: str) -> str:
    """Strip apparatus trailers from a continuation heading."""
    text = _SYNOPSIS_SUFFIX_RE.sub("", heading_text).strip(" .,:;[]()-")
    return " ".join(text.split()).strip()


def _is_ignorable_fallback_heading(heading_text: str) -> bool:
    """Return True for heading-scan rows that are likely contents or inline subheads."""
    if _NON_SUBTITLE_HEADING_RE.fullmatch(heading_text):
        return True
    if _STANDALONE_APPARATUS_HEADING_RE.match(heading_text):
        return True
    if _ENUMERATED_SUBHEADING_RE.match(heading_text):
        return True
    return len(_LIST_ITEM_MARKER_RE.findall(heading_text)) >= 2


def _is_refinement_heading(heading_text: str) -> bool:
    """Return True when a body heading is strong enough to refine a TOC."""
    if _heading_keyword(heading_text):
        return True
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return True
    return _PLAIN_NUMBER_HEADING_RE.fullmatch(heading_text) is not None


def _is_toc_section_heading(
    heading_text: str,
    *,
    link_text: str,
    heading_rank: int,
    is_emphasized: bool,
) -> bool:
    """Return True when a TOC entry points at a real structural section."""
    if is_emphasized or heading_rank <= 2:
        return True
    if _BRACKETED_NUMERIC_HEADING_RE.fullmatch(heading_text):
        return True
    if _BRACKETED_NUMERIC_HEADING_RE.fullmatch(link_text):
        return True
    if _is_refinement_heading(heading_text):
        return True
    return _is_refinement_heading(link_text)


def _is_dialogue_speaker_heading(heading_text: str) -> bool:
    """Return True for uppercase speaker attributions like ``SOCRATES - GLAUCON``."""
    if " - " not in heading_text:
        return False

    parts = [part.strip(" .,:;!?()[]") for part in heading_text.split(" - ")]
    if len(parts) < 2:
        return False

    for part in parts:
        words = part.split()
        if not words or len(words) > 5:
            return False
        if not any(char.isalpha() for char in part):
            return False
        if part != part.upper():
            return False
    return True


def _is_single_speaker_dialogue_heading(
    heading_text: str,
    *,
    previous_heading: str | None,
    next_heading: str | None,
) -> bool:
    """Return True for uppercase single-speaker headings within dialogue runs."""
    if _heading_keyword(heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return False
    if heading_text != heading_text.upper():
        return False

    words = heading_text.split()
    if not 1 <= len(words) <= 3:
        return False
    if not all(any(char.isalpha() for char in word) for word in words):
        return False

    adjacent_headings = [text for text in (previous_heading, next_heading) if text]
    return any(_is_dialogue_speaker_heading(text) for text in adjacent_headings)


def _is_short_uppercase_stage_heading(row: _HeadingRow) -> bool:
    """Return True for short all-caps dramatic cues like ``FAUST`` or ``NIGHT``."""
    if row.rank < 5:
        return False
    if _heading_keyword(row.heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(row.heading_text):
        return False
    if row.heading_text.upper().startswith("OF "):
        return False

    words = [word for word in row.heading_text.split() if any(char.isalpha() for char in word)]
    if not words or len(words) > 4:
        return False

    letters = "".join(char for char in row.heading_text if char.isalpha())
    return bool(letters) and letters == letters.upper()


def _is_single_letter_subheading(row: _HeadingRow) -> bool:
    """Return True for deep-rank alphabet markers like ``C.`` in acrostic sections."""
    if row.rank < 4:
        return False
    if _heading_keyword(row.heading_text):
        return False

    normalized = row.heading_text.strip(" .,:;!?()[]'\"")
    return len(normalized) == 1 and normalized.isalpha()


def _is_deep_rank_bare_numeral_heading(row: _HeadingRow) -> bool:
    """Return True for deep-rank numeral-only subheads like ``II.`` or ``VI.``."""
    if row.rank < 4:
        return False
    if _heading_keyword(row.heading_text):
        return False
    return _PLAIN_NUMBER_HEADING_RE.fullmatch(row.heading_text) is not None


def _is_rank5_subheading_under_nonchapter_section(
    row: _HeadingRow,
    *,
    previous_kept_heading: str | None,
) -> bool:
    """Return True for h5 dramatic cues nested under non-chapter parent sections."""
    if row.rank < 5:
        return False
    if previous_kept_heading is None:
        return False
    if _heading_keyword(row.heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(row.heading_text):
        return False
    if row.heading_text.upper().startswith("OF "):
        return False

    parent_keyword = _heading_keyword(previous_kept_heading)
    return parent_keyword not in {"chapter", "stave", "section", "adventure"}


def _normalized_heading_continuation(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    doc_index: _DocumentIndex,
) -> str | None:
    """Return a normalized continuation subtitle for a bare heading, if present."""
    if _headings_have_text_between(current, next_row, doc_index=doc_index):
        return None
    subtitle = _normalize_heading_subtitle(next_row.heading_text)
    if not subtitle:
        return None
    if not _next_heading_is_subtitle(subtitle):
        return None
    return subtitle


def _is_editorial_placeholder_heading(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True for editorial 'missing chapter' headings that should be skipped."""
    if not _EDITORIAL_PLACEHOLDER_HEADING_RE.search(current.heading_text):
        return False
    if not _HEADING_KEYWORD_RE.match(next_row.heading_text):
        return False
    return not _headings_have_text_between(current, next_row, doc_index=doc_index)


def _style_has_emphasized_font(style: str) -> bool:
    match = _FONT_SIZE_STYLE_RE.search(style)
    if not match:
        return False
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "%":
        return value > 100
    if unit in {"em", "rem"}:
        return value > 1.0
    return value > 16.0


def _is_emphasized_toc_link(link: Tag) -> bool:
    """Return True when a TOC link is visually emphasized as a broad division."""
    if link.find(["b", "strong"]) is not None:
        return True
    for el in [link, *link.find_all(True)]:
        style = str(el.get("style", ""))
        if style and _style_has_emphasized_font(style):
            return True
    return False


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


def _find_gutenberg_bounds(
    soup: BeautifulSoup,
    tag_positions: dict[int, int],
    subtree_end_positions: dict[int, int],
) -> _ContentBounds:
    """Locate START/END delimiter bounds in document order."""

    def _find_marker_parent(marker_re: re.Pattern[str]) -> Tag | None:
        marker_text = soup.find(
            string=lambda text: (
                isinstance(text, str) and marker_re.search(" ".join(text.split())) is not None
            )
        )
        if marker_text is None:
            return None
        return marker_text.parent if isinstance(marker_text.parent, Tag) else None

    def _subtree_end_pos(tag: Tag | None) -> int | None:
        if tag is None:
            return None
        return subtree_end_positions.get(id(tag), _tag_position(tag, tag_positions))

    start_parent = _find_marker_parent(_START_DELIMITER_RE)
    end_parent = _find_marker_parent(_END_DELIMITER_RE)
    start_pos = _tag_position(start_parent, tag_positions) if start_parent else None
    end_pos = _tag_position(end_parent, tag_positions) if end_parent else None

    # Fallback for editions with missing/non-standard delimiter text.
    header = soup.find(id="pg-header")
    footer = soup.find(id="pg-footer")
    header_end_pos = _subtree_end_pos(header if isinstance(header, Tag) else None)
    footer_start_pos = _tag_position(footer, tag_positions) if isinstance(footer, Tag) else None

    if start_pos is None:
        start_pos = header_end_pos
    if end_pos is None:
        end_pos = footer_start_pos

    if start_pos is not None and end_pos is not None and end_pos <= start_pos:
        if footer_start_pos is not None and footer_start_pos > start_pos:
            end_pos = footer_start_pos
        else:
            end_pos = None
    return _ContentBounds(start_pos=start_pos, end_pos=end_pos)
