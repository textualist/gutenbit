<div class="homepage-identity">
  <h1 class="identity-wordmark">
    <span class="identity-wordmark__sr-only">gutenbit</span>
    <span class="tri" aria-hidden="true">
      <span class="layer red">gutenbit</span>
      <span class="layer blue">gutenbit</span>
      <span class="layer black">gutenbit</span>
    </span>
  </h1>
  <p class="identity-tagline">Fast local search across public-domain literary works. Find, browse, and search books from your terminal or Python script.</p>
</div>

## Install

Install the latest stable release from PyPI:

```bash
uv tool install gutenbit
```

Then run `gutenbit --help`. For one-off use without a persistent install:

```bash
uvx gutenbit --help
```

gutenbit stores its database and catalog cache in a `.gutenbit/` folder. Stable releases are published to PyPI from `vX.Y.Z` tags, while installs from the default branch remain development builds.

To try an unreleased development build from GitHub instead:

```bash
uvx --from git+https://github.com/textualist/gutenbit gutenbit --help
uv tool install git+https://github.com/textualist/gutenbit
```

To use `gutenbit` as a project dependency instead of a standalone CLI tool, prefer PyPI:

```bash
uv add gutenbit
```

Use `uv add git+https://github.com/textualist/gutenbit` only when you specifically need an unreleased branch or commit.

## CLI

```bash
gutenbit catalog --author "Austen, Jane"                              # find Pride and Prejudice
gutenbit add 1342                                                     # download and store it
gutenbit toc 1342                                                     # inspect numbered sections (default: 2 levels)
gutenbit view 1342                                                    # read the opening
gutenbit view 1342 --section 1 --forward 5                            # jump into chapter 1
gutenbit search "truth universally acknowledged" --book 1342 --phrase
gutenbit search "bennet" --book 1342 --limit 3 --radius 1             # read hits in context
```

All commands support `--json` for machine-readable output.
CLI-managed state is stored under `.gutenbit/` by default, including the database at
`.gutenbit/gutenbit.db` and the catalog cache under `.gutenbit/cache/`.

## Python

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
book = catalog.get(1342)

if book is not None:
    with Database(".gutenbit/gutenbit.db") as db:
        db.ingest([book])
        for hit in db.search("truth universally acknowledged", book_id=1342):
            print(hit.title, hit.div1, hit.content[:80])
```

## Next steps

- [Getting Started](getting-started.md) walks through a complete workflow.
- [CLI](cli.md) documents every subcommand and flag.
- [Python API](python-api.md) covers the library in full.
- [Concepts](concepts.md) explains how chunking, divisions, and search work.
- [API Reference](reference/index.md) has auto-generated module documentation.

## Project Gutenberg Access

gutenbit is an open-source project not affiliated with Project Gutenberg. It is for individual downloads, not bulk downloading. It prefers official mirrors and uses the main site only as a zip fallback, with a default `2.0` second delay between downloads. gutenbit also sends an identifying default `User-Agent` on Gutenberg and PGLAF requests: `gutenbit/<version> (+https://gutenbit.textualist.org)`. Review Project Gutenberg's [Robot Access Policy](https://www.gutenberg.org/policy/robot_access.html) and [Terms of Use](https://www.gutenberg.org/policy/terms_of_use.html).
