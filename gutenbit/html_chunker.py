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
CHUNKER_VERSION = 12

# Bare chapter-number headings: "CHAPTER I", "CHAPTER IV.", "BOOK 2" etc.
# with no subtitle text — used to merge consecutive number + title headings.
_BARE_HEADING_NUMBER_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)"
    r"\.?\s+[IVXLCDM0-9]+\.?$",
    re.IGNORECASE,
)

_HEADING_KEYWORD_RE = re.compile(
    r"^(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)\.?\s",
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
    r"\b(BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)(\.?)\s*([IVXLCDM0-9]+)\b",
    re.IGNORECASE,
)
_STRUCTURAL_HEADING_TRAILER_RE = re.compile(
    r"(\b(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION)\.?\s*[IVXLCDM0-9]+\b.*)$",
    re.IGNORECASE,
)
_NUMERIC_LINK_TEXT_RE = re.compile(r"^\[?\d+\]?$")
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
_PLAIN_NUMBER_HEADING_RE = re.compile(r"^(?:[IVXLCDM]+|[0-9]+)\.?$", re.IGNORECASE)
_PAGE_HEADING_RE = re.compile(r"^(?:page|p\.)\s+\d+\b", re.IGNORECASE)
_NON_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:notes?|footnotes?|endnotes?|transcriber's note|transcribers note|"
    r"editor's note|editors note)\b",
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
    """Split an HTML book into labelled chunks using TOC plus body heading cues.

    Each ``<p>`` element becomes its own chunk. Returns chunks in document order.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag_positions = _tag_positions(soup)
    bounds = _find_gutenberg_bounds(soup, tag_positions)

    # Build section list from TOC links and refine with body headings when the
    # TOC is a coarse but valid subsequence of the body structure.
    toc_sections = _parse_toc_sections(soup, tag_positions=tag_positions, bounds=bounds)
    if toc_sections:
        heading_sections = _parse_heading_sections(
            soup,
            tag_positions=tag_positions,
            bounds=bounds,
        )
        sections = _refine_toc_sections(toc_sections, heading_sections)
    else:
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

    # Pre-collect all <p> tags with their document positions for fast range
    # lookups (avoids O(sections * paragraphs) find_all_next traversal).
    p_index = _build_paragraph_index(soup, tag_positions, bounds)

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
            p_index,
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
            for text in _paragraphs_in_range(p_index, start_pos_val, stop_pos_val):
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
        if not _is_structural_toc_link(link):
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
        if _is_non_structural_heading_text(heading_text):
            continue

        is_emphasized = _is_emphasized_toc_link(link)
        level = _classify_level(heading_text, is_emphasized)
        sections.append(_Section(anchor_id, heading_text, level, body_anchor))

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
    r"(?:BOOK|PART|ACT|VOLUME|CHAPTER|STAVE|SCENE|SECTION)"
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


@dataclass(frozen=True, slots=True)
class _HeadingRow:
    """One cleaned heading candidate used by heading-scan fallback."""

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
            merged.append(_Section(sec.anchor_id, combined_text, combined_level, sec.body_anchor))
            i += 2
        else:
            merged.append(sec)
            i += 1
    return merged


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
    heading_rows: list[_HeadingRow] = []
    for heading in soup.find_all(_HEADING_TAGS):
        if not _tag_within_bounds(heading, tag_positions, bounds):
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

        if next_row is not None and _is_editorial_placeholder_heading(
            row,
            next_row,
            tag_positions=tag_positions,
        ):
            i += 1
            continue

        if _BARE_HEADING_NUMBER_RE.fullmatch(heading_text) and next_row is not None:
            subtitle = _normalized_heading_continuation(
                row,
                next_row,
                tag_positions=tag_positions,
            )
            if subtitle:
                heading_text = f"{heading_text} {subtitle}"
                i += 1

        elif _is_ignorable_fallback_heading(heading_text):
            i += 1
            continue

        level = _classify_level(heading_text, False)
        anchor_id = str(row.anchor.get("id", ""))
        sections.append(_Section(anchor_id, heading_text, level, row.anchor))
        i += 1

    return sections


def _refine_toc_sections(
    toc_sections: list[_Section],
    heading_sections: list[_Section],
) -> list[_Section]:
    """Supplement a valid TOC with deeper body headings.

    The TOC remains authoritative for matched headings so visually emphasized
    broad divisions keep their TOC-derived levels. Body headings are inserted
    only when they refine that TOC in document order.
    """
    if not toc_sections or not heading_sections:
        return toc_sections

    matches: list[tuple[int, int]] = []
    heading_idx = 0
    for toc_idx, toc_section in enumerate(toc_sections):
        while heading_idx < len(heading_sections):
            if heading_sections[heading_idx].heading_text == toc_section.heading_text:
                matches.append((toc_idx, heading_idx))
                heading_idx += 1
                break
            heading_idx += 1
        else:
            return toc_sections

    matched_toc_by_heading_idx = {
        heading_idx: toc_sections[toc_idx] for toc_idx, heading_idx in matches
    }
    refined: list[_Section] = []
    added = 0
    current_toc: _Section | None = None

    for heading_idx in range(matches[0][1], len(heading_sections)):
        matched_toc = matched_toc_by_heading_idx.get(heading_idx)
        if matched_toc is not None:
            refined.append(matched_toc)
            current_toc = matched_toc
            continue

        if current_toc is None:
            continue

        candidate = heading_sections[heading_idx]
        if candidate.level <= current_toc.level:
            continue
        if not _is_refinement_heading(candidate.heading_text):
            continue
        refined.append(candidate)
        added += 1

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


def _classify_level(heading_text: str, is_emphasized_in_toc: bool) -> int:
    """Determine structural level: 1=broad (BOOK/PART), 2=chapter, 3=sub-chapter."""
    if is_emphasized_in_toc:
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


@dataclass(frozen=True, slots=True)
class _IndexedParagraph:
    """A paragraph tag with its pre-computed position and extracted text."""

    position: int
    text: str


def _build_paragraph_index(
    soup: BeautifulSoup,
    tag_positions: dict[int, int],
    bounds: _ContentBounds,
) -> list[_IndexedParagraph]:
    """Pre-collect all usable paragraphs with positions and text.

    Builds a sorted list once so section-range queries use bisect instead of
    repeated ``find_all_next("p")`` traversals.
    """
    body = soup.find("body")
    if not body:
        return []
    result: list[_IndexedParagraph] = []
    for p in body.find_all("p"):
        pos = tag_positions.get(id(p))
        if pos is None or not bounds.contains(pos):
            continue
        if _is_toc_paragraph(p):
            continue
        text = _extract_paragraph_text(p)
        if text:
            result.append(_IndexedParagraph(pos, text))
    return result


def _paragraphs_in_range(
    p_index: list[_IndexedParagraph],
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
    positions = [ip.position for ip in p_index]
    lo = bisect_right(positions, start_pos) if start_pos is not None else 0
    hi = bisect_left(positions, stop_pos) if stop_pos is not None else len(p_index)

    _heading_texts = heading_texts or set()
    paragraphs: list[str] = []
    for ip in p_index[lo:hi]:
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

    # Slow path: re-parse only the rare paragraphs that need modification.
    paragraph_copy = BeautifulSoup(str(paragraph), "html.parser").find("p")
    if paragraph_copy is None:
        return ""

    for pagenum in paragraph_copy.select("span.pagenum"):
        pagenum.decompose()

    for img in paragraph_copy.find_all("img"):
        alt_value = img.get("alt")
        alt_text = " ".join(str(alt_value or "").split()).strip()
        if alt_text:
            img.replace_with(NavigableString(alt_text))
        else:
            img.decompose()

    return " ".join(paragraph_copy.get_text().split()).strip()


_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


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
    full_text = paragraph.get_text()
    residue = full_text
    for link in links:
        link_text = link.get_text()
        if link_text:
            residue = residue.replace(link_text, "", 1)
    residue = " ".join(residue.split()).strip()
    return _NON_ALNUM_RE.sub("", residue) == ""


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
    if _NUMERIC_LINK_TEXT_RE.fullmatch(link_text):
        return False
    # Filter front-matter headings (CONTENTS, ILLUSTRATIONS, etc.)
    if _is_non_structural_heading_text(link_text):
        return False
    # Filter bare roman numerals (I, II, III — sub-section markers, not chapters)
    return not _ROMAN_NUMERAL_RE.fullmatch(link_text)


def _heading_tag_rank(tag: Tag) -> int | None:
    if tag.name and len(tag.name) == 2 and tag.name.startswith("h") and tag.name[1].isdigit():
        return int(tag.name[1])
    return None


def _fallback_start_index(heading_rows: list[_HeadingRow]) -> int | None:
    """Return the first body-structure heading to use for heading-scan fallback."""
    structural_rows = [
        (idx, row) for idx, row in enumerate(heading_rows) if _HEADING_KEYWORD_RE.match(row.heading_text)
    ]
    if structural_rows:
        start_rows = structural_rows
    else:
        start_rows = list(enumerate(heading_rows))
    start_rank = min(row.rank for _, row in start_rows)
    for idx, row in start_rows:
        if row.rank == start_rank:
            return idx
    return None


def _subtree_end_position(tag: Tag, tag_positions: dict[int, int]) -> int | None:
    """Return the last document-order position covered by *tag*'s subtree."""
    end_pos = _tag_position(tag, tag_positions)
    for child in tag.find_all(True):
        child_pos = _tag_position(child, tag_positions)
        if child_pos is not None and (end_pos is None or child_pos > end_pos):
            end_pos = child_pos
    return end_pos


