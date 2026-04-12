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

### 3. Two-pass structured TOC inspection

A single smoke-test pass is not enough.  A surprising number of real
parser bugs hide behind output that looks plausible at a glance — the
surface structure matches the source HTML while the parser is silently
dropping, merging, or flattening real structure.  Do both passes.

#### Pass 1 — structural checklist

After running `toc --expand all`, systematically check each of these:

1. **Depth distribution** — Are chapters at the expected depth? If there are PARTs or BOOKs, chapters should be depth 2, not depth 1.
2. **Heading count** — Does the number of chapter-level entries match what the raw HTML has? Count them.
3. **Front-matter placement** — Is PREFACE / DEDICATION / etc. a sibling of the first structural heading, not a parent that nests everything under it?
4. **Orphan entries** — Are there single-line entries at unexpected depths that look like subtitle fragments or description text that didn't merge into their chapter heading?
5. **Terminal entries** — Does the TOC end with the right closing matter (APPENDIX, NOTE, GLOSSARY, etc.)?
6. **Heading text completeness** — For chapters with subtitles or descriptions, is the full heading text present, or was it split into separate TOC entries?

Write down every anomaly.

#### Pass 2 — UX rating and limitation challenge

Now read the `toc --expand all` output line by line as a user would.
Rate the work on this scale:

| Rating | Criteria |
|--------|----------|
| **PERFECT** | Structure exactly matches what a reader would expect. |
| **GOOD** | Minor cosmetic issues (empty title sections, trivial noise) that do not impede navigation. |
| **ACCEPTABLE** | Navigable but with structural quirks (duplicate headings, odd nesting, minor content leak). |
| **POOR** | Degraded reading experience (flat where it should nest, oversized sections, missing sub-chapters) but content is still reachable. |
| **BROKEN** | Content is invisible, wildly merged, or reduced to a single giant section. |

**Any work rated POOR or BROKEN triggers a root-cause investigation
before labeling it "source HTML limitation".**  The classification
agents in the first round of a batch review default to "source HTML
limitation" far too easily — a BROKEN rating forces you to prove the
parser could not have done better.

Before accepting any "source HTML limitation" classification, ask:

1. Does the raw HTML contain structural information the parser is dropping? (Dump `anchor_map`, heading tags, TOC links.)
2. Is there a generalizable rule that would recover the structure without branching on PG IDs?
3. Would a competing PG-style parser do better on this input?

If any answer is yes, it is a parser bug, not a source HTML limitation.

#### Triage table (start filling in during Pass 1)

Use this exact shape so results compound into the final step-9 report
without rework:

| Work | PG | Sections | UX | Issues | Failure family |
|------|---:|---------:|----|--------|----------------|

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

**Anchor-map completeness check.**  When a book has `pginternal` TOC
links but the parser produces zero or very few sections, the root
cause is almost always that `anchor_map` does not contain the link
targets.  Run this diagnostic:

```python
from gutenbit.html_chunker._scanning import _scan_document

doc_index = _scan_document(soup)
unresolved = [
    link for link in doc_index.toc_links
    if (href := str(link.get("href", ""))).startswith("#")
    and href[1:] not in doc_index.anchor_map
]
print(f"TOC links: {len(doc_index.toc_links)}, unresolved: {len(unresolved)}")
for link in unresolved[:5]:
    print(f"  {link.get('href')!r} -> {link.get_text(strip=True)[:60]!r}")
```

Common causes of unresolved links: the `id` sits on the heading tag
itself (`<h4 id="...">Title</h4>`) rather than a child `<a>`; the id
is on a `<span class="pagenum">` wrapper; the target element is
outside the document bounds.

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

**Iterative fix narrowing.**  When a bug spans multiple parser
layers, work in widening rings, starting at the data layer:

1. **Data layer** — does the parser even see the structure? Verify
   `anchor_map`, `toc_links`, raw headings.  If the data is missing,
   fix the scan (`_scanning.py`) first.
2. **Resolution layer** — does the right element get selected from
   the data? Fix the resolver (`_sections.py`, `_toc.py`) second.
3. **Validation layer** — do filters accept the resolved element?
   Fix the validator (`_headings.py`) last.

After each change, run the existing corpus tests (`uv run pytest -m
network`).  If something breaks, the fix is too broad — narrow the
guard before widening again.  A common failure mode is to relax a
global threshold (e.g. `heading_rank <= 2` → `<= 4`) and only later
discover that it damaged an adjacent book's subtitle merging.
Prefer a guard that is conditional on the specific signal (e.g.
"the anchor IS the heading tag") over a global threshold relaxation.

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

**Cover PERFECT cases, not just fixes.**  After writing regression
tests for the bugs you fixed, add 3–5 **synthetic** fixtures in
`tests/test_html_chunker.py` that pin the structural invariants of a
random sample of PERFECT-rated works from the current batch.  These
are free insurance: they run in <1s and catch silent regressions
where a future fix quietly damages a case that was already working.
Network tests only catch regressions in books that happen to still
be in the active battle-test corpus; synthetic fixtures protect the
underlying pattern permanently.

When the synthetic parser output does not exactly match the real
parser output (because the real book has surrounding content the
fixture lacks), assert the **structural invariant** directly rather
than pinning the exact `div1`/`div2` layout.  Example: "front matter
headings must not have `div1` equal to any part name" instead of
"front matter has `div1 == 'Introduction By John Cournos'`".

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

**Batch testing.** When battle testing a themed corpus of 10+ works,
split across parallel subagents with roughly 5–15 works each, with
one agent per cohesive author or work cluster.  Every subagent must
run the Pass-1 checklist and the Pass-2 UX rating before the batch
moves on.  Compile the master triage table (see step 3) across all
agents before beginning any fixes.  Group failures into issue
families, fix one family at a time, and re-run the full test suite
between families to catch cascading regressions early.

**Post-fix quality review.**  After the fix passes all tests but
before committing, run a quality review pass on the changed files.
The most reliable form is three parallel agents on the same diff
(reuse, quality, efficiency) — the `/simplify` skill already wires
this up.  This catches the patterns that accumulate during iterative
fix narrowing: redundant state, parameter sprawl, dead-weight guards,
duplicated comments, and weak docstrings.  Address each concrete
finding before committing.  A common outcome of this pass is
collapsing a multi-file fix by ~30% without changing behavior.

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

- **UX rating summary**: one row per rating level with a count and
  the list of PG IDs in that bucket.
- **PERFECT / GOOD cases**: Work | PG | Sections | Notes
- **ACCEPTABLE / POOR cases**: Work | PG | Issue | Why not fixable now
- **BROKEN cases**: Work | PG | Root cause | Fix (or deferred)
- **Regressions investigated**: Regression | Root cause | Resolution

Post results as a comment on the parent GitHub Issue using `mcp__github__add_issue_comment`.
If a PR is created to fix the issue families, include `Closes #NNN` in the PR description to
auto-close the parent issue on merge.

**Deferred issues.**  Any BROKEN or POOR case that is not fixed in
the current session must be recorded in
[references/kei-17-corpus.md](references/kei-17-corpus.md) under
"Deferred issues" with: work, PG ID, root-cause summary, what a fix
would look like structurally, and why deferring.  This turns the
corpus document into a living backlog instead of a closed-case log,
so the next agent working on the parser can pick up where this one
left off.

## References

- Read [references/kei-17-corpus.md](references/kei-17-corpus.md) for the battle-test failure families and regression-writing heuristics.
- Read `tests/test_battle.py` for the live corpus currently enforced in code.
- Read `AGENTS.md` for the project's canonical working rules.
