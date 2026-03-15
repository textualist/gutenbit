# CLI

The `gutenbit` command-line tool provides seven subcommands that follow a natural workflow: find books, add them, explore their structure, read text, and search.

Start with `gutenbit --help` for the workflow overview, then use `gutenbit COMMAND --help` for command-specific flags and examples.

## catalog

Search the Project Gutenberg catalog for books by metadata.

```bash
gutenbit catalog --author "Dickens"
gutenbit catalog --title "Christmas" --author "Dickens"
gutenbit catalog --author "Dickens" --refresh
gutenbit catalog --subject "Philosophy" --limit 50
```

| Flag | Description |
|------|-------------|
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--subject TEXT` | Filter by subject (substring match) |
| `--language CODE` | Filter by language code (e.g. `en`) |
| `--limit N` | Maximum results (default: 20) |
| `--refresh` | Ignore the local catalog cache and redownload it now |
| `--json` | Output as JSON |

Filters combine with AND logic. All matching is case-insensitive. The catalog is cached locally for two hours, filtered to English text records, and can be forced to redownload with `--refresh`.

## add

Download books from Project Gutenberg and store them in the database.

```bash
gutenbit add 1342
gutenbit add 46 730 967
gutenbit add 1342 --refresh
gutenbit add 2600 --delay 2.0
```

| Flag | Description |
|------|-------------|
| `BOOK_IDS` | One or more Project Gutenberg IDs (positional) |
| `--delay SECONDS` | Pause between downloads (default: 2.0) |
| `--refresh` | Ignore the local catalog cache, redownload it now, and reprocess matching stored books |
| `--json` | Output as JSON |

Books already stored at the current parser version are skipped unless you pass `--refresh`, which also refreshes the catalog cache before reprocessing the requested book IDs. IDs that map to a different canonical edition are remapped automatically.

## books

List all books stored in the database, or refresh stored books whose parser version is stale.

```bash
gutenbit books
gutenbit books --json
gutenbit books --refresh
gutenbit books --refresh --force
gutenbit books --refresh --dry-run
gutenbit books --refresh 2600
```

| Flag | Description |
|------|-------------|
| `BOOK_IDS` | Optional positional book IDs to target with `--refresh` |
| `--refresh` | Reprocess stored books whose parser version is stale |
| `--delay SECONDS` | Pause between downloads in refresh mode (default: 2.0) |
| `--force` | Reprocess all stored books in refresh mode, even if already current |
| `--dry-run` | Show which stored books would be refreshed without downloading |
| `--json` | Output as JSON |

Without `--refresh`, `books` behaves exactly as before and just lists stored books.
With `--refresh`, gutenbit checks the local database and reprocesses only books whose
stored text is out of date for the current parser version. Pass one or more book IDs
to target specific books for refresh. `--force` refreshes every stored book, and
`--dry-run` reports what would be refreshed without doing any work.

## remove

Remove books from the database.

```bash
gutenbit remove 1342
gutenbit remove 46 730 967
```

| Flag | Description |
|------|-------------|
| `BOOK_IDS` | One or more Project Gutenberg IDs (positional) |
| `--json` | Output as JSON |

Exits with code 1 if any requested ID was not found.

## search

Full-text search across stored books using SQLite FTS5 with BM25 ranking. Search
targets paragraph chunks by default.

```bash
gutenbit search "bennet"
gutenbit search "don't stop"                              # punctuation is ok
gutenbit search "truth universally acknowledged" --phrase
gutenbit search "ghost OR spirit" --raw                   # FTS5 boolean query
gutenbit search "bennet" --book 1342 --order first
gutenbit search "truth universally acknowledged" --book 1342 --section 1 --phrase
gutenbit search "chapter" --book 1342 --kind heading
gutenbit search "bennet" --book 1342 --radius 1           # include surrounding passage
gutenbit search "bennet" --book 1342 --limit 3
gutenbit search "bennet" --book 1342 --count
```

| Flag | Description |
|------|-------------|
| `QUERY` | Search query (positional) |
| `--phrase` | Treat query as an exact phrase (mutually exclusive with `--raw`) |
| `--raw` | Pass query directly to FTS5 for advanced syntax (mutually exclusive with `--phrase`) |
| `--order ORDER` | `rank` (default), `first`, or `last` |
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--book ID` | Restrict to a single book |
| `--kind KIND` | Paragraph chunk kind to search: `text` (default), `heading`, or `all` |
| `--section SELECTOR` | Restrict to a section by path prefix or number from `toc` (number requires `--book`) |
| `--limit N` | Maximum results (default: 10) |
| `--radius N` | Surrounding passage to include on each side of each hit |
| `--count` | Just print the number of matches |
| `--json` | Output as JSON |

### Query modes

By default, punctuation in the query is auto-escaped so apostrophes, hyphens, and other punctuation just work. Tokens are implicitly AND'd.

