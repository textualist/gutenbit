"""Shared data structures, compiled regex patterns, and pure text helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import NavigableString, Tag

# ---------------------------------------------------------------------------
# Constants and frozen sets
# ---------------------------------------------------------------------------

_BROAD_KEYWORDS = frozenset({"book", "part", "act", "epilogue", "volume"})
_BROAD_NESTING_DEPTHS = {
    "volume": 1,
    "part": 2,
    "epilogue": 2,
    "book": 3,
    "act": 3,
}
_STRUCTURAL_KEYWORD_ALIASES = {
    "actus": "act",
    "scena": "scene",
    "scoena": "scene",
}

_FRONT_MATTER_HEADINGS = frozenset(
    {
        "contents",
        "illustrations",
        "table of contents",
        "list of illustrations",
    }
)

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_HEADING_TAG_SET = frozenset(_HEADING_TAGS)

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

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
_FALLBACK_START_HEADING_RE = re.compile(
    r"^(?:preface|introduction|introductory note|prelude|prologue\b|"
    r"note\b|note to\b|letter\b|a letter from\b|the publisher to the reader\b|"
    r"before the curtain\b|etymology\b|extracts\b|some commendatory verses\b)",
    re.IGNORECASE,
)
_TAIL_SECTION_HEADING_RE = re.compile(
    r"^(?:note\b|note to\b|letter\b|a letter from\b|finale\b|the conclusion\b)",
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

# Tail-boundary pattern: only clearly apparatus headings, not ambiguous
# singular "NOTE" which can be a narrative epilogue (e.g. Dracula).
_TAIL_BOUNDARY_HEADING_RE = re.compile(
    r"^(?:footnotes?|endnotes?|notes\b|transcriber'?s?\s+note|editor'?s?\s+note)",
    re.IGNORECASE,
)

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class _HeadingRow:
    """One cleaned section candidate used by heading-scan fallback."""

    tag: Tag
    anchor: Tag
    heading_text: str
    rank: int


# ---------------------------------------------------------------------------
# Pure text helpers
# ---------------------------------------------------------------------------


def _extract_heading_text(heading_el: Tag) -> str:
    """Get clean heading text from a heading tag.

    Handles: ``<br>`` line breaks, inline formatting (``<i>``, ``<b>``, etc.),
    ``<img alt="...">`` fallback, and strips ``<span class="pagenum">`` elements.
    """
    has_pagenum = heading_el.find("span", class_="pagenum") is not None
    has_br = heading_el.find("br") is not None

    # Fast path: no special elements to strip or replace.
    if not has_pagenum and not has_br:
        text = " ".join(heading_el.get_text().split()).strip()
        if text:
            return text
        img = heading_el.find("img", alt=True)
        if img:
            return " ".join(str(img["alt"]).split()).strip()
        return ""

    # Walk the tree directly instead of re-parsing with BeautifulSoup.
    parts: list[str] = []
    _collect_heading_parts(heading_el, parts)
    text = " ".join("".join(parts).split()).strip()
    if text:
        return text

    img = heading_el.find("img", alt=True)
    if img:
        return " ".join(str(img["alt"]).split()).strip()
    return ""


def _collect_heading_parts(node: Tag, parts: list[str]) -> None:
    """Collect text parts from a heading, skipping pagenum spans and replacing <br> with space."""
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append(" ")
            elif child.name == "span" and "pagenum" in (child.get("class") or []):
                continue
            elif child.name == "img":
                alt_value = child.get("alt")
                alt_text = " ".join(str(alt_value or "").split()).strip()
                if alt_text:
                    parts.append(alt_text)
            else:
                _collect_heading_parts(child, parts)


def _clean_heading_text(heading_text: str) -> str:
    """Normalize heading text while preserving source terminal punctuation."""
    text = " ".join(heading_text.split()).strip()
    text = _HEADING_CITATION_SUFFIX_RE.sub("", text)
    text = _STRUCTURAL_HEADING_SPACING_RE.sub(r"\1\2 \3", text)
    if _BRACKETED_NUMERIC_HEADING_RE.fullmatch(text):
        return text
    trailer_match = _STRUCTURAL_HEADING_TRAILER_RE.search(text)
    if trailer_match:
        prefix = text[: trailer_match.start()].strip(" .,:;!?'\"-")
        if prefix:
            text = trailer_match.group(1).strip()
    return text


def _heading_tag_rank(tag: Tag) -> int | None:
    if tag.name and len(tag.name) == 2 and tag.name.startswith("h") and tag.name[1].isdigit():
        return int(tag.name[1])
    return None


def _front_matter_heading_key(heading_text: str) -> str:
    return " ".join(heading_text.split()).strip().lower().rstrip(" .,:;!?])")
