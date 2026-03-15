<p align="center">
  <br>
  <img src="assets/brand/gutenbit-brand-readme.png" alt="gutenbit brand mark" width="230">
  <br>
  <br>
  <em>A command line tool for fast local search across public-domain literary works.<br>Find, browse and search books from your terminal.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/gutenbit/"><img src="https://img.shields.io/pypi/v/gutenbit?color=%2334D058" alt="PyPI version"></a>
  <a href="https://pypi.org/project/gutenbit/"><img src="https://img.shields.io/badge/python-3.11%2B-%2334D058" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/textualist/gutenbit" alt="License"></a>
  <a href="https://gutenbit.textualist.org"><img src="https://img.shields.io/badge/docs-site-%2334D058" alt="Docs site"></a>
</p>

## CLI Install

Try the latest stable release from PyPI without a persistent install:

```bash
uvx gutenbit --help
```

Or install it like this and then run `gutenbit --help`:

```bash
uv tool install gutenbit
```

gutenbit stores its database and catalog cache in `~/.gutenbit/`.

## CLI Example

Find a book in the Project Gutenberg catalog and download it locally. Sections are parsed automatically during import.

```bash
gutenbit catalog --author "Jane Austen"
gutenbit add 1342
```

Inspect the table of contents, read the opening, or jump into a section. If a
book is missing locally, `gutenbit toc <id>` adds it automatically first.

```bash
gutenbit toc 1342
gutenbit view 1342
gutenbit view 1342 --section 2 --forward 5
```

Search within the book and read exact matches or nearby context.

```bash
gutenbit search "truth universally acknowledged" --book 1342 --phrase
gutenbit search "bennet" --book 1342 --limit 3 --radius 1
```

All commands support `--json` for machine-readable output.
CLI-managed state is stored under `~/.gutenbit/` by default, including the database at
`~/.gutenbit/gutenbit.db` and the catalog cache under `~/.gutenbit/cache/`.

## Python

gutenbit can also be used as a python module. Add it to your project with:

```bash
uv add gutenbit
```

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
book = catalog.get(1342)

if book is not None:
    with Database("~/.gutenbit/gutenbit.db") as db:
        db.ingest([book])
        for hit in db.search("truth universally acknowledged", book_ids=[1342]):
            print(hit.title, hit.div1, hit.content[:80])
```

## Documentation

Full documentation: [Getting Started](docs/getting-started.md) | [CLI](docs/cli.md) | [Python API](docs/python-api.md) | [Concepts](docs/concepts.md)

## Project Gutenberg Access

gutenbit is an open-source project not affiliated with Project Gutenberg. It is for individual downloads, not bulk downloading. It prefers official mirrors and uses the main site only as a fallback, with a default 2 second delay between downloads. gutenbit also sends an identifying default `User-Agent` on Gutenberg requests. Review Project Gutenberg's [Robot Access Policy](https://www.gutenberg.org/policy/robot_access.html) and [Terms of Use](https://www.gutenberg.org/policy/terms_of_use.html).

## Development

```bash
uv run pytest                    # fast local suite (excludes live Gutenberg downloads)
uv run pytest -m network         # live parser regression corpus against Gutenberg
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## License

MIT
