---
name: cli-api-review
description: >
  Review Gutenbit CLI commands for consistency, ergonomics, and alignment with the project's
  established design patterns. Use this skill whenever the user asks to "review the CLI API",
  "check cli consistency", "audit the command design", "review cli ergonomics", or when filing
  or working on a Linear issue prefixed with "cli api:" or "cli ergonomics:". Also trigger for
  requests like "is the cli consistent?", "what cli issues should we fix?", "review the help
  text", or "check our verb choices across commands".
---

# CLI API Review

This skill audits the Gutenbit CLI for internal consistency, ergonomic clarity, and alignment
with the design patterns the project has established over time. The goal is a CLI that feels
like one coherent tool — where a user who knows one command can correctly guess how the others
behave.

## Step 1 — Capture the current CLI surface

Run these from the project root:

```bash
uv run gutenbit --help
uv run gutenbit add --help
uv run gutenbit toc --help
uv run gutenbit view --help
uv run gutenbit search --help
uv run gutenbit books --help
# Add any other subcommands that appear in --help output
```

Save the full output. This is your source of truth.

## Step 2 — Check verb and flag consistency

Gutenbit's established conventions (as of the current codebase):

| Pattern | Correct | Avoid |
|---|---|---|
| Reprocessing/updating | `--update` | `--refresh`, `--reprocess`, `--reload` |
| Showing all results | `--all` | `--full`, `--complete`, `--everything` |
| Filtering by book | `--book <id>` | `--book-id`, `--id`, `-b` (without long form) |
| Section reference | `--section <N>` | `--chapter`, `--part`, `--chunk` |

Check every flag across every subcommand for these patterns. Flag any deviations. If a new
convention seems like an improvement over the established one, note it explicitly as a
"proposed convention change" rather than a plain inconsistency — that distinction matters.

## Step 3 — Check auto-behavior conventions

A key ergonomic principle in Gutenbit: commands should "do the right thing" rather than make
the user jump through hoops. Known patterns:

- **Auto-add**: If a user runs `toc <id>` for a book not in the DB, the command should add it
  automatically rather than prompt. The user asking for a TOC implies they want the book.
- **ID remapping**: If a user passes a non-canonical PG ID that resolves to a different
  canonical ID, commands should follow the remap silently and succeed.
- **Multi-ID input**: Where it makes sense (e.g., `search`), commands should accept multiple
  `--book` IDs at once.

Check each command: does it apply these principles? Where it doesn't, note whether the gap is
a bug, a missing feature, or a deliberate design choice.

## Step 4 — Review help text quality

Good help text is the entry portal for new users. Check:

- **Description lines**: Clear, concise, jargon-free? Does the top-level `--help` describe
  what Gutenbit does in a way that a newcomer immediately understands?
- **Option descriptions**: Do they say what the option *does*, not just what it *is*?
  (e.g., "reprocess stored books" not "update flag")
- **Formatting artifacts**: Any `{curly_braces}`, raw variable names, or unparsed template
  strings showing through?
- **Consistent capitalization and punctuation** across all help strings?
- **Output labels**: Are search/view results labeled clearly? (e.g., "Section No. 181" vs
  bare "No. 181")

## Step 5 — Write the review report

Structure your findings like this:

```
## CLI API Review — <date>

### Summary
<2–3 sentence overview of overall consistency and top concerns>

### Inconsistencies Found
For each issue:
- **[COMMAND] [FLAG/BEHAVIOR]**: Description of the inconsistency
  - Current behavior: ...
  - Expected (per convention): ...
  - Suggested fix: ...
  - Severity: low / medium / high

### Auto-Behavior Gaps
<Same format as above>

### Help Text Issues
<Same format>

### Proposed Convention Changes
<List any places where the existing convention itself seems worth revisiting, with rationale>

### Recommended Linear Issues
<For each finding that warrants a ticket, suggest a title following the "cli api: ..." or
"cli ergonomics: ..." naming pattern the project uses>
```

If this review was triggered in the context of an existing Linear issue, post the report as
a comment and link any newly created follow-up issues.
