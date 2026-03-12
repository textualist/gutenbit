# Concepts

This page explains the internal model that shapes gutenbit's output. Understanding these concepts makes search results, chunk metadata, and structural divisions easier to interpret.

## Pipeline

gutenbit processes books in four stages:

1. **Catalog.** A CSV feed from Project Gutenberg provides metadata for every book: title, author, subjects, language, and Gutenberg ID. The catalog is filtered to English text and deduplicated so each work has one canonical ID.

2. **Download.** Each book's HTML is fetched from official mirrors first, with a fallback to Gutenberg's epub-cache zip when needed.

3. **Chunk.** The HTML is parsed into discrete text units. Each `<p>` element becomes one chunk. Structural metadata (divisions and kinds) is attached to every chunk.

4. **Store.** Chunks, book metadata, and a reconstructed full text are written to a SQLite database. An FTS5 virtual table indexes all chunk content for full-text search.

## Chunking

The chunker reads the book's HTML table of contents to build a structural map. Project Gutenberg HTML files include TOC links (`<a class="pginternal">`) that point to anchors on heading tags in the body. These links define where sections begin and what their headings are.

Each `<p>` element between two section boundaries becomes its own chunk. Paragraphs are never merged or split. This preserves the exact paragraph structure of the original text.

Content outside the Gutenberg `*** START ***` and `*** END ***` delimiters is excluded. If these delimiters are absent, the chunker falls back to Gutenberg's `pg-header` and `pg-footer` elements.

When a book has no TOC links, the chunker falls back to scanning for heading tags (`<h1>` through `<h3>`) directly.

## Structural divisions

Every chunk carries four division fields: `div1`, `div2`, `div3`, and `div4`. These represent a hierarchy from broadest to most specific.

The heading level assigned to each section depends on the heading's content:

- **Level 1** (broad): Headings with keywords like BOOK, PART, ACT, VOLUME, or EPILOGUE. Also headings marked bold in the TOC.
- **Level 2** (chapter): Headings with CHAPTER, STAVE, LETTER, or similar keywords.
- **Level 3** (sub-chapter): All other headings.

### Level compaction

Levels are compacted so the shallowest heading always fills `div1`. A book with only chapters puts them in `div1`. A book with both BOOK and CHAPTER headings puts books in `div1` and chapters in `div2`.

Examples:

| Book | Structure | div1 | div2 |
|------|-----------|------|------|
| Pride and Prejudice | Chapters only | `Chapter 1` | (empty) |
| War and Peace | Books + Chapters | `BOOK ONE: 1805` | `CHAPTER I` |
| Crime and Punishment | Parts + Chapters | `PART I` | `CHAPTER I` |

This compaction means `div1` always represents the broadest structural unit present in any given book, regardless of how many levels the book uses.

## Chunk kinds

Each chunk has a `kind` field:

| Kind | Meaning |
|------|---------|
| `heading` | A section heading extracted from the TOC structure |
| `text` | A body paragraph within a section |

## Corpus policy

gutenbit enforces a fixed corpus policy during catalog fetching:

- **Language:** English only (`en`).
- **Media type:** Text only.
- **Deduplication:** When multiple Gutenberg IDs correspond to the same work (same title and author), the lowest ID is kept as the canonical edition. All other IDs remap to it.

The `canonical_id` method on `Catalog` resolves any Gutenberg ID to its canonical form. During `gutenbit add`, requested IDs are remapped automatically.

## Search

Search uses SQLite FTS5 with BM25 ranking. The index is configured with Porter stemming and unicode61 tokenization, which means:

- Stemming reduces words to roots. Searching for "running" also matches "run" and "runs."
- Unicode normalization handles accented characters and non-ASCII text.
- Ranking considers term frequency, document length, and inverse document frequency across the corpus.

Search results include the full text of each matching chunk along with its structural metadata, so you can identify where in a book a match occurs without a separate lookup. The CLI can also attach surrounding passage with `--radius` when you want local reading context around each hit.
