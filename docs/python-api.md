# Python API

gutenbit exposes four public classes and one function from its top-level package.

```python
from gutenbit import Catalog, BookRecord, Database, SearchResult, Chunk, chunk_html
```

## Catalog

`Catalog` fetches and searches the Project Gutenberg metadata catalog.

### Fetch

```python
from gutenbit import Catalog

catalog = Catalog.fetch()
```

`fetch()` downloads the CSV catalog from Project Gutenberg, filters it to English text records, and deduplicates entries so each work maps to a single canonical ID (the lowest Gutenberg ID for that title/author pair). The result is a `Catalog` instance held in memory.

### Search

```python
results = catalog.search(author="Dickens")
results = catalog.search(title="Christmas", author="Dickens")
results = catalog.search(subject="Philosophy")
```

All filters use case-insensitive substring matching. When multiple filters are given, they combine with AND logic. Returns a list of `BookRecord` objects.

### Lookup

```python
book = catalog.get(1342)           # BookRecord or None
cid = catalog.canonical_id(1342)   # canonical ID or None
```

`canonical_id` resolves alternate edition IDs to the canonical one.

### BookRecord

A frozen dataclass with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int` | Project Gutenberg ID |
| `title` | `str` | Book title |
| `authors` | `str` | Semicolon-separated author names |
| `language` | `str` | Language code (e.g. `"en"`) |
| `subjects` | `str` | Semicolon-separated subjects |
| `locc` | `str` | Library of Congress Classification |
| `bookshelves` | `str` | Gutenberg bookshelves |
| `issued` | `str` | Publication date |
| `type` | `str` | Media type (e.g. `"Text"`) |

### Catalog policy

By default, `fetch()` applies a policy that keeps only English text and deduplicates by lowest ID per work. To customize:

```python
from gutenbit.catalog import CatalogPolicy

policy = CatalogPolicy(
    allowed_language_codes=frozenset({"en", "fr"}),
    dedupe_strategy="none",
)
catalog = Catalog.fetch(policy=policy)
```

See the [API Reference](reference/gutenbit/catalog.md) for full details on `CatalogPolicy`.

## Database

`Database` wraps a SQLite file. Use it as a context manager:

```python
from gutenbit import Database

with Database("~/.gutenbit/gutenbit.db") as db:
    # all operations here
    ...
```

Or manage the connection manually with `db.close()`.

### Ingest

```python
catalog = Catalog.fetch()
books = catalog.search(author="Tolstoy")

with Database("~/.gutenbit/gutenbit.db") as db:
    db.ingest(books)
```

`ingest` downloads each book's HTML from Project Gutenberg, parses it into chunks, and stores everything in the database. Books already present (at the current chunker version) are skipped unless you pass `force=True`.

The `delay` parameter controls the pause between downloads. The default is 1 second, which is polite to Gutenberg's servers:

```python
db.ingest(books, delay=2.0)
db.ingest(books, force=True)  # reprocess even if already current
```

### Search

```python
results = db.search("battle")
```

Returns a list of `SearchResult` objects ordered by BM25 rank by default.

**Filters** narrow the result set:

```python
results = db.search("battle", author="Tolstoy")
results = db.search("battle", book_id=2600)
results = db.search("battle", kind="text")
results = db.search("battle", title="War")
```

Metadata filters (`author`, `title`, `language`, `subject`) use substring matching. `book_id` and `kind` are exact.

**Order** controls result ordering:

```python
db.search("battle", order="rank")    # BM25 score (default)
db.search("battle", order="first")   # book_id asc, position asc
db.search("battle", order="last")    # book_id desc, position desc
```

**Limit** controls the maximum number of results:

```python
db.search("battle", limit=50)  # default is 20
```

**FTS5 query syntax** is supported directly:

```python
db.search('"to be or not to be"')         # exact phrase
db.search("war AND peace")                 # boolean
db.search("war NOT peace")                 # exclusion
db.search("philos*")                       # prefix match
```

### SearchResult

A frozen dataclass with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | `int` | Internal row ID |
| `book_id` | `int` | Project Gutenberg ID |
| `title` | `str` | Book title |
| `authors` | `str` | Author names |
| `language` | `str` | Language code |
| `subjects` | `str` | Subjects |
| `div1` | `str` | Broadest structural division |
| `div2` | `str` | Second level |
| `div3` | `str` | Third level |
| `div4` | `str` | Deepest level |
| `position` | `int` | Chunk index in document order |
| `content` | `str` | Full text of the matching chunk |
| `kind` | `str` | `"heading"` or `"text"` |
| `char_count` | `int` | Character length of content |
| `score` | `float` | BM25 relevance score (higher is better) |

### Reading chunks

Several methods retrieve chunks without a search query.

**All chunks for a book:**

```python
records = db.chunk_records(1342)
for chunk in records:
    print(chunk.position, chunk.div1, chunk.kind, chunk.content[:60])
```

**Filter by kind:**

```python
headings = db.chunk_records(1342, kinds=["heading"])
```

**By position:**

```python
chunk = db.chunk_by_position(1342, position=50)
```

**A window around a position:**

```python
window = db.chunk_window(1342, position=50, around=3)
# Returns chunks at positions 47, 48, 49, 50, 51, 52, 53
```

The CLI `view --radius` and `search --radius` options use this same centered-window concept, but present it as a simple surrounding passage in reading order.

**By section path:**

```python
section = db.chunks_by_div(1342, "Chapter 1")
section = db.chunks_by_div(1342, "BOOK ONE/CHAPTER I", kinds=["text"], limit=10)
```

`chunks_by_div` matches by prefix on the div hierarchy. Stored headings preserve source punctuation, while matching ignores trailing punctuation and is case-insensitive.

### ChunkRecord

A frozen dataclass with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | `int` | Internal row ID |
| `book_id` | `int` | Project Gutenberg ID |
| `div1` | `str` | Broadest structural division |
| `div2` | `str` | Second level |
| `div3` | `str` | Third level |
| `div4` | `str` | Deepest level |
| `position` | `int` | Chunk index in document order |
| `content` | `str` | Full text |
| `kind` | `str` | Chunk kind |
| `char_count` | `int` | Character length of content |

### Full text

```python
text = db.text(1342)
```

Returns the full reconstructed text (all chunks joined with double newlines), or `None` if the book is not stored.

### Book management

```python
all_books = db.books()             # list of BookRecord
stale_books = db.stale_books()     # stored books that need reprocessing
book = db.book(1342)               # BookRecord or None
db.has_text(1342)                  # True if stored
db.has_current_text(1342)          # True if stored at current chunker version
db.remove_book(1342)               # returns True if removed, False if not found
```

## Chunking HTML directly

For advanced use, you can chunk HTML without the database:

```python
from gutenbit import chunk_html

html = open("book.html").read()
chunks = chunk_html(html)

for chunk in chunks[:10]:
    print(chunk.position, chunk.div1, chunk.kind, chunk.content[:60])
```

### Chunk

A frozen dataclass with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `position` | `int` | Chunk index in document order |
| `div1` | `str` | Broadest structural division |
| `div2` | `str` | Second level |
| `div3` | `str` | Third level |
| `div4` | `str` | Deepest level |
| `content` | `str` | Text content |
| `kind` | `str` | `"heading"` or `"text"` |

See [Concepts](concepts.md) for how divisions and chunk kinds work.
