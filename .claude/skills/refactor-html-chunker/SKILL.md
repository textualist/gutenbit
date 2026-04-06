---
name: refactor-html-chunker
description: >
  Systematically refactor the Gutenbit HTML parser module (gutenbit/html_chunker/) to improve
  code quality, efficiency, modularity, and documentation with zero behavioral change. Use when
  asked to "refactor the parser", "clean up html_chunker", "improve parser code quality",
  "deduplicate parser code", "split _sections.py", "optimize the parser", "document the parser
  internals", or any variation of "tighten/clean/modernize the html chunker". Also trigger for
  GitHub Issues titled "refactor: html_chunker" or "code quality: parser".
---

# Refactor HTML Chunker

## Overview

Make the HTML chunker module tight, clean, efficient, and well-documented — without changing
any parsing behavior. The parsed output for every Project Gutenberg work must remain identical
before and after refactoring. Every refactoring step is one commit that passes all tests.

The module is ~3,950 lines across 6 files with ~107 functions. It works well — 147 unit tests
and 91 battle tests confirm this — but the code has grown organically and has accumulated
duplication, overlong functions, and sparse documentation. This skill systematically addresses
that without risking regressions.

**Non-goal**: changing parser behavior, adding features, or restructuring the public API. If
you discover a parser bug during refactoring, file it separately and do not fix it in the
refactoring branch.

## Workflow

### 1. Baseline and behavioral snapshot

Establish the behavioral contract before touching anything.

**Run the full test gauntlet:**

```bash
uv run pytest
uv run pytest -m network
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

All five must pass cleanly. If any fail, fix them first in a separate commit before starting
the refactoring work.

**Capture a behavioral fingerprint:**

Generate chunk output for a representative sample of works. Use at least 10 works from the
battle test corpus. Save the output to compare against after refactoring.

```python
import json
from gutenbit.download import download_html
from gutenbit.html_chunker import chunk_html

SAMPLE_IDS = [1342, 84, 1661, 2701, 11, 1232, 74, 98, 174, 1399]  # adjust from test_battle.py

fingerprint = {}
for pg_id in SAMPLE_IDS:
    html = download_html(pg_id)
    chunks = tuple(chunk_html(html))
    fingerprint[pg_id] = [
        {"pos": c.position, "div1": c.div1, "div2": c.div2, "div3": c.div3,
         "div4": c.div4, "content": c.content, "kind": c.kind}
        for c in chunks
    ]

with open("/tmp/parser_fingerprint.json", "w") as f:
    json.dump(fingerprint, f, indent=2)
