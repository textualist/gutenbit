# CLI

The `gutenbit` command-line tool provides seven subcommands that follow a natural workflow: find books, add them, explore their structure, read text, and search.

All commands store data in a local SQLite file. Use `--db PATH` to specify a non-default location (default: `gutenbit.db`). All commands support `--json` for machine-readable output.

## catalog

Search the Project Gutenberg catalog for books by metadata.

```bash
gutenbit catalog --author "Dickens"
gutenbit catalog --title "Christmas" --author "Dickens"
gutenbit catalog --subject "Philosophy" --limit 50
```

| Flag | Description |
|------|-------------|
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--subject TEXT` | Filter by subject (substring match) |
| `--language CODE` | Filter by language code (e.g. `en`) |
| `--limit N` | Maximum results (default: 20) |
| `--json` | Output as JSON |

Filters combine with AND logic. All matching is case-insensitive. The catalog is fetched from Project Gutenberg on each call and filtered to English text records.

## add

Download books from Project Gutenberg and store them in the database.

```bash
gutenbit add 1342
gutenbit add 46 730 967
gutenbit add 2600 --delay 2.0
```

| Flag | Description |
|------|-------------|
| `BOOK_IDS` | One or more Project Gutenberg IDs (positional) |
| `--delay SECONDS` | Pause between downloads (default: 1.0) |
| `--json` | Output as JSON |

Books already stored at the current chunker version are skipped. IDs that map to a different canonical edition are remapped automatically.

## books

List all books stored in the database.

```bash
gutenbit books
gutenbit books --json
```

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON |

## delete

Remove books and their chunks from the database.

```bash
gutenbit delete 1342
gutenbit delete 46 730 967
```

| Flag | Description |
|------|-------------|
| `BOOK_IDS` | One or more Project Gutenberg IDs (positional) |
| `--json` | Output as JSON |

Exits with code 1 if any requested ID was not found.

## search

Full-text search across stored books using SQLite FTS5 with BM25 ranking. Search
targets text chunks by default.

```bash
gutenbit search "battle"
gutenbit search "don't stop"                              # punctuation just works
gutenbit search "truth universally acknowledged" --phrase
gutenbit search "ghost OR spirit" --raw                   # FTS5 boolean query
gutenbit search "Levin" --book 1399 --mode first
gutenbit search "battle" --section "BOOK ONE" --book 2600
gutenbit search "STAVE" --book 46 --kind heading
gutenbit search "ghost" --radius 2                        # include surrounding passage
gutenbit search "ghost" --limit 3
gutenbit search "battle" --count
```

| Flag | Description |
|------|-------------|
| `QUERY` | Search query (positional) |
| `--phrase` | Treat query as an exact phrase (mutually exclusive with `--raw`) |
| `--raw` | Pass query directly to FTS5 for advanced syntax (mutually exclusive with `--phrase`) |
| `--mode MODE` | `ranked` (default), `first`, or `last` |
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--book ID` | Restrict to a single book |
| `--kind KIND` | Chunk kind to search: `text` (default), `heading`, or `all` |
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

### Search modes

- **ranked**: Results ordered by BM25 relevance score, then book, then position.
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

Show the structural table of contents for a stored book, with numbered sections.

```bash
gutenbit toc 1342
gutenbit toc 2600 --json
```

| Flag | Description |
|------|-------------|
| `BOOK_ID` | Project Gutenberg book ID (positional) |
| `--json` | Output as JSON |

Section numbers in the output can be passed to `view --section` or `search --section`.

## view

Read stored book text. Starts at the first structural section by default. Use selectors to focus on a specific part.

```bash
gutenbit view 1342                              # first structural section
gutenbit view 1342 --all                        # full book
gutenbit view 1342 --section 3                  # section by number
gutenbit view 1342 --section 3 --all            # full section
gutenbit view 1342 --section "Chapter 1" --forward 10  # section by path
gutenbit view 1342 --position 50 --forward 5           # from exact position
gutenbit view 1342 --position 50 --radius 2     # surrounding passage around position
gutenbit view 1342 --section 3 --radius 2       # surrounding passage around section start
```

| Flag | Description |
|------|-------------|
| `BOOK_ID` | Project Gutenberg book ID (positional) |
| `--section SELECTOR` | Section number (from `toc`) or path prefix (e.g. `"BOOK I/CHAPTER I"`) |
| `--position N` | Exact chunk position |
| `--all` | Read the full selected scope (whole book or whole section) |
| `--forward N` | Passages to read forward (default: 3 for opening, 1 for section/position) |
| `--radius N` | Surrounding passage to include on each side of the selected center passage |
| `--json` | Output as JSON |

Use `--section` or `--position`, not both. `--forward`, `--radius`, and `--all` are mutually exclusive in `view`. Use `--all` for a whole book or whole section; it does not apply to `--position`. Run `toc` first to see available section numbers.

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

When `ok` is `false`, the `errors` list contains error messages. The `data` field holds command-specific results. The `warnings` list captures non-fatal issues (e.g. a requested ID not found during bulk delete).

For `view`, the response body is content-first. Successful responses include a shared passage shape: `book`, `title`, `author`, `section`, `section_number`, `position`, `forward`, `radius`, `all`, and `content`.

For `search`, `data["filters"]` includes the resolved `kind`, and `data["items"]` remains the hit list. Each hit uses that same passage shape, with search-specific fields such as `kind`, `rank`, and `score` appended after the shared fields. When `--radius` is used, `content` is the joined surrounding passage in reading order.

## Global flags

These flags apply to all subcommands:

| Flag | Description |
|------|-------------|
| `--db PATH` | SQLite database path (default: `gutenbit.db`) |
| `-v`, `--verbose` | Enable debug logging |
