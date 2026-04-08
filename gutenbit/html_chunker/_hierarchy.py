"""Section hierarchy, nesting, and level normalisation passes.

Layer 3 — depends on ``_common``, ``_headings``, ``_scanning``, and ``_merging``.

Rank nesting: ``_respect_heading_rank_nesting``, ``_should_reset_keyword_peer``
Broad-keyword nesting: ``_nest_broad_subdivisions``, ``_nest_chapters_under_broad_containers``
Level normalisation: ``_normalize_collection_titles``, ``_promote_more_prominent_heading_runs``,
``_flatten_single_work_title_wrapper``, ``_equalize_orphan_level_gap``
Title-page stripping: ``_strip_leading_title_page_sections``, ``_is_title_page_candidate``,
``_collapse_degenerate_title_block``
Modal-rank detection: ``_broad_keywords_at_modal_rank``, ``_demote_same_rank_broad_keywords``
"""

from __future__ import annotations

from collections import Counter, defaultdict

from gutenbit.html_chunker._common import (
    _BROAD_KEYWORDS,
    _DRAMATIC_BROAD_KEYWORDS,
    _FALLBACK_START_HEADING_RE,
    _REFINEMENT_STOP_HEADING_RE,
    _STANDALONE_STRUCTURAL_RE,
    _Section,
)
from gutenbit.html_chunker._headings import (
    _PLAIN_NUMBER_HEADING_RE,
    _broad_nesting_depth,
    _heading_keyword,
    _is_front_matter_heading,
    _is_refinement_heading,
    _is_title_like_heading,
    _starts_with_enumerated_heading_prefix,
)
from gutenbit.html_chunker._merging import (
    _has_paragraphs_between,
    _paragraphs_between_are_metadata_only,
)
from gutenbit.html_chunker._scanning import (
    _DocumentIndex,
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Maximum number of sections at min_level before _equalize_orphan_level_gap
# treats them as the primary structure rather than orphan outliers.
_MAX_ORPHAN_LEVEL_COUNT = 2
# Minimum ratio of next-level sections to min-level sections required before
# _equalize_orphan_level_gap will demote the min-level outliers.
_MIN_MAJORITY_RATIO = 3

# Maximum number of title-like headings at one level for which a single
# container (one title with a broad-keyword child) triggers promotion.
# Collected editions rarely have more than ~10 work titles; chapter-rich
# books (e.g. PG 1998 Zarathustra with 82 discourse headings) must not
# be falsely promoted.
_MAX_TITLES_FOR_SINGLE_CONTAINER = 10


# ---------------------------------------------------------------------------
# Rank nesting
# ---------------------------------------------------------------------------


def _is_valid_rank_parent(
    parent: _Section,
    child: _Section,
    *,
    infer_from_rank: bool,
) -> bool:
    """Return True when *parent* can serve as a rank-based container for *child*.

    In the strict TOC path (*infer_from_rank* False), only refinement headings
    (those with a structural keyword) qualify as parents.

    In the relaxed heading-scan path (*infer_from_rank* True), non-keyword
    headings like ``OUR PARISH`` may parent when the rank gap is exactly 1,
    except front-matter headings which should never act as containers.
    """
    parent_is_refinement = _is_refinement_heading(parent.heading_text)
    if parent_is_refinement:
        # Broad-keyword children (PART, BOOK, …) should only nest under
        # parents that themselves carry a keyword — never under a bare
        # numeral like "VIII" that happens to sit at a shallower rank due
        # to inconsistent source HTML tags.
        child_kw = _heading_keyword(child.heading_text)
        if child_kw and child_kw in _BROAD_KEYWORDS:
            return _heading_keyword(parent.heading_text) is not None
        return True
    if not infer_from_rank:
        return False
    if child.heading_rank is None or parent.heading_rank is None:
        return False
    if child.heading_rank - parent.heading_rank != 1:
        return False
    return not _is_front_matter_heading(parent.heading_text)


def _should_skip_same_keyword_nesting(
    parent: _Section,
    child: _Section,
    *,
    infer_from_rank: bool,
) -> bool:
    """Return True when same-keyword parent/child should NOT nest.

    Sequential headings sharing a keyword (e.g. CHAPTER I, CHAPTER II) are
    siblings, not parent-child.  In heading-scan mode, same-keyword nesting
    is allowed when the child rank is strictly deeper and the keyword is not
    a broad container (e.g. ``h3 CHAPTER I → h4 CHAPTER I.``).
    """
    parent_kw = _heading_keyword(parent.heading_text)
    child_kw = _heading_keyword(child.heading_text)
    if not parent_kw or parent_kw != child_kw:
        return False
    if not infer_from_rank:
        return True
    # Broad keywords (BOOK, PART, …) are handled by the separate
    # _nest_chapters_under_broad_containers pass, so always skip here
    # to avoid double-nesting.
    if parent_kw in _BROAD_KEYWORDS:
        return True
    if parent.heading_rank is None or child.heading_rank is None:
        return True
    return child.heading_rank <= parent.heading_rank


def _should_reset_keyword_peer(
    sections: list[_Section],
    idx: int,
    parent_idx: int,
    new_levels: list[int],
    keyword_rank_seen: set[tuple[str, int]],
) -> bool:
    """Return True when a keyword-peer reset should skip nesting under *parent*.

    When a keyword child (e.g. CHAPTER XVIII at h5) finds a non-keyword parent
    (e.g. "THE ECCENTRIC PROJECTION" at h4) but the same keyword already
    appeared at the same rank earlier (CHAPTER XVII at h5), the child may be
    a sibling of that peer — not a child of the intervening subsection heading.

    Guard: only fire when the candidate parent is a subsection heading (deeper
    rank than the peer's container), not a new top-level section.
    """
    section = sections[idx]
    parent = sections[parent_idx]
    child_kw = _heading_keyword(section.heading_text)
    parent_kw = _heading_keyword(parent.heading_text)
    if not child_kw or parent_kw or (child_kw, section.heading_rank) not in keyword_rank_seen:
        return False

    latest_peer_idx = -1
    for i in range(idx - 1, -1, -1):
        if (
            _heading_keyword(sections[i].heading_text) == child_kw
            and sections[i].heading_rank == section.heading_rank
        ):
            latest_peer_idx = i
            break

    if latest_peer_idx < 0:
        return False

    if parent_idx > latest_peer_idx:
        # Parent sits between the peer and the child.  Check the container
        # rank of the peer: only reset when the parent is deeper.
        peer_container_rank: int | None = None
        peer_rank = sections[latest_peer_idx].heading_rank
        if peer_rank is not None:
            for i in range(latest_peer_idx - 1, -1, -1):
                r = sections[i].heading_rank
                if r is not None and r < peer_rank:
                    peer_container_rank = r
                    break
        return peer_container_rank is None or (
            parent.heading_rank is not None and parent.heading_rank > peer_container_rank
        )

    # The peer comes after the parent but is at the same or shallower
    # level — it already "reset" past the parent.
    return new_levels[latest_peer_idx] <= new_levels[parent_idx]


def _respect_heading_rank_nesting(
    sections: list[_Section],
    *,
    infer_from_rank: bool = False,
) -> list[_Section]:
    """Raise levels when heading ranks show a section was flattened too far.

    When *infer_from_rank* is True (heading-scan fallback only), non-keyword
    headings may serve as parents when the rank gap is exactly 1 (e.g. h2 "OUR
    PARISH" → h3 "CHAPTER I").  In the TOC path the hierarchy is already
    authoritative, so this relaxation is disabled by default.
    """
    if len(sections) < 2:
        return sections

    # Pre-index keyword occurrences by (keyword, heading_rank) for the
    # keyword-peer reset check below.
    keyword_rank_seen: set[tuple[str, int]] = set()
    for s in sections:
        kw = _heading_keyword(s.heading_text)
        if kw and s.heading_rank is not None:
            keyword_rank_seen.add((kw, s.heading_rank))

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        if section.heading_rank is None:
            continue

        parent_idx: int | None = None
        for prev_idx in range(idx - 1, -1, -1):
            previous = sections[prev_idx]
            if previous.heading_rank is None or previous.heading_rank >= section.heading_rank:
                continue
            if _is_valid_rank_parent(previous, section, infer_from_rank=infer_from_rank):
                parent_idx = prev_idx
                break

        if parent_idx is None:
            continue

        parent = sections[parent_idx]

        if _should_reset_keyword_peer(sections, idx, parent_idx, new_levels, keyword_rank_seen):
            continue

        # Use the (possibly updated) parent level so that multi-level
        # rank chains (e.g. h2 → h3 → h4) nest correctly.
        effective_parent_level = new_levels[parent_idx]
        if new_levels[idx] > effective_parent_level:
            continue

        if _should_skip_same_keyword_nesting(parent, section, infer_from_rank=infer_from_rank):
            continue

        new_levels[idx] = min(4, effective_parent_level + 1)
        changed = True

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _collapse_degenerate_title_block(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Collapse title-only heading runs with no content into the last section.

    When heading-scan finds only decorative title-page headings (no structural
    keywords, no front-matter start headings) and no body paragraphs appear
    before the final section, the headings are title fragments — not real
    structure.  Keep only the last section, which owns all content.
    """
    if len(sections) < 2:
        return sections
    if not all(
        _is_title_like_heading(s.heading_text)
        and not _FALLBACK_START_HEADING_RE.match(s.heading_text)
        for s in sections[:-1]
    ):
        return sections
    if _has_paragraphs_between(sections[0], sections[-1], doc_index=doc_index):
        return sections
    return [sections[-1]]


def _is_title_page_candidate(
    section: _Section,
    next_section: _Section | None,
    *,
    skip_children_guard: bool = False,
) -> bool:
    """Return True if *section* looks like a title-page heading to strip."""
    if _heading_keyword(section.heading_text):
        return False
    if _FALLBACK_START_HEADING_RE.match(section.heading_text):
        return False
    if not _is_title_like_heading(section.heading_text):
        return False
    # Bare enumeration labels (e.g. "I", "II", "1") and enumerated titles
    # (e.g. "I. FROM MISS AURORA CHURCH...") are chapter entries, not
    # title-page candidates.
    if _PLAIN_NUMBER_HEADING_RE.fullmatch(section.heading_text):
        return False
    if _starts_with_enumerated_heading_prefix(section.heading_text.strip()):
        return False
    if section.anchor_id:
        return False
    # Title-like sections with children at a deeper level are structural
    # containers (e.g. "OUR PARISH" nesting chapters), not title pages.
    return skip_children_guard or next_section is None or next_section.level <= section.level


def _strip_leading_title_page_sections(
    sections: list[_Section],
    *,
    doc_index: _DocumentIndex,
) -> list[_Section]:
    """Drop title-like sections at the start that precede all real structure.

    A leading prefix of sections where each is ``_is_title_like_heading`` and
    has no structural keyword or front-matter keyword is a title-page cluster.
    Drop it when no content paragraphs exist between the cluster and the first
    structural section (prevents false positives on legitimate title headings
    with body text like *The Metamorphosis*).

    Publisher-metadata bypass: when the paragraphs between the leading cluster
    and the first real section are ALL short imprint lines (byline, publisher
    name, city/year, copyright), treat the zone as empty and strip the cluster.
    The children-depth guard is also bypassed in this case, since real chapter
    containers never have only imprint metadata between their title and first
    child.
    """
    if len(sections) < 2:
        return sections

    def _first_non_candidate(*, skip_children_guard: bool = False) -> int | None:
        """Return index of the first section that is not a title-page candidate."""
        return next(
            (
                idx
                for idx, section in enumerate(sections)
                if not _is_title_page_candidate(
                    section,
                    sections[idx + 1] if idx + 1 < len(sections) else None,
                    skip_children_guard=skip_children_guard,
                )
            ),
            None,
        )

    # First pass: strict guards (no children, no paragraphs).
    first_real = _first_non_candidate()
    if (
        first_real is not None
        and first_real > 0
        and not _has_paragraphs_between(sections[0], sections[first_real], doc_index=doc_index)
    ):
        return sections[first_real:]

    # Second pass: publisher-metadata bypass.
    first_real_relaxed = _first_non_candidate(skip_children_guard=True)
    if (
        first_real_relaxed is not None
        and first_real_relaxed > 0
        and _paragraphs_between_are_metadata_only(
            sections[0], sections[first_real_relaxed], doc_index=doc_index
        )
    ):
        return sections[first_real_relaxed:]

    return sections


def _normalize_collection_titles(sections: list[_Section]) -> list[_Section]:
    """Promote repeated title rows that act as anthology/work containers."""
    if len(sections) < 3:
        return sections

    def _is_collection_title(section: _Section) -> bool:
        """Return True for title-like headings at rank h1–h2 (collection wrappers)."""
        if _is_front_matter_heading(section.heading_text):
            return False
        return _is_title_like_heading(section.heading_text) and (
            section.heading_rank is None or section.heading_rank <= 2
        )

    title_indices_by_level: dict[int, list[int]] = defaultdict(list)
    container_title_indices_by_level: dict[int, list[int]] = defaultdict(list)

    # Cache _is_collection_title per section index to avoid repeated
    # calls through _is_title_like_heading → _is_non_structural_heading_text
    # (12+ regex operations each).
    _ct_cache: dict[int, bool] = {}

    def _ct(idx: int) -> bool:
        """Cached wrapper around ``_is_collection_title`` by section index."""
        if idx not in _ct_cache:
            _ct_cache[idx] = _is_collection_title(sections[idx])
        return _ct_cache[idx]

    def _has_same_level_collection_title_since_lower_level(title_idx: int, *, level: int) -> bool:
        """Return True if a same-level collection title exists before the next lower level."""
        for previous_idx in range(title_idx - 1, -1, -1):
            previous_section = sections[previous_idx]
            if previous_section.level < level:
                return False
            if previous_section.level == level and _ct(previous_idx):
                return True
        return False

    for idx, section in enumerate(sections):
        if not _ct(idx):
            continue
        title_indices_by_level[section.level].append(idx)

        for next_idx in range(idx + 1, len(sections)):
            next_section = sections[next_idx]
            if _ct(next_idx) and next_section.level == section.level:
                break
            next_depth = _broad_nesting_depth(next_section.heading_text)
            if next_depth is None:
                continue
            if _heading_keyword(next_section.heading_text) in _BROAD_KEYWORDS:
                # Guard: when the broad keyword immediately follows this
                # title (no non-collection sections between) and another
                # collection title at the same level preceded us, skip.
                # This prevents poetry collections (PG 1322 Leaves of
                # Grass) from being falsely promoted.  When intermediate
                # non-collection sections exist (e.g. front-matter like
                # Dedication/Preface in PG 29363), the guard is bypassed
                # so the work title can still be detected as a container.
                non_collection_between = sum(1 for j in range(idx + 1, next_idx) if not _ct(j))
                if (
                    non_collection_between == 0
                    and _has_same_level_collection_title_since_lower_level(
                        idx, level=section.level
                    )
                ):
                    break
                container_title_indices_by_level[section.level].append(idx)
                break

    promoted_levels = {
        level
        for level, container_indices in container_title_indices_by_level.items()
        if len(title_indices_by_level[level]) >= 3
        and (
            len(container_indices) >= 2
            or (
                len(container_indices) >= 1
                and len(title_indices_by_level[level]) <= _MAX_TITLES_FOR_SINGLE_CONTAINER
            )
        )
    }
    if not promoted_levels:
        return sections

    new_levels = [section.level for section in sections]
    for level in sorted(promoted_levels):
        title_indices = title_indices_by_level[level]
        for idx in title_indices:
            new_levels[idx] = max(1, sections[idx].level - 1)
        for index_pos, idx in enumerate(title_indices):
            next_idx = (
                title_indices[index_pos + 1]
                if index_pos + 1 < len(title_indices)
                else len(sections)
            )
            for section_idx in range(idx + 1, next_idx):
                new_levels[section_idx] += 1

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _broad_keywords_at_modal_rank(
    sections: list[_Section],
) -> frozenset[str]:
    """Return broad keywords whose modal heading rank matches the overall mode.

    Only fires for heading-scan sections (no TOC anchors), exactly one
    broad keyword type present, and ≥ 80% of sections sharing the same
    rank — i.e. the HTML used a single tag for everything and the
    keyword-based level-1 assignment is misleading.
    """
    # TOC-driven sections have anchor_ids → hierarchy is authoritative.
    if any(s.anchor_id for s in sections):
        return frozenset()

    all_ranks: Counter[int] = Counter()
    broad_kw_ranks: dict[str, Counter[int]] = {}
    for section in sections:
        if section.heading_rank is None:
            continue
        all_ranks[section.heading_rank] += 1
        kw = _heading_keyword(section.heading_text)
        if kw and kw in _BROAD_KEYWORDS and kw not in _DRAMATIC_BROAD_KEYWORDS:
            broad_kw_ranks.setdefault(kw, Counter())[section.heading_rank] += 1

    if not all_ranks or len(broad_kw_ranks) != 1:
        return frozenset()

    overall_mode = all_ranks.most_common(1)[0][0]
    mode_count = all_ranks[overall_mode]
    total_ranked = sum(all_ranks.values())
    if mode_count < total_ranked * 0.8:
        return frozenset()

    return frozenset(
        kw for kw, ranks in broad_kw_ranks.items() if ranks.most_common(1)[0][0] == overall_mode
    )


def _demote_same_rank_broad_keywords(
    sections: list[_Section],
    *,
    demote_keywords: frozenset[str],
) -> list[_Section]:
    """Demote broad keywords to chapter level when they share the modal rank.

    *demote_keywords* is the output of :func:`_broad_keywords_at_modal_rank`,
    computed once in the pipeline and reused here to avoid a redundant
    iteration over all sections.
    """
    if len(sections) < 3 or not demote_keywords:
        return sections

    new_sections = []
    changed = False
    for section in sections:
        kw = _heading_keyword(section.heading_text)
        if kw and kw in demote_keywords and section.level == 1:
            new_sections.append(section._with_level(2))
            changed = True
        else:
            new_sections.append(section)

    return new_sections if changed else sections


def _nest_broad_subdivisions(sections: list[_Section]) -> list[_Section]:
    """Nest same-rank broad headings when their keywords imply containment.

    Some Gutenberg editions use the same heading rank for containers like
    ``PART`` and their child ``Book`` headings. Preserve that hierarchy by
    shifting the inner broad run and its descendants one level deeper.
    """
    if len(sections) < 3:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        outer_depth = _broad_nesting_depth(section.heading_text)
        if outer_depth is None:
            continue

        outer_level = new_levels[idx]
        found_nested_broad = False

        for inner_idx in range(idx + 1, len(sections)):
            current_level = new_levels[inner_idx]
            if current_level < outer_level:
                break

            current_depth = _broad_nesting_depth(sections[inner_idx].heading_text)
            if current_level == outer_level:
                if current_depth is None or current_depth <= outer_depth:
                    break
                found_nested_broad = True

            if not found_nested_broad:
                continue

            if current_level == outer_level or current_level > outer_level:
                shifted_level = min(4, current_level + 1)
                if shifted_level != current_level:
                    new_levels[inner_idx] = shifted_level
                    changed = True

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _nest_chapters_under_broad_containers(
    sections: list[_Section],
    *,
    skip_keywords: frozenset[str] = frozenset(),
) -> list[_Section]:
    """Nest chapter-level sections under broad keyword containers at the same level.

    When BOOK and CHAPTER share the same heading rank (both ``<h3>``), the
    broad keyword (BOOK) is a structural container and the chapter keyword
    should sit one level deeper.  This handles works like Les Misérables
    where VOLUME > BOOK > CHAPTER all appear in the TOC.
    """
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False

    for idx, section in enumerate(sections):
        keyword = _heading_keyword(section.heading_text)
        if keyword not in _BROAD_KEYWORDS:
            continue
        # Front-matter headings like "PREFACE TO THE FIRST VOLUME" match a
        # broad keyword but are not structural containers.
        if _is_front_matter_heading(section.heading_text):
            continue
        # Skip keywords that were demoted by _demote_same_rank_broad_keywords.
        if keyword in skip_keywords:
            continue

        broad_level = new_levels[idx]

        for inner_idx in range(idx + 1, len(sections)):
            inner_level = new_levels[inner_idx]
            if inner_level < broad_level:
                break
            if inner_level == broad_level:
                inner_kw = _heading_keyword(sections[inner_idx].heading_text)
                if inner_kw and inner_kw in _BROAD_KEYWORDS:
                    break  # next broad container at same level — stop
                # Standalone structural headings and apparatus closures are
                # top-level peers, not children of the preceding broad keyword.
                inner_text = sections[inner_idx].heading_text
                if _STANDALONE_STRUCTURAL_RE.search(inner_text):
                    break
                if _REFINEMENT_STOP_HEADING_RE.match(inner_text):
                    break
                shifted = min(4, inner_level + 1)
                if shifted != inner_level:
                    new_levels[inner_idx] = shifted
                    changed = True

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _promote_more_prominent_heading_runs(sections: list[_Section]) -> list[_Section]:
    """Promote runs whose heading rank outranks their assigned parent.

    Some books place opening matter like ``Proem`` in ``h2`` and then start the
    real top-level work structure in ``h1``. If the later run is currently
    nested under that lower-rank opener, lift the run until its first section
    becomes a sibling of the false parent.
    """
    if len(sections) < 2:
        return sections

    new_levels = [section.level for section in sections]
    changed = False
    idx = 0

    while idx < len(sections):
        current_level = new_levels[idx]
        current_rank = sections[idx].heading_rank
        if current_level <= 1 or current_rank is None:
            idx += 1
            continue

        parent_idx: int | None = None
        for previous_idx in range(idx - 1, -1, -1):
            if new_levels[previous_idx] < current_level:
                parent_idx = previous_idx
                break

        if parent_idx is None:
            idx += 1
            continue

        parent_rank = sections[parent_idx].heading_rank
        parent_level = new_levels[parent_idx]
        if parent_rank is None or current_rank >= parent_rank:
            idx += 1
            continue

        shift = current_level - parent_level
        run_end = idx
        while run_end < len(sections) and new_levels[run_end] > parent_level:
            promoted_level = max(1, new_levels[run_end] - shift)
            if promoted_level != new_levels[run_end]:
                new_levels[run_end] = promoted_level
                changed = True
            run_end += 1

        idx = run_end

    if not changed:
        return sections

    return [section._with_level(new_levels[idx]) for idx, section in enumerate(sections)]


def _flatten_single_work_title_wrapper(sections: list[_Section]) -> list[_Section]:
    """Flatten title-like headings that wrap structural children.

    When a non-keyword heading (e.g. "Metamorphosis", "THE PRINCE") sits one
    level above enumerated chapters, it is a work title — not a structural
    container.  Promote its children so chapters become peers at min_level.

    Guard: ≥ 2 title-like wrappers at *min_level* → anthology (Shakespeare) → skip.
    Guard: when the wrapper has title-like peers at *min_level* AND its
    direct children are all bare enumeration labels (``I``, ``II``, ``III``),
    the wrapper is one essay among many in a collection and its bare-numeral
    children are sub-sections — skip flattening to preserve that nesting.
    """
    if len(sections) < 2:
        return sections

    min_level = min(s.level for s in sections)

    # Precompute direct children at min_level + 1 for each min_level
    # section in a single forward pass.  Used both for the peer-essay
    # check and for the wrapper-identification loop below.
    children_of: dict[int, list[_Section]] = {}
    for idx in range(len(sections)):
        if sections[idx].level != min_level:
            continue
        children: list[_Section] = []
        for next_idx in range(idx + 1, len(sections)):
            if sections[next_idx].level <= min_level:
                break
            if sections[next_idx].level == min_level + 1:
                children.append(sections[next_idx])
        if children:
            children_of[idx] = children

    has_peer_essay = any(
        s.level == min_level
        and _is_title_like_heading(s.heading_text)
        and not _is_front_matter_heading(s.heading_text)
        and not _starts_with_enumerated_heading_prefix(s.heading_text.strip())
        and idx not in children_of
        for idx, s in enumerate(sections)
    )

    # Identify title-like sections at min_level that have children one level deeper.
    wrapper_indices: list[int] = []
    for idx, section in enumerate(sections):
        if section.level != min_level:
            continue
        if not _is_title_like_heading(section.heading_text):
            continue
        # Indexed headings (e.g. "I. A SCANDAL IN BOHEMIA") are sections
        # within a larger work, not work titles — don't flatten them.
        if _starts_with_enumerated_heading_prefix(section.heading_text.strip()):
            continue
        direct_children = children_of.get(idx)
        if not direct_children:
            continue
        # Essay-collection bare-numeral sub-section guard.
        if has_peer_essay and all(
            _PLAIN_NUMBER_HEADING_RE.fullmatch(child.heading_text) is not None
            for child in direct_children
        ):
            return sections
        wrapper_indices.append(idx)
        if len(wrapper_indices) >= 2:
            return sections

    if not wrapper_indices:
        return sections

    # Flatten: shift every descendant of the single wrapper up by 1.
    new_levels = [s.level for s in sections]

    for wrapper_idx in wrapper_indices:
        # Find the span of children (up to the next min_level section).
        span_end = len(sections)
        for next_idx in range(wrapper_idx + 1, len(sections)):
            if sections[next_idx].level <= min_level:
                span_end = next_idx
                break

        for i in range(wrapper_idx + 1, span_end):
            new_levels[i] = max(1, new_levels[i] - 1)

    return [s._with_level(new_levels[i]) for i, s in enumerate(sections)]


def _equalize_orphan_level_gap(sections: list[_Section]) -> list[_Section]:
    """Demote orphan min-level sections when the vast majority sit one level deeper.

    In PG 946 (Lady Susan) the TOC puts CONCLUSION at level 1 while the 41
    Roman-numeral letters land at level 2, producing empty div1 slots.  When
    only a tiny minority (≤ 2) of non-keyword sections occupy min_level and the
    next level has ≥ 3× as many sections, flatten the outliers down.
    """
    if len(sections) < 3:
        return sections

    min_level = min(s.level for s in sections)
    at_min = [i for i, s in enumerate(sections) if s.level == min_level]
    at_next = [i for i, s in enumerate(sections) if s.level == min_level + 1]

    if len(at_min) > _MAX_ORPHAN_LEVEL_COUNT or len(at_next) < len(at_min) * _MIN_MAJORITY_RATIO:
        return sections

    # Don't demote structural containers (BOOK, PART, etc.).
    if any(_heading_keyword(sections[i].heading_text) in _BROAD_KEYWORDS for i in at_min):
        return sections

    # Don't demote sections that have children at the next level — they are
    # legitimate wrappers (e.g. a collection title), not orphans.
    for i in at_min:
        for j in range(i + 1, len(sections)):
            if sections[j].level <= min_level:
                break
            if sections[j].level == min_level + 1:
                return sections

    new_levels = [s.level for s in sections]
    for i in at_min:
        new_levels[i] = min_level + 1

    return [s._with_level(new_levels[i]) for i, s in enumerate(sections)]
