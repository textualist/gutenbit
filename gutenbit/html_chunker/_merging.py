"""Section merging, deduplication, and paragraph-range utilities.

Layer 3 — depends on ``_common``, ``_headings``, and ``_scanning``.

Merge passes: ``_merge_bare_heading_pairs``, ``_merge_adjacent_duplicate_sections``,
``_merge_chapter_subtitle_sections``, ``_merge_chapter_description_paragraphs``
Filter passes: ``_strip_printed_toc_page_runs``, ``_drop_empty_interior_title_repeats``
Paragraph utilities: ``_paragraph_range_between``, ``_has_paragraphs_between``,
``_paragraphs_between_are_metadata_only``
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right

from bs4 import Tag

from gutenbit.html_chunker._common import (
    _BARE_HEADING_NUMBER_RE,
    _BROAD_KEYWORDS,
    _DRAMATIC_CONTEXT_HEADING_RE,
    _HEADING_TAGS,
    _PLAIN_NUMBER_HEADING_RE,
    _TERMINAL_MARKER_RE,
    _heading_element_or_anchor,
    _Section,
)
from gutenbit.html_chunker._headings import (
    _broad_heading_with_enumerated_child,
    _classify_level,
    _heading_key,
    _heading_keyword,
    _is_bare_keyword_heading,
    _is_title_like_heading,
    _next_heading_is_subtitle,
    _same_heading_text,
)
from gutenbit.html_chunker._scanning import (
    _DocumentIndex,
    _paragraphs_in_range,
    _tag_position,
)

# ---------------------------------------------------------------------------
# Thresholds for _merge_chapter_description_paragraphs
# ---------------------------------------------------------------------------

# Longest description paragraph to merge (chars).  Gutenberg chapter
# descriptions are typically 1–2 lines; anything longer is body prose.
_MAX_DESCRIPTION_PARAGRAPH_LEN = 300
# Minimum fraction of alpha chars that must be uppercase for the paragraph
# to be considered an ALL-CAPS description (allows minor OCR artifacts).
_MIN_UPPERCASE_RATIO = 0.9
# Maximum word count between duplicate heading pairs that qualifies as an
# epigraph/introductory poem rather than a full section body.  When the
# content between two same-text headings is at most this many words, the
# first heading is treated as an epigraph wrapper and merged with the second.
# Observed ceiling: PG 75942 MARY MOODY EMERSON at 353 words.  Cannot
# raise above 400 without false-positiving on PG 2302 (Poor Folk), an
# epistolary novel with short same-date letters that hit the ≥ 3 pairs guard.
_MAX_EPIGRAPH_WORDS = 400

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Trailing isolated page number on a heading (e.g. "THE WILL TO BELIEVE 1").
# Matches a space-separated bare integer at the end of the heading text.
# Used to detect printed-TOC entries that leak into the heading sequence.
_TRAILING_PAGE_NUMBER_RE = re.compile(r"\s+\d{1,4}\s*$")

# Minimum length for a printed-TOC run before it is suppressed.  Three entries
# keeps the filter far from real chapter titles that happen to end with a
# number (e.g. a standalone "ACT 2" or "HENRY V, PART 2").
_PRINTED_TOC_RUN_MIN = 3

# Minimum word count (excluding the trailing number) required for a heading
# to be treated as a printed-TOC entry.  Printed-TOC rows for essay
# collections are multi-word titles like "THE WILL TO BELIEVE" or "THE
# SENTIMENT OF RATIONALITY"; shorter forms like "Letter 1" or "Scene 2"
# are bare enumeration labels that must not be suppressed.
_PRINTED_TOC_MIN_BODY_WORDS = 3

# Publisher/copyright metadata paragraph patterns used by title-page
# stripping to determine whether the paragraphs between a leading title
# heading and the first real section are imprint noise rather than prose.
# These patterns are intentionally tight: each alternative must anchor to
# the start or end of the (short) paragraph line.
_PUBLISHER_METADATA_PARA_RE = re.compile(
    r"^(?:"
    r"by\s+[A-Z][^.]*$"  # "BY HENRY JAMES"
    r"|by\.?\s*$"  # standalone "BY" or "BY." (name on next line)
    r"|(?:translated|edited|illustrated|selected)"
    r"\s+(?:and\s+\w+\s+)?by\b"
    r"|(?:first\s+|originally\s+)?"
    r"(?:published|printed|issued|reprinted)\b"
    r"|copyright\b"
    r"|all\s+rights\s+reserved\b"
    r"|printed\s+in\b"
    r"|new\s+york\s*$"  # standalone city
    r"|london\s*$"
    r"|\d{4}[.,]?$"  # bare year "1921." or "1921"
    r"|[\w\s,.-]+\s+\d{4}\.?$"  # "London, 1921"
    r"|.{1,60}\b(?:&|and)\s+co\.?"
    r"(?:,\s*(?:ltd|inc|limited)\.?)?\s*$"
    r"|.{1,60}\bco\.?,?\s*(?:ltd|inc|limited)\.?\s*$"
    r"|.{1,60}\b(?:ltd|inc|limited|sons|press|"
    r"printers?|publishers?)\.?\s*$"
    r"|(?:volume|vol\.?|part)\s+[IVXLCDM0-9]+\.?\s*$"
    # Address line: "ST. MARTIN'S STREET, LONDON".  The trailing
    # comma + short city name + end-of-string acts as the practical
    # anchor (imprint lines end "STREET, LONDON", not mid-sentence).
    r"|[A-Z][\w'.-]*(?:\s+[\w'.-]+){0,4}\s*"
    r"(?:street|road|avenue|square|place|lane|"
    r"court|row|boulevard),\s*\S+\s*$"
    r")",
    re.IGNORECASE,
)

# Very short all-uppercase lines on a title page are likely author names
# or publisher fragments that fell on their own `<p>` element (e.g.
# "HENRY JAMES", "BOSTON").  Only matched in the publisher-metadata
# zone, NOT as a standalone classifier.
_SHORT_ALLCAPS_LINE_MAX_WORDS = 3

# Cap on paragraph count between a title heading and the first real
# section to qualify as a publisher-metadata-only title zone.  Keeps the
# check off substantive opening content like prologues or framing prose.
_MAX_TITLE_ZONE_METADATA_PARAS = 8

# Maximum word count per paragraph for the publisher-metadata check.
# Real prose paragraphs routinely exceed this; imprint lines are short.
_MAX_METADATA_PARA_WORDS = 12


# ---------------------------------------------------------------------------
# Section merging and deduplication
#
# These passes clean up the section list after initial parsing:
# - _merge_bare_heading_pairs: join "CHAPTER I" + "CHAPTER I THE TITLE"
# - _merge_adjacent_duplicate_sections: remove/merge consecutive duplicates
# - _strip_printed_toc_page_runs: filter inline printed-TOC page references
# - _drop_empty_interior_title_repeats: remove decorative ghost title repeats
# - _merge_chapter_subtitle_sections: fold subtitles into parent headings
# - _merge_chapter_description_paragraphs: fold ALL-CAPS descriptions
# ---------------------------------------------------------------------------


def _merge_bare_heading_pairs(sections: list[_Section]) -> list[_Section]:
    """Merge bare chapter-number headings with their immediately following subtitle.

    Detects the pattern ``<h3>CHAPTER I</h3><h5>WHO WILL BE THE NEW BISHOP?</h5>``
    (common in Project Gutenberg editions) and combines them into a single section
    with heading text ``"CHAPTER I WHO WILL BE THE NEW BISHOP?"``.

    Only merges when the subtitle is at a deeper level than the bare heading
    number — i.e., the subtitle is a child, not a sibling section.
    Separate TOC entries that happen to be adjacent at the same level (e.g.
    "CHAPTER 25" followed by a different work "LIGEIA") are never merged.
    """
    if len(sections) < 2:
        return sections

    merged: list[_Section] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if (
            i + 1 < len(sections)
            and _BARE_HEADING_NUMBER_RE.fullmatch(sec.heading_text)
            and not _broad_heading_with_enumerated_child(
                sec.heading_text,
                sections[i + 1].heading_text,
            )
            and _next_heading_is_subtitle(sections[i + 1].heading_text)
            and sections[i + 1].level > sec.level
        ):
            # Merge: keep anchor and level from the chapter-number heading,
            # combine heading text.
            next_sec = sections[i + 1]
            combined_text = f"{sec.heading_text} {next_sec.heading_text}"
            combined_level = _classify_level(combined_text, False)
            merged.append(
                _Section(
                    sec.anchor_id,
                    combined_text,
                    combined_level,
                    sec.body_anchor,
                    sec.heading_rank,
                )
            )
            i += 2
        else:
            merged.append(sec)
            i += 1
    return merged


def _merge_adjacent_duplicate_sections(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop immediately repeated section headings such as duplicate running headers.

    When three or more consecutive sections share the same text, level, and
    rank they are treated as deliberate structural repetition (e.g. four
    "LEGENDS OF THE PROVINCE HOUSE" headings each introducing a different
    story) and are all kept.  Runs of exactly two are collapsed to one —
    those are typically redundant heading tags in the source HTML.

    Guard: when *doc_index* is available and the positional gap between two
    same-text siblings is large enough to contain real content (> 8 document
    positions), the pair is kept — they are genuine distinct sections (e.g.
    two letters dated "July 28th." in an epistolary novel).

    Epigraph merge: when many same-text heading pairs (≥ 3) bracket short
    content (≤ _MAX_EPIGRAPH_WORDS words), the first heading of each pair is
    an epigraph wrapper and the pair is collapsed to keep only the second
    (body) heading.  This handles Emerson-style essays where each essay has
    an introductory poem bracketed by duplicate heading tags.
    """
    if len(sections) < 2:
        return sections

    tag_positions = doc_index.tag_positions

    # Pre-compute same-text run lengths so we can distinguish genuine
    # structural runs (≥3) from HTML-duplicate pairs (exactly 2).
    # NOTE: The threshold of 2 can false-positive on genuine 2-item
    # anthologies where both entries share the same series title.  This is
    # acceptable because true HTML duplicates (same heading emitted twice
    # by the source) are far more common than 2-item anthology runs.
    n = len(sections)
    run_length = [1] * n
    i = 0
    while i < n:
        j = i + 1
        while (
            j < n
            and sections[j].level == sections[i].level
            and sections[j].heading_rank == sections[i].heading_rank
            and _same_heading_text(sections[j].heading_text, sections[i].heading_text)
        ):
            j += 1
        length = j - i
        for k in range(i, j):
            run_length[k] = length
        i = j

    # Pass 1: identify epigraph pairs — same-text heading pairs (run of 2)
    # with a large-enough positional gap but short content between them.
    # The run_length invariant already guarantees that consecutive entries
    # with run_length == 2 share the same level, rank, and heading text.
    epigraph_pair_indices: set[int] = set()  # indices of the *first* section in a pair
    for idx in range(n - 1):
        if run_length[idx] != 2 or run_length[idx + 1] != 2:
            continue
        a, b = sections[idx], sections[idx + 1]
        a_pos = _tag_position(a.body_anchor, tag_positions)
        b_pos = _tag_position(b.body_anchor, tag_positions)
        if a_pos is None or b_pos is None or b_pos - a_pos <= 8:
            continue  # close gap — will be collapsed by the normal dedup
        between_words = sum(
            len(t.split())
            for t in _paragraphs_in_range(
                doc_index.paragraphs,
                doc_index.paragraph_positions,
                a_pos,
                b_pos,
            )
        )
        if between_words <= _MAX_EPIGRAPH_WORDS:
            epigraph_pair_indices.add(idx)

    # Only apply epigraph merge when the pattern is widespread (≥ 3 pairs),
    # preventing false positives on one-off date-heading duplicates in
    # epistolary novels.
    apply_epigraph_merge = len(epigraph_pair_indices) >= 3

    # Pass 2: build merged list.
    merged = [sections[0]]
    for idx, section in enumerate(sections[1:], start=1):
        previous = merged[-1]
        if (
            previous.level == section.level
            and previous.heading_rank == section.heading_rank
            and _same_heading_text(previous.heading_text, section.heading_text)
            and run_length[idx] <= 2
        ):
            prev_pos = _tag_position(previous.body_anchor, tag_positions)
            curr_pos = _tag_position(section.body_anchor, tag_positions)
            if prev_pos is not None and curr_pos is not None and curr_pos - prev_pos > 8:
                # Epigraph merge: replace the first heading with the second.
                if apply_epigraph_merge and (idx - 1) in epigraph_pair_indices:
                    merged[-1] = section
                    continue
                merged.append(section)
                continue
            # Small gap — normal HTML duplicate, drop the second.
            continue
        merged.append(section)
    return merged


