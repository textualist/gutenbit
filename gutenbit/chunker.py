"""Split book text into labelled chunks with structural awareness.

Chunk kinds
-----------
- ``"front_matter"`` — title page, author, publisher info before the TOC/body
- ``"toc"``          — table of contents entries
- ``"heading"``      — chapter / section headings
- ``"paragraph"``    — prose text (short blocks accumulated to ≥ 50 chars)
- ``"end_matter"``   — footnotes, appendices, etc. after the last chapter

Structural divisions (div1–div4)
---------------------------------
Each chunk records its position in the book hierarchy via four fields:

- ``div1`` — broadest division: BOOK, PART, ACT
- ``div2`` — chapter-level: CHAPTER, STAVE, SCENE
- ``div3`` — sub-chapter: SECTION
- ``div4`` — reserved for deeper nesting

Fields are empty strings when the corresponding level has not yet appeared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches common chapter/part/book headings.
# Examples: "CHAPTER I", "Chapter 12", "BOOK III", "Part 2", "ACT IV", "SCENE 2",
#           "STAVE I:  Subtitle", "CHAPTER. XVIII." (Locke-style with period after keyword),
#           "BOOK ONE: 1805" (Tolstoy-style word ordinals)
_ORDINAL_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|"
    "eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    "eleventh|twelfth|thirteenth|fourteenth|fifteenth"
)
_HEADING_RE = re.compile(
    r"^(?:CHAPTER|BOOK|PART|ACT|SCENE|SECTION|STAVE)"
    r"\.?\s+(?:[\dIVXLCDMivxlcdm]+|(?:" + _ORDINAL_WORDS + r"))[.:]?(?:\s.*)?$",
    re.IGNORECASE,
)

_TOC_RE = re.compile(r"^(?:CONTENTS|TABLE OF CONTENTS)\s*$", re.IGNORECASE)

_END_MATTER_RE = re.compile(
    r"^(?:FOOTNOTES?|APPENDIX|GLOSSARY|INDEX|END OF)\b",
    re.IGNORECASE,
)

# Minimum character length for a paragraph chunk.
_MIN_CHUNK_LEN = 50

# Maps lowercase heading keyword → structural rank (1 = broadest).
# Any unrecognised keyword defaults to rank 2.
_HEADING_RANK: dict[str, int] = {
    "book": 1,
    "part": 1,
    "act": 1,
    "chapter": 2,
    "stave": 2,
    "scene": 2,
    "section": 3,
}


@dataclass(frozen=True, slots=True)
class Chunk:
    """A discrete text block extracted from a book, labelled by kind."""

    position: int
    div1: str  # broadest division (BOOK, PART, ACT)
    div2: str  # chapter-level (CHAPTER, STAVE, SCENE)
    div3: str  # sub-chapter (SECTION)
    div4: str  # reserved
    content: str
    kind: str


def chunk_text(text: str) -> list[Chunk]:
    """Split *text* into labelled chunks, tracking structural divisions.

    Returns chunks in document order so that
    ``"\\n\\n".join(c.content for c in chunks)`` reproduces the text.

    Each chunk carries ``div1``–``div4`` reflecting its position in the
    book hierarchy at the point it appears (e.g. ``div1="PART II"``,
    ``div2="CHAPTER III"``).  Levels not yet encountered are empty strings.
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
        chunks.append(
            Chunk(
                position=position, div1="", div2="", div3="", div4="",
                content=blocks[i], kind=kind,
            )
        )
        position += 1

    # --- Body: headings, paragraphs, end matter ---
    divs = ["", "", "", ""]  # [div1, div2, div3, div4]
    buffer: list[str] = []
    in_end_matter = False

    def _flush() -> None:
        nonlocal position
        if not buffer:
            return
        content = "\n\n".join(buffer)
        kind = "end_matter" if in_end_matter else "paragraph"
        chunks.append(
            Chunk(
                position=position, div1=divs[0], div2=divs[1], div3=divs[2], div4=divs[3],
                content=content, kind=kind,
            )
        )
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
            rank = _heading_rank(block)
            divs[rank - 1] = _normalise_heading(block)
            # Clear all finer-grained divisions when a broader one is set.
            for lvl in range(rank, 4):
                divs[lvl] = ""
            chunks.append(
                Chunk(
                    position=position, div1=divs[0], div2=divs[1], div3=divs[2], div4=divs[3],
                    content=block, kind="heading",
                )
            )
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

    When prose is found, returns the heading *immediately preceding* it
    (not the first heading that triggered the scan), so that a long TOC
    with trailing headings close to the first prose paragraph does not
    push the body start too early.
    """
    for i, block in enumerate(blocks):
        if not _is_heading(block):
            continue
        for j in range(i + 1, min(i + 6, len(blocks))):
            if not _is_heading(blocks[j]) and len(blocks[j]) >= 100:
                # Walk back from the prose block to the nearest heading.
                for k in range(j - 1, i - 1, -1):
                    if _is_heading(blocks[k]):
                        # Also step back through any directly preceding headings
                        # with a strictly broader rank (e.g. "PART I" before
                        # "CHAPTER I").  Equal-rank headings are TOC entries
                        # and must not be pulled into the body.
                        while k > 0 and _is_heading(blocks[k - 1]):
                            if _heading_rank(blocks[k - 1]) >= _heading_rank(blocks[k]):
                                break
                            k -= 1
                        return k
                return i
    return 0


def _heading_rank(block: str) -> int:
    """Return the structural rank (1 = broadest) for a heading block."""
    keyword = block.split()[0].rstrip(".]").lower()
    return _HEADING_RANK.get(keyword, 2)


def _is_heading(block: str) -> bool:
    """Return True if *block* looks like a chapter/section heading."""
    lines = block.splitlines()
    if len(lines) > 3:
        return False
    # Strip a trailing ']' that may appear when a chapter heading is embedded
    # inside a split [Illustration: ...] tag (e.g. "Chapter I.]").
    return bool(_HEADING_RE.match(lines[0].strip().rstrip("]")))


def _is_toc_header(block: str) -> bool:
    """Return True if *block* is a 'CONTENTS' / 'TABLE OF CONTENTS' header."""
    return bool(_TOC_RE.match(block.splitlines()[0].strip()))


def _is_end_matter(block: str) -> bool:
    """Return True if *block* starts a footnotes/appendix section."""
    return bool(_END_MATTER_RE.match(block.splitlines()[0].strip()))


def _normalise_heading(block: str) -> str:
    """Clean up a heading block into a concise chapter label."""
    return " ".join(block.split()).rstrip("]").strip()
