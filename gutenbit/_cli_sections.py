"""Section/TOC data model for the gutenbit CLI.

Builds and queries the structured representation of a book's sections.
Used by the ``toc`` and ``view`` commands.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict, cast

from gutenbit.db import ChunkRecord, Database, _div_parts_match, _normalize_div_segment

from gutenbit._cli_utils import (
    _estimate_read_time,
    _preview,
    _quick_action_search_query,
    _single_line,
    _split_semicolon_list,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_OPENING_LINE_PREVIEW_CHARS = 140
OPENING_PREVIEW_PARAGRAPH_LIMIT = 4
OPENING_SECTION_SKIP_HEADINGS = frozenset(
    {
        "preface",
        "introduction",
        "foreword",
        "prologue",
        "contents",
        "table of contents",
        "list of illustrations",
        "illustrations",
        "transcriber's note",
        "transcribers note",
        "author's note",
        "authors note",
    }
)
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
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]*$')

# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------


class _SectionState(TypedDict):
    heading: str
    path: str
    position: int
    paragraphs: int
    chars: int
    first_position: int
    opening_candidates: list[str]


class _BookSummary(TypedDict):
    id: int
    title: str
    authors: str
    language: str
    issued: str
    type: str
    locc: str
    subjects: list[str]
    bookshelves: list[str]


class _ChunkCounts(TypedDict):
    heading: int
    text: int


class _OverviewSummary(TypedDict):
    chunks_total: int
    chunk_counts: _ChunkCounts
    sections_total: int
    sections_shown: int
    levels_total: int
    levels_shown: int
    paragraphs_total: int
    chars_total: int
    est_words: int
    est_read_time: str


class _SectionRow(TypedDict):
    section_number: int
    section: str
    position: int
    paras: int
    chars: int
    est_words: int
    est_read: str
    opening_line: str


class _QuickActions(TypedDict):
    toc_expand_all: str
    search: str
    view_first_section: str
    view_by_position: str
    view_all: str


class _SectionSummary(TypedDict):
    book: _BookSummary
    overview: _OverviewSummary
    sections: list[_SectionRow]
    quick_actions: _QuickActions


# ---------------------------------------------------------------------------
# Text / title analysis
# ---------------------------------------------------------------------------


def _normalize_apostrophes(s: str) -> str:
    """Replace curly/typographic apostrophes with ASCII for matching."""
    return s.replace("\u2019", "'").replace("\u2018", "'")


def _opening_preview_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip("()[]{}\"'""'',;:-")
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


# ---------------------------------------------------------------------------
# Section path utilities
# ---------------------------------------------------------------------------


def _section_path(*levels: str) -> str:
    return " / ".join(level for level in levels if level) or "(unsectioned opening)"


def _section_path_parts(section: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in section.split(" / ") if part.strip())


def _section_depth(section: str) -> int:
    return len(_section_path_parts(section)) or 1


def _section_selector_parts(raw: str) -> list[str]:
    parts = [_normalize_div_segment(part) for part in raw.split("/") if part.strip()]
    if len(parts) > 4:
        raise ValueError("div path has too many segments (max 4: div1/div2/div3/div4)")
    return parts


def _canonical_section_match(
    summary: _SectionSummary | None, selector: str
) -> tuple[str, int] | None:
    if summary is None:
        return None
    query_parts = _section_selector_parts(selector)
    if not query_parts:
        return None
    for section in summary["sections"]:
        section_path = str(section["section"]).strip()
        if not section_path:
            continue
        if _div_parts_match(query_parts, _section_selector_parts(section_path)):
            return section_path, int(section["section_number"])
    return None


def _truncate_section_label(label: str, width: int) -> str:
    """Truncate a section path, preferring the most specific (deepest) level.

    When the full path ("BOOK TITLE / CHAPTER 1") exceeds *width*,
    show the deepest level with a ".../ " prefix so users see the
    chapter name rather than a truncated book title.
    """
    if len(label) <= width:
        return label
    parts = label.split(" / ")
    if len(parts) > 1:
        deepest = parts[-1]
        prefix = ".../ "
        if len(prefix) + len(deepest) <= width:
            return prefix + deepest
        # Deepest level itself is too long — truncate it with prefix
        keep = max(1, width - len(prefix) - 3)
        return prefix + deepest[:keep] + "..."
    # Single level, just truncate
    keep = max(1, width - 3)
    return label[:keep] + "..."


# ---------------------------------------------------------------------------
# Opening / reading window helpers
# ---------------------------------------------------------------------------


def _opening_rows(db: Database, book_id: int, n: int) -> list[ChunkRecord]:
    """Return a default reading window, skipping common front-matter headings.

    Skips headings that match the book title, byline patterns ("by ..."),
    and well-known front-matter labels (preface, introduction, etc.).
    """
    rows = db.chunk_records(book_id)
    if not rows:
        return []

    skip = set(OPENING_SECTION_SKIP_HEADINGS)
    book = db.book(book_id)
    title_lower = ""
    if book:
        title_lower = _normalize_apostrophes(book.title.casefold())
        skip.add(title_lower)

    first_heading_index = 0
    for idx, row in enumerate(rows):
        if row.kind != "heading":
            continue
        heading = _normalize_apostrophes(row.content.casefold())
        if heading in skip:
            continue
        if heading.startswith("by "):
            continue
        # Skip headings that match the book title or a prefix/expansion of it
        # (e.g. "NOSTROMO" for "Nostromo: A Tale of the Seaboard", or
        # "THE MIRROR OF THE SEA MEMORIES AND IMPRESSIONS" for "The Mirror of the Sea").
        if (
            title_lower
            and len(heading) >= 3
            and (title_lower.startswith(heading) or heading.startswith(title_lower))
        ):
            continue
        first_heading_index = idx
        break

    window = rows[first_heading_index : first_heading_index + n]
    # Ensure the window includes at least one text chunk when possible.
    # Books with nested headings (PART → SUBTITLE → CHAPTER) can exhaust
    # the default window with headings only, showing no prose.
    if window and all(r.kind == "heading" for r in window):
        end = first_heading_index + n
        while end < len(rows) and rows[end].kind == "heading":
            end += 1
        if end < len(rows):
            window = rows[first_heading_index : end + 1]
    return window


def _section_reading_window(rows: list[ChunkRecord], *, text_passages: int) -> list[ChunkRecord]:
    """Return a readable section window with heading context plus prose.

    Includes any leading heading rows, then keeps reading until *text_passages*
    text chunks have been collected. This makes ``view --section`` land on prose
    by default instead of stopping at a bare heading.
    """
    if not rows or text_passages <= 0:
        return []

    window: list[ChunkRecord] = []
    seen_text = 0
    for row in rows:
        window.append(row)
        if row.kind == "text":
            seen_text += 1
            if seen_text >= text_passages:
                break
    return window


# ---------------------------------------------------------------------------
# Section summary building
# ---------------------------------------------------------------------------


def _collapse_section_rows(
    section_rows: list[_SectionRow], *, expand_depth: int
) -> list[_SectionRow]:
    if expand_depth >= 4:
        return [cast(_SectionRow, dict(section)) for section in section_rows]

    visible_rows: list[_SectionRow] = []
    visible_parts: list[tuple[str, ...]] = []
    for section in section_rows:
        row = cast(_SectionRow, dict(section))
        parts = _section_path_parts(str(row["section"]))
        if len(parts) <= expand_depth:
            visible_rows.append(row)
            visible_parts.append(parts)
            continue

        for idx in range(len(visible_rows) - 1, -1, -1):
            ancestor_parts = visible_parts[idx]
            if len(ancestor_parts) > len(parts) or parts[: len(ancestor_parts)] != ancestor_parts:
                continue
            visible_rows[idx]["paras"] = int(visible_rows[idx]["paras"]) + int(row["paras"])
            visible_rows[idx]["chars"] = int(visible_rows[idx]["chars"]) + int(row["chars"])
            if (
                not str(visible_rows[idx]["opening_line"]).strip()
                and str(row["opening_line"]).strip()
            ):
                visible_rows[idx]["opening_line"] = str(row["opening_line"])
            break

    for row in visible_rows:
        chars = int(row["chars"])
        words = round(chars / 5) if chars else 0
        row["est_words"] = words
        row["est_read"] = _estimate_read_time(words)
    return visible_rows


def _visible_section_number(
    section_rows: list[_SectionRow],
    *,
    target_section: str,
) -> int | None:
    target_parts = _section_path_parts(target_section)
    best_match: tuple[int, int] | None = None
    for row in section_rows:
        parts = _section_path_parts(str(row["section"]))
        if not parts or len(parts) > len(target_parts):
            continue
        if target_parts[: len(parts)] != parts:
            continue
        candidate = (len(parts), int(row["section_number"]))
        if best_match is None or candidate[0] > best_match[0]:
            best_match = candidate
    return best_match[1] if best_match is not None else None


def _build_section_summary(
    db: Database, book_id: int, *, expand_depth: int | None = None
) -> _SectionSummary | None:
    chunk_records = db.chunk_records(book_id)
    if not chunk_records:
        return None

    book = db.book(book_id)
    title = _single_line(book.title) if book else f"Book {book_id}"
    authors = _single_line(book.authors) if book and book.authors else ""
    language = book.language if book else ""
    issued = book.issued if book else ""
    book_type = book.type if book else ""
    locc = _single_line(book.locc) if book and book.locc else ""
    subjects = _split_semicolon_list(book.subjects) if book else []
    bookshelves = _split_semicolon_list(book.bookshelves) if book else []

    sections: list[_SectionState] = []
    kind_counts: _ChunkCounts = {"heading": 0, "text": 0}
    total_chars = 0
    for rec in chunk_records:
        if rec.kind == "heading":
            kind_counts["heading"] += 1
        elif rec.kind == "text":
            kind_counts["text"] += 1
        total_chars += rec.char_count

        if rec.kind == "heading":
            path = _section_path(rec.div1, rec.div2, rec.div3, rec.div4)
            if path == "(unsectioned opening)":
                path = ""
            sections.append(
                {
                    "heading": _single_line(rec.content) or "(untitled section)",
                    "path": path,
                    "position": rec.position,
                    "paragraphs": 0,
                    "chars": 0,
                    "first_position": rec.position,
                    "opening_candidates": [],
                }
            )
        elif rec.kind == "text" and sections:
            sections[-1]["paragraphs"] = int(sections[-1]["paragraphs"]) + 1
            sections[-1]["chars"] = int(sections[-1]["chars"]) + rec.char_count
            opening_candidates = sections[-1]["opening_candidates"]
            if len(opening_candidates) < OPENING_PREVIEW_PARAGRAPH_LIMIT:
                opening_candidates.append(rec.content)

    total_chunks = len(chunk_records)
    total_sections = len(sections)
    total_paragraphs = kind_counts.get("text", 0)
    est_words = round(total_chars / 5) if total_chars else 0
    read_time = _estimate_read_time(est_words)

    raw_section_rows: list[_SectionRow] = []
    for idx, sec in enumerate(sections, start=1):
        chars = int(sec["chars"])
        est_words_for_section = round(chars / 5)
        opening_line = _select_section_opening_line(sec["opening_candidates"])
        raw_section_rows.append(
            {
                "section_number": idx,
                "section": str(sec["path"]) or str(sec["heading"]),
                "position": (
                    int(sec["first_position"])
                    if sec.get("first_position") is not None
                    else int(sec["position"])
                ),
                "paras": int(sec["paragraphs"]),
                "chars": chars,
                "est_words": est_words_for_section,
                "est_read": _estimate_read_time(est_words_for_section),
                "opening_line": opening_line,
            }
        )

    visible_section_rows: list[_SectionRow]
    if expand_depth is not None:
        visible_section_rows = _collapse_section_rows(
            raw_section_rows,
            expand_depth=expand_depth,
        )
    else:
        visible_section_rows = [cast(_SectionRow, dict(section)) for section in raw_section_rows]

    total_levels = max(
        (_section_depth(str(row["section"])) for row in raw_section_rows),
        default=0,
    )
    shown_levels = max(
        (_section_depth(str(row["section"])) for row in visible_section_rows),
        default=0,
    )

    opening_section_num: int | None = None
    opening_position: int | None = None
    opening_rows = _opening_rows(db, book_id, 1)
    if opening_rows:
        opening_position = opening_rows[0].position
        opening_section = _section_path(
            opening_rows[0].div1,
            opening_rows[0].div2,
            opening_rows[0].div3,
            opening_rows[0].div4,
        )
        opening_section_num = _visible_section_number(
            visible_section_rows,
            target_section=opening_section,
        )

    opening_example_rows = opening_rows or chunk_records
    search_query = _quick_action_search_query(opening_example_rows)
    search_cmd = f'gutenbit search "{search_query}" --book {book_id}'

    first_section_cmd = ""
    if opening_section_num is not None:
        first_section_cmd = f"gutenbit view {book_id} --section {opening_section_num} --forward 20"

    view_position_cmd = ""
    if opening_position is not None:
        view_position_cmd = f"gutenbit view {book_id} --position {opening_position} --forward 20"

    toc_expand_all_cmd = ""
    if expand_depth is not None and expand_depth < 4:
        toc_expand_all_cmd = f"gutenbit toc {book_id} --expand all"

    view_all_cmd = f"gutenbit view {book_id} --all"

    summary: _SectionSummary = {
        "book": {
            "id": book_id,
            "title": title,
            "authors": authors,
            "language": language,
            "issued": issued,
            "type": book_type,
            "locc": locc,
            "subjects": subjects,
            "bookshelves": bookshelves,
        },
        "overview": {
            "chunks_total": total_chunks,
            "chunk_counts": kind_counts,
            "sections_total": total_sections,
            "sections_shown": len(visible_section_rows),
            "levels_total": total_levels,
            "levels_shown": shown_levels,
            "paragraphs_total": total_paragraphs,
            "chars_total": total_chars,
            "est_words": est_words,
            "est_read_time": read_time,
        },
        "sections": visible_section_rows,
        "quick_actions": {
            "toc_expand_all": toc_expand_all_cmd,
            "search": search_cmd,
            "view_first_section": first_section_cmd,
            "view_by_position": view_position_cmd,
            "view_all": view_all_cmd,
        },
    }
    return summary


def _section_summary_json_payload(summary: _SectionSummary) -> dict[str, Any]:
    json_sections: list[dict[str, Any]] = []
    for sec in summary["sections"]:
        sec_json = dict(sec)
        sec_json.pop("position", None)
        sec_json["opening_line"] = _preview(sec["opening_line"], JSON_OPENING_LINE_PREVIEW_CHARS)
        json_sections.append(sec_json)

    return {
        "book": dict(summary["book"]),
        "overview": {
            **summary["overview"],
            "chunk_counts": dict(summary["overview"]["chunk_counts"]),
        },
        "sections": json_sections,
        "quick_actions": dict(summary["quick_actions"]),
    }


def _section_examples(db: Database, book_id: int, *, limit: int = 5) -> list[str]:
    summary = _build_section_summary(db, book_id)
    if summary is not None:
        numbered_examples: list[str] = []
        for sec in summary["sections"]:
            if sec["section_number"] > 0 and sec["section"].strip():
                numbered_examples.append(f"{sec['section_number']}. {sec['section'].strip()}")
            if len(numbered_examples) >= limit:
                break
        if numbered_examples:
            return numbered_examples

    examples: list[str] = []
    seen: set[str] = set()
    for _pos, div1, div2, div3, div4, _content, _kind, _char_count in db.chunks(
        book_id, kinds=["heading"]
    ):
        section = _section_path(div1, div2, div3, div4)
        if section == "(unsectioned opening)" or section in seen:
            continue
        seen.add(section)
        examples.append(section)
        if len(examples) >= limit:
            break
    return examples


def _section_number_lookup(db: Database) -> Any:
    cache: dict[int, dict[str, int]] = {}

    def lookup(book: int, section: str | None) -> int | None:
        if not section:
            return None
        if book not in cache:
            summary = _build_section_summary(db, book)
            cache[book] = (
                {str(sec["section"]): int(sec["section_number"]) for sec in summary["sections"]}
                if summary is not None
                else {}
            )
        return cache[book].get(section)

    return lookup
