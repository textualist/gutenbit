"""Split book text into labelled chunks with structural awareness.

Chunk kinds
-----------
- ``"front_matter"`` — title page, author, publisher info before the TOC/body
- ``"toc"``          — table of contents entries
- ``"heading"``      — chapter / section headings
- ``"paragraph"``    — prose text (short blocks accumulated to ≥ 50 chars)
- ``"end_matter"``   — footnotes, appendices, etc. after the last chapter
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches common chapter/part/book headings.
# Examples: "CHAPTER I", "Chapter 12", "BOOK III", "Part 2", "ACT IV", "SCENE 2"
_HEADING_RE = re.compile(
    r"^(?:CHAPTER|BOOK|PART|ACT|SCENE|SECTION|STAVE)"
    r"\s+[\dIVXLCDMivxlcdm]+[.:]?(?:\s.*)?$",
    re.IGNORECASE,
)

_TOC_RE = re.compile(r"^(?:CONTENTS|TABLE OF CONTENTS)\s*$", re.IGNORECASE)

_END_MATTER_RE = re.compile(
    r"^(?:FOOTNOTES?|APPENDIX|GLOSSARY|INDEX|END OF)\b",
    re.IGNORECASE,
)

# Minimum character length for a paragraph chunk.
_MIN_CHUNK_LEN = 50


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete text block extracted from a book, labelled by kind."""

    position: int
    chapter: str
    content: str
    kind: str


def chunk_text(text: str) -> list[Chunk]:
    """Split *text* into labelled chunks, tracking chapter headings.

    Returns chunks in document order so that
    ``"\\n\\n".join(c.content for c in chunks)`` reproduces the text.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return []

    body_start = _find_body_start(blocks)
    chunks: list[Chunk] = []
    position = 0

    # --- Pre-body: front matter and TOC ---
    toc_start = None
    for i in range(body_start):
        if _is_toc_header(blocks[i]):
            toc_start = i
            break

    for i in range(body_start):
        kind = "toc" if toc_start is not None and i >= toc_start else "front_matter"
        chunks.append(Chunk(position=position, chapter="", content=blocks[i], kind=kind))
        position += 1

    # --- Body: headings, paragraphs, end matter ---
    chapter = ""
    buffer: list[str] = []
    in_end_matter = False

    def _flush() -> None:
        nonlocal position
        if not buffer:
            return
        content = "\n\n".join(buffer)
        kind = "end_matter" if in_end_matter else "paragraph"
        chunks.append(Chunk(position=position, chapter=chapter, content=content, kind=kind))
        position += 1
        buffer.clear()

    for i in range(body_start, len(blocks)):
        block = blocks[i]

        if not in_end_matter and _is_end_matter(block):
            _flush()
            in_end_matter = True

        if in_end_matter:
            buffer.append(block)
            continue

        if _is_heading(block):
            _flush()
            chapter = _normalise_heading(block)
            chunks.append(Chunk(position=position, chapter=chapter, content=block, kind="heading"))
            position += 1
        else:
            buffer.append(block)
            if sum(len(b) for b in buffer) >= _MIN_CHUNK_LEN:
                _flush()

    _flush()
    return chunks


def _find_body_start(blocks: list[str]) -> int:
    """Find the index of the first body block (after front matter / TOC).

    Looks for a heading followed within 5 blocks by a substantial prose
    block (≥100 chars).  TOC chapter references are naturally skipped
    because they are followed by more short entries, not prose.
    """
    for i, block in enumerate(blocks):
        if not _is_heading(block):
            continue
        for j in range(i + 1, min(i + 6, len(blocks))):
            if not _is_heading(blocks[j]) and len(blocks[j]) >= 100:
                return i
    return 0


def _is_heading(block: str) -> bool:
    """Return True if *block* looks like a chapter/section heading."""
    lines = block.splitlines()
    if len(lines) > 3:
        return False
    return bool(_HEADING_RE.match(lines[0].strip()))


def _is_toc_header(block: str) -> bool:
    """Return True if *block* is a 'CONTENTS' / 'TABLE OF CONTENTS' header."""
    return bool(_TOC_RE.match(block.splitlines()[0].strip()))


def _is_end_matter(block: str) -> bool:
    """Return True if *block* starts a footnotes/appendix section."""
    return bool(_END_MATTER_RE.match(block.splitlines()[0].strip()))


def _normalise_heading(block: str) -> str:
    """Clean up a heading block into a concise chapter label."""
    return " ".join(block.split())
