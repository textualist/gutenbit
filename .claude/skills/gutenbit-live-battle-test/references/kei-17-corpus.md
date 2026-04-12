# KEI-17 Battle-Test Corpus

Use this file to classify new failures quickly and to mirror the test-writing style already established in `tests/test_battle.py`.

## Failure classes

| Work | PG ID | Failure class | Key lesson |
| --- | ---: | --- | --- |
| Hamlet | 1122 | Catastrophic play parsing | Recover act/scene structure; do not let `FINIS` collapse the play. |
| Macbeth | 1129 | Catastrophic play parsing | Paragraph fallback can recover full play structure when TOC links fail. |
| The Republic | 150 | Lost top-level structure | Preserve `BOOK I` through `BOOK X` over speaker headings. |
| Faust -- Part 1 | 3023 | Synthetic dramatic headings | Keep top-level dramatic sections; reject character/speech labels as structure. |
| The Canterbury Tales | 2383 | Garbage Roman numeral headings | Do not promote stray single-letter or numeral headings; keep real book divisions. |
| The Inferno | 41537 | Garbage front-matter headings | Reject stray numeral sections before the real canto sequence. |
| Leviathan | 3207 | Lost deep structure | Refine beyond the TOC when body headings expose real subsections. |
| Moby-Dick | 15 | Omitted opening matter | Preserve `ETYMOLOGY` and `EXTRACTS` before chapter one. |
| Dracula | 345 | Omitted closing matter | Preserve the final `NOTE` after the last chapter. |
| Middlemarch | 145 | Omitted closing matter | Preserve `FINALE` and closing sections after the last numbered chapter. |
| Jane Eyre | 1260 | Omitted opening matter | Keep `PREFACE` and edition notes before chapter one. |
| Les Miserables | 135 | Omitted opening and closing matter | Keep preface material and the final letter. |
| A Christmas Carol | 46 | Omitted opening matter | Preserve `PREFACE` before the stave sequence. |
| Tom Sawyer | 74 | Omitted opening matter | Preserve `PREFACE` before chapter one. |
| Gulliver's Travels | 829 | Omitted opening matter | Preserve prefatory letters before `PART I`. |
| Don Quixote | 996 | Omitted opening matter | Keep the prefatory block and commendatory verses. |
| Bleak House | 1023 | Omitted opening matter | Preserve `PREFACE` before chapter one. |
| Vanity Fair | 599 | Omitted opening matter | Preserve `BEFORE THE CURTAIN` before chapter one. |
| Black Beauty | 271 | Lost part-level structure | Keep part headings as standalone sections instead of merging them into chapters. |
| Candide | 19942 | Attribution/publisher noise | Keep real front matter and reject publisher/credit lines as headings. |
| Fall of the House of Usher | 932 | Title-block author-name leakage | Standalone "BY" heading + author name in title blocks leak into fallback heading scan; filter byline pairs in pre-pass. |
| The Cask of Amontillado | 1063 | Title-block author-name leakage | Same byline pattern as PG 932; `_STANDALONE_BYLINE_RE` pre-pass catches both. |
| Poe Works Vol 3 | 2149 | Bare heading cross-merge | Bare chapter number merges with unrelated next story title at same level; level guard in `_merge_bare_heading_pairs` blocks sibling merges. |
| English Notebooks Vol 2 | 7877 | Volume title child-merge | VOLUME heading absorbs first child section as subtitle when children have same-rank peers; peer-rank check in `_normalized_heading_continuation` blocks the merge. |
| Twice-Told Tales | 508 | Identical heading over-deduplication | Four same-text anthology series headings collapsed to one; run-length heuristic keeps runs of 3+ and collapses runs of exactly 2 (HTML duplicates). |
| Middlemarch (terminal marker) | 145 | Terminal marker subtitle merge | "THE END" / "FINIS" merged as subtitle of preceding heading; `_TERMINAL_MARKER_RE` guard prevents this. Distinct from the existing omitted-closing-matter entry. |
| Poe Works Vols 1, 3, 4 | 2147 | Split-title empty parent | h2 main title + h3 subtitle creates empty parent section; documented but not yet resolved. |
| Shakespeare Complete Works | 100 | Dramatic parent keyword refinement | Restrict dramatic parent→child heading promotion to known keyword pairs (e.g. ACT→SCENE); unrelated keywords (e.g. CHAPTER) must not refine between SCENE entries. |
| The Chorus Girl and Other Stories | 13418 | Anchor-on-heading-tag | The TOC link target `id` sits on the heading tag itself (`<h4 id="id00016">Title</h4>`) rather than on a child `<a>`. `anchor_map` must record heading-tag ids alongside `<a id=...>` anchors, and `find_parent` must not be the sole resolution path — it never returns the element itself. Trust the rank-filter bypass only when the body anchor IS the heading (identity guard, not rank threshold). |

## Patterns to reuse

- Prefer structural explanations over title-specific explanations. "speaker labels are being mistaken for headings" is useful; "The Republic is weird" is not.
- Compare the parsed TOC against raw HTML for the same edition. Do not use another Gutenberg edition or a print edition as the ground truth.
- Expect the broad failure families above to recur in unseen works. Design fixes around the family, not the current title.
- Be especially cautious with Roman numerals, speaker names, dramatic labels, attribution lines, publisher lines, and contents scaffolding.
- When the TOC is incomplete, look for real heading structure in the body before concluding the book is unstructured.
- Run-length heuristics distinguish structural repetition (3+ identical headings = real structure) from HTML duplicates (exactly 2 = collapse).
- Level guards prevent cross-merging: only merge a bare heading with a following heading when the follower is at a deeper level (child), not at the same level (sibling).
- Peer-rank checks prevent false subtitle merges: when the following heading has same-rank peers, it is a content section, not a subtitle.
- `_TERMINAL_MARKER_RE` prevents end-of-book markers ("THE END", "FINIS") from being absorbed as subtitles.
- When batch-testing an author corpus, fix one issue family at a time and verify no regressions across the full corpus before fixing the next.

