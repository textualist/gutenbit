"""Heading text classification, predicates, and play structure detection."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable

from bs4 import Tag

from gutenbit.html_chunker._common import (
    _BRACKETED_NUMERIC_HEADING_RE,
    _BROAD_KEYWORDS,
    _BROAD_NESTING_DEPTHS,
    _FALLBACK_START_HEADING_RE,
    _FRONT_MATTER_HEADINGS,
    _HEADING_KEYWORD_RE,
    _NON_ALNUM_RE,
    _NUMERIC_LINK_TEXT_RE,
    _PLAY_HEADING_PARAGRAPH_RE,
    _STANDALONE_STRUCTURAL_RE,
    _clean_heading_text,
    _front_matter_heading_key,
    _HeadingRow,
    _Section,
)
from gutenbit.html_chunker._scanning import (
    _DocumentIndex,
    _subtree_end_position,
    _tag_position,
)

# ---------------------------------------------------------------------------
# Compiled regex patterns (used only within this module)
# ---------------------------------------------------------------------------

_STRUCTURAL_KEYWORD_ALIASES = {
    "actus": "act",
    "scena": "scene",
    "scoena": "scene",
}

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
_TRAILING_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:THE\s+)?(?P<index>[A-Z0-9]+)\s+"
    r"(?P<keyword>BOOK|PART|ACT|ACTUS|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SCENA|"
    r"SCOENA|SECTION|ADVENTURE)\.?\s*$",
    re.IGNORECASE,
)
_EMBEDDED_HEADING_RE = re.compile(
    r"(?:BOOK|PART|ACT|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)"
    r"\.?\s+[IVXLCDM0-9]+",
    re.IGNORECASE,
)
_PAGE_HEADING_RE = re.compile(r"^(?:page|p\.)\s+\d+\b", re.IGNORECASE)
_NON_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:notes|footnotes?|endnotes?|transcriber's note|transcribers note|"
    r"editor's note|editors note|finis)\b",
    re.IGNORECASE,
)
_FRONT_MATTER_ATTRIBUTION_RE = re.compile(
    r"^(?:by|translated\s+by|edited\s+by|illustrated\s+by)\s",
    re.IGNORECASE,
)
_FRONT_MATTER_ATTRIBUTION_HEADING_RE = re.compile(
    r"^(?:introduction|preface|foreword|afterword)\s+by\b",
    re.IGNORECASE,
)
_PLAIN_NUMBER_HEADING_RE = re.compile(r"^(?:[IVXLCDM]+|[0-9]+)\.?$", re.IGNORECASE)
_NON_SUBTITLE_HEADING_RE = re.compile(r"^(?:chap(?:ters?)?)\.?$", re.IGNORECASE)
_SYNOPSIS_SUFFIX_RE = re.compile(r"\s+SYNOPSIS OF\b.*$", re.IGNORECASE)
_EDITORIAL_PLACEHOLDER_HEADING_RE = re.compile(
    r"(?:\[\s*(?:not\b|omitted\b|wanting\b)|\bnot in early editions\b)",
    re.IGNORECASE,
)
_ENUMERATED_SUBHEADING_RE = re.compile(r"^(?:[IVXLCDM]+|[0-9]+)\.\s+\S", re.IGNORECASE)
_ENUMERATED_HEADING_PREFIX_RE = re.compile(
    r"^(?:[IVXLCDM]+|[0-9]+)(?:[.)])?\s+\S",
    re.IGNORECASE,
)
_LIST_ITEM_MARKER_RE = re.compile(r"(?:^|\s)(?:[IVXLCDM]+|[0-9]+)\.\s+\S", re.IGNORECASE)
_STANDALONE_APPARATUS_HEADING_RE = re.compile(r"^SYNOPSIS OF\b", re.IGNORECASE)
_FONT_SIZE_STYLE_RE = re.compile(
    r"font-size\s*:\s*([0-9.]+)\s*(%|em|rem|px)",
    re.IGNORECASE,
)
_DRAMATIC_CONTEXT_HEADING_RE = re.compile(
    r"\b(?:act|scene|prologue|epilogue|tragedy|comedy)\b",
    re.IGNORECASE,
)
_STRONG_DRAMATIC_CONTEXT_HEADING_RE = re.compile(
    r"\b(?:act|scene|tragedy|comedy)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Heading text analysis
# ---------------------------------------------------------------------------


def _heading_keyword(heading_text: str) -> str:
    match = _HEADING_KEYWORD_RE.match(heading_text)
    if match:
        keyword = heading_text.split()[0].rstrip(".,:]").lower()
        canonical = _STRUCTURAL_KEYWORD_ALIASES.get(keyword, keyword)

        remainder = heading_text[len(heading_text.split()[0]) :].lstrip(" .,:;!?-\u2014\u2013")
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


def _is_non_structural_heading_text(heading_text: str) -> bool:
    """Return True for apparatus headings that should not become sections."""
    text = " ".join(heading_text.split()).strip()
    lowered = text.lower()
    if lowered in _FRONT_MATTER_HEADINGS:
        return True
    if _front_matter_heading_key(text) in _FRONT_MATTER_HEADINGS:
        return True
    if _PAGE_HEADING_RE.match(text):
        return True
    if _FRONT_MATTER_ATTRIBUTION_RE.match(text):
        return True
    return _NON_STRUCTURAL_HEADING_RE.match(text) is not None


def _is_title_like_heading(heading_text: str) -> bool:
    if _heading_keyword(heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return False
    if _is_dialogue_speaker_heading(heading_text):
        return False
    return not _is_non_structural_heading_text(heading_text)


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


def _rank_relative_level(candidate: _Section, toc_section: _Section) -> int:
    if candidate.heading_rank is None or toc_section.heading_rank is None:
        return candidate.level
    return max(1, min(4, toc_section.level + candidate.heading_rank - toc_section.heading_rank))


def _is_refinement_heading(heading_text: str) -> bool:
    """Return True when a body heading is strong enough to refine a TOC."""
    if _heading_keyword(heading_text):
        return True
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return True
    return _PLAIN_NUMBER_HEADING_RE.fullmatch(heading_text) is not None


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


def _broad_nesting_depth(heading_text: str) -> int | None:
    return _BROAD_NESTING_DEPTHS.get(_heading_keyword(heading_text))


# ---------------------------------------------------------------------------
# Subtitle and continuation helpers
# ---------------------------------------------------------------------------


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


def _normalize_heading_subtitle(heading_text: str) -> str:
    """Strip apparatus trailers from a continuation heading."""
    text = _SYNOPSIS_SUFFIX_RE.sub("", heading_text).strip(" .,:;[]()-")
    return " ".join(text.split()).strip()


def _starts_with_enumerated_heading_prefix(heading_text: str) -> bool:
    return _ENUMERATED_HEADING_PREFIX_RE.match(heading_text) is not None


def _broad_heading_with_enumerated_child(
    current_heading_text: str,
    next_heading_text: str,
) -> bool:
    return _heading_keyword(
        current_heading_text
    ) in _BROAD_KEYWORDS and _starts_with_enumerated_heading_prefix(next_heading_text)


def _is_ignorable_fallback_heading(
    heading_text: str,
    *,
    heading_rank: int | None,
) -> bool:
    """Return True for heading-scan rows that are likely contents or inline subheads."""
    if _NON_SUBTITLE_HEADING_RE.fullmatch(heading_text):
        return True
    if _STANDALONE_APPARATUS_HEADING_RE.match(heading_text):
        return True
    if _ENUMERATED_SUBHEADING_RE.match(heading_text) and (
        heading_rank is None or heading_rank >= 4
    ):
        return True
    return len(_LIST_ITEM_MARKER_RE.findall(heading_text)) >= 2


def _is_fallback_start_heading_text(heading_text: str) -> bool:
    """Return True when a heading is strong enough to start fallback scanning."""
    if _heading_keyword(heading_text):
        return True
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return True
    return _FALLBACK_START_HEADING_RE.match(heading_text) is not None


# ---------------------------------------------------------------------------
# Dialogue and speaker predicates
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Stage and uppercase heading predicates
# ---------------------------------------------------------------------------


def _is_short_uppercase_stage_heading(
    row: _HeadingRow,
    *,
    previous_kept_heading: str | None,
    previous_row: _HeadingRow | None,
    next_row: _HeadingRow | None,
    dramatic_context_active: bool,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True for short all-caps dramatic cues like ``FAUST`` or ``NIGHT``."""
    if not _is_short_uppercase_heading_candidate(row):
        return False
    if dramatic_context_active:
        return True
    if previous_kept_heading and _DRAMATIC_CONTEXT_HEADING_RE.search(previous_kept_heading):
        return True
    return _has_adjacent_heading_candidate(
        row,
        previous_row=previous_row,
        next_row=next_row,
        doc_index=doc_index,
        predicate=_is_short_uppercase_heading_candidate,
    )