def _strip_printed_toc_page_runs(sections: list[_Section]) -> list[_Section]:
    """Drop runs of non-keyword headings that end with a trailing page number.

    Some Gutenberg editions embed a printed table-of-contents page as
    heading tags whose text ends with an isolated page number (e.g.
    ``<h2>THE WILL TO BELIEVE 1</h2>``, ``<h2>THE SENTIMENT OF
    RATIONALITY 63</h2>``). These printed-TOC entries leak into the section
    list and duplicate the real essay headings that appear later.

    A run of at least :data:`_PRINTED_TOC_RUN_MIN` consecutive non-keyword
    headings with trailing page numbers is treated as a printed TOC and
    dropped.  Requiring ≥3 entries keeps the filter from touching isolated
    chapter titles that end in a number (e.g. a single ``ACT 2`` heading);
    requiring no structural keyword keeps it off runs like
    ``CHAPTER 1 / CHAPTER 2 / CHAPTER 3``.
    """
    if len(sections) < _PRINTED_TOC_RUN_MIN:
        return sections

    def _looks_printed_toc(section: _Section) -> bool:
        """Return True when section heading has a trailing page number."""
        if _heading_keyword(section.heading_text):
            return False
        if _TRAILING_PAGE_NUMBER_RE.search(section.heading_text) is None:
            return False
        # Require the text before the trailing number to have enough words
        # to look like an essay title; bare enumeration labels like
        # "Letter 1" / "Scene 2" must survive.
        body = _TRAILING_PAGE_NUMBER_RE.sub("", section.heading_text).strip()
        return len(body.split()) >= _PRINTED_TOC_MIN_BODY_WORDS

    drop = [False] * len(sections)
    i = 0
    while i < len(sections):
        if not _looks_printed_toc(sections[i]):
            i += 1
            continue
        j = i + 1
        while j < len(sections) and _looks_printed_toc(sections[j]):
            j += 1
        if j - i >= _PRINTED_TOC_RUN_MIN:
            for k in range(i, j):
                drop[k] = True
        i = j

    if not any(drop):
        return sections
    return [section for section, dropped in zip(sections, drop, strict=True) if not dropped]


