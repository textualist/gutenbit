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
catalog = Catalog.fetch()
books = catalog.search(author="Shakespeare", language="en")

# Download HTML, chunk, and store in SQLite
with Database("gutenberg.db") as db:
    db.ingest(books)

    # Retrieve cleaned text
    text = db.text(book_id=1661)

    # Full-text search with BM25 ranking
    results = db.search("to be or not to be")

    # Filter by metadata
    results = db.search("whale", author="melville", language="en")

    for r in results:
        print(f"[{r.title}] {r.div2} (score={r.score:.1f}, {r.char_count} chars)")
        print(r.content[:200])
```

Each `<p>` element in the HTML becomes its own chunk. Headings are detected via TOC links and tracked as div1–div4 structural divisions. Search results include the matching paragraph, its structural position, book metadata, character count, and a BM25 relevance score.

## Development

```bash
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## License

MIT
