"""Split book text into searchable paragraph chunks with chapter detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches common chapter/part/book headings.
# Examples: "CHAPTER I", "Chapter 12", "BOOK III", "Part 2", "ACT IV", "SCENE 2"
_HEADING_RE = re.compile(
    r"^(?:CHAPTER|BOOK|PART|ACT|SCENE|SECTION)"
    r"\s+[\dIVXLCDMivxlcdm]+\.?(?:\s.*)?$",
    re.IGNORECASE,
)

# Minimum character length for a chunk to be worth indexing.
_MIN_CHUNK_LEN = 50


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete paragraph extracted from a book's text."""

    position: int
    chapter: str
    content: str


def chunk_text(text: str) -> list[Chunk]:
    """Split *text* into paragraph chunks, tracking chapter headings.

    Paragraphs are blocks separated by one or more blank lines. Short blocks
    (< 50 chars) that look like headings update the current chapter label but
    are not emitted as standalone chunks. Short blocks that aren't headings are
    discarded as noise (decorative rules, stray numbers, etc.).
    """
    blocks = re.split(r"\n\s*\n", text)
    chunks: list[Chunk] = []
    chapter = ""
    position = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Check if this block is a chapter heading.
        if _is_heading(block):
            chapter = _normalise_heading(block)
            continue

        # Skip very short blocks (noise).
        if len(block) < _MIN_CHUNK_LEN:
            continue

        chunks.append(Chunk(position=position, chapter=chapter, content=block))
        position += 1

    return chunks


def _is_heading(block: str) -> bool:
    """Return True if *block* looks like a chapter/section heading."""
    # Multi-line blocks are never headings.
    lines = block.splitlines()
    if len(lines) > 3:
        return False
    first = lines[0].strip()
    return bool(_HEADING_RE.match(first))


def _normalise_heading(block: str) -> str:
    """Clean up a heading block into a concise chapter label."""
    return " ".join(block.split())
