# gutenbit CLI Battle Test Error Report

Date: March 6, 2026 (America/New_York)
Repo: `/Users/keinan/Code/gutenbit`
Tester: Codex (GPT-5)
Requested execution style: `uv run gutenbit`

## 1. Scope and Method

This report summarizes a live, end-to-end CLI battle test focused on:
- Ease of use
- Interpretability
- Clarity and consistency
- Ergonomics for both humans and agents
- Perfect-path and edge-case behavior

### Corpus under test (major Dickens novels)

Ingested and tested by PG ID:
- `98` A Tale of Two Cities
- `564` The Mystery of Edwin Drood
- `580` The Pickwick Papers
- `700` The Old Curiosity Shop
- `730` Oliver Twist
- `766` David Copperfield
- `786` Hard Times
- `821` Dombey and Son
- `883` Our Mutual Friend
- `917` Barnaby Rudge
- `963` Little Dorrit
- `967` Nicholas Nickleby
- `968` Martin Chuzzlewit
- `1023` Bleak House
- `1400` Great Expectations

Database used for primary corpus test:
- `/tmp/gutenbit_dickens_major.db`

### Commands exercised

- Help: top-level + all subcommands
- `catalog`, `ingest`, `books`, `view`, `search`, `delete`
- `view` modes: default, `--json`, `--all`, `--position`, `--section`, `--full`, `--kind`, `--limit`, `--around`
- `search` modes: ranked, first, last, phrase, kind filter, author/title/book filters
- Error-path tests: invalid selector combos, malformed FTS query, invalid limits, missing records, empty DB

### Automated validation completed

- Full per-book smoke matrix (`view` summary + section + position + scoped search) across all 15 novels: **15/15 pass**
- Network CLI test suite subset: `18 passed`

---

## 2. Executive Summary

The CLI is broadly functional and well-structured. Core workflows (ingest/list/view/search/delete) are stable across all major Dickens novels.

However, several high-value issues and ergonomics debts were identified:
- ~~One **high-severity structural correctness issue** in chunking/section progression on `Hard Times`.~~
  - Resolved on March 7, 2026 (delimiter-bounded parsing + document-order TOC section sorting + stale-ingest auto-refresh).
- ~~Several **medium-severity usability/agentic** issues around section matching strictness, option validation consistency, and mode semantics clarity.~~
  - Resolved on March 7, 2026 (`--section` normalization improvements + strict `--preview-chars` validation + explicit `search --mode` semantics in help/docs).
- Some **performance/documentation debt** around catalog fetch behavior and dedupe expectations.

---

## 3. Findings (Prioritized)

## ~~F-001: Structural sectioning anomaly and end-matter bleed on `Hard Times` (PG 786)~~

Status: **Resolved (March 7, 2026)**  
Resolution summary:
- Parsing is now bounded by Gutenberg START/END delimiters.
- TOC-derived sections are sorted by body document order (fixes `CHAPTER IV`/`CHAPTER V` mis-order).
- Non-structural TOC links (citation/footnote/page-number) are filtered out.
- Fallback heading-scan parsing restores missing `CHAPTER I` sections in `BOOK THE FIRST/SECOND/THIRD` for PG 786.
- Spurious position-0 heading (`Hard Times and Reprinted Pieces [0`) no longer appears as a section.
- Legacy chunk-kind complexity was simplified to `heading`/`paragraph`.
- Existing stale ingests are now auto-reprocessed via chunker-version tracking.

Severity: **High**  
Type: Functional correctness / data quality  
Area: HTML chunking and section traversal

### Impact

- Section/chapter order appears inconsistent (e.g., `CHAPTER V` before `CHAPTER IV` within `BOOK THE SECOND` in output ordering).
- `end_matter` appears to begin mid-flow and then normal chapter flow resumes, which causes potential mislabeling and confusing navigation/search context.
- This affects trust in structural metadata, which is central to `view --section` and semantic retrieval.

### Evidence

