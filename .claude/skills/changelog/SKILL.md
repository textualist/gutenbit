---
name: changelog
description: >
  Generate or update a project changelog following the Keep a Changelog standard
  (keepachangelog.com) and Semantic Versioning (semver.org). Use this skill whenever the user
  asks to "create the changelog", "update the changelog", "generate release notes", "write the
  CHANGELOG.md", or "add a new changelog entry". Also trigger for requests like "what changed
  in this release", "draft the v1.x changelog", or "document what's new since the last version".
---

# Changelog

This skill produces a `CHANGELOG.md` that follows the [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
standard with [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The core principle: the changelog is for humans, not git diffs. Every entry should describe
what changed from a user's perspective, not the implementation detail.

## Step 1 — Understand the scope

Clarify with the user (or infer from context):
- Are we creating a fresh `CHANGELOG.md`, or appending a new release entry to an existing one?
- What version are we documenting? (Check `git tag --sort=-version:refname | head -5` and the
  current version in `pyproject.toml`, `package.json`, or the project's version command)
- What's the date range? (Since the last tag, since a specific commit, or all time?)

## Step 2 — Gather changes

Run these in the project root to collect raw material:

```bash
# Get recent commits since last tag
git log $(git describe --tags --abbrev=0)..HEAD --oneline --no-merges

# Or all commits if starting fresh
git log --oneline --no-merges | head -100

# See what tags exist
git tag --sort=-version:refname | head -10
```

Also check issue trackers (Linear, GitHub Issues, Jira, etc.) for recently closed issues —
they often contain the clearest description of what changed from the user's perspective.

## Step 3 — Categorize changes

Map each commit or issue into one of these Keep a Changelog sections. Use your judgment — the
categories are about user impact, not code location:

| Section | When to use |
|---|---|
| **Added** | New features, new commands, new flags, new behaviors the user can now do |
| **Changed** | Changes to existing behavior, renames, updated defaults, UX improvements |
| **Deprecated** | Features that still work but will be removed in a future release |
| **Removed** | Features, flags, or commands that no longer exist |
| **Fixed** | Bug fixes — things that were broken and now work correctly |
| **Security** | Security patches, vulnerability fixes, compliance changes |

Entries within each section should be written as concise imperative sentences from the user's
perspective: "Add `--output` flag to `export` command" not "Implemented OutputFormat parameter
in ExportHandler".

Skip purely internal changes (refactors with no user-facing effect, CI tweaks, test
infrastructure) unless they affect performance in a measurable way.

## Step 4 — Determine the version number

Use semantic versioning rules:
- **Patch** (x.x.N): Only bug fixes, no new features, no breaking changes
- **Minor** (x.N.0): New features added in a backward-compatible way
- **Major** (N.0.0): Breaking changes (removed commands, changed flag behavior, etc.)

Check the last release version: `git describe --tags --abbrev=0`

## Step 5 — Write the changelog entry

Use this exact format:

```markdown
## [<version>] - <YYYY-MM-DD>

### Added
- <entry>
- <entry>

### Changed
- <entry>

### Fixed
- <entry>
```

Only include sections that have entries — omit empty ones.

For a fresh `CHANGELOG.md`, add the header:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [<version>] - <YYYY-MM-DD>
...
```

The `[Unreleased]` section at the top accumulates changes since the latest release. Leave it
empty if there's nothing yet.

## Step 6 — Write the file

Write or update `CHANGELOG.md` at the project root. If updating, insert the new entry
immediately after the `## [Unreleased]` section (or at the top if there's no Unreleased block).

Preserve all existing entries exactly — only add, never rewrite history.

## Output checklist

- [ ] Version number follows semver
- [ ] Date is accurate (today's date for a new release, or the tag date for historical)
- [ ] Each entry is written from the user's perspective
- [ ] No internal implementation details in user-facing entries
- [ ] Empty sections are omitted
- [ ] `[Unreleased]` section is present at the top
- [ ] Links to PRs or issues are included where useful (optional but helpful)
