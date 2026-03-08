# CLI

The `gutenbit` command-line tool provides seven subcommands that follow a natural workflow: find books, download them, explore their structure, read text, and search.

All commands store data in a local SQLite file. Use `--db PATH` to specify a non-default location (default: `gutenbit.db`). All commands support `--json` for machine-readable output.

## catalog

Search the Project Gutenberg catalog for books by metadata.

```bash
gutenbit catalog --author "Dickens"
gutenbit catalog --title "Christmas" --author "Dickens"
gutenbit catalog --subject "Philosophy" -n 50
```

| Flag | Description |
|------|-------------|
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--subject TEXT` | Filter by subject (substring match) |
| `--language CODE` | Filter by language code (e.g. `en`) |
| `-n`, `--limit N` | Maximum results (default: 20) |
| `--json` | Output as JSON |

Filters combine with AND logic. All matching is case-insensitive. The catalog is fetched from Project Gutenberg on each call and filtered to English text records.

## ingest

Download books from Project Gutenberg and store them in the database.

```bash
gutenbit ingest 1342
gutenbit ingest 46 730 967
gutenbit ingest 2600 --delay 2.0
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

Full-text search across all stored books using SQLite FTS5 with BM25 ranking.

```bash
gutenbit search "battle"
gutenbit search "truth universally acknowledged" --phrase
gutenbit search "Levin" --book-id 1399 --mode first
gutenbit search "freedom" --kind text -n 5
gutenbit search "ghost" --full -n 3
```

| Flag | Description |
|------|-------------|
| `QUERY` | FTS5 search query (positional) |
| `--phrase` | Treat query as an exact phrase |
| `--mode MODE` | `ranked` (default), `first`, or `last` |
| `--author TEXT` | Filter by author (substring match) |
| `--title TEXT` | Filter by title (substring match) |
| `--book-id ID` | Restrict to a single book |
| `--kind KIND` | Filter by chunk kind: `heading` or `text` (`paragraph` is accepted as an alias for `text`) |
| `-n`, `--limit N` | Maximum results (default: 20 for ranked, 1 for first/last) |
| `--full` | Print full chunk text instead of previews |
| `--preview-chars N` | Preview length per result (default: 140) |
| `--json` | Output as JSON |

### Search modes

- **ranked**: Results ordered by BM25 relevance score, then book ID, then position.
- **first**: Earliest matches. Ordered by book ID ascending, then position ascending.
- **last**: Latest matches. Ordered by book ID descending, then position descending.

### FTS5 query syntax

The query is passed directly to SQLite FTS5. Supported syntax:

| Syntax | Meaning |
|--------|---------|
| `war peace` | Both terms (implicit AND) |
| `war OR peace` | Either term |
| `war NOT peace` | First term, excluding second |
| `"to be or not"` | Exact phrase |
| `philos*` | Prefix match |

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

Section numbers in the output can be passed to `view --section`.

## view

Read stored book text. Starts at the first structural section by default. Use selectors to focus on a specific part.

```bash
gutenbit view 1342                              # first structural section
gutenbit view 1342 -n 0                         # full text
gutenbit view 1342 --section 3                  # section by number
gutenbit view 1342 --section "Chapter 1" -n 10  # section by path
gutenbit view 1342 --position 50 -n 5           # from exact position
gutenbit view 1342 --section 3 --meta           # with metadata headers
gutenbit view 1342 --preview --chars 120        # concise previews
```

| Flag | Description |
|------|-------------|
| `BOOK_ID` | Project Gutenberg book ID (positional) |
| `--section SELECTOR` | Section number (from `toc`) or path prefix (e.g. `"BOOK I/CHAPTER I"`) |
| `--position N` | Exact chunk position |
| `-n N` | Chunks to return (default: 3 for opening, 1 for section/position; 0 = all) |
| `--preview` | Show truncated previews instead of full text |
| `--chars N` | Preview length when using `--preview` (default: 140) |
| `--meta` | Include chunk metadata headers in text output |
| `--json` | Output as JSON |

Use `--section` or `--position`, not both. Run `toc` first to see available section numbers.

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

## Global flags

These flags apply to all subcommands:

| Flag | Description |
|------|-------------|
| `--db PATH` | SQLite database path (default: `gutenbit.db`) |
| `-v`, `--verbose` | Enable debug logging |
