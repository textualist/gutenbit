# gutenbit

ETL package for Project Gutenberg: download, parse, and store texts in SQLite.

## Commands

- `uv run pytest` — Run tests
- `uv run ruff check .` — Lint
- `uv run ruff format --check .` — Check formatting
- `uv run ty check` — Type check

## Architecture

- `gutenbit/catalog.py` — CSV catalog fetch and search
- `gutenbit/download.py` — Text download and header/footer stripping
- `gutenbit/chunker.py` — Structural text chunking
- `gutenbit/db.py` — SQLite storage with FTS5 search

### Chunker

Splits book text into labelled chunks. Kinds: `front_matter` (title page, etc.),
`toc` (table of contents), `heading` (chapter/section headings), `paragraph` (prose),
`end_matter` (footnotes, appendices, etc.).

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

Typical live check:

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
