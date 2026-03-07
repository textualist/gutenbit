# Getting Started

This guide walks through a complete workflow: find a book, download it, explore its structure, and search its text. Both CLI and Python examples use *Pride and Prejudice* (Project Gutenberg ID 1342).

## Installation

```bash
pip install gutenbit
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add gutenbit
```

## CLI walkthrough

### Find a book

Search the Project Gutenberg catalog by author, title, subject, or language:

```bash
gutenbit catalog --author "Austen"
```

```
  Fetching catalog from Project Gutenberg (English text corpus)…
      ID  AUTHORS                                   TITLE
  ------  ----------------------------------------  -----
    1342  Austen, Jane                              Pride and Prejudice
     158  Austen, Jane                              Emma
     161  Austen, Jane                              Sense and Sensibility
     105  Austen, Jane                              Persuasion
```

### Download and store

Pass one or more Project Gutenberg IDs to `ingest`:

```bash
gutenbit ingest 1342
```

The book's HTML is downloaded, parsed into paragraph-level chunks with structural metadata, and stored in a local SQLite database (`gutenbit.db` by default).

### Explore structure

View the table of contents with numbered sections:

```bash
gutenbit toc 1342
```

Each section number can be used with `view --section` to jump directly to that part of the book.

### Read text

View the opening of the book:

```bash
gutenbit view 1342
```

Read a specific section:

```bash
gutenbit view 1342 --section 1 -n 10
```

Read from an exact chunk position:

```bash
gutenbit view 1342 --position 50 -n 5
```

The `-n` flag controls how many chunks to return. Use `-n 0` for all chunks in scope.

### Search

Full-text search across all stored books:

```bash
gutenbit search "pride"
```

Narrow results to a single book:

```bash
gutenbit search "pride" --book-id 1342
```

Search for an exact phrase:

```bash
gutenbit search "truth universally acknowledged" --phrase
```

All commands accept `--json` for machine-readable output.

## Python walkthrough

### Fetch the catalog

```python
from gutenbit import Catalog

catalog = Catalog.fetch()
books = catalog.search(author="Austen")
for book in books[:5]:
    print(book.id, book.title)
```

The catalog is fetched from Project Gutenberg on each call, filtered to English text, and deduplicated.

### Ingest books

```python
from gutenbit import Database

with Database("gutenbit.db") as db:
    db.ingest(books[:3])
```

`ingest` downloads each book's HTML, parses it into chunks, and stores everything in SQLite. Books already in the database are skipped.

### Search

```python
results = db.search("pride")
for hit in results:
    print(f"{hit.title} | {hit.div1} | {hit.content[:80]}")
```

Results are ranked by BM25 relevance. Each `SearchResult` includes the matching text, its structural position (div1 through div4), book metadata, and a relevance score.

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

## What just happened

The pipeline has four stages. The catalog provides book metadata and IDs. The downloader fetches HTML from Project Gutenberg's epub cache. The chunker parses the HTML using its table of contents as a structural map, turning each paragraph into a discrete chunk with a position and a place in the book's heading hierarchy. The database stores chunks in SQLite with FTS5 indexing for fast full-text search with BM25 ranking.
