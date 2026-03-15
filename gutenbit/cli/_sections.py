"""Section/TOC data model, summary builders, and reading-window logic."""

from __future__ import annotations

from typing import Any, cast

from gutenbit.cli._context import _display, _load_catalog, _normalize_apostrophes
from gutenbit.cli._display import CliDisplay
from gutenbit.cli._json import JSON_OPENING_LINE_PREVIEW_CHARS
from gutenbit.cli._query import (
    DEFAULT_DOWNLOAD_DELAY,
    OPENING_PREVIEW_PARAGRAPH_LIMIT,
    OPENING_SECTION_SKIP_HEADINGS,
    _no_chunks_display_message,
    _quick_action_search_query,
    _section_depth,
    _section_path,
    _section_path_parts,
)
from gutenbit.cli._text_utils import (
    _preview,
    _select_section_opening_line,
    _single_line,
    _split_semicolon_list,
)
from gutenbit.cli._types import (
    _ChunkCounts,
    _QuickActions,
    _SectionRow,
    _SectionState,
    _SectionSummary,
)
from gutenbit.db import (
    ChunkRecord,
    Database,
    TextState,
    div_parts_match,
    normalize_div_segment,
)

# ---------------------------------------------------------------------------
# Read time estimation
# ---------------------------------------------------------------------------


def _estimate_read_time(words: int, *, wpm: int = 250) -> str:
    if words <= 0:
        return "n/a"
    minutes = max(1, round(words / wpm))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# ---------------------------------------------------------------------------
# Section path / selector utilities
# ---------------------------------------------------------------------------


def _section_selector_parts(raw: str) -> list[str]:
    parts = [normalize_div_segment(part) for part in raw.split("/") if part.strip()]
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
        if div_parts_match(query_parts, _section_selector_parts(section_path)):
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


# ---------------------------------------------------------------------------
# Section number lookup
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Reading window functions
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
# Section summary builders
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
    opening_rows_result = _opening_rows(db, book_id, 1)
    if opening_rows_result:
        opening_position = opening_rows_result[0].position
        opening_section = _section_path(
            opening_rows_result[0].div1,
            opening_rows_result[0].div2,
            opening_rows_result[0].div3,
            opening_rows_result[0].div4,
        )
        opening_section_num = _visible_section_number(
            visible_section_rows,
            target_section=opening_section,
        )

    opening_example_rows = opening_rows_result or chunk_records
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


# ---------------------------------------------------------------------------
# Section display helpers
# ---------------------------------------------------------------------------


def _render_section_summary(db: Database, book_id: int, *, expand_depth: int) -> int:
    summary = _build_section_summary(db, book_id, expand_depth=expand_depth)
    if summary is None:
        _display().error(_no_chunks_display_message(db, book_id))
        return 1
    _display().section_summary(summary)
    return 0


def _print_passage(
    payload: dict[str, Any],
    *,
    action_hints: dict[str, str] | None = None,
    footer_stats: list[str] | None = None,
) -> None:
    _display().passage(payload, action_hints=action_hints, footer_stats=footer_stats)


def _view_action_hints(book_id: int, summary: _SectionSummary | None) -> dict[str, str]:
    quick_actions: _QuickActions = (
        summary["quick_actions"]
        if summary is not None
        else {
            "toc_expand_all": "",
            "search": "",
            "view_first_section": "",
            "view_by_position": "",
            "view_all": "",
        }
    )
    return {
        "toc": f"gutenbit toc {book_id}",
        "view_first_section": quick_actions["view_first_section"],
        "view_all": quick_actions["view_all"],
        "search": quick_actions["search"],
    }


def _resolve_toc_book_id(
    db: Database,
    requested_id: int,
    *,
    refresh: bool = False,
    display: CliDisplay,
    as_json: bool,
    process_books_for_ingest: Any,
) -> tuple[int | None, list[str]]:
    """Resolve a toc request to stored text, auto-adding the canonical book when needed."""
    if db.has_text(requested_id):
        return requested_id, []

    catalog = _load_catalog(refresh, display=display, as_json=as_json)
    rec = catalog.get(requested_id)
    if rec is None:
        return requested_id, []

    title = _single_line(rec.title)
    if rec.id != requested_id and not as_json:
        display.status(f"  remapped {requested_id} -> {rec.id}: {title} (canonical edition)")

    state = db.text_states([rec.id]).get(rec.id, TextState(has_text=False, has_current_text=False))
    if state.has_current_text:
        return rec.id, []

    statuses, errors = process_books_for_ingest(
        db,
        [rec],
        delay=DEFAULT_DOWNLOAD_DELAY,
        as_json=as_json,
        display=display,
        failure_action="add",
        force=False,
    )
    if statuses.get(rec.id) == "failed":
        return None, errors
    return rec.id, []
