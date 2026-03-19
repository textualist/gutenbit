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

## Patterns to reuse

- Prefer structural explanations over title-specific explanations. "speaker labels are being mistaken for headings" is useful; "The Republic is weird" is not.
- Compare the parsed TOC against raw HTML for the same edition. Do not use another Gutenberg edition or a print edition as the ground truth.
- Expect the broad failure families above to recur in unseen works. Design fixes around the family, not the current title.
- Be especially cautious with Roman numerals, speaker names, dramatic labels, attribution lines, publisher lines, and contents scaffolding.
- When the TOC is incomplete, look for real heading structure in the body before concluding the book is unstructured.

## Test-writing heuristics from `tests/test_battle.py`

- Name the test after the parser guarantee, not the issue number.
- Assert the smallest slice that proves the regression is fixed.
- Use exact heading slices for compact opening/closing matter regressions.
- Use representative anchor assertions for large books. `Leviathan` proves nested structure with a few specific headings instead of a full snapshot.
- Assert both presence and absence when the bug has a noisy false-positive mode. `Candide`, `Faust`, `Canterbury`, and `Inferno` all follow this pattern.
- Assert parent-child structure explicitly when hierarchy matters. The play and multi-level tests rely on `div1`, `div2`, and `div3`.
- Avoid brittle counts unless the count itself is the invariant. For some books the exact number of headings matters; for others a few anchor headings are better.
- Keep live tests book-specific and readable. A future reader should understand the regression from the test name and the assertions alone.

## Verification standard

After fixing one book, check the whole live corpus:

```bash
uv run pytest tests/test_battle.py -k "<target>"
uv run pytest
uv run pytest -m network
```

The target book passing is necessary but not sufficient. The fix is only done when the broader corpus still holds.
