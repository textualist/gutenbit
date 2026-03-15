<div class="homepage-identity">
  <h1 class="identity-wordmark">
    <span class="identity-wordmark__sr-only">gutenbit</span>
    <span class="tri" aria-hidden="true">
      <span class="layer red">gutenbit</span>
      <span class="layer blue">gutenbit</span>
      <span class="layer black">gutenbit</span>
    </span>
  </h1>
  <p class="identity-tagline">A command line tool for fast local search across public-domain literary works. Find, browse, and search books from your terminal or Python script.</p>
</div>

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
gutenbit catalog --author "Austen, Jane"
gutenbit add 1342
```

Inspect the table of contents, read the opening, or jump into a section.

```bash
gutenbit toc 1342
gutenbit view 1342
gutenbit view 1342 --section 1 --forward 5
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

## Next steps

- [Getting Started](getting-started.md) walks through a complete workflow.
- [CLI](cli.md) documents every subcommand and flag.
- [Python API](python-api.md) covers the library in full.
- [Concepts](concepts.md) explains how chunking, divisions, and search work.
- [API Reference](reference/index.md) has auto-generated module documentation.

## Project Gutenberg Access

gutenbit is an open-source project not affiliated with Project Gutenberg. It is for individual downloads, not bulk downloading. It prefers official mirrors and uses the main site only as a fallback, with a default 2 second delay between downloads. gutenbit also sends an identifying default `User-Agent` on Gutenberg requests. Review Project Gutenberg's [Robot Access Policy](https://www.gutenberg.org/policy/robot_access.html) and [Terms of Use](https://www.gutenberg.org/policy/terms_of_use.html).
