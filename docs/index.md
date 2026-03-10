# Gutenbit

Fast local search across Project Gutenberg's literary works.

Gutenbit downloads public-domain books, parses their HTML into paragraph-level chunks with structural metadata, and stores everything in a local SQLite database with full-text search.

## Install

Gutenbit is not published on PyPI yet, so the fastest way to sample the CLI is:

```bash
uvx --from git+https://github.com/keinan1/gutenbit gutenbit --help
```

Install it persistently when you want the `gutenbit` command on your `PATH`:

```bash
uv tool install git+https://github.com/keinan1/gutenbit
```

Then run `gutenbit --help`. Remove it later with `uv tool uninstall gutenbit`.

If `gutenbit` is not found after install, run `uv tool update-shell` once and restart your shell.

## Python in 30 seconds

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
books = catalog.search(title="Pride and Prejudice")

with Database("gutenbit.db") as db:
    db.ingest(books)
    for hit in db.search("truth universally acknowledged"):
        print(hit.title, hit.div1, hit.content[:100])
```

## CLI in 30 seconds

```bash
gutenbit catalog --title "Pride and Prejudice"
gutenbit add 1342
gutenbit search "truth universally acknowledged"            # text chunks by default
gutenbit search "chapter" --book 1342 --kind heading
gutenbit view 1342 --section 1 --forward 5
gutenbit search "truth universally acknowledged" --limit 3 --radius 1
```

## Next steps

- [Getting Started](getting-started.md) walks through a complete workflow.
- [Python API](python-api.md) covers the library in full.
- [CLI](cli.md) documents every subcommand and flag.
- [Concepts](concepts.md) explains how chunking, divisions, and search work.
- [API Reference](reference/index.md) has auto-generated module documentation.
