# gutenbit

ETL package for Project Gutenberg: download, parse, and store texts in SQLite.

## Commands

- `uv run pytest` — Run tests
- `uv run ruff check .` — Lint
- `uv run ruff format --check .` — Check formatting
- `uv run ty check` — Type check

## Architecture

- `gutenbit/catalog.py` — CSV catalog fetch and search
- `gutenbit/download.py` — Text/HTML download and header/footer stripping
- `gutenbit/chunker.py` — Structural text chunking (plain-text fallback)
- `gutenbit/html_chunker.py` — TOC-driven HTML chunking (primary)
- `gutenbit/db.py` — SQLite storage with FTS5 search

### HTML Chunker (primary)

Uses the table of contents `<a class="pginternal">` links in Project Gutenberg
HTML as the structural map. Each TOC link points to a body anchor inside an
`<h2>`–`<h3>` tag, giving section boundaries and heading text from the markup.
Hierarchy is determined by bold (`<b>`) tags in TOC links (broad divisions like
BOOK/PART) and keyword-based classification as fallback.

### Text Chunker (fallback)

Regex-based chunking of plain text. Used when HTML is unavailable.

## Verification

Before considering any change complete, test it against real books by actually running the library and inspecting results. Unit tests are a secondary check — live output comes first.

Canonical test corpus (covers diverse structural patterns):

| Book | PG ID | Why it matters |
|------|-------|----------------|
| War and Peace (Tolstoy) | 2600 | Word-ordinal BOOK headings (`BOOK ONE: 1805`), 15 books × ~28 chapters |
| Crime and Punishment (Dostoevsky) | 2554 | PART + CHAPTER two-level hierarchy |
| A Christmas Carol (Dickens) | 46 | STAVE headings with colon subtitles |
| Nicholas Nickleby (Dickens) | 967 | Multi-chapter with trailing short text at boundaries |
| Oliver Twist (Dickens) | 730 | Short dialogue accumulation |
| Pride and Prejudice (Austen) | 1342 | Illustrated edition with `Chapter I.]` bracket artifacts |
| Locke's Second Treatise | 7370 | `CHAPTER. I.` period-after-keyword format |

Typical live check (HTML — preferred):

```python
import httpx, zipfile, io
from gutenbit.html_chunker import chunk_html

book_id = 2600  # swap as needed
url = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}-h.zip"
resp = httpx.get(url, verify=False, timeout=60)
z = zipfile.ZipFile(io.BytesIO(resp.content))
html = z.read([n for n in z.namelist() if n.endswith('.html')][0]).decode()
chunks = chunk_html(html)
headings = [c for c in chunks if c.kind == "heading"]
for h in headings[:20]:
    print(f"div1={h.div1!r:25s} div2={h.div2!r:20s}  {h.content!r}")
```

Plain-text fallback:

```python
import httpx
from gutenbit.download import strip_headers
from gutenbit.chunker import chunk_text

book_id = 2600  # swap as needed
url = f"https://www.gutenberg.org/files/{book_id}/{book_id}-0.txt"
text = strip_headers(httpx.get(url, verify=False, timeout=60).text)
chunks = chunk_text(text)
headings = [c for c in chunks if c.kind == "heading"]
for h in headings[:20]:
    print(f"div1={h.div1!r:25s} div2={h.div2!r:20s}  {h.content!r}")
```

Check: correct number of headings, right div1/div2 split, no TOC entries leaking into the body, search results carry expected div fields.



- Modern Python (3.11+), type-annotated
- Keep it simple — stdlib where possible, minimal dependencies
