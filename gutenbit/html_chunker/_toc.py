"""TOC link classification and matching helpers."""

from __future__ import annotations

from bisect import bisect_left

from bs4 import Tag

from gutenbit.html_chunker._common import (
    _FRONT_MATTER_HEADINGS,
    _NON_ALNUM_RE,
    _NUMERIC_LINK_TEXT_RE,
    _ROMAN_NUMERAL_RE,
    _clean_heading_text,
    _front_matter_heading_key,
)
from gutenbit.html_chunker._headings import (
    _heading_key,
    _is_non_structural_heading_text,
    _same_heading_text,
)
from gutenbit.html_chunker._scanning import (
    _container_residue_without_link_text,
    _DocumentIndex,
    _is_dense_chapter_index_paragraph,
    _is_toc_paragraph,
)


def _is_toc_context_link(link: Tag) -> bool:
    """Return True when *link* sits in a TOC-like container."""
    if link.find_parent("tr") is not None:
        return True

    for name in ("p", "li", "div"):
        container = link.find_parent(name)
        if container is None:
            continue
        if container.name == "p" and _is_toc_paragraph(container):
            return True

        classes = {str(c).lower() for c in (container.get("class") or [])}
        if "toc" in classes or "contents" in classes:
            return True

        residue = _container_residue_without_link_text(container)
        if _NON_ALNUM_RE.sub("", residue) == "":
            return True
    return False


def _toc_context_text(link: Tag) -> str:
    """Return nearby non-link TOC text for a link, if any."""
    for name in ("tr", "p", "li", "div"):
        container = link.find_parent(name)
        if container is None:
            continue
        text = _clean_heading_text(_container_residue_without_link_text(container))
        if text:
            return text
    return ""


def _looks_enumerated_toc_entry(text: str) -> bool:
    """Return True for entries like ``I. Title`` or ``12. Title``."""
    if not text:
        return False
    first_token = text.split(maxsplit=1)[0].rstrip(".)")
    return _ROMAN_NUMERAL_RE.fullmatch(first_token) is not None or first_token.isdigit()


def _previous_heading_text(link: Tag, *, doc_index: _DocumentIndex) -> str:
    """Return the nearest preceding heading text, if any.

    Uses the precomputed heading index with bisect for O(log n) lookup.
    """
    link_pos = doc_index.tag_positions.get(id(link))
    if link_pos is not None and doc_index.heading_positions:
        idx = (
            bisect_left(doc_index.heading_positions, link_pos) - 1
        )  # last heading strictly before link_pos
        if idx >= 0:
            return doc_index.headings[idx].text
    return ""


def _is_structural_toc_link(link: Tag, link_text: str, *, doc_index: _DocumentIndex) -> bool:
    """Return True for TOC links that can map to actual section headings."""
    if not _is_toc_context_link(link):
        return False

    previous_heading = _front_matter_heading_key(_previous_heading_text(link, doc_index=doc_index))
    if previous_heading in _FRONT_MATTER_HEADINGS and previous_heading not in {
        "contents",
        "table of contents",
    }:
        return False

    link_classes = {str(cls).lower() for cls in (link.get("class") or [])}
    if "citation" in link_classes:
        return False

    paragraph = link.find_parent("p")
    if paragraph is not None and _is_dense_chapter_index_paragraph(paragraph):
        cleaned_link_text = _clean_heading_text(" ".join(link.get_text().split()))
        chapter_marker = cleaned_link_text.rstrip(".,;:)")
        if cleaned_link_text.lower().startswith("chapter") or _ROMAN_NUMERAL_RE.fullmatch(
            chapter_marker
        ):
            return False

    href = str(link.get("href", ""))
    if href.startswith("#"):
        target_id = href[1:].lower()
        if target_id.startswith(("footnote", "citation")):
            return False

    if link.find_parent("span", class_="pagenum"):
        return False

    if not link_text:
        return False
    if _NUMERIC_LINK_TEXT_RE.fullmatch(link_text):
        return False
    # Filter front-matter headings (CONTENTS, ILLUSTRATIONS, etc.)
    if _is_non_structural_heading_text(link_text):
        return False
    # Filter bare roman numerals (I, II, III — sub-section markers, not chapters)
    return not _ROMAN_NUMERAL_RE.fullmatch(link_text)


def _toc_entry_matches_heading(entry_text: str, heading_text: str) -> bool:
    """Return True when a TOC entry label clearly aligns with a heading."""
    if not entry_text:
        return False
    if _same_heading_text(entry_text, heading_text):
        return True

    entry_key = _heading_key(entry_text)
    heading_key_val = _heading_key(heading_text)
    if len(entry_key) < 6:
        return False
    return entry_key in heading_key_val or heading_key_val in entry_key
