"""TOC link classification and matching helpers."""

from __future__ import annotations

from bisect import bisect_left
from weakref import WeakSet

from bs4 import Tag

from gutenbit.html_chunker._common import (
    _FRONT_MATTER_HEADINGS,
    _HEADING_TAG_SET,
    _HEADING_TAGS,
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

# Cache for paragraphs already checked by the multi-link TOC heuristic.
# Using WeakSet so entries are garbage-collected when the soup is freed.
_multi_link_toc_paragraphs: WeakSet[Tag] = WeakSet()
_multi_link_toc_non_paragraphs: WeakSet[Tag] = WeakSet()


_toc_context_cache: dict[int, bool] = {}


def _is_toc_context_link(link: Tag) -> bool:
    """Return True when *link* sits in a TOC-like container."""
    # Table-based TOC — always True, no paragraph caching needed.
    if link.find_parent("tr") is not None:
        return True

    paragraph = link.find_parent("p")
    if paragraph is not None:
        key = id(paragraph)
        cached = _toc_context_cache.get(key)
        if cached is not None:
            return cached

    def _cache_result(value: bool) -> bool:
        if paragraph is not None:
            _toc_context_cache[id(paragraph)] = value
        return value

    for name in ("p", "li", "div"):
        container = paragraph if name == "p" else link.find_parent(name)
        if container is None:
            continue
        if container.name == "p" and _is_toc_paragraph(container):
            return _cache_result(True)

        classes = {str(c).lower() for c in (container.get("class") or [])}
        if "toc" in classes or "contents" in classes:
            return _cache_result(True)

        residue = _container_residue_without_link_text(container)
        if _NON_ALNUM_RE.sub("", residue) == "":
            return _cache_result(True)

    # Check ancestor list/nav elements for TOC class (e.g. <ul class="toc">
    # wrapping <li> entries where individual items have non-empty residue
    # such as a "PAGE" column header).
    for name in ("ul", "ol", "nav"):
        ancestor = link.find_parent(name)
        if ancestor is None:
            continue
        classes = {str(c).lower() for c in (ancestor.get("class") or [])}
        if "toc" in classes or "contents" in classes:
            return _cache_result(True)

    # Multi-link paragraphs immediately following a "CONTENTS" heading
    # are TOC blocks even when the residue is non-empty (e.g., discourse
    # titles alongside Roman-numeral links).  Results are cached in
    # module-level WeakSets to avoid O(n^2) find_all calls when every
    # link in the same paragraph hits this path.
    if paragraph is not None:
        if paragraph in _multi_link_toc_paragraphs:
            return _cache_result(True)
        if paragraph not in _multi_link_toc_non_paragraphs:
            is_toc = False
            # limit=20: we only care whether there are at least 20 links,
            # so stop searching after that threshold is reached.
            links = paragraph.find_all("a", class_="pginternal", limit=20)
            # 20+ internal links in a single paragraph is far above normal
            # prose density — only dense TOC blocks reach this threshold.
            if len(links) >= 20:
                prev = paragraph.find_previous_sibling()
                if prev is not None:
                    heading_el = (
                        prev if prev.name in _HEADING_TAG_SET else prev.find(_HEADING_TAGS)
                    )
                    if heading_el is not None:
                        prev_text = _front_matter_heading_key(heading_el.get_text())
                        if prev_text in _FRONT_MATTER_HEADINGS:
                            is_toc = True
            if is_toc:
                _multi_link_toc_paragraphs.add(paragraph)
                return _cache_result(True)
            _multi_link_toc_non_paragraphs.add(paragraph)
    return _cache_result(False)


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
    return not _is_non_structural_heading_text(link_text)


def _toc_entry_matches_heading(entry_text: str, heading_text: str) -> bool:
    """Return True when a TOC entry label clearly aligns with a heading."""
    if not entry_text:
        return False
    if _same_heading_text(entry_text, heading_text):
        return True

    entry_key = _heading_key(entry_text)
    heading_key_val = _heading_key(heading_text)
    if len(entry_key) < 6:
        # Short TOC entries (e.g. "I.", "IV.") still match when they are
        # a prefix of the heading text, which means the TOC abbreviates
        # the full heading (e.g. "I. THE THREE METAMORPHOSES.").
        # Use the original text (not stripped key) to avoid false positives
        # like "I." matching "In Chancery" via key prefix.
        entry_stripped = entry_text.rstrip("., ")
        heading_stripped = heading_text.lstrip()
        if entry_stripped and heading_stripped.startswith(entry_stripped):
            # Ensure the match ends at a word boundary (space, period, or end).
            after = heading_stripped[len(entry_stripped) : len(entry_stripped) + 1]
            if not after or after in " .,;:":
                return True
        return False
    return entry_key in heading_key_val or heading_key_val in entry_key