def _is_short_uppercase_heading_candidate(row: _HeadingRow) -> bool:
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


def _is_front_matter_attribution_heading(row: _HeadingRow) -> bool:
    if row.rank < 4:
        return False
    return _FRONT_MATTER_ATTRIBUTION_HEADING_RE.match(row.heading_text) is not None


# ---------------------------------------------------------------------------
# Single-letter and bare-numeral predicates
# ---------------------------------------------------------------------------


def _is_single_letter_subheading(
    row: _HeadingRow,
    *,
    previous_kept_heading: str | None,
    previous_row: _HeadingRow | None,
    next_row: _HeadingRow | None,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True for deep-rank alphabet markers like ``C.`` in acrostic sections."""
    if not _is_single_letter_heading_candidate(row):
        return False
    if previous_kept_heading and _looks_like_letter_series_heading(previous_kept_heading):
        return True
    return _has_adjacent_heading_candidate(
        row,
        previous_row=previous_row,
        next_row=next_row,
        doc_index=doc_index,
        predicate=_is_single_letter_heading_candidate,
    )


def _is_single_letter_heading_candidate(row: _HeadingRow) -> bool:
    if row.rank < 4:
        return False
    if _heading_keyword(row.heading_text):
        return False

    normalized = row.heading_text.strip(" .,:;!?()[]'\"")
    return len(normalized) == 1 and normalized.isalpha()


def _is_deep_rank_bare_numeral_heading(
    row: _HeadingRow,
    *,
    previous_kept_heading: str | None,
    previous_row: _HeadingRow | None,
    next_row: _HeadingRow | None,
    row_index: int,
    bare_numeral_run_indices: set[int],
    doc_index: _DocumentIndex,
) -> bool:
    """Return True for deep-rank numeral-only subheads like ``II.`` or ``VI.``."""
    if not _is_deep_rank_bare_numeral_candidate(row):
        return False
    if previous_kept_heading is None:
        return False
    return _has_adjacent_heading_candidate(
        row,
        previous_row=previous_row,
        next_row=next_row,
        doc_index=doc_index,
        predicate=_is_deep_rank_bare_numeral_candidate,
    ) or (not _heading_keyword(previous_kept_heading) and row_index in bare_numeral_run_indices)


def _is_deep_rank_bare_numeral_candidate(row: _HeadingRow) -> bool:
    if row.rank < 4:
        return False
    if _heading_keyword(row.heading_text):
        return False
    return _PLAIN_NUMBER_HEADING_RE.fullmatch(row.heading_text) is not None


def _looks_like_letter_series_heading(heading_text: str) -> bool:
    return len(re.findall(r"\b[A-Z]\b", heading_text.upper())) >= 3


# ---------------------------------------------------------------------------
# Adjacent heading helpers
# ---------------------------------------------------------------------------


def _has_adjacent_heading_candidate(
    row: _HeadingRow,
    *,
    previous_row: _HeadingRow | None,
    next_row: _HeadingRow | None,
    doc_index: _DocumentIndex,
    predicate: Callable[[_HeadingRow], bool],
) -> bool:
    if (
        previous_row is not None
        and predicate(previous_row)
        and not _headings_have_text_between(previous_row, row, doc_index=doc_index)
    ):
        return True
    return (
        next_row is not None
        and predicate(next_row)
        and not _headings_have_text_between(row, next_row, doc_index=doc_index)
    )


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


def _deep_rank_bare_numeral_run_indices(heading_rows: list[_HeadingRow]) -> set[int]:
    """Return indices of deep-rank numeral runs bounded by shallower headings."""
    run_indices: set[int] = set()
    idx = 0
    while idx < len(heading_rows):
        row = heading_rows[idx]
        if not _is_deep_rank_bare_numeral_candidate(row):
            idx += 1
            continue

        run_start = idx
        run_rank = row.rank
        idx += 1
        while idx < len(heading_rows):
            candidate = heading_rows[idx]
            if not _is_deep_rank_bare_numeral_candidate(candidate) or candidate.rank != run_rank:
                break
            idx += 1

        run_end = idx - 1
        previous_row = heading_rows[run_start - 1] if run_start > 0 else None
        next_row = heading_rows[idx] if idx < len(heading_rows) else None
        if (
            run_end - run_start >= 2
            and previous_row is not None
            and previous_row.rank < run_rank
            and next_row is not None
            and next_row.rank < run_rank
        ):
            run_indices.update(range(run_start, run_end + 1))
    return run_indices


# ---------------------------------------------------------------------------
# Play structure
# ---------------------------------------------------------------------------


def _heading_text_suggests_play_structure(heading_text: str) -> bool:
    lowered = heading_text.lower()
    return _DRAMATIC_CONTEXT_HEADING_RE.search(heading_text) is not None or (
        "dramatis personae" in lowered
    )


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


def _update_dramatic_context_state(
    dramatic_context_active: bool,
    heading_text: str,
) -> bool:
    """Track dramatic context only within the current local container."""
    if _STRONG_DRAMATIC_CONTEXT_HEADING_RE.search(heading_text):
        return True

    keyword = _heading_keyword(heading_text)
    if keyword in {"chapter", "section", "adventure", "stave"}:
        return False
    if keyword in _BROAD_KEYWORDS:
        return dramatic_context_active
    if _is_title_like_heading(heading_text):
        return False
    if _STANDALONE_STRUCTURAL_RE.search(heading_text):
        return False
    return dramatic_context_active


# ---------------------------------------------------------------------------
# Title page and repeat predicates
# ---------------------------------------------------------------------------


def _is_title_page_subtitle(
    row: _HeadingRow,
    *,
    previous_kept_row: _HeadingRow | None,
) -> bool:
    if previous_kept_row is None:
        return False
    if row.rank < 4 or row.rank <= previous_kept_row.rank:
        return False
    if not _is_title_like_heading(previous_kept_row.heading_text):
        return False
    if not _is_title_like_heading(row.heading_text):
        return False
    if row.heading_text.upper().startswith("OF "):
        return True

    words = [word for word in row.heading_text.split() if any(char.isalpha() for char in word)]
    letters = "".join(char for char in row.heading_text if char.isalpha())
    return len(words) >= 5 and bool(letters) and letters == letters.upper()


def _is_shorter_adjacent_title_repeat(
    row: _HeadingRow,
    previous_row: _HeadingRow,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    if not _is_title_like_heading(previous_row.heading_text):
        return False
    if not _is_title_like_heading(row.heading_text):
        return False
    if _headings_have_text_between(previous_row, row, doc_index=doc_index):
        return False
    return previous_row.heading_text.startswith(row.heading_text + " ")


def _is_rank5_subheading_under_nonchapter_section(
    row: _HeadingRow,
    *,
    previous_kept_heading: str | None,
    dramatic_context_active: bool,
) -> bool:
    """Return True for h5 dramatic cues nested under non-chapter parent sections."""
    if row.rank < 5:
        return False
    if previous_kept_heading is None:
        return False
    if dramatic_context_active:
        return _DRAMATIC_CONTEXT_HEADING_RE.search(row.heading_text) is None
    if not _is_short_uppercase_heading_candidate(row):
        return False
    return _DRAMATIC_CONTEXT_HEADING_RE.search(previous_kept_heading) is not None


def _normalized_heading_continuation(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    following_row: _HeadingRow | None,
    previous_kept_heading: str | None,
    dramatic_context_active: bool,
    doc_index: _DocumentIndex,
) -> str | None:
    """Return a normalized continuation subtitle for a bare heading, if present."""
    if _headings_have_text_between(current, next_row, doc_index=doc_index):
        return None
    if _is_short_uppercase_heading_candidate(next_row) and (
        dramatic_context_active
        or (
            previous_kept_heading is not None
            and _DRAMATIC_CONTEXT_HEADING_RE.search(previous_kept_heading)
        )
        or _has_adjacent_heading_candidate(
            next_row,
            previous_row=None,
            next_row=following_row,
            doc_index=doc_index,
            predicate=_is_short_uppercase_heading_candidate,
        )
    ):
        return None
    subtitle = _normalize_heading_subtitle(next_row.heading_text)
    if not subtitle:
        return None
    if _broad_heading_with_enumerated_child(current.heading_text, subtitle):
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


def _is_empty_front_matter_stub_heading(
    current: _HeadingRow,
    next_row: _HeadingRow,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Skip empty front-matter stubs that only label a following real section."""
    if not _FALLBACK_START_HEADING_RE.match(current.heading_text):
        return False
    if current.heading_text.lower() == "introductory note":
        return current.rank >= 4 and next_row.rank is not None and next_row.rank < current.rank
    if current.rank < 4 or next_row.rank is None or next_row.rank >= current.rank:
        return False
    return not _headings_have_text_between(current, next_row, doc_index=doc_index)


# ---------------------------------------------------------------------------
# TOC emphasis
# ---------------------------------------------------------------------------


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
