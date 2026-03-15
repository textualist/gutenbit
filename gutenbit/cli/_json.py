"""JSON envelope, payload builders, and serialization utilities."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from gutenbit.cli._text_utils import _single_line

if TYPE_CHECKING:
    from gutenbit.db import ChunkRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_BOOK_ID_KEY = "book_id"
JSON_OPENING_LINE_PREVIEW_CHARS = 140

# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def _json_envelope(
    command: str,
    *,
    ok: bool,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "data": data,
        "warnings": warnings or [],
        "errors": errors or [],
    }


def _print_json_envelope(
    command: str,
    *,
    ok: bool,
    data: dict[str, Any] | list[Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    print(
        json.dumps(
            _json_envelope(command, ok=ok, data=data, warnings=warnings, errors=errors),
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _json_search_filters(
    *,
    author: str | None,
    title: str | None,
    book_ids: tuple[int, ...],
    kind: str,
    section: str | None,
) -> dict[str, Any]:
    if not book_ids:
        bid: int | list[int] | None = None
    elif len(book_ids) == 1:
        bid = book_ids[0]
    else:
        bid = list(book_ids)
    return {
        "author": author,
        "title": title,
        JSON_BOOK_ID_KEY: bid,
        "kind": kind,
        "section": section,
    }


def _book_payload(book: Any) -> dict[str, Any]:
    return {
        "id": book.id,
        "title": _single_line(book.title),
        "authors": _single_line(book.authors),
        "language": _single_line(book.language),
        "subjects": _single_line(book.subjects),
        "locc": _single_line(book.locc),
        "bookshelves": _single_line(book.bookshelves),
        "issued": _single_line(book.issued),
        "type": _single_line(book.type),
    }


def _passage_payload(
    *,
    book_id: int,
    title: str,
    author: str,
    section: str | None,
    section_number: int | None,
    position: int | None,
    forward: int | None,
    radius: int | None,
    all_scope: bool | None = None,
    content: str,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        JSON_BOOK_ID_KEY: book_id,
        "title": title,
        "author": author,
        "section": section,
        "section_number": section_number,
        "position": position,
        "forward": forward,
        "radius": radius,
        "all": all_scope,
        "content": content,
    }
    if extras:
        payload.update(extras)
    return payload


def _joined_chunk_text(
    rows: list[ChunkRecord],
) -> str:
    return "\n\n".join(row.content for row in rows)
