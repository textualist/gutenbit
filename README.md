# Gutenbit

Fast local search across public-domain literary works. Find, browse, and search books from your terminal or Python script.

## Install

Gutenbit is not published on PyPI yet, so the quickest way to try it is to run it directly from the GitHub repo:

```bash
uvx --from git+https://github.com/keinan1/gutenbit gutenbit --help
```

If you want to keep it installed for repeated use:

```bash
uv tool install git+https://github.com/keinan1/gutenbit
```

Then run `gutenbit --help`. Remove it later with `uv tool uninstall gutenbit`.

If this is your first `uv`-managed tool, run `uv tool update-shell` once and restart your shell so `gutenbit` is on your `PATH`.

To use `gutenbit` as a project dependency instead of a standalone CLI tool:

```bash
uv add git+https://github.com/keinan1/gutenbit
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
gutenbit books --update                               # refresh stale stored books
gutenbit search "pride"                                     # text chunks by default
gutenbit search "chapter" --book 1342 --kind heading       # search headings only
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
