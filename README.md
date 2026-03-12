# Gutenbit

Fast local search across public-domain literary works. Find, browse, and search books from your terminal or Python script.

## Install

Gutenbit is not published on PyPI yet, so the quickest way to try it is to run it directly from the GitHub repo:

```bash
uvx --from git+https://github.com/textualist/gutenbit gutenbit --help
```

If you want to keep it installed for repeated use:

```bash
uv tool install git+https://github.com/textualist/gutenbit
```

Then run `gutenbit --help`. Remove it later with `uv tool uninstall gutenbit`.
Gutenbit stores its database and catalog cache in a `.gutenbit/` folder.
Installs from the default branch are development builds. Stable releases are the tagged GitHub releases in the `vX.Y.Z` format.
To use `gutenbit` as a project dependency instead of a standalone CLI tool:

```bash
uv add git+https://github.com/textualist/gutenbit
```

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

## Documentation

Full documentation: [Getting Started](docs/getting-started.md) | [Python API](docs/python-api.md) | [CLI](docs/cli.md) | [Concepts](docs/concepts.md)

## Project Gutenberg Access

Gutenbit is an open-source project not affiliated with Project Gutenberg. It is for individual downloads, not bulk downloading. It prefers official mirrors and uses the main site only as a zip fallback, with a default `2.0` second delay between downloads. Gutenbit also sends an identifying default `User-Agent` on Gutenberg and PGLAF requests: `gutenbit/<version> (+https://gutenbit.textualist.org)`. Review Project Gutenberg's [Robot Access Policy](https://www.gutenberg.org/policy/robot_access.html) and [Terms of Use](https://www.gutenberg.org/policy/terms_of_use.html).

## Development

```bash
uv run pytest                    # fast local suite (excludes live Gutenberg downloads)
uv run pytest -m network         # live parser regression corpus against Gutenberg
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run ty check                  # type check
```

## Releases

Versioning is tag-driven via `hatch-vcs`. Merging to `main` does not create a release or require a manual version bump, and installs from `main` are development builds. Cut a release by creating a GitHub tag or release such as `v0.1.6` on the target `main` commit; the release workflow will build and attach the wheel and sdist to GitHub Releases. Do not edit version strings in source files. See [RELEASING.md](RELEASING.md).

## License

MIT
