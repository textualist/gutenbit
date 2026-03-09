# gutenbit

ETL package for Project Gutenberg: download, parse, and store HTML texts in SQLite.

## Commands

- `uv run pytest` — Run tests
- `uv run ruff check .` — Lint
- `uv run ruff format --check .` — Check formatting
- `uv run ty check` — Type check

## Architecture

- `gutenbit/catalog.py` — CSV catalog fetch and search
- `gutenbit/download.py` — HTML download from Project Gutenberg epub zips
- `gutenbit/html_chunker.py` — TOC-driven HTML chunking (paragraph-level)
- `gutenbit/db.py` — SQLite storage with FTS5 search

### HTML Chunker

Uses the table of contents `<a class="pginternal">` links in Project Gutenberg
HTML as the structural map. Each TOC link points to a body anchor inside an
`<h2>`–`<h3>` tag, giving section boundaries and heading text from the markup.

Each `<p>` element becomes its own chunk — no accumulation or merging. This
preserves the exact paragraph structure of the original HTML.

Hierarchy is determined by bold (`<b>`) tags in TOC links (broad divisions like
BOOK/PART) and keyword-based classification as fallback. Levels are compacted
so the shallowest heading level always fills div1 first — a chapter-only book
has chapters in div1, while a book with BOOK + CHAPTER uses div1/div2.

Heading text has trailing punctuation (`. , ; : ] )`) and whitespace stripped
for clean display.

Chunk kinds: `"heading"`, `"text"`.

Each chunk in the database includes a `char_count` column for efficient
length-based queries.

## Verification

Before considering any change complete, test it against real books by actually running the library and inspecting results. Unit tests are a secondary check — live output comes first.

Canonical test corpus (covers diverse structural patterns):

| Book | PG ID | Why it matters |
|------|-------|----------------|
| War and Peace (Tolstoy) | 2600 | Word-ordinal BOOK headings (`BOOK ONE: 1805`), 15 books × ~28 chapters |
| Anna Karenina (Tolstoy) | 1399 | Partial TOC: parts in TOC, chapters only in body headings; TOC refinement must recover chapter structure |
| Crime and Punishment (Dostoevsky) | 2554 | PART + CHAPTER two-level hierarchy |
| A Christmas Carol (Dickens) | 46 | STAVE headings with colon subtitles |
| Nicholas Nickleby (Dickens) | 967 | Multi-chapter with trailing short text at boundaries |
| Oliver Twist (Dickens) | 730 | Short dialogue accumulation |
| Pride and Prejudice (Austen) | 1342 | Illustrated edition with `Chapter I.]` bracket artifacts |
| King James Bible | 10 | TOC emphasis distinguishes Testament-level broad divisions from individual books |
| Locke, Essay Concerning Human Understanding, Vol. 1 | 10615 | Split BOOK/CHAPTER headings that should merge cleanly without false subsections |
| Locke, Essay Concerning Human Understanding, Vol. 2 | 10616 | Noisy contents/synopsis/subheads; fallback heading scan must reject non-structural headings |
| Locke's Second Treatise | 7370 | `CHAPTER. I.` period-after-keyword format |
| Moby Dick (Melville) | 15 | 136 chapters, ETYMOLOGY/EXTRACTS as unsectioned opening |
| The Odyssey (Homer/Butler) | 1727 | BOOK-based epic, endnotes after last section |
| Frankenstein (Shelley) | 84 | Letter + Chapter mixed structure |
| Dracula (Stoker) | 345 | Journal-attributed chapters, singular "NOTE" epilogue |
| The Republic (Plato/Jowett) | 150 | BOOK + speaker sub-sections, early PG edition |
| Tom Sawyer (Twain) | 74 | Simple chapters with CONCLUSION |
| Metamorphosis (Kafka) | 5200 | No TOC links, heading-fallback with front-matter attribution |
| The Art of War (Sun Tzu/Giles) | 132 | Short numbered chapters with prefaces |
| Ulysses (Joyce) | 4300 | `— I —` parts with `[ 1` episode numbers |
| Brothers Karamazov (Dostoyevsky) | 28054 | PART + Book + Chapter three-level hierarchy with EPILOGUE |

Typical live check:

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

Check: correct number of headings, right div1/div2 split, no TOC entries leaking into the body, search results carry expected div fields.

## Style

- Modern Python (3.11+), type-annotated
- Keep it simple — stdlib where possible, minimal dependencies
