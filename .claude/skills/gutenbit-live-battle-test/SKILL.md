---
name: gutenbit-live-battle-test
description: >
  Run and resolve live Gutenbit parsing and CLI battle tests for Project Gutenberg works.
  Use when asked to battle test a new Gutenberg title, inspect gutenbit add/toc/view/search
  output against raw Gutenberg HTML, diagnose parser failures, design a generalizable parser
  fix, or add and update targeted regression tests without causing regressions on the existing
  corpus. Also trigger for Linear issues titled "Gutenbit cli and parsing battle test: [title]",
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
- Read [references/kei-17-corpus.md](references/kei-17-corpus.md) to classify the issue against the 20 prior battle-test failures.

### 2. Run the live CLI smoke test first

Start with the real CLI because parser defects usually reveal themselves in visible structure
before they are obvious in code.

Run:

```bash
uv run gutenbit add <pg_id>
uv run gutenbit toc <pg_id>
uv run gutenbit view <pg_id>
uv run gutenbit search "<query>" --book <pg_id>
```

Use `toc` as the primary structural signal. Use `view` and `search` to confirm whether the
bad structure also damages navigation or search context.

While inspecting output, write down:

- missing sections
- extra/synthetic sections
- wrong nesting between `div1` / `div2` / `div3`
- front matter or closing matter that disappeared
- noisy attribution/publisher text that was promoted into headings
- cases where the parser kept chapter headings but lost higher-level structure such as PART / BOOK / ACT

### 3. Compare against raw Gutenberg HTML before forming a fix

Do not infer the correct structure from intuition or another edition. Confirm it from the
downloaded HTML for the same Gutenberg work.

Use a small Python inspection snippet when needed:

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

Also inspect the raw HTML directly to find the source anchors and heading tags that Gutenbit
should preserve. Confirm:

- which headings are real structure
- which text is only contents scaffolding, attribution, or decorative matter
- whether the TOC is incomplete and body-heading refinement is required
- whether fallback heading scanning is over-triggering on speaker names, Roman numerals, or dramatic dialogue labels

### 4. Classify the failure before changing code

Map the bug to an existing failure class from [references/kei-17-corpus.md](references/kei-17-corpus.md).
Most new regressions fit one of these families:

- omitted opening matter
- omitted closing matter
- synthetic garbage headings
- lost multi-level structure
- catastrophic play parsing
- attribution or publisher noise promoted into headings

If the case does not fit an existing family, define the new family in structural terms, not
title-specific terms.

### 5. Design the smallest general fix

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

### 6. Add focused regression coverage

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

Do not add broad snapshots that are hard to maintain and do not isolate the structural invariant.

### 7. Verify in widening rings

After the code change:

1. Re-run the live CLI commands on the target book.
2. Re-run the specific network regression that covers the target behavior.
3. Re-run the full non-network suite.
4. Re-run the full network battle corpus.

Use:

```bash
uv run pytest tests/test_battle.py -k "<target>"
uv run pytest
uv run pytest -m network
```

Treat `uv run pytest -m network` as mandatory before closing the work unless network access
is unavailable. The goal is not only to fix the new book, but to prove the parser still holds
across the existing live corpus.

### 8. Report the result in parser terms

When documenting the outcome, summarize:

- the observable CLI failure
- the raw HTML truth that contradicted the parser output
- the structural rule that changed
- why the fix should generalize to unseen works
- which focused and full-suite tests were run

If no bug is present, record that explicitly and explain why the observed output matches the
source HTML.

## References

- Read [references/kei-17-corpus.md](references/kei-17-corpus.md) for the 20 battle-test lessons and regression-writing heuristics.
- Read `tests/test_battle.py` for the live corpus currently enforced in code.
- Read `AGENTS.md` for the project's canonical working rules.