Observed chunk-kind totals for `786`:
- `front_matter: 87`
- `heading: 34`
- `paragraph: 3714`
- `end_matter: 1988`

Observed boundary behavior near transition:
- `position=2312` is end-matter note
- `position=2313` resumes with heading `CHAPTER IV`

### Reproduction

```bash
DB=/tmp/gutenbit_dickens_major.db
uv run gutenbit --db "$DB" view 786
uv run gutenbit --db "$DB" view 786 --section "BOOK THE SECOND" --kind heading --full
uv run gutenbit --db "$DB" view 786 --position 2312 --around 3 --full
```

### Expected

- Stable heading progression consistent with reading order within each book section.
- End matter starts only when genuinely entering terminal back matter, and does not interleave with later body chapters.

### Actual

- Chapter progression appears non-sequential in rendered section listing.
- End matter gets marked, then body-like chapter content continues afterward.

### Likely root causes

- TOC links are iterated in source order without explicit body-anchor sort by document position.
- End-matter flag is sticky per section once regex triggers, potentially too coarse for noisy/annotated PG HTML.

Code references:
- Section parse/build path: [/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:142](/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:142)
- Section iteration and paragraph collection: [/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:112](/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:112)
- End-matter trigger and sticky flag: [/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:126](/Users/keinan/Code/gutenbit/gutenbit/html_chunker.py:126)

### Recommendation

- Sort parsed sections by body-document position before chunking.
- Replace one-way per-section `in_end_matter` toggling with stronger boundary detection (anchor/heading-aware end-matter segmentation).
- Add regression tests specifically for PG 786 ordering and boundary transitions.

### Acceptance criteria

- `view 786 --section "BOOK THE SECOND" --kind heading --full` yields monotonic chapter order.
- End-matter chunks appear only in terminal sections (or clearly isolated sections), not interleaved before later chapter headings.

---

## ~~F-002: `view --section` matching is too strict (case and punctuation-spacing fragility)~~

Status: **Resolved (March 7, 2026)**  
Resolution summary:
- `view --section` matching is now case-insensitive (`casefold()` normalization).
- Punctuation-spacing variants are normalized (e.g., `CHAPTER I.The` equals `CHAPTER I. The`).
- Trailing punctuation-insensitive behavior remains intact.

Severity: **Medium**  
Type: UX / ergonomics / agent compatibility  
Area: Section path matching

### Impact

- Humans and agents must match exact case and punctuation spacing, which is brittle.
- Minor formatting differences produce hard failures even when intent is clear.

### Reproduction

```bash
DB=/tmp/gutenbit_dickens_major.db
# Works
uv run gutenbit --db "$DB" view 98 --section "Book the First—Recalled to Life/CHAPTER I.The Period" -n 1

# Fails (spacing variation)
uv run gutenbit --db "$DB" view 98 --section "Book the First—Recalled to Life/CHAPTER I. The Period" -n 1

# Fails (case variation)
uv run gutenbit --db "$DB" view 98 --section "book the first—recalled to life/chapter i.the period" -n 1
```

### Expected

- Case-insensitive matching with normalized punctuation/whitespace variants.

### Actual

- Exact-ish segment matching only; trailing punctuation normalized, but casing and internal punctuation spacing are not.

### Likely root cause

- Segment normalization strips only trailing punctuation and whitespace collapse.

Code references:
- Normalization: [/Users/keinan/Code/gutenbit/gutenbit/db.py:94](/Users/keinan/Code/gutenbit/gutenbit/db.py:94)
- Section compare logic: [/Users/keinan/Code/gutenbit/gutenbit/db.py:345](/Users/keinan/Code/gutenbit/gutenbit/db.py:345)

### Recommendation

- Add `casefold()` to segment normalization.
- Normalize punctuation spacing (`CHAPTER I. The` vs `CHAPTER I.The`) for matching purposes.
- Consider `--section-fuzzy` or best-match suggestions for near misses.

---

## ~~F-003: Inconsistent option validation (`--preview-chars` silently coerced)~~