## Test-writing heuristics from `tests/test_battle.py`

- Name the test after the parser guarantee, not the issue number.
- Assert the smallest slice that proves the regression is fixed.
- Use exact heading slices for compact opening/closing matter regressions.
- Use representative anchor assertions for large books. `Leviathan` proves nested structure with a few specific headings instead of a full snapshot.
- Assert both presence and absence when the bug has a noisy false-positive mode. `Candide`, `Faust`, `Canterbury`, and `Inferno` all follow this pattern.
- Assert parent-child structure explicitly when hierarchy matters. The play and multi-level tests rely on `div1`, `div2`, and `div3`.
- Avoid brittle counts unless the count itself is the invariant. For some books the exact number of headings matters; for others a few anchor headings are better.
- Keep live tests book-specific and readable. A future reader should understand the regression from the test name and the assertions alone.
- Prefer synthetic non-network tests (`tests/test_html_chunker.py`) for most regression coverage. Network tests are expensive and should be reserved for high-value structural regressions that require live Gutenberg HTML.
- Use `toc --expand all --json` output as the ground truth reference when designing test assertions.
- When testing anthology or collection works, assert that each distinct work in the collection has its own TOC entry.

## Verification standard

After fixing one book, check the whole live corpus:

```bash
uv run pytest tests/test_battle.py -k "<target>"
uv run pytest
uv run pytest -m network
```

The target book passing is necessary but not sufficient. The fix is only done when the broader corpus still holds.

When batch-testing multiple works, record all results in a structured table (clean passes,
source HTML limitations, issues found) before beginning fixes. This prevents redundant work
on issues that share a common family and makes the final close-out comment on the GitHub
Issue self-contained.

## Deferred issues

Work that was discovered during a battle-test pass but whose fix was
deferred to a later session.  Each entry records what is broken, the
root cause at a structural level, what a fix would look like, and why
this session did not attempt it.  When a new session revisits the
parser, start here.

### Stories by Foreign Authors: Russian (PG 5741)

- **Observable failure:** The MUMU story (~12,000 words, ~160 paragraphs) is invisible in navigation.  It falls into the unsectioned opening text.  `THE SHOT` also loses its story title and only its numbered chapters appear.
- **Root cause:** No `pginternal` TOC links exist, so the heading-scan fallback runs.  The raw HTML has `<h1>STORIES BY FOREIGN AUTHORS</h1>` (book title), then `<h2>MUMU</h2>` followed by `<h5>BY</h5>` + `<h5>IVAN TURGENEV</h5>` (title-block pattern), then `<h1>THE SHOT</h1>`, etc.  The leading title-page stripping logic (`_strip_leading_title_page_sections` in `_hierarchy.py`) treats the `MUMU` + `BY` + author triplet as a title-page cluster and drops MUMU entirely.  The `_paragraphs_between_are_metadata_only` check is too loose for this case.
- **Fix sketch:** Make the title-page stripping more conservative: when the next "content" heading after a title-like prefix is followed by 100+ paragraphs of real story text (not metadata), the prefix was structural, not a title page.  Alternatively, detect the `MUMU / BY / author` byline pattern explicitly and keep MUMU as a peer of the subsequent `<h1>` stories.
- **Why deferred:** Requires careful rework of `_strip_leading_title_page_sections` with regression coverage for the actual title-page cases it was designed to handle (PG 5200 Metamorphosis, PG 19942 Candide, etc.).  Risk of cascading regressions is high without a broader synthetic test pass first.

### Project Gutenberg Compilation of Short Stories by Chekhov (PG 57333)

- **Observable failure:** Massively merged sections.  "PEASANTS I" is 93,601 words (should be ~1,500).  "MY LIFE" is 97,408 words (should be ~55,000).  Twenty-four empty `[A]`–`[Z]` alphabetical index anchors appear as sections.  Non-Chekhov content (Pushkin, Tolstoy, Dostoyevsky) is mixed in without distinction.
- **Root cause:** The book is a compilation of 16+ separate Chekhov collections plus a `Best Russian Short Stories` anthology.  Each sub-collection uses its own heading hierarchy (h2/h3/h4/h5 all appear with different semantics in different sub-collections).  There is no single valid rank assignment that produces correct nesting across all sub-collections.  When the parser picks one rank as the story-title level, stories in sub-collections that use a different rank get absorbed into the preceding story.
- **Fix sketch:** Detect when multiple sub-collections use inconsistent rank assignments and treat each sub-collection independently — either via TOC structure clustering or by recognising sub-collection divider headings as reset points.  Also filter `[A]`–`[Z]` index anchors (bracketed single letter, zero content, alphabetical sequence).
- **Why deferred:** Requires rethinking the parser's assumption that a book has a single globally-consistent heading hierarchy.  This is a fundamental architectural change; the PG 13418 fix (2026-04) unblocked 12 additional stories in this book via the anchor-on-heading path, but the structural problems remain.