def _drop_empty_interior_title_repeats(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop interior title-like headings that duplicate an earlier title and own no content.

    Some editions repeat the work title as a decorative heading *after* the
    front matter (e.g. between the preface and ``BOOK FIRST``, or nested as
    an empty sibling inside a book container).  Those ghost headings add
    empty TOC entries with no content beneath them.

    A repeat is dropped when:

    1. Its text matches an earlier kept section's text (case-insensitive,
       punctuation-insensitive).
    2. No body paragraphs appear between it and the next section — i.e. it
       owns no content of its own.
    3. It is title-like (no structural keyword and not front/back matter),
       so deliberate ``PART I`` / ``BOOK I`` containers are unaffected.

    Deliberate anthology repetitions like *Twice-Told Tales* keep their
    copies because each one introduces body paragraphs of the next tale.
    """
    if len(sections) < 2:
        return sections

    seen_keys: set[str] = set()
    keep = [True] * len(sections)
    for idx, section in enumerate(sections):
        key = _heading_key(section.heading_text)
        if not _is_title_like_heading(section.heading_text):
            seen_keys.add(key)
            continue
        if key not in seen_keys:
            seen_keys.add(key)
            continue
        if idx + 1 >= len(sections):
            continue
        if _has_paragraphs_between(section, sections[idx + 1], doc_index=doc_index):
            continue
        keep[idx] = False

    if all(keep):
        return sections
    return [section for section, kept in zip(sections, keep, strict=True) if kept]


def _merge_chapter_subtitle_sections(
    sections: list[_Section],
    *,
    toc_anchor_ids: frozenset[str] = frozenset(),
) -> list[_Section]:
    """Merge a chapter heading with an immediately following non-keyword subtitle.

    Handles the pattern where ``<h2>CHAPTER ONE</h2>`` is followed by
    ``<h3>INTRODUCTORY, CONCERNING THE PEDIGREE…</h3>`` — the subtitle should
    be part of the chapter title, not a separate sub-section.

    Also handles title-like headings (no keyword) followed by a deeper-rank
    title-like subtitle — e.g. ``<h2>KING PEST</h2>`` followed by
    ``<h3>A Tale Containing an Allegory.</h3>``.

    Merging is skipped when the subtitle has a structural keyword, or when the
    subtitle's anchor appears in *toc_anchor_ids* (it was a deliberate TOC
    entry and should remain a standalone section).

    Complements :func:`_merge_chapter_description_paragraphs`, which merges
    subtitles encoded as ``<p>`` elements rather than heading tags.
    """
    if len(sections) < 2:
        return sections

    merged: list[_Section] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if i + 1 < len(sections):
            nxt = sections[i + 1]
            keyword = _heading_keyword(sec.heading_text)
            shared_merge_conditions = (
                not _heading_keyword(nxt.heading_text)
                and nxt.heading_rank is not None
                and sec.heading_rank is not None
                and nxt.heading_rank == sec.heading_rank + 1
                and nxt.anchor_id not in toc_anchor_ids
            )
            # Merge keyword headings (CHAPTER, SCENE, etc.) — not BROAD.
            # Also merge title-like headings with their rank+1 subtitle.
            # For keyword headings, require nxt.level > sec.level to avoid
            # merging peers.  For title-like headings, level may have been
            # equalised by earlier passes, so rank alone is sufficient.
            # For title-like pairs, only merge when the subtitle has no
            # same-rank peers — if the next-next section is at the same rank,
            # we have sibling stories, not a title + subtitle.  Bare numbers
            # (Roman numerals, digits) on either side are never title + subtitle.
            nxt_is_lone_subtitle = False
            if not keyword:
                sec_stripped = sec.heading_text.strip()
                nxt_stripped = nxt.heading_text.strip()
                nxt_is_lone_subtitle = (
                    _is_title_like_heading(sec_stripped)
                    and not _PLAIN_NUMBER_HEADING_RE.fullmatch(sec_stripped)
                    and not _PLAIN_NUMBER_HEADING_RE.fullmatch(nxt_stripped)
                    and _next_heading_is_subtitle(nxt_stripped)
                    and not _DRAMATIC_CONTEXT_HEADING_RE.match(nxt_stripped)
                    and not _TERMINAL_MARKER_RE.fullmatch(nxt_stripped)
                    # Skip merge when nxt is the last section — a document-end
                    # heading is more likely a standalone closing entry (e.g.
                    # "THE END") than a subtitle of the previous section.
                    and i + 2 < len(sections)
                    and sections[i + 2].heading_rank != nxt.heading_rank
                )
            if shared_merge_conditions and (
                (keyword and keyword not in _BROAD_KEYWORDS and nxt.level > sec.level)
                or nxt_is_lone_subtitle
            ):
                combined = f"{sec.heading_text} {nxt.heading_text}"
                merged.append(
                    _Section(
                        sec.anchor_id,
                        combined,
                        sec.level,
                        sec.body_anchor,
                        sec.heading_rank,
                    )
                )
                i += 2
                continue
        merged.append(sec)
        i += 1
    return merged


def _merge_chapter_description_paragraphs(
    sections: list[_Section],
) -> tuple[list[_Section], set[int]]:
    """Merge ALL-CAPS description ``<p>`` elements into bare chapter headings.

    Returns the updated sections and a set of paragraph tag ``id()`` values
    to skip during chunk emission (safe because the same BeautifulSoup parse
    tree is alive for the entire ``chunk_html`` call).

    Handles the common Gutenberg pattern where a chapter heading like
    ``<h2>CHAPTER TWO</h2>`` is followed by a ``<p>WHEREIN CERTAIN
    PERSONS ARE PRESENTED TO THE READER…</p>`` that is really the
    chapter description, not body text.

    Complements :func:`_merge_chapter_subtitle_sections`, which merges
    subtitles encoded as *heading* elements (``<h3>``).  This function
    targets subtitles encoded as *paragraph* elements (``<p>``).
    """
    skip_paragraph_ids: set[int] = set()
    new_sections = list(sections)

    for idx, sec in enumerate(new_sections):
        keyword = _heading_keyword(sec.heading_text)
        if (
            not keyword
            or keyword in _BROAD_KEYWORDS
            or not _is_bare_keyword_heading(sec.heading_text, keyword)
        ):
            continue

        # Find the heading element and then the next <p> sibling.
        anchor = sec.body_anchor
        heading_el = _heading_element_or_anchor(anchor)
        next_p: Tag | None = None
        for sibling in heading_el.next_siblings:
            if isinstance(sibling, Tag):
                if sibling.name in _HEADING_TAGS:
                    break  # hit next heading, no description paragraph
                if sibling.name == "p":
                    next_p = sibling
                    break

        if next_p is None:
            continue

        ptext = " ".join(next_p.get_text().split()).strip()
        if not ptext or len(ptext) > _MAX_DESCRIPTION_PARAGRAPH_LEN:
            continue

        # Check if the text is ALL-CAPS (allow minor non-alpha chars).
        alpha_chars = [c for c in ptext if c.isalpha()]
        if not alpha_chars:
            continue
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        if upper_ratio < _MIN_UPPERCASE_RATIO:
            continue

        # Merge description into heading text.
        combined = f"{sec.heading_text} {ptext}"
        new_sections[idx] = _Section(
            sec.anchor_id,
            combined,
            sec.level,
            sec.body_anchor,
            sec.heading_rank,
        )
        skip_paragraph_ids.add(id(next_p))

    return new_sections, skip_paragraph_ids


def _paragraph_range_between(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> tuple[int, int] | None:
    """Return ``(lo, hi)`` paragraph-position indices between two sections.

    Returns ``None`` when either section's position is unknown.
    """
    pos_a = _tag_position(section_a.body_anchor, doc_index.tag_positions)
    pos_b = _tag_position(section_b.body_anchor, doc_index.tag_positions)
    if pos_a is None or pos_b is None:
        return None
    return (
        bisect_right(doc_index.paragraph_positions, pos_a),
        bisect_left(doc_index.paragraph_positions, pos_b),
    )


def _has_paragraphs_between(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True if any indexed paragraphs exist between two sections."""
    rng = _paragraph_range_between(section_a, section_b, doc_index=doc_index)
    if rng is None:
        return True  # assume content exists when positions are unknown
    return rng[0] < rng[1]


