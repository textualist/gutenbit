---
name: gutenbit-live-battle-test
description: >
  Run and resolve live Gutenbit parsing and CLI battle tests for Project Gutenberg works.
  Use when asked to battle test a new Gutenberg title, inspect gutenbit add/toc/view/search
  output against raw Gutenberg HTML, diagnose parser failures, design a generalizable parser
  fix, or add and update targeted regression tests without causing regressions on the existing
  corpus. Also trigger for GitHub Issues titled "Gutenbit cli and parsing battle test: [title]",
  requests to "run the battle test for [work]", "check how gutenbit parses [book]", or
  "fix a parsing issue" for any Project Gutenberg work.
---

# Gutenbit Live Battle Test

## Overview

Use this skill to evaluate a real Project Gutenberg work end to end, compare Gutenbit's
parsed structure against the source HTML, and turn any issue into a minimal, generalizable
fix with focused regression coverage.

Treat the raw Gutenberg HTML as the truth. Optimize for parser behavior that is fast,
accurate, and generalizable to unseen works. Do not add book-specific heuristics keyed to a
single title or Project Gutenberg ID.

## Workflow

### 1. Establish the target and current expectations

Identify the Project Gutenberg ID, the suspected failure, and whether the task is discovery
only or includes a fix.

Before editing code:

- Read `AGENTS.md` for the project's live-verification expectations and canonical corpus.
- Read `tests/test_battle.py` to see how the current network regression suite expresses parser guarantees.
- Read [references/kei-17-corpus.md](references/kei-17-corpus.md) to classify the issue against the prior battle-test failure families.

### 2. Run the live CLI smoke test first

Start with the real CLI because parser defects usually reveal themselves in visible structure
before they are obvious in code.

Run these commands in order:

```bash
uv run gutenbit add <pg_id>

# Quick sanity check — top-level structure only
uv run gutenbit toc <pg_id>

# PRIMARY DIAGNOSTIC VIEW — every heading at every level
uv run gutenbit toc <pg_id> --expand all

# Machine-readable — for counting sections and checking parent-child relationships
uv run gutenbit toc <pg_id> --expand all --json

uv run gutenbit view <pg_id>
uv run gutenbit search "<query>" --book <pg_id>
```

The `--expand all` output is the primary diagnostic view. The default depth-2 view hides
many classes of bug: chapters that should nest under a PART/BOOK but don't, subtitle or
description lines that appear as orphan siblings, and front-matter headings that swallowed
everything below them. Always inspect `--expand all` before concluding the structure is correct.

The `--json` output lets you programmatically count sections at each depth and verify
parent-child relationships, which catches nesting bugs that are hard to spot visually in a
long TOC.

Use `view` and `search` to confirm whether the bad structure also damages navigation or
search context.

### 3. Structured TOC inspection checklist

After running `toc --expand all`, systematically check each of these:

1. **Depth distribution** — Are chapters at the expected depth? If there are PARTs or BOOKs, chapters should be depth 2, not depth 1.
2. **Heading count** — Does the number of chapter-level entries match what the raw HTML has? Count them.
3. **Front-matter placement** — Is PREFACE / DEDICATION / etc. a sibling of the first structural heading, not a parent that nests everything under it?
4. **Orphan entries** — Are there single-line entries at unexpected depths that look like subtitle fragments or description text that didn't merge into their chapter heading?
5. **Terminal entries** — Does the TOC end with the right closing matter (APPENDIX, NOTE, GLOSSARY, etc.)?
6. **Heading text completeness** — For chapters with subtitles or descriptions, is the full heading text present, or was it split into separate TOC entries?

Write down every anomaly before looking at code.

### 4. Compare against raw Gutenberg HTML before forming a fix

Do not infer the correct structure from intuition or another edition. Confirm it from the
downloaded HTML for the same Gutenberg work.

First, dump the raw HTML heading tags to see the ground-truth hierarchy:

```python
from gutenbit.download import download_html
from bs4 import BeautifulSoup

html = download_html(pg_id)
soup = BeautifulSoup(html, "html.parser")

for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
    print(f"{tag.name}  {tag.get_text(strip=True)[:80]}")
```

This shows exactly what heading tags exist and at what HTML rank. The gap between this output
and `toc --expand all` is exactly where the bugs live.

Then inspect the parsed chunks to see how the parser interpreted them:

```python
from gutenbit.download import download_html
from gutenbit.html_chunker import chunk_html

book_id = 0  # replace
html = download_html(book_id)
chunks = tuple(chunk_html(html))
headings = [chunk for chunk in chunks if chunk.kind == "heading"]

for heading in headings[:80]:
    print(heading.div1, heading.div2, heading.div3, heading.content)
```

Compare these two outputs and confirm:

- which headings are real structure
- which text is only contents scaffolding, attribution, or decorative matter
- whether the TOC is incomplete and body-heading refinement is required
- whether fallback heading scanning is over-triggering on speaker names, Roman numerals, or dramatic dialogue labels
- whether subtitle or description elements following chapter headings were merged or left as orphans

### 5. Classify the failure before changing code

Map the bug to an existing failure class from [references/kei-17-corpus.md](references/kei-17-corpus.md).
Most new regressions fit one of these families:

