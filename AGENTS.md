# gutenbit

HTML-first Project Gutenberg ETL and search. The core loop is: resolve a catalog entry, download the Gutenberg HTML, chunk it into structural headings plus paragraph chunks, store it in SQLite, and expose it through the CLI and Python API.

## Commands

- `uv run pytest` — default test suite; excludes live Gutenberg network tests
- `uv run pytest -m network` — live parser regression corpus; run for parser behavior changes
- `uv run ruff check .` — lint
- `uv run ruff format --check .` — format check
- `uv run ty check` — type check

## Code map

- `gutenbit/catalog.py` — Gutenberg catalog fetch and search
- `gutenbit/download.py` — HTML download from Gutenberg epub zips
- `gutenbit/html_chunker/` — structural parsing and chunk generation
- `gutenbit/db.py` — SQLite storage and FTS search
- `gutenbit/cli/` — CLI surface (Click commands, display, section summaries, JSON output)
- `tests/test_battle.py` — live parser regression corpus

## Parsing model

- Prefer the Gutenberg HTML as the source of truth.
- The parser is HTML-only. It uses TOC links when available, with body-heading fallback when needed.
- Each paragraph becomes its own chunk. Do not merge paragraphs unless the design changes deliberately.
- Hierarchy is compacted into `div1` / `div2` / `div3` based on actual structure, not fixed heading levels.

## Working rules

- Keep fixes tight, clear, and generalizable. No PG-ID-specific, title-specific, or ad hoc parser rules.
- For parser or CLI issues, verify live first with `gutenbit add`, `toc`, `view`, and `search`.
- When output looks wrong, compare it against the raw Gutenberg HTML for the same edition before changing code.
- Add focused regressions that capture the structural invariant. Avoid broad snapshots unless the full shape is the invariant.
- If behavior changes, update tests and docs in the same pass.

## Battle tests

- Use `$gutenbit-live-battle-test` from `.codex/skills/gutenbit-live-battle-test/SKILL.md` for live parsing and CLI battle tests.
- Use `.codex/skills/gutenbit-live-battle-test/references/kei-17-corpus.md` to classify known parser failure families and mirror the existing network-test style.
- For parser fixes, finish by running `uv run pytest` and `uv run pytest -m network`.

## Style

- Modern Python, explicit types, minimal dependencies.
- Prefer simple, local reasoning over clever abstractions.
- Preserve CLI/API consistency; if a name or behavior changes, update tests, help text, README, and docs.
