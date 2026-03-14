"""Pure utility functions for the gutenbit CLI.

No Click, no display calls, no database side-effects — only stdlib and
gutenbit.db types are imported here so this module stays at the bottom of
the internal dependency graph.
"""

from __future__ import annotations

import re
import sqlite3
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from gutenbit.db import ChunkRecord

# ---------------------------------------------------------------------------
# FTS5 constants
# ---------------------------------------------------------------------------

# FTS5 operator tokens that signal an intentional advanced query.
_FTS_OPERATOR_RE = re.compile(
    r"""
    \bAND\b | \bOR\b | \bNOT\b | \bNEAR\b
    | [*"()\^]
    """,
    re.VERBOSE,
)
_SEARCH_QUERY_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")
_SEARCH_QUERY_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "before",
        "being",
        "call",
        "could",
        "first",
        "from",
        "have",
        "having",
        "however",
        "into",
        "little",
        "never",
        "ought",
        "shall",
        "should",
        "since",
        "some",
        "there",
        "these",
        "those",
        "through",
        "under",
        "until",
        "upon",
        "when",
        "where",
        "which",
        "while",
        "would",
        "years",
    }
)

# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------


def _preview(text: str, limit: int) -> str:
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _single_line(text: str) -> str:
    """Collapse all whitespace so tabular CLI output stays on one line."""
    return " ".join(text.split())


def _indent_block(text: str, prefix: str = "    ") -> str:
    lines = text.splitlines()
    if not lines:
        return prefix if text else ""
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)


def _joined_chunk_text(
    rows: list[ChunkRecord],
) -> str:
    return "\n\n".join(row.content for row in rows)


def _format_int(value: int) -> str:
    return f"{value:,}"


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------


def _estimate_read_time(words: int, *, wpm: int = 250) -> str:
    if words <= 0:
        return "n/a"
    minutes = max(1, round(words / wpm))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


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


# ---------------------------------------------------------------------------
# FTS utilities
# ---------------------------------------------------------------------------


def _fts_phrase_query(query: str) -> str:
    """Wrap a raw query as an exact FTS5 phrase, escaping inner quotes."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def _has_fts_operators(query: str) -> bool:
    """Return True if *query* contains FTS5 operator syntax."""
    return bool(_FTS_OPERATOR_RE.search(query))


def _safe_fts_query(query: str) -> str:
    """Escape a plain-text query so punctuation doesn't trigger FTS5 errors.

    Each whitespace-separated token is individually quoted so that
    apostrophes, hyphens, periods, and other punctuation are treated as
    literal characters while FTS5 still performs an implicit-AND across
    tokens.
    """
    tokens = query.split()
    if not tokens:
        return query
    quoted = [_fts_phrase_query(t) for t in tokens]
    return " ".join(quoted)


def _quick_action_search_query(rows: list[ChunkRecord]) -> str:
    """Choose a real in-book token for quick-action search examples."""
    text_rows = [row.content for row in rows if row.kind == "text"]
    for content in text_rows:
        tokens = _SEARCH_QUERY_TOKEN_RE.findall(content)
        for token in tokens:
            if len(token) >= 4 and token.casefold() not in _SEARCH_QUERY_STOPWORDS:
                return token
    for content in text_rows:
        tokens = _SEARCH_QUERY_TOKEN_RE.findall(content)
        if tokens:
            return tokens[0]
    return "chapter"


def _format_fts_error(exc: sqlite3.Error) -> str:
    detail = " ".join(str(exc).split()).strip().rstrip(".")
    if not detail:
        return "Invalid FTS query syntax."
    return f"Invalid FTS query syntax: {detail}."


# ---------------------------------------------------------------------------
# Table printing
# ---------------------------------------------------------------------------


def _print_key_value_table(
    rows: list[tuple[str, str]],
    *,
    show_header: bool = True,
    key_header: str = "Field",
    value_header: str = "Value",
) -> None:
    if not rows:
        return
    key_width = max(len(key_header), max(len(key) for key, _ in rows))
    if show_header:
        print(f"  {key_header:<{key_width}}  {value_header}")
        print(f"  {'-' * key_width}  {'-' * len(value_header)}")
    for key, value in rows:
        shown = _single_line(value) if value else "-"
        print(f"  {key:<{key_width}}  {shown}")


def _print_table(headers: list[str], rows: list[list[str]], *, right_align: set[int]) -> None:
    if not headers:
        return
    widths = []
    for idx, header in enumerate(headers):
        widest = len(header)
        for row in rows:
            widest = max(widest, len(row[idx]))
        widths.append(widest)

    def _fmt(cell: str, idx: int) -> str:
        width = widths[idx]
        if idx in right_align:
            return f"{cell:>{width}}"
        return f"{cell:<{width}}"

    print("  " + "  ".join(_fmt(header, i) for i, header in enumerate(headers)))
    print("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        print("  " + "  ".join(_fmt(cell, i) for i, cell in enumerate(row)))


def _print_block_header(title: str) -> None:
    print(f"\n[{title.upper()}]")


# ---------------------------------------------------------------------------
# Package / config utilities
# ---------------------------------------------------------------------------


def _package_version() -> str:
    try:
        return package_version("gutenbit")
    except PackageNotFoundError:
        try:
            from gutenbit import __version__
        except ImportError:
            return "0.dev0+unknown"
        return __version__


def _toc_expand_depth(expand: str) -> int:
    return 4 if expand == "all" else int(expand)
