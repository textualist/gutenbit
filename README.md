# gutenbit

Download, parse, and store [Project Gutenberg](https://www.gutenberg.org/) HTML texts in SQLite.

## Install

```bash
uv sync
```

## Usage

```python
from gutenbit import Catalog, Database

# Fetch the catalog and search for books
# (catalog is pre-filtered to English Text records)
catalog = Catalog.fetch()
books = catalog.search(author="Shakespeare")

# Download HTML, chunk, and store in SQLite
with Database("gutenberg.db") as db:
    db.ingest(books)

    # Retrieve cleaned text
    text = db.text(book_id=1661)

    # Full-text search with BM25 ranking
    results = db.search("to be or not to be")

    # Filter by metadata
    results = db.search("whale", author="melville")

    for r in results:
        print(f"[{r.title}] {r.div2} (score={r.score:.1f}, {r.char_count} chars)")
        print(r.content[:200])
```

Each `<p>` element in the HTML becomes its own chunk. Headings are detected via TOC links and tracked as div1–div4 structural divisions. Search results include the matching paragraph, its structural position, book metadata, character count, and a BM25 relevance score.

## CLI JSON interface

All CLI commands support `--json` and emit a unified envelope:

```json
{
  "ok": true,
  "command": "search",
  "data": {},
  "warnings": [],
  "errors": []
}
```

For `search --mode`, ordering semantics are explicit:
- `ranked`: BM25 rank, then `book_id`, then position
- `first`: `book_id` ascending, then position ascending
- `last`: `book_id` descending, then position descending

## CLI ergonomics

- `gutenbit toc <book_id>`: structural summary and numbered sections
- `gutenbit view <book_id>`: opening excerpt (safe default for large books)
- `gutenbit view <book_id> -n 0`: full reconstructed text
- `gutenbit view <book_id> --section <N>`: view by section number from `toc`
- `gutenbit view <book_id> --position <N>`: view by exact chunk position
- `gutenbit view ... -n <N>`: unified chunk count for opening/section/position (`-n 0` = all in scope)
- `gutenbit view ... --preview --chars 120`: concise previews
- `gutenbit view ... --meta`: include position/section metadata in text output

## Corpus boundaries

`gutenbit` enforces a curated ingestion policy in `gutenbit/catalog.py`:
- English-language records only (`en`)
- Text media only (`Type=Text`)
- Duplicate work entries are collapsed to a canonical record (lowest Gutenberg ID)

The constants are hard-coded for this package's English-text scope, but are
explicit and centralized so they can be adjusted if requirements change.

## Development

```bash
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## Documentation

```bash
uv sync --group dev --extra docs
uv run hatch run docs:build
uv run hatch run docs:serve
uv run hatch run docs:check
```

## License

MIT
