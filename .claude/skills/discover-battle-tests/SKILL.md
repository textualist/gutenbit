---
name: discover-battle-tests
description: >
  Discover new Project Gutenberg works and create templated Linear battle-test sub-issues.
  Use when the user asks to "discover N new battle tests", "find new battle test candidates",
  "create battle test issues", "add more battle tests to Linear", or any variation of
  "discover/find/create N battle test issues". The user specifies how many to discover
  (default 10). This skill handles discovery and issue creation only — it does not run the
  battle tests themselves.
argument-hint: "[count]"
---

# Discover Battle Tests

Create exactly `$ARGUMENTS` (default: 10) new child issues for live CLI/parsing battle tests
under a single parent issue in Linear. This skill is discovery and issue-creation only — do
not run the battle tests.

## Goal

Identify `$ARGUMENTS` **new**, previously unseen Project Gutenberg works in English,
prioritizing canonical and widely known literature, and file them as templated child issues
in Linear.

## Step 1 — Build the exclusion set

Before selecting candidates, gather every PG ID and work already covered. A work is "seen"
if it appears in **any** of these sources:

1. **Linear**: Search for issues whose title starts with
   `Gutenbit cli and parsing battle test:`. Collect every PG ID and work title from the
   results. Use `list_issues` with query `Gutenbit cli and parsing battle test` in the
   `Gutenbit` project.

2. **Test files**: Read `tests/test_battle.py` and any other test files under `tests/` that
   reference PG IDs. Extract every PG ID.

3. **Corpus reference**: Read
   `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md` and extract every
   PG ID listed in the failure-class table.

Merge all three sources into a single deduplicated exclusion set of PG IDs and work titles.

## Step 2 — Build a candidate list

Generate a candidate list of at least 2× the requested count (e.g., 20 candidates for 10
requested issues). Candidates must be:

- **English-language** Project Gutenberg works.
- **Major, well-known works of literature** with strong parser-coverage value — not filler
  catalog entries.
- **Broad across authors, periods, and structural styles.** Avoid clustering on one author
  or era.
- **Likely to exercise parser edge cases.** Favor works with:
  - substantial front matter (prefaces, dedications, introductions)
  - deep section nesting (parts → books → chapters → sub-sections)
  - plays and dramatic works
  - poetry collections
  - epistolary / letter-based narratives
  - essays and treatises
  - translations with translator/editor matter
  - unusual or complex tables of contents

For each candidate, note the work title, expected PG ID, and a brief note on why it has
parser-coverage value.

## Step 3 — Resolve and deduplicate

For each serious candidate:

1. Confirm the correct Project Gutenberg edition and PG ID. Use the Gutenberg catalog or
   `uv run gutenbit add <pg_id>` to verify the ID resolves to the expected work.
2. Check the exclusion set from Step 1. Remove any candidate whose PG ID or title (fuzzy
   match) is already seen.

After deduplication, select the top `$ARGUMENTS` works from the remaining candidates, ranked
by literary importance and expected parser-coverage value.

## Step 4 — Create the parent issue

Create a single parent issue in Linear:

- **Team**: `Keinan`
- **Project**: `Gutenbit`
- **Labels**: `["Test"]`
- **Priority**: `2` (High)
- **Title**: `Discover <N> new English live battle cases and add them as templated subissues`
- **Description**: Use this exact template, replacing `<N>` with the count:

```markdown
Create exactly <N> new child issues for live CLI/parsing battle tests. This issue is
discovery and issue-creation only; do not run the battle tests here.

## Selection criteria

* English-language Project Gutenberg works only.
* Prioritize major, well-known works of literature.
* Each work is confirmed absent from existing Linear battle-test issues,
  `tests/test_battle.py`, and the kei-17-corpus reference.
* Breadth across authors, periods, and structural styles.

## Child issues created

(See sub-issues below.)
```

Record the parent issue identifier (e.g., `KEI-NNN`) for use in Step 5.

## Step 5 — Create child issues

For each of the selected works, create a child issue under the parent from Step 4.

Every child issue must use this **exact description** — do not modify it per-work:

```markdown
Run a full live battle test of the CLI and parsing functionality for the work named in this issue title.

Template rule:

* Only customize the issue title, using `Gutenbit cli and parsing battle test: <work title>`.
* Infer the target work from the title. Resolve the Project Gutenberg ID from the catalog if needed.

Primary guide:

* Use the `gutenbit-live-battle-test` skill.

Start with:

* Resolve the PG ID for the work named in the issue title.
* `uv run gutenbit add <pg_id>`
* `uv run gutenbit toc <pg_id>`
* `uv run gutenbit search "<short distinctive query>" --book <pg_id>`
* `uv run gutenbit view <pg_id>`

Focus:

* Inspect `toc` first for missing, extra, or mis-nested sections.
* Compare any suspicious parser output against the raw Gutenberg HTML for the same PG work; treat that HTML as the truth.
* If a parser bug is found, implement the smallest generalizable fix possible. No book-specific or PG-ID-specific rules.
* Add a focused live regression in `tests/test_battle.py` if behavior needs to be locked in.
* Before closing, run `uv run pytest` and `uv run pytest -m network`.

Useful references:

* `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
* `tests/test_battle.py`
* `AGENTS.md`

Close-out should record either:

* no parser issue found, with a brief note on why output matches the source HTML, or
* the observed failure, the raw-HTML truth, the structural fix, the added regression test, and the verification run.
```

Each child issue must have:

- **Title**: `Gutenbit cli and parsing battle test: <work title> - id <pg_id>`
- **Team**: `Keinan`
- **Project**: `Gutenbit`
- **Labels**: `["Test"]`
- **Priority**: `2` (High)
- **Parent**: the parent issue identifier from Step 4

Do **not** add work-specific notes to the child issue body. Keep it identical across all
child issues — only the title changes.

## Step 6 — Close out the parent

After all child issues are created, leave a closing comment on the parent issue listing the
created child issues. Use this format:

```markdown
## Created child issues

| # | Work | PG ID | Issue |
|---|------|-------|-------|
| 1 | <title> | <pg_id> | <KEI-NNN> |
| 2 | ... | ... | ... |
...

## Deduplication

All <N> works confirmed absent from:
- Linear issues matching `Gutenbit cli and parsing battle test:`
- `tests/test_battle.py`
- `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
```

## Acceptance criteria

- Exactly `$ARGUMENTS` child issues are created under the parent.
- All works are English-language and previously unseen.
- All works are well-known literary works, not filler catalog entries.
- Every child issue has an identical description (only the title differs).
- The parent issue has a close-out comment listing all child issues and PG IDs.
