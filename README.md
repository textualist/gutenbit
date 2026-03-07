# gutenbit

Fast local search across Project Gutenberg's literary works. Download public-domain books, parse their HTML into paragraph-level chunks, and search them with SQLite FTS5.

## Install

```bash
pip install gutenbit
```

## Python

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
books = catalog.search(author="Austen")

with Database("gutenbit.db") as db:
    db.ingest(books)
    for hit in db.search("pride"):
        print(hit.title, hit.div1, hit.content[:80])
```

## CLI

```bash
gutenbit catalog --author "Austen"
gutenbit ingest 1342
gutenbit search "pride"
gutenbit view 1342 --section 1 -n 5
```

All commands support `--json` for machine-readable output.

## Documentation

Full documentation: [Getting Started](docs/getting-started.md) | [Python API](docs/python-api.md) | [CLI](docs/cli.md) | [Concepts](docs/concepts.md)

## Development

```bash
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## License

MIT
