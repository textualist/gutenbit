"""Shared data structures, compiled regex patterns, and pure text helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import Comment, NavigableString, Tag

# ---------------------------------------------------------------------------
# Constants and frozen sets
# ---------------------------------------------------------------------------

_BROAD_KEYWORDS = frozenset({"book", "part", "act", "epilogue", "induction", "volume"})
_BROAD_NESTING_DEPTHS = {
    "volume": 1,
    "part": 2,
    "epilogue": 2,
    "book": 3,
    "act": 3,
    "induction": 3,
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
# Compiled regex patterns (used by multiple modules)
# ---------------------------------------------------------------------------

# Bare chapter-number headings: "CHAPTER I", "CHAPTER IV.", "BOOK 2" etc.
# with no subtitle text — used to merge consecutive number + title headings.
_BARE_HEADING_NUMBER_RE = re.compile(
    r"^(?:"
    r"(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)"
    r"\.?\s+[IVXLCDM0-9]+\.?"
    r"|[IVXLCDM]+\."  # standalone Roman numeral with period (e.g. "I.", "XLIII.")
    r")$",
    re.IGNORECASE,
)

_HEADING_KEYWORD_RE = re.compile(
    r"^(?:(?:BOOK|PART|ACT|ACTUS|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SCENA|SCOENA|SECTION|ADVENTURE)\.?\s|EPILOGUE\b|INDUCTION\b)",
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
    r"(\b(?:BOOK|PART|ACT|EPILOGUE|VOLUME|CHAPTER|STAVE|SCENE|SECTION|ADVENTURE)\.?\s*[IVXLCDM0-9]+\b.*"
    r"|\bSECTION\s+[A-Z](?=[.\s\u2014\u2013:;,)}\]!?-]).*)$",
    re.IGNORECASE,
)
_BRACKETED_NUMERIC_HEADING_RE = re.compile(r"^\[\s*\d+\s*\]$")
_NUMERIC_LINK_TEXT_RE = re.compile(r"^\[?\d+\]?$")
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
_PLAY_HEADING_PARAGRAPH_RE = re.compile(
    r"^(?:(?P<act>(?:ACTUS|ACT)\s+[A-Z0-9IVXLCDM]+\.?)"
    r"(?:\s+(?P<scene>(?:SC(?:OE|E)NA|SCENE)\s+[A-Z0-9IVXLCDM]+\.?))?"
    r"|(?P<scene_only>(?:SC(?:OE|E)NA|SCENE)\s+[A-Z0-9IVXLCDM]+\.?))$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")

# Keywords that are almost exclusively structural even without a trailing number.
_STANDALONE_STRUCTURAL_RE = re.compile(
    r"\bEPILOGUE\b|\bPROLOGUE\b|\bAPPENDIX\b|\bINDUCTION\b",
    re.IGNORECASE,
)
_FALLBACK_START_HEADING_RE = re.compile(
    r"^(?:preface|introduction|introductory note|prelude|prologue\b|"
    r"note\b|note to\b|letter\b|a letter from\b|the publisher to the reader\b|"
    r"before the curtain\b|etymology\b|extracts\b|some commendatory verses\b)",
    re.IGNORECASE,
)

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

    def _with_level(self, level: int) -> _Section:
        """Return a copy with only the level changed."""
        return _Section(
            self.anchor_id, self.heading_text, level, self.body_anchor, self.heading_rank
        )


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
    ``<img alt="...">`` fallback, strips ``<span class="pagenum">`` elements,
    and strips HTML comments (``<!-- ... -->``).
    """
    has_pagenum = heading_el.find("span", class_="pagenum") is not None
    has_br = heading_el.find("br") is not None
    has_comment = any(isinstance(c, Comment) for c in heading_el.children)

    # Fast path: no special elements to strip or replace.
    if not has_pagenum and not has_br and not has_comment:
        text = " ".join(heading_el.get_text().split()).strip()
        if text:
            return text
        img = heading_el.find("img", alt=True)
        if img:
            return " ".join(str(img["alt"]).split()).strip()
        return ""

    # Walk the tree directly instead of re-parsing with BeautifulSoup.
    parts: list[str] = []
    _collect_text_parts(heading_el, parts)
    text = " ".join("".join(parts).split()).strip()
    if text:
        return text

    img = heading_el.find("img", alt=True)
    if img:
        return " ".join(str(img["alt"]).split()).strip()
    return ""


def _collect_text_parts(node: Tag, parts: list[str], *, replace_br: bool = True) -> None:
    """Collect text parts from an element, skipping pagenum spans.

    When *replace_br* is True (the default, used for headings), ``<br>`` tags
    are replaced with a space.  When False (used for paragraphs), ``<br>`` is
    ignored — whitespace collapsing handles it.
    """
    for child in node.children:
        if isinstance(child, Comment):
            continue
        elif isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                if replace_br:
                    parts.append(" ")
            elif child.name == "span" and "pagenum" in {
                c.lower() for c in (child.get("class") or [])
            }:
                continue
            elif child.name == "img":
                alt_value = child.get("alt")
                alt_text = " ".join(str(alt_value or "").split()).strip()
                if alt_text:
                    parts.append(alt_text)
            else:
                _collect_text_parts(child, parts, replace_br=replace_br)


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