Status: **Resolved (March 7, 2026)**  
Resolution summary:
- Non-positive `--preview-chars` is now rejected consistently for both `search` and `view` with exit code `1`.

Severity: **Medium**  
Type: UX consistency / agent safety  
Area: CLI arg validation

### Impact

- Invalid values for `--preview-chars` do not error; they silently default to 140.
- Other numeric options (`--limit`, `--around`) correctly hard-fail for invalid negatives.
- Silent coercion is risky for automated callers and obscures mistakes.

### Reproduction

```bash
DB=/tmp/gutenbit_dickens_major.db
uv run gutenbit --db "$DB" search "door" --book-id 98 -n 1 --preview-chars -5
uv run gutenbit --db "$DB" view 98 --position 434 --preview-chars -5
```

### Expected

- Reject non-positive `--preview-chars` with explicit error and exit code 1 (or at least warn).

### Actual

- Command succeeds, defaulting preview length silently.

### Likely root cause

- Manual fallback to default when `<= 0`.

Code references:
- Search path: [/Users/keinan/Code/gutenbit/gutenbit/cli.py:448](/Users/keinan/Code/gutenbit/gutenbit/cli.py:448)
- View path: [/Users/keinan/Code/gutenbit/gutenbit/cli.py:818](/Users/keinan/Code/gutenbit/gutenbit/cli.py:818)

### Recommendation

- Use argument validators (or explicit checks) to enforce `preview_chars > 0`.
- Keep behavior consistent with `--limit`/`--around` validation style.

---

## ~~F-004: `search --mode first/last` semantics under-specified for multi-book corpora~~

Status: **Resolved (March 7, 2026)**  
Resolution summary:
- CLI help/examples now explicitly document ordering semantics:
  - `ranked`: BM25 rank, then `book_id`, then position
  - `first`: `book_id` ascending, then position ascending
  - `last`: `book_id` descending, then position descending
- README now mirrors these mode semantics for discoverability.

Severity: **Medium**  
Type: API clarity / ergonomics  
Area: Search ordering semantics

### Impact

- Users may assume global first/last hit by relevance or document chronology.
- Actual ordering is by `book_id` and position (plus rank), so results depend on corpus composition.

### Reproduction

```bash
DB=/tmp/gutenbit_dickens_major.db
uv run gutenbit --db "$DB" search "door" --mode first
uv run gutenbit --db "$DB" search "door" --mode last
```

### Expected

- Semantics explicitly documented and predictable, or mode names that reflect behavior more clearly.

### Actual

- Behavior is deterministic but non-obvious without reading SQL ordering logic.

### Likely root cause

- SQL ordering for modes is currently implementation-centric.

Code reference:
- Ordering logic: [/Users/keinan/Code/gutenbit/gutenbit/db.py:420](/Users/keinan/Code/gutenbit/gutenbit/db.py:420)

### Recommendation

- Clarify docs/help: define exact sort semantics in terms of `book_id` and position.
- Consider renaming/adding modes (`earliest-position`, `latest-position`, `best-ranked`) for unambiguous intent.

---

## F-005: Catalog retrieval path is uncached and memory-heavy each run

Severity: **Low-Medium**  
Type: Performance / technical debt  
Area: Catalog fetch and parsing

### Impact

- Every `catalog` and `ingest` run re-downloads and decompresses full catalog.
- Adds latency and resource overhead in repetitive workflows and CI.

### Evidence

Timing sample:

```bash
/usr/bin/time -lp uv run gutenbit --db /tmp/gutenbit_dickens_major.db catalog --author Dickens -n 5
```

Observed around `real ~1.00s` with high peak RSS during parse in this environment.

### Likely root cause

- No local cache layer for catalog payload.

Code reference:
- Direct fetch/decompress path: [/Users/keinan/Code/gutenbit/gutenbit/catalog.py:154](/Users/keinan/Code/gutenbit/gutenbit/catalog.py:154)

### Recommendation

