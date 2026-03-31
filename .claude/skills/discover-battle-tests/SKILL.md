---
name: discover-battle-tests
description: >
  Discover new Project Gutenberg works and create a templated GitHub Issue for battle testing.
  Use when the user asks to "discover N new battle tests", "find new battle test candidates",
  "create battle test issues", "add more battle tests", or any variation of
  "discover/find/create N battle test issues". The user specifies how many to discover
  (default 10). This skill handles discovery and issue creation only — it does not run the
  battle tests themselves.
argument-hint: "[count]"
---

# Discover Battle Tests

Create a single GitHub Issue on `textualist/gutenbit` containing exactly `$ARGUMENTS`
(default: 10) new works for live CLI/parsing battle tests. This skill is discovery and
issue-creation only — do not run the battle tests.

## Goal

Identify `$ARGUMENTS` **new**, previously unseen Project Gutenberg works in English,
prioritizing canonical and widely known literature, and file them as a checklist in a GitHub
Issue.

## Step 1 — Build the exclusion set

Before selecting candidates, gather every PG ID and work already covered. A work is "seen"
if it appears in **any** of these sources:

1. **GitHub Issues**: Search for issues on `textualist/gutenbit` with the `test` label whose
   title contains `battle test`. Use `mcp__github__search_issues` or
   `mcp__github__list_issues`. Parse the issue body for PG IDs — parent battle-test issues
   use a checklist format with `- [x] Work Title — PG XXXX` lines.

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
  - anthology or multi-work collections (tests deduplication and series heading handling)
  - standalone short stories with title-block author attribution (tests byline filtering)

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

## Step 4 — Create the parent GitHub Issue

Create a single issue on `textualist/gutenbit` using `mcp__github__issue_write`:

- **Labels**: `["test"]`
- **Title**: `Battle test <N> new English works` (or `Battle test all works of <Author>` when
  the batch focuses on a single author)
- **Body**: Use this template, replacing `<N>` with the count and filling in the checklist:

```markdown
Run a full live battle test of the CLI and parsing functionality for each work below.

## Selection criteria

* English-language Project Gutenberg works only.
* Prioritize major, well-known works of literature.
* Each work is confirmed absent from existing GitHub battle-test issues,
  `tests/test_battle.py`, and the kei-17-corpus reference.
* Breadth across authors, periods, and structural styles.

## Works (<N> total)

- [ ] Work Title 1 — PG XXXX
- [ ] Work Title 2 — PG XXXX
...

## Guide

Use the `gutenbit-live-battle-test` skill for each work. Start with:

```
uv run gutenbit add <pg_id>
uv run gutenbit toc <pg_id> --expand all
uv run gutenbit toc <pg_id> --expand all --json
uv run gutenbit search "" --book <pg_id>
uv run gutenbit view <pg_id>
```

Focus on `toc --expand all` for missing, extra, or mis-nested sections. Compare suspicious
output against the raw Gutenberg HTML (ground truth). If a parser bug is found, implement
the smallest generalizable fix — no book-specific rules. Add a focused regression test if
needed (prefer synthetic non-network tests; reserve network tests for high-value cases).
Before closing each work, run `uv run pytest` and `uv run pytest -m network`.

## References

- `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
- `tests/test_battle.py`
- `AGENTS.md`

## Close-out

For each work, check the box and annotate with either:
- ✅ if no parser issue found, with a brief note
- ⚠️ if a known limitation exists (source HTML defect, pre-existing issue family), with a note
- 🔧 if a parser fix was applied, referencing the commit or PR

Post a summary comment with structured results tables (clean passes, issues found, fixes
applied) before closing the issue.
```

Record the issue number (e.g., `#NNN`) for the close-out step.

## Step 5 — Verify the issue

After creating the issue:

1. Confirm the issue was created with the correct title, body, and `test` label.
2. Verify the checklist contains exactly `$ARGUMENTS` works.
3. Verify all PG IDs in the checklist are valid and not in the exclusion set.

## Step 6 — Close out

Leave a summary comment on the issue listing the selected works. Use this format with
`mcp__github__add_issue_comment`:

```markdown
## Selected works

| # | Work | PG ID | Parser-coverage value |
|---|------|-------|---------------------|
| 1 | <title> | <pg_id> | <note> |
| 2 | ... | ... | ... |
...

## Deduplication

All <N> works confirmed absent from:
- GitHub Issues with `test` label on `textualist/gutenbit`
- `tests/test_battle.py`
- `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
```

## Acceptance criteria

- A single GitHub Issue is created on `textualist/gutenbit` with the `test` label.
- The issue body contains a checklist with exactly `$ARGUMENTS` works.
- All works are English-language and previously unseen.
- All works are well-known literary works, not filler catalog entries.
- The issue has a close-out comment listing all works with PG IDs and parser-coverage notes.
