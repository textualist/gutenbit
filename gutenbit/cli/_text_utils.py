"""Pure text utility functions shared across CLI and display modules."""

from __future__ import annotations

import re


def _format_int(value: int) -> str:
    return f"{value:,}"


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _single_line(text: str) -> str:
    """Collapse all whitespace so tabular CLI output stays on one line."""
    return " ".join(text.split())


def _split_semicolon_list(raw: str) -> list[str]:
    return [_single_line(part) for part in raw.split(";") if part.strip()]


def _summarize_semicolon_list(raw: str, *, max_items: int) -> str:
    items = _split_semicolon_list(raw)
    if not items:
        return ""
    if len(items) <= max_items:
        return "; ".join(items)
    shown = "; ".join(items[:max_items])
    return f"{shown}; +{len(items) - max_items} more"


def _indent_block(text: str, prefix: str = "    ") -> str:
    lines = text.splitlines()
    if not lines:
        return prefix if text else ""
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)


# ---------------------------------------------------------------------------
# Opening line selection heuristics
# ---------------------------------------------------------------------------

_TITLE_STYLE_CONNECTORS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_TITLE_STYLE_WORD_RE = re.compile(r"^[A-Za-z]+(?:['\u2019][A-Za-z]+)*$")
# Case-insensitive variant; the html_chunker has a separate case-sensitive
# _ROMAN_NUMERAL_RE in _common.py for heading classification.
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]*$')


def _opening_preview_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip("()[]{}\"''',;:-")
        if not token:
            continue
        tokens.append(token)
    return tokens


def _is_title_style_token(token: str) -> bool:
    if _ROMAN_NUMERAL_RE.fullmatch(token):
        return True
    if token.isupper() and any(ch.isalpha() for ch in token):
        return True
    if not _TITLE_STYLE_WORD_RE.fullmatch(token):
        return False
    lower = token.casefold()
    if lower in _TITLE_STYLE_CONNECTORS:
        return True
    return token[0].isupper() and token[1:] == token[1:].lower()


def _looks_like_opening_title_line(text: str) -> bool:
    flat = _single_line(text).strip()
    if not flat or _SENTENCE_END_RE.search(flat):
        return False
    if "," in flat or ";" in flat:
        return False
    tokens = _opening_preview_tokens(flat)
    if not tokens or len(tokens) > 8:
        return False
    return all(_is_title_style_token(token) for token in tokens)


def _select_section_opening_line(paragraphs: list[str]) -> str:
    """Choose a representative opening line for a section preview.

    Keep the first paragraph as the fallback, but skip a short title-like
    opening block when it is immediately followed by body text.
    """
    preview_lines: list[str] = []
    for text in paragraphs:
        flat = _single_line(text)
        if flat:
            preview_lines.append(flat)
    if not preview_lines:
        return ""

    prefix_len = 0
    while prefix_len < len(preview_lines) and _looks_like_opening_title_line(
        preview_lines[prefix_len]
    ):
        prefix_len += 1

    if prefix_len < len(preview_lines):
        first_line = preview_lines[0]
        if prefix_len > 1 or first_line.endswith(":"):
            return preview_lines[prefix_len]

    return preview_lines[0]