- Add optional catalog cache with TTL and explicit refresh flag.
- Consider streaming parse or lightweight persisted index for frequent CLI use.

---

## F-006: Dedupe policy behavior may not match user expectation of “canonical edition” remapping

Severity: **Low-Medium**  
Type: Product semantics / docs mismatch risk  
Area: Catalog dedupe

### Impact

- Some likely variant editions do not remap, despite policy language suggesting duplicate collapse.
- Users may expect stronger canonicalization than strict normalized title+author matching currently provides.

### Reproduction

```bash
DB=/tmp/gutenbit_dickens_dedupe.db
rm -f "$DB"
uv run gutenbit --db "$DB" ingest 27924 --delay 0
```

Observed: no remap message; ingests ID as-is.

### Likely root cause

- Dedupe key is conservative and exact after normalization.

Code references:
- Dedupe strategy and keying: [/Users/keinan/Code/gutenbit/gutenbit/catalog.py:105](/Users/keinan/Code/gutenbit/gutenbit/catalog.py:105)
- Canonical resolution in ingest: [/Users/keinan/Code/gutenbit/gutenbit/cli.py:393](/Users/keinan/Code/gutenbit/gutenbit/cli.py:393)

### Recommendation

- Either strengthen canonicalization heuristics, or narrow wording in docs/help to reflect conservative dedupe behavior.
- Add test cases for expected remap/non-remap examples.

---

## 4. Additional UX/Consistency Notes

- Help text quality is generally strong and discoverable.
- Output is human-readable and concise.
- `--json` is now available across commands (`catalog`, `ingest`, `delete`, `books`, `search`, `view`) with a unified envelope (`ok`, `command`, `data`, `warnings`, `errors`).
- `books`/`catalog` truncate author string to 30 chars, which can hide co-authors/metadata in ways that may confuse filtering.

Relevant references:
- Author truncation in `catalog`: [/Users/keinan/Code/gutenbit/gutenbit/cli.py:373](/Users/keinan/Code/gutenbit/gutenbit/cli.py:373)
- Author truncation in `books`: [/Users/keinan/Code/gutenbit/gutenbit/cli.py:423](/Users/keinan/Code/gutenbit/gutenbit/cli.py:423)

---

## 5. Verified Working Behavior (No issue)

The following worked consistently in live testing:
- Ingesting all 15 major Dickens novels in one run.
- Re-ingest behavior:
  - skips already-downloaded books when chunker version is current
  - auto-reprocesses already-downloaded books when chunker version is stale
- Delete behavior and post-delete search/view outcomes.
- Empty DB UX (books/search/view/delete responses are coherent).
- Network battle tests in `tests/test_battle.py` CLI-focused subset.

---

## 6. Recommended Remediation Order

1. ~~Fix F-001 (`Hard Times` structural ordering/end-matter boundary correctness).~~ **Done (March 7, 2026).**  
2. ~~Fix F-002 (robust section matching normalization).~~ **Done (March 7, 2026).**  
3. ~~Fix F-003 (strict/consistent numeric validation for preview chars).~~ **Done (March 7, 2026).**  
4. ~~Clarify/adjust F-004 mode semantics in docs and/or API naming.~~ **Done (March 7, 2026).**  
5. Address F-005 catalog caching for performance and reliability.  
6. Resolve F-006 via stronger dedupe or clearer policy wording.

---

## 7. Suggested New Regression Tests

- ~~`test_hard_times_heading_order_and_end_matter_boundary` (PG 786).~~ Implemented.
- ~~`test_view_section_case_insensitive_and_punctuation_spacing_normalization`.~~ Implemented.
- ~~`test_preview_chars_non_positive_rejected` for both `search` and `view`.~~ Implemented.
- ~~`test_search_mode_first_last_documented_ordering`.~~ Covered by existing ordering tests + updated help/docs checks.
- `test_catalog_cache_hit_behavior` (if cache implemented).
- `test_canonical_dedupe_examples` with explicit remap expectations.
