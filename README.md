# Gutenbit

Fast local search across public-domain literary works. Find, browse, and search books from your terminal or Python script.

## Install

```bash
uv add gutenbit
```

## Python

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
books = catalog.search(author="Austen, Jane")

with Database("gutenbit.db") as db:
    db.ingest(books)
    for hit in db.search("pride"):
        print(hit.title, hit.div1, hit.content[:80])
```

## CLI

```bash
gutenbit catalog --author "Austen, Jane"
gutenbit add 1342
gutenbit search "pride"
gutenbit view 1342 --section 1 --forward 5
gutenbit search "truth universally acknowledged" --limit 3 --radius 1   # include surrounding passage
gutenbit view 1342 --section 1 --all                          # read the full section
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