def _headings_have_text_between(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    tag_positions: dict[int, int],
) -> bool:
    """Return True when body paragraphs intervene between two heading rows."""
    start_pos = _subtree_end_position(current.tag, tag_positions)
    stop_pos = _tag_position(next_row.tag, tag_positions)
    if start_pos is None or stop_pos is None or stop_pos <= start_pos:
        return True
    for paragraph in current.tag.find_all_next("p"):
        pos = _tag_position(paragraph, tag_positions)
        if pos is None:
            continue
        if pos >= stop_pos:
            break
        if pos > start_pos and _extract_paragraph_text(paragraph):
            return True
    return False


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
    if _HEADING_KEYWORD_RE.match(heading_text):
        return True
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return True
    return _PLAIN_NUMBER_HEADING_RE.fullmatch(heading_text) is not None


def _normalized_heading_continuation(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    tag_positions: dict[int, int],
) -> str | None:
    """Return a normalized continuation subtitle for a bare heading, if present."""
    if _headings_have_text_between(current, next_row, tag_positions=tag_positions):
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
    tag_positions: dict[int, int],
) -> bool:
    """Return True for editorial 'missing chapter' headings that should be skipped."""
    if not _EDITORIAL_PLACEHOLDER_HEADING_RE.search(current.heading_text):
        return False
    if not _HEADING_KEYWORD_RE.match(next_row.heading_text):
        return False
    return not _headings_have_text_between(current, next_row, tag_positions=tag_positions)


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


def _find_gutenberg_bounds(soup: BeautifulSoup, tag_positions: dict[int, int]) -> _ContentBounds:
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
        end_pos = _tag_position(tag, tag_positions)
        for child in tag.find_all(True):
            child_pos = _tag_position(child, tag_positions)
            if child_pos is not None and (end_pos is None or child_pos > end_pos):
                end_pos = child_pos
        return end_pos

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
