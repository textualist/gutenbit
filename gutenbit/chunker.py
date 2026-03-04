"""Split book text into labelled chunks with chapter detection.

Every text block separated by blank lines is preserved and labelled with a
*kind* so that downstream consumers can reconstruct the full original text
or filter to just the content they need.

Chunk kinds
-----------
- ``"paragraph"`` — substantive prose (≥ 50 chars)
- ``"heading"``   — chapter / section headings
- ``"short"``     — short text that is not a heading or separator (e.g. dialogue)
- ``"separator"`` — decorative rules and dinkuses (``* * *``, ``---``, …)
"""

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

# Matches decorative separators / dinkuses.
# Covers patterns like: * * *, ***, ---, ===, ~~~, _ _ _, -----, etc.
_SEPARATOR_RE = re.compile(
    r"^[\s*\-=_~.#·•]+$",
)

# Minimum character length for a block to be classified as a full paragraph.
_MIN_CHUNK_LEN = 50


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete text block extracted from a book, labelled by kind."""

    position: int
    chapter: str
    content: str
    kind: str  # "paragraph", "heading", "short", or "separator"


def chunk_text(text: str) -> list[Chunk]:
    """Split *text* into labelled chunks, tracking chapter headings.

    Paragraphs are blocks separated by one or more blank lines.  Every block
    is preserved and assigned a *kind*:

    - Blocks that match a heading pattern → ``"heading"`` (also updates the
      running chapter label for subsequent chunks).
    - Blocks that consist only of punctuation / decoration → ``"separator"``.
    - Short blocks (< 50 chars) that aren't headings or separators → ``"short"``.
    - Everything else → ``"paragraph"``.

    Returns chunks in document order so that
    ``"\\n\\n".join(c.content for c in chunks)`` reproduces the text.
    """
    blocks = re.split(r"\n\s*\n", text)
    chunks: list[Chunk] = []
    chapter = ""
    position = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        if _is_heading(block):
            chapter = _normalise_heading(block)
            kind = "heading"
        elif _is_separator(block):
            kind = "separator"
        elif len(block) < _MIN_CHUNK_LEN:
            kind = "short"
        else:
            kind = "paragraph"

        chunks.append(Chunk(position=position, chapter=chapter, content=block, kind=kind))
        position += 1

    return chunks


def _is_heading(block: str) -> bool:
    """Return True if *block* looks like a chapter/section heading."""
    lines = block.splitlines()
    if len(lines) > 3:
        return False
    first = lines[0].strip()
    return bool(_HEADING_RE.match(first))


def _is_separator(block: str) -> bool:
    """Return True if *block* is a decorative rule or dinkus."""
    # Must be a single line and match the separator pattern.
    if "\n" in block:
        return False
    return bool(_SEPARATOR_RE.fullmatch(block))


def _normalise_heading(block: str) -> str:
    """Clean up a heading block into a concise chapter label."""
    return " ".join(block.split())