- omitted opening matter
- omitted closing matter
- synthetic garbage headings
- lost multi-level structure
- catastrophic play parsing
- attribution or publisher noise promoted into headings
- unrecognized index vocabulary (number words, letter indices, ordinals the parser doesn't know)
- unmerged subtitle or description (subtitle/description line appears as a separate TOC entry instead of merging into its chapter heading)
- front-matter nesting contamination (a front-matter heading becomes a container that nests all subsequent chapters under it)
- non-keyword heading nesting failure (chapters don't nest under structurally valid parent headings because the parent lacks a keyword like PART/BOOK)
- title-block author-name leakage (standalone "BY" heading + author name in title blocks promoted as structural headings by the fallback heading scan)
- bare heading cross-merge (bare chapter number like "CHAPTER 25" merges with unrelated next entry at the same level, e.g. a different story title)
- volume/part title child-merge (a VOLUME/PART/BOOK heading absorbs the first child section as a subtitle when children have same-rank peers)
- identical heading text over-deduplication (multiple same-text headings collapsed when they are deliberate structural repetition, e.g. anthology series titles)
- terminal marker subtitle merge ("THE END" / "FINIS" merged as subtitle of preceding heading instead of kept as a standalone section)
- split-title empty parent (h2 main title + h3 subtitle creates an empty parent section with all content under the subtitle child)

If the case does not fit an existing family, define the new family in structural terms, not
title-specific terms.

### 6. Design the smallest general fix

Prefer fixes that improve the parser's structural rules rather than special-casing a single book.

Good directions:

- refine heading classification thresholds
- improve TOC cleanup or heading normalization
- refine body-heading fallback so it recovers real structure without promoting noise
- preserve broad divisions such as BOOK / PART / ACT when lower-level headings also exist
- suppress known non-structural patterns only when the rule is structurally justified

Reject fixes that:

- branch on a specific Project Gutenberg ID
- key off an exact title or author when a structural pattern is available
- fix the new book while weakening an existing corpus case
- add wide heuristics without checking how they affect the network regression corpus

State the intended invariant before editing code. Example: "preserve part-level headings as
standalone sections instead of merging them into the first chapter heading."

### 7. Add focused regression coverage

Use `tests/test_battle.py` for live Gutenberg regression coverage. Follow the existing style:

- write one test per behavioral guarantee
- keep assertions tight and specific to the structural defect
- assert the positive signal and the exclusion signal when both matter
- use short heading slices for compact books and representative anchor assertions for long books
- assert parent-child relationships through `div1` / `div2` / `div3` when hierarchy is the bug

Examples of good test shapes:

- assert the exact opening heading slice when front matter was missing
- assert the exact closing heading slice when an epilogue or note was missing
- assert that garbage headings are absent while the real headings remain
- assert one or two representative nested headings for large multi-level works instead of snapshotting hundreds of headings
- when the bug is a merge failure, assert that the merged heading **contains the subtitle/description text**, not just that the chapter exists
- when the bug is a nesting failure, assert the `div1`/`div2` relationship explicitly and also assert the **count** of entries at each nesting level
- use `toc --expand all --json` output as a reference for what the test assertions should look like

Do not add broad snapshots that are hard to maintain and do not isolate the structural invariant.

**Network vs. synthetic tests.** Network tests (`tests/test_battle.py`, `@pytest.mark.network`)
download live Gutenberg HTML and are expensive. Reserve them for high-value structural
regressions that cannot be captured with synthetic HTML fixtures. For most issue families,
write a synthetic non-network test in `tests/test_html_chunker.py` using `_make_html()` to
construct a minimal HTML fragment that reproduces the parser behavior. Use network tests only
when the real Gutenberg HTML has structural complexity that a synthetic fixture cannot
faithfully represent.

### 8. Verify in widening rings

After the code change:

1. Re-run the live CLI commands on the target book, including `toc --expand all`.
2. Re-run the specific network regression that covers the target behavior.
3. Re-run the full non-network suite.
4. Re-run the full network battle corpus.
5. Run a JSON-based machine check on the target book.

Use:

```bash
uv run pytest tests/test_battle.py -k "<target>"
uv run pytest
uv run pytest -m network
```

For the machine check, verify section counts and depth distribution via JSON:

```bash
uv run gutenbit toc <pg_id> --expand all --json | python3 -c "
import json, sys
toc = json.load(sys.stdin)
# Verify: section count at each depth, no orphan fragments,
# front matter is a sibling not a parent, etc.
for entry in toc:
    print(entry.get('depth', 0), entry.get('heading', ''))
"
```

Treat `uv run pytest -m network` as mandatory before closing the work unless network access
is unavailable. The goal is not only to fix the new book, but to prove the parser still holds
across the existing live corpus.

**Batch testing.** When battle testing multiple works by the same author (or a themed corpus),
test all works first and record results in a structured table before beginning fixes. Group
failures into issue families, then fix one family at a time. After each family fix, re-run
the full test suite to catch regressions before moving to the next family. This prevents
cascading regressions and avoids redundant work on issues that share a root cause.

### 9. Report the result in parser terms

When documenting the outcome, summarize:

- the observable CLI failure
- the raw HTML truth that contradicted the parser output
- the structural rule that changed
- why the fix should generalize to unseen works
- which focused and full-suite tests were run

If no bug is present, record that explicitly and explain why the observed output matches the
source HTML.

When reporting results for a batch of works, use structured tables:

- **Clean passes table**: Work | PG | Sections | Notes
- **Source HTML limitations** (not parser bugs): Work | PG | Issue
- **Issues found** (grouped by failure family): Work | PG | Issue
- **Regressions investigated**: Regression | Root cause | Resolution

Post results as a comment on the parent GitHub Issue using `mcp__github__add_issue_comment`.
If a PR is created to fix the issue families, include `Closes #NNN` in the PR description to
auto-close the parent issue on merge.

## References

- Read [references/kei-17-corpus.md](references/kei-17-corpus.md) for the battle-test failure families and regression-writing heuristics.
- Read `tests/test_battle.py` for the live corpus currently enforced in code.
- Read `AGENTS.md` for the project's canonical working rules.