```

This fingerprint is the contract. After every refactoring step, diff against it to prove zero
behavioral change.

**Record baseline metrics:**

```bash
# Line counts per module
wc -l gutenbit/html_chunker/*.py

# Function counts per module
grep -c '^def \|^    def ' gutenbit/html_chunker/*.py

# Cyclomatic complexity (if radon is available)
uv run --with radon radon cc gutenbit/html_chunker/ -s -a

# Code duplication
uv run --with radon radon raw gutenbit/html_chunker/ -s
```

Save these numbers. They are the "before" in your final report.

### 2. Static analysis audit

Use tooling to systematically catalog issues before writing any code. Do not fix anything yet
— just build the inventory.

**Dead code and unused imports:**

```bash
uv run ruff check gutenbit/html_chunker/ --select F401,F841
```

Also manually check: are there functions defined but never called from outside their own
module? Grep for each function name across the codebase:

```bash
# For each function in a module, check if it's imported or called elsewhere
grep -rn '<function_name>' gutenbit/
```

**Overly long functions:**

Identify functions longer than 60 lines. These are candidates for extraction. Key suspects:

- `_sections.py`: `_parse_heading_sections`, `_refine_toc_sections`,
  `_filter_fallback_heading_rows`
- `_headings.py`: predicates with deeply nested conditions

**Duplicate or near-duplicate logic:**

Look for repeated patterns:
- Regex patterns that appear in both `_common.py` and individual modules
- `_heading_text.lower().split()` patterns repeated across predicates
- Similar `_is_*` predicate functions that test overlapping conditions
- `tag_positions.get(id(...))` lookups repeated inline instead of through a helper

```bash
# Find regex definitions outside _common.py
grep -n 're.compile' gutenbit/html_chunker/_headings.py gutenbit/html_chunker/_sections.py gutenbit/html_chunker/_scanning.py
```

**Complex conditionals:**

Look for deeply nested if/elif chains and boolean expressions spanning multiple lines,
especially in `_headings.py` predicates like `_is_ignorable_fallback_heading`.

**Build the inventory as a checklist.** Group findings by category:

- [ ] Dead code (unused functions, unreachable branches)
- [ ] Unused imports
- [ ] Functions over 60 lines
- [ ] Duplicate regex patterns
- [ ] Near-duplicate predicate functions
- [ ] Repeated inline patterns that could be helpers
- [ ] Complex conditionals that need extraction or simplification

### 3. Module boundary review

Assess whether the current 6-file split makes sense. The dependency graph today:

```
__init__.py  →  _common.py, _scanning.py, _toc.py, _sections.py
_sections.py →  _common.py, _headings.py, _scanning.py, _toc.py
_headings.py →  _common.py, _scanning.py
_toc.py      →  _common.py, _headings.py, _scanning.py
_scanning.py →  _common.py
_common.py   →  (no internal deps)
```

**Check these specific concerns:**

- [ ] `_sections.py` at ~1,800 lines and 34 functions is too large. Does it naturally split?
  Candidate splits:
  - **Merge/subtitle passes** (`_merge_bare_heading_pairs`,
    `_merge_adjacent_duplicate_sections`, `_merge_chapter_subtitle_sections`,
    `_merge_chapter_description_paragraphs`) into a `_merging.py` module
  - **Nesting/hierarchy passes** (`_nest_broad_subdivisions`,
    `_nest_chapters_under_broad_containers`, `_respect_heading_rank_nesting`,
    `_promote_more_prominent_heading_runs`, `_flatten_single_work_title_wrapper`,
    `_equalize_orphan_level_gap`) into a `_nesting.py` module
  - **Title/front-matter passes** (`_strip_leading_title_page_sections`,
    `_normalize_collection_titles`, `_drop_leading_repeated_title_sections`) into a
    `_title_cleanup.py` module
  - **Core section parsing** (`_parse_toc_sections`, `_parse_heading_sections`,
    `_parse_paragraph_sections`, `_refine_toc_sections`) stays in `_sections.py`

- [ ] Are there functions in the wrong file? Check if any `_headings.py` functions are only
  called from `_sections.py` and would be more naturally co-located with their caller.

- [ ] Could `_toc.py` (217 lines) be absorbed into another module, or is it better kept
  separate? (Likely keep separate — it has a clear, focused responsibility.)

- [ ] Are there circular dependency risks? The current graph is a DAG; any split must preserve
  this property.

**Decision rule**: only split `_sections.py` if the resulting modules each have a cohesive
responsibility and the split reduces cross-module imports rather than increasing them. Do not
split just to hit a line-count target.

### 4. Deduplication and consolidation

Work through the inventory from Step 2. For each item, make one small commit.

**Regex consolidation:**

- Move any compiled regex from `_headings.py` or `_sections.py` into `_common.py` if it is
  used by more than one module.
- Consolidate regex patterns that test similar things. Check whether patterns like
  `_BARE_HEADING_NUMBER_RE`, `_HEADING_KEYWORD_RE`, and `_STANDALONE_STRUCTURAL_RE` overlap
  and whether their consumers could share logic.
- Check for regex patterns compiled inside function bodies that could be module-level constants.

**Predicate consolidation in `_headings.py`:**

The module has 45 functions, many `_is_*` / `_has_*` predicates. Check for:
- Predicates that are strict subsets of others (one always implies the other)
- Predicates with identical or near-identical first few conditions
- Predicates that could be combined with a parameter instead of being separate functions

Do NOT merge predicates that have genuinely different semantics even if they share some code.
The predicate names are documentation — preserve clarity.

**Inline pattern extraction:**

- `_heading_text.lower().strip()` chains: if repeated more than 3 times, extract a helper
- `tag_positions.get(id(tag))` lookups: ensure all callers use a consistent helper
- `frozenset(... for ... in ...)` constructions: if the same set is built multiple times,
  compute it once

**Verification after each commit:**

```bash
uv run pytest && uv run pytest -m network
```

Both must pass. If either fails, revert the commit immediately and investigate.

### 5. Algorithmic efficiency

Review hot paths for unnecessary recomputation and redundant work.

**Checklist:**

- [ ] **Redundant soup traversals**: Does `_parse_heading_sections` or `_scan_document` walk
  the entire soup tree more than once for the same purpose? Can traversals be combined?

- [ ] **Regex recompilation**: Are any `re.compile()` calls inside loops or functions that
  run per-document? All regex should be module-level constants. Check:
  ```bash
  grep -n 're.compile\|re.match\|re.search\|re.fullmatch' gutenbit/html_chunker/*.py
  ```
  Any `re.match` / `re.search` with a string pattern (not a compiled pattern) inside a
  frequently-called function should use the compiled constant instead.

- [ ] **O(n^2) patterns**: Look for nested loops over sections, particularly in
  `_merge_chapter_subtitle_sections` and `_refine_toc_sections` which iterate over section
  lists while also scanning forward. Can any be converted to single-pass with an index?

- [ ] **Cache effectiveness**: The module uses `lru_cache` and per-parse caches
  (`_container_residue_cache`, `_is_toc_paragraph_cache`, `_toc_context_cache`). Check:
  - Are the per-parse caches actually hit? Add temporary counters to measure hit rates.
  - Is the `lru_cache` maxsize appropriate for typical workloads?
  - Are there other pure functions called repeatedly with the same arguments that would
    benefit from caching?

- [ ] **Bisect usage**: `_scanning.py` uses `bisect_left` / `bisect_right` for position
  lookups. Confirm these are used correctly and that the underlying lists are sorted.

- [ ] **Paragraph position lookups**: `_paragraphs_in_range` uses binary search on
  `paragraph_positions`. Confirm the positions list is built once in `_scan_document` and
  reused, not rebuilt per call.

**Do not optimize speculatively.** Only change code where you can identify a concrete
inefficiency. The parser processes one book at a time; micro-optimizations are not worth the
readability cost unless they fix an actual O(n^2) or repeated-traversal problem.

### 6. Readability and documentation

This step improves the code for the next developer (or the next Claude session).

**Docstrings:**

- Every public function in `__init__.py` must have a docstring.
- Every internal function with 10+ lines should have a one-line docstring explaining what it
  does and returns. Many `_sections.py` functions lack docstrings entirely.
- For complex heuristics (heading classification, subtitle merging, nesting logic), add a
  brief "Why" comment explaining the structural pattern the code handles. Example:
  ```python
  # Gutenberg editions often split "CHAPTER I" and "THE ADVENTURE BEGINS" into
  # separate headings. This pass merges the bare number heading with its
  # immediately following subtitle heading when both are at the same level.
  ```

**Naming review:**

- [ ] Are there single-letter variables in complex functions? (e.g., `s` for section, `h`
  for heading row) — rename to `section`, `row` for clarity.
- [ ] Are there function names that don't clearly convey what they return?
- [ ] Are there magic numbers? (e.g., threshold values without a named constant or comment)
  — extract to named constants with a comment explaining the threshold.

**Type annotations:**

```bash
uv run ty check
```

Fix any type errors. Add return type annotations to any functions that lack them.

**Inline comments for heuristic thresholds:**

The parser uses many numeric thresholds and heuristic conditions. Each should have a brief
comment explaining why that value was chosen. Check for undocumented thresholds, especially
in `_headings.py` and `_parse_heading_sections`.

**Verification after each commit:**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
```

### 7. Verification

After all refactoring is complete, run the full gauntlet and compare against the baseline.

**Full test gauntlet:**

```bash
uv run pytest
uv run pytest -m network
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

All five must pass cleanly.

**Behavioral fingerprint comparison:**

Re-generate the chunk output for the same sample works from Step 1 and diff against the saved
fingerprint:

```python
import json
from gutenbit.download import download_html
from gutenbit.html_chunker import chunk_html

SAMPLE_IDS = [1342, 84, 1661, 2701, 11, 1232, 74, 98, 174, 1399]

new_fingerprint = {}
for pg_id in SAMPLE_IDS:
    html = download_html(pg_id)
    chunks = tuple(chunk_html(html))
    new_fingerprint[pg_id] = [
        {"pos": c.position, "div1": c.div1, "div2": c.div2, "div3": c.div3,
         "div4": c.div4, "content": c.content, "kind": c.kind}
        for c in chunks
    ]

with open("/tmp/parser_fingerprint.json") as f:
    old_fingerprint = json.load(f)

for pg_id in SAMPLE_IDS:
    old = old_fingerprint[str(pg_id)]
    new = new_fingerprint[pg_id]
    if old != new:
        print(f"REGRESSION: PG {pg_id} — chunk output differs")
        for i, (o, n) in enumerate(zip(old, new)):
            if o != n:
                print(f"  First diff at chunk {i}: {o} != {n}")
                break
    else:
        print(f"OK: PG {pg_id}")
```

Every work must print `OK`. Any `REGRESSION` line means a behavioral change was introduced
and must be investigated and reverted.

### 8. Report

Write a structured summary of all changes made.

**Format:**

```markdown
## HTML Chunker Refactoring Report

### Metrics

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Total lines | ~3,954 | ??? | ??? |
| _sections.py lines | 1,797 | ??? | ??? |
| _headings.py lines | 965 | ??? | ??? |
| Function count | ~107 | ??? | ??? |
| Avg cyclomatic complexity | ??? | ??? | ??? |
| Functions without docstrings | ??? | ??? | ??? |

### Changes by category

#### Dead code removed
- (list each removed function/import with one-line justification)

#### Deduplication
- (list each consolidation with before/after)

#### Module restructuring
- (describe any file splits or moves)

#### Efficiency improvements
- (list each optimization with rationale)

#### Documentation added
- (count of docstrings added, notable "why" comments)

#### Naming improvements
- (list renames with rationale)

### Verification

- [ ] `uv run pytest` — PASS
- [ ] `uv run pytest -m network` — PASS
- [ ] `uv run ruff check .` — PASS
- [ ] `uv run ruff format --check .` — PASS
- [ ] `uv run ty check` — PASS
- [ ] Behavioral fingerprint — identical for all sample works

### Commits

(list each commit hash and one-line summary)
```

If this refactoring was triggered by a GitHub Issue, post the report as a comment using
`mcp__github__add_issue_comment`.

## Acceptance Criteria

- [ ] All unit tests pass (`uv run pytest`)
- [ ] All battle tests pass (`uv run pytest -m network`)
- [ ] Lint clean (`uv run ruff check .`)
- [ ] Format clean (`uv run ruff format --check .`)
- [ ] Type check clean (`uv run ty check`)
- [ ] Behavioral fingerprint is identical for all sample works (zero regressions)
- [ ] Measurable improvement in at least one of: total line count, cyclomatic complexity,
  number of functions without docstrings, or identified code duplication
- [ ] Each refactoring step is a separate commit that passes all tests independently
- [ ] No parser behavior changes, no new features, no bug fixes mixed in

## References

- `gutenbit/html_chunker/__init__.py` — public API and main chunking pipeline
- `gutenbit/html_chunker/_common.py` — shared constants, regex, data structures
- `gutenbit/html_chunker/_scanning.py` — document scanning, indexing, paragraph extraction
- `gutenbit/html_chunker/_headings.py` — heading classification, 45 functions incl. predicates
- `gutenbit/html_chunker/_sections.py` — section parsing, refinement, normalization (largest)
- `gutenbit/html_chunker/_toc.py` — TOC link classification and matching
- `tests/test_html_chunker.py` — unit tests
- `tests/test_battle.py` — live network regression tests
- `AGENTS.md` — project working rules and code map