def _paragraphs_between_are_metadata_only(
    section_a: _Section,
    section_b: _Section,
    *,
    doc_index: _DocumentIndex,
) -> bool:
    """Return True when every paragraph between *a* and *b* is imprint metadata.

    Recognises byline, publisher, city/year, copyright, and "VOLUME N" lines
    that sit on a title page between the book title and the first structural
    section.  Requires the paragraph count to be small and each paragraph to
    be short enough that real prose cannot slip through.
    """
    rng = _paragraph_range_between(section_a, section_b, doc_index=doc_index)
    if rng is None:
        return False
    lo, hi = rng
    if lo >= hi:
        return False  # no paragraphs — caller handles the empty case
    if hi - lo > _MAX_TITLE_ZONE_METADATA_PARAS:
        return False
    for ip in doc_index.paragraphs[lo:hi]:
        text = " ".join(ip.text.split()).strip(" .,:;")
        if not text:
            continue
        words = text.split()
        if len(words) > _MAX_METADATA_PARA_WORDS:
            return False
        if _PUBLISHER_METADATA_PARA_RE.match(text) is not None:
            continue
        # Short all-caps lines (≤ 3 words) like "HENRY JAMES" or
        # "BOSTON" are author names / publisher city fragments that
        # landed on their own <p>.  A bare Roman numeral like "IV"
        # would also pass this check, but that's harmless: Roman-
        # numeral chapter headings are <h*> tags, not <p> elements,
        # so they never appear in the title-page paragraph zone.
        if (
            len(words) <= _SHORT_ALLCAPS_LINE_MAX_WORDS
            and any(c.isalpha() for c in text)
            and all(c.isupper() or not c.isalpha() for c in text)
        ):
            continue
        return False
    return True