- **(default)**: Plain text — punctuation is auto-escaped, words are AND'd.
- **--phrase**: Exact phrase — word order and adjacency must match exactly.
- **--raw**: FTS5 syntax — AND, OR, NOT, NEAR(), prefix\*, "phrases", (groups).

### Search order

- **rank**: Results ordered by BM25 relevance score, then book, then position.
- **first**: Earliest matches. Ordered by book ascending, then position ascending.
- **last**: Latest matches. Ordered by book descending, then position descending.

### Result shaping

- Use `--limit` to control how many hits are returned. The default is 10.
- Use `--radius` to read surrounding passage around each hit in normal reading order.
- `--count` cannot be combined with `--radius`.
- Use `--kind heading` to search structural headings, or `--kind all` to include both headings and text.

### FTS5 query syntax

When using `--raw`, the query is passed directly to SQLite FTS5. Supported syntax:

| Syntax | Meaning |
|--------|---------|
| `war peace` | Both terms (implicit AND) |
| `war OR peace` | Either term |
| `war NOT peace` | First term, excluding second |
| `"to be or not"` | Exact phrase |
| `philos*` | Prefix match |
| `NEAR(war peace, 5)` | Terms within 5 tokens of each other |
| `(war OR battle) AND peace` | Grouped boolean logic |

Use `--phrase` to auto-wrap the entire query as an exact phrase without manual quoting.

## toc

Show the structural table of contents for a book, with numbered sections. If the
book is not stored yet, `toc` adds it automatically first and follows canonical
ID remaps. By default the table shows two heading levels; use `--expand` to
collapse further or reveal all nested levels.

```bash
gutenbit toc 1342
gutenbit toc 100 --expand 1
gutenbit toc 100 --expand all
gutenbit toc 2600 --json
```

| Flag | Description |
|------|-------------|
| `BOOK_ID` | Project Gutenberg book ID (positional) |
| `--expand {1,2,3,4,all}` | Show heading levels up to this depth (default: `2`; `all` shows every stored level) |
| `--json` | Output as JSON |

Collapsed rows roll hidden descendants into the lowest shown level. For example, with `--expand 2`, visible act rows include the stats for their hidden scenes. Section numbers remain stable and can be passed to `view --section` or `search --section`.

## view

Read stored book text. Starts at the first structural section by default. Use selectors to focus on a specific part.

```bash
gutenbit view 1342                              # first structural section
gutenbit view 1342 --all                        # full book
gutenbit view 1342 --section 1                  # section by number
gutenbit view 1342 --section 1 --all            # full section, including nested subsections
gutenbit view 1342 --section "Chapter 1" --forward 10  # section by path
gutenbit view 100 --section "ALL’S WELL THAT ENDS WELL / ACT I" --all  # full act incl. scenes
gutenbit view 1342 --position 1 --forward 5           # from exact position
gutenbit view 1342 --position 1 --radius 2     # surrounding passage around position
gutenbit view 1342 --section 1 --radius 2       # surrounding passage around section start
```

| Flag | Description |
|------|-------------|
| `BOOK_ID` | Project Gutenberg book ID (positional) |
| `--section SELECTOR` | Section number (from `toc`) or path prefix (e.g. `"BOOK I/CHAPTER I"`) |
| `--position N` | Exact paragraph chunk position |
| `--all` | Read the full selected scope (whole book or selected section, including nested subsections) |
| `--forward N` | Passages to read forward (default: 3 for opening, 1 for section/position) |
| `--radius N` | Surrounding passage to include on each side of the selected center passage |
| `--json` | Output as JSON |

Use `--section` or `--position`, not both. `--forward`, `--radius`, and `--all` are mutually exclusive in `view`. Use `--all` for a whole book or selected section subtree; choosing a parent section such as a play or act includes its nested descendants. `--all` does not apply to `--position`. Run `toc` first to see available section numbers.

## JSON output

Every command accepts `--json` and returns a unified envelope:

```json
{
  "ok": true,
  "command": "search",
  "data": { ... },
  "warnings": [],
  "errors": []
}
```

When `ok` is `false`, the `errors` list contains error messages. The `data` field holds command-specific results. The `warnings` list captures non-fatal issues (e.g. a requested ID not found during bulk remove).

For `view`, the response body is content-first. Successful responses include a shared passage shape: `book`, `title`, `author`, `section`, `section_number`, `position`, `forward`, `radius`, `all`, and `content`.

For `search`, `data["order"]` records the selected result order, `data["filters"]` includes the resolved `kind`, and `data["items"]` remains the hit list. Each hit uses that same passage shape, with search-specific fields such as `kind`, `rank`, and `score` appended after the shared fields. When `--radius` is used, `content` is the joined surrounding passage in reading order.

## Global flags

These flags apply to all subcommands:

| Flag | Description |
|------|-------------|
| `--db PATH` | SQLite database path (default: `~/.gutenbit/gutenbit.db`) |
| `-v`, `--verbose` | Enable debug logging |
