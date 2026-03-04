# gutenbit

Download, parse, and store [Project Gutenberg](https://www.gutenberg.org/) texts in SQLite.

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

# Download texts and store them in SQLite
with Database("gutenberg.db") as db:
    db.ingest(books)

    # Retrieve cleaned text
    text = db.text(book_id=1661)
```

## Development

```bash
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## License

MIT
