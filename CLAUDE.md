# gutenbit

## Project overview

ETL package for Project Gutenberg: download, parse, and store texts in SQLite.

## Development setup

```bash
uv sync
```

## Commands

- `uv run pytest` — Run tests
- `uv run ruff check .` — Lint
- `uv run ruff format --check .` — Check formatting
- `uv run ty check` — Type check

## Architecture

- `gutenbit/catalog.py` — CSV catalog fetch and search
- `gutenbit/download.py` — Text download and header/footer stripping
- `gutenbit/chunker.py` — Text chunking with kind-labelled preservation of all blocks
- `gutenbit/db.py` — SQLite storage with FTS5 search

### Chunker design

Every text block separated by blank lines is preserved as a `Chunk` with a `kind` label:

- `"paragraph"` — substantive prose (≥ 50 chars)
- `"heading"` — chapter/section headings (also updates the running chapter label)
- `"short"` — short text that isn't a heading or separator (dialogue, brief paragraphs)
- `"separator"` — decorative rules, dinkuses (`* * *`, `---`, etc.)

Nothing is discarded. Users can reconstruct the full original text from all chunks
in position order, or filter to just `paragraph` + `short` for clean prose, etc.

## Test corpus

Tests use excerpts from four Dickens novels (Project Gutenberg IDs):

- **The Pickwick Papers** — PG 580
- **Oliver Twist** — PG 730
- **The Old Curiosity Shop** — PG 700
- **Nicholas Nickleby** — PG 967

## Style

- Modern Python (3.11+), type-annotated
- Keep it simple — stdlib where possible, minimal dependencies
- No unnecessary abstractions
