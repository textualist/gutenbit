# Getting Started

This guide walks through a complete workflow: find a book, download it, explore its structure, and search its text. Both CLI and Python examples use *Pride and Prejudice* (Project Gutenberg ID 1342).

## Installation

Try the latest stable release from PyPI without a persistent install:

```bash
uvx gutenbit --help
```

Or install it like this and then run `gutenbit --help`:

```bash
uv tool install gutenbit
```

gutenbit stores its database and catalog cache in a `.gutenbit/` folder.

## CLI walkthrough

### Find a book

Search the Project Gutenberg catalog by author, title, subject, or language:

```bash
gutenbit catalog --author "Austen, Jane"
```

```
  Downloaded catalog from Project Gutenberg (English text corpus).
      ID  AUTHORS                                   TITLE
  ------  ----------------------------------------  -----
    1342  Austen, Jane                              Pride and Prejudice
     158  Austen, Jane                              Emma
     161  Austen, Jane                              Sense and Sensibility
     105  Austen, Jane                              Persuasion
```

### Download and store

Pass one or more Project Gutenberg IDs to `add`:

```bash
gutenbit add 1342
```

The book's HTML is downloaded, parsed into paragraph-level chunks with structural metadata, and stored in a local SQLite database (`.gutenbit/gutenbit.db` by default).

### Explore structure

View the table of contents with numbered sections:

```bash
gutenbit toc 1342
```

By default, `toc` shows two heading levels. Use `--expand 1`, `--expand 3`, or `--expand all` to collapse further or reveal the full nested structure.

```bash
gutenbit toc 100 --expand all
```

Each section number can be used with `view --section` to jump directly to that part of the book. When deeper levels are collapsed, the visible lowest-level rows include the stats for the hidden descendants beneath them.

### Read text

View the opening of the book:

```bash
gutenbit view 1342
```

Read a specific section:

```bash
gutenbit view 1342 --section 1 --forward 10
```

Read a full section:

```bash
gutenbit view 1342 --section 1 --all
```

If the selected section has nested subsections, `--all` includes the entire subtree. For example, selecting an act includes all of its scenes.

Read from an exact chunk position:

```bash
gutenbit view 1342 --position 1 --forward 5
```

Read surrounding passage around a position or section start:

```bash
gutenbit view 1342 --position 1 --radius 2
gutenbit view 1342 --section 1 --radius 2
```

Use `--forward` for forward reading, `--radius` for a surrounding passage window, and `--all` for a full book or selected section subtree. `--all` does not apply to `--position`.

### Search

Full-text search across all stored books. Search targets text chunks by default:

```bash
gutenbit search "pride"
```

Search headings explicitly when needed:

```bash
gutenbit search "chapter" --book 1342 --kind heading
```

Narrow results to a single book:

```bash
gutenbit search "pride" --book 1342
```

Search for an exact phrase:

```bash
gutenbit search "truth universally acknowledged" --phrase
```

Search with nearby chunk context:

```bash
gutenbit search "truth universally acknowledged" --book 1342 --limit 3 --radius 1
```

All commands accept `--json` for machine-readable output.

## Python walkthrough

### Fetch the catalog

```python
from gutenbit import Catalog

catalog = Catalog.fetch()
books = catalog.search(author="Austen, Jane")
for book in books[:5]:
    print(book.id, book.title)
```

The catalog is cached locally for two hours under `.gutenbit/cache/`, filtered to English text, and deduplicated by normalized title plus primary author, keeping the lowest Project Gutenberg ID as canonical. Use `--refresh` to force a redownload.

### Ingest books

```python
from gutenbit import Database

with Database(".gutenbit/gutenbit.db") as db:
    db.ingest(books[:3])
```

`ingest` downloads each book's HTML, parses it into chunks, and stores everything in SQLite. Books already in the database are skipped.

### Search

```python
results = db.search("pride")
for hit in results:
    print(f"{hit.title} | {hit.div1} | {hit.content[:80]}")
```

Results use BM25 rank ordering by default. Each `SearchResult` includes the matching text, its structural position (div1 through div4), book metadata, and a relevance score.

### Read structured chunks

```python
# All chunks for a book
chunks = db.chunk_records(1342)

# Chunks in a specific section
section = db.chunks_by_div(1342, "Chapter 1")

# A window of chunks around a position
window = db.chunk_window(1342, position=50, around=2)
```

### Full text

```python
text = db.text(1342)
print(text[:500])
```

## How it works

The pipeline has four stages. The catalog provides book metadata and IDs. The downloader prefers official mirror HTML and falls back to the main site's HTML zip when needed. The chunker parses the HTML using its table of contents as a structural map, turning each paragraph into a discrete chunk with a position and a place in the book's heading hierarchy. The database stores chunks in SQLite with FTS5 indexing for fast full-text search with BM25 ranking.
