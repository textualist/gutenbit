# Battle test all works of Emerson, Thoreau, Melville, and Robert Louis Stevenson

**Labels:** `test`

---

Run a full live battle test of the CLI and parsing functionality for each work below.

## Selection criteria

* English-language Project Gutenberg works only.
* Comprehensive: every distinct English-language work by each author available on Project Gutenberg.
* Each work is confirmed absent from existing GitHub battle-test issues (#162, #164, #166, #168),
  `tests/test_battle.py`, and the kei-17-corpus reference.
* One PG ID per distinct work (duplicate editions excluded; preferred edition verified via `uv run gutenbit add`).
* Breadth across novels, essays, poetry, letters, journals, lectures, travel writing, story collections, and drama.

## Already-excluded works (in existing battle tests)

* **Moby-Dick; or, The Whale** — PG 15 (in `test_battle.py` and kei-17 corpus)
* **Treasure Island** — PG 120 (in `test_battle.py`)
* **The Strange Case of Dr. Jekyll and Mr. Hyde** — PG 42 (in `test_battle.py`)

## Works (100 total)

### Ralph Waldo Emerson (17 works)

- [ ] Essays, First Series — PG 2944
- [ ] Essays, Second Series — PG 2945
- [ ] Essays by Ralph Waldo Emerson — PG 16643
- [ ] Nature — PG 29433
- [ ] Poems, Household Edition — PG 12843
- [ ] Representative Men: Seven Lectures — PG 6312
- [ ] The Conduct of Life — PG 39827
- [ ] English Traits — PG 39862
- [ ] Society and Solitude: Twelve Chapters — PG 69258
- [ ] Miscellanies — PG 71683
- [ ] Letters and Social Aims — PG 71393
- [ ] Lectures and Biographical Sketches — PG 75942
- [ ] Natural History of Intellect, and Other Papers — PG 71558
- [ ] May-Day, and Other Pieces — PG 15963
- [ ] The Correspondence of Thomas Carlyle and Ralph Waldo Emerson, 1834-1872, Vol. I — PG 13583
- [ ] The Correspondence of Thomas Carlyle and Ralph Waldo Emerson, 1834-1872, Vol. II — PG 13660
- [ ] Compensation — PG 73035

### Henry David Thoreau (18 works)

- [ ] Walden, and On The Duty Of Civil Disobedience — PG 205
- [ ] On the Duty of Civil Disobedience — PG 71
- [ ] Walking — PG 1022
- [ ] Familiar Letters (Writings, Vol. 6 of 20) — PG 43523
- [ ] The Maine Woods (Writings, Vol. 3 of 20) — PG 42500
- [ ] Excursions, and Poems (Writings, Vol. 5 of 20) — PG 42553
- [ ] A Week on the Concord and Merrimack Rivers — PG 4232
- [ ] Cape Cod — PG 34392
- [ ] Poems of Nature — PG 59988
- [ ] Excursions — PG 9846
- [ ] A Plea for Captain John Brown — PG 2567
- [ ] Canoeing in the Wilderness — PG 34990
- [ ] A Yankee in Canada, with Anti-Slavery and Reform Papers — PG 70123
- [ ] Wild Apples — PG 4066
- [ ] Paradise (to be) Regained — PG 63459
- [ ] The Service — PG 60951
- [ ] Journal 01, 1837-1846 (Writings, Vol. 7 of 20) — PG 57393
- [ ] Journal 02, 1850-September 15, 1851 (Writings, Vol. 8 of 20) — PG 59031

### Herman Melville (16 works)

- [ ] Typee: A Romance of the South Seas — PG 1900
- [ ] Omoo: Adventures in the South Seas — PG 4045
- [ ] Mardi, and a Voyage Thither, Vol. 1 (of 2) — PG 13720
- [ ] Mardi, and a Voyage Thither, Vol. 2 (of 2) — PG 13721
- [ ] Redburn: His First Voyage — PG 8118
- [ ] White-Jacket; Or, The World on a Man-of-War — PG 10712
- [ ] Pierre; or The Ambiguities — PG 34970
- [ ] Israel Potter: His Fifty Years of Exile — PG 15422
- [ ] The Confidence-Man: His Masquerade — PG 21816
- [ ] Billy Budd — PG 76513
- [ ] The Piazza Tales — PG 15859
- [ ] Battle-Pieces and Aspects of the War — PG 12384
- [ ] John Marr and Other Poems — PG 12841
- [ ] The Apple-Tree Table, and Other Sketches — PG 53861
- [ ] I and My Chimney — PG 2694
- [ ] Bartleby, the Scrivener: A Story of Wall-Street — PG 11231

### Robert Louis Stevenson (49 works)

#### Novels and novellas

- [ ] Kidnapped — PG 421
- [ ] Catriona (David Balfour) — PG 589
- [ ] The Black Arrow: A Tale of the Two Roses — PG 848
- [ ] The Master of Ballantrae: A Winter's Tale — PG 864
- [ ] The Wrecker — PG 1024
- [ ] The Ebb-Tide: A Trio and Quartette — PG 1604
- [ ] The Wrong Box — PG 1585
- [ ] St. Ives: Being the Adventures of a French Prisoner in England — PG 322
- [ ] Prince Otto, a Romance — PG 372
- [ ] Weir of Hermiston: An Unfinished Romance — PG 380
- [ ] The Dynamiter — PG 647

#### Story and fable collections

- [ ] New Arabian Nights — PG 839
- [ ] The Merry Men, and Other Tales and Fables — PG 344
- [ ] Island Nights' Entertainments — PG 329
- [ ] Tales and Fantasies — PG 426
- [ ] Fables — PG 343
- [ ] The Waif Woman — PG 19750

#### Essays and criticism

- [ ] Virginibus Puerisque, and Other Papers — PG 386
- [ ] Familiar Studies of Men and Books — PG 425
- [ ] Memories and Portraits — PG 381
- [ ] Across the Plains, with Other Memories and Essays — PG 614
- [ ] Essays in the Art of Writing — PG 492
- [ ] Essays of Travel — PG 627
- [ ] Lay Morals, and Other Papers — PG 373
- [ ] An Apology for Idlers, and Other Essays — PG 69825
- [ ] Essays of Robert Louis Stevenson (selected, ed. Phelps) — PG 10761

#### Travel writing

- [ ] Travels with a Donkey in the Cevennes — PG 535
- [ ] An Inland Voyage — PG 534
- [ ] In the South Seas — PG 464
- [ ] The Silverado Squatters — PG 516
- [ ] Edinburgh: Picturesque Notes — PG 382

#### History and biography

- [ ] A Footnote to History: Eight Years of Trouble in Samoa — PG 536
- [ ] Records of a Family of Engineers — PG 280
- [ ] Memoir of Fleeming Jenkin — PG 698

#### Poetry

- [ ] A Child's Garden of Verses — PG 136
- [ ] Songs of Travel, and Other Verses — PG 487
- [ ] Underwoods — PG 438
- [ ] Ballads — PG 413
- [ ] New Poems, and Variant Readings — PG 441
- [ ] Moral Emblems — PG 772

#### Letters

- [ ] The Letters of Robert Louis Stevenson, Volume 1 — PG 622
- [ ] The Letters of Robert Louis Stevenson, Volume 2 — PG 637
- [ ] Vailima Letters — PG 387

#### Short non-fiction and miscellaneous

- [ ] Prayers Written At Vailima, and A Lowden Sabbath Morn — PG 616
- [ ] Father Damien: An Open Letter to the Reverend Dr. Hyde of Honolulu — PG 281
- [ ] A Christmas Sermon — PG 14535
- [ ] The Sea Fogs — PG 5272
- [ ] The Pocket R.L.S.: Being Favourite Passages from the Works of Stevenson — PG 2537

#### Drama

- [ ] The Plays of W. E. Henley and R. L. Stevenson — PG 719

## Notes for the testing agent

### Edition and redirect notes

These PG IDs were verified via `uv run gutenbit add` on 2026-04-06. Some Gutenberg IDs
redirect to canonical editions:

| Searched ID | Resolved ID | Title |
|---|---|---|
| 43 | 42 | Jekyll and Hyde (already excluded) |
| 32954 | 848 | The Black Arrow |
| 25609 | 136 | A Child's Garden of Verses |
| 2701 | 15 | Moby-Dick (already excluded) |
| 71812 | 71683 | Miscellanies (Emerson) |

### Potential edition overlaps to investigate

* **Emerson**: PG 16643 "Essays" may partially overlap with PG 2944 (First Series) + PG 2945
  (Second Series). Verify during testing whether it's a combined edition or a distinct compilation.
* **Thoreau**: PG 9846 "Excursions" and PG 42553 "Excursions, and Poems" (Writings edition) may
  share essay content but have different HTML structures. Both are worth testing.
* **Thoreau**: PG 205 combines Walden + Civil Disobedience. PG 71 is standalone Civil Disobedience.
  Both have distinct HTML and structure.
* **Stevenson**: 25 Swanston Edition volumes (PG 21686, 30527, 30729, 30700, 30744, 30393, 30807,
  31484, 30598, 31916, 30870, 30939, 30954, 30659, 30643, 30990, 31012, 31557, 31037, 30650,
  31291, 31809, 30894, 30849, 30714) are collected works. NOT included in this issue because all
  individual works are available separately. If collected-edition testing is desired, create a
  separate issue.
* **Stevenson**: PG 10761 and PG 69825 are selected/edited essay collections that may overlap with
  other essay volumes (PG 386, 381, 614, etc.) but have distinct editorial framing and HTML.

### Parser-coverage value by author

**Emerson** — Exercises essay-collection structure with varied front matter, numbered chapters,
and lecture series formats. The Carlyle correspondence (PG 13583/13660) exercises epistolary
letter-date structure. `Nature` (PG 29433) is a compact treatise with Roman-numeral sections.

**Thoreau** — Exercises travel narrative structure (day-based or chapter-based), combined
multi-work volumes (PG 205), journal date-entry structure (PG 57393/59031), and short standalone
essays. The Writings series volumes (PG 42500, 42553, 43523) have series-level front matter.

**Melville** — Exercises long chapter-based novels, multi-volume splits (Mardi PG 13720/13721),
story collections (Piazza Tales PG 15859, Apple-Tree Table PG 53861), poetry collections
(Battle-Pieces PG 12384), and standalone short stories (Bartleby PG 11231, I and My Chimney
PG 2694). Pierre (PG 34970) has deep book→chapter nesting. Billy Budd (PG 76513) includes
additional prose pieces in anthology format.

**Stevenson** — The largest and most diverse set. Exercises novels with part→chapter structure
(Black Arrow, Master of Ballantrae), linked story-cycle collections (New Arabian Nights PG 839),
travel narratives, essay collections of varying editorial complexity, poetry collections, epistolary
volumes (Letters vols 1-2, Vailima Letters), dramatic works (Plays PG 719), biography/memoir,
unfinished works (Weir of Hermiston PG 380), and very short standalone pieces (Father Damien,
Christmas Sermon, Sea Fogs). The breadth makes this corpus excellent for catching edge cases
across many structural families.

## Guide

Use the `gutenbit-live-battle-test` skill for each work. Start with:

```
uv run gutenbit add <pg_id>
uv run gutenbit toc <pg_id> --expand all
uv run gutenbit toc <pg_id> --expand all --json
uv run gutenbit search "" --book <pg_id>
uv run gutenbit view <pg_id>
```

Focus on `toc --expand all` for missing, extra, or mis-nested sections. Compare suspicious
output against the raw Gutenberg HTML (ground truth). If a parser bug is found, implement
the smallest generalizable fix — no book-specific rules. Add a focused regression test if
needed (prefer synthetic non-network tests; reserve network tests for high-value cases).
Before closing each work, run `uv run pytest` and `uv run pytest -m network`.

### Recommended testing order

Given the scale (100 works), work through authors one at a time. Within each author, start
with the most structurally complex works (novels, collections, multi-volume) before moving to
simpler ones (standalone essays, short poems). This surfaces parser issues early when they're
most likely to affect multiple works.

**Suggested order:**
1. **Melville** (16 works) — complex novels, multi-volume, story collections
2. **Stevenson** (49 works) — most diverse; novels → collections → essays → poetry → letters → misc
3. **Emerson** (17 works) — essay collections, lectures, correspondence
4. **Thoreau** (18 works) — travel narratives, journals, essays

### Batching strategy

With 100 works, batch by author and fix one issue family at a time. Record all results in a
structured table before beginning fixes (per kei-17-corpus guidance). This prevents redundant
work on issues that share a common family.

## References

- `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
- `tests/test_battle.py`
- `AGENTS.md`
- Prior battle test issues: #162 (Hawthorne/Poe), #164 (Hardy/Trollope), #166 (Tolstoy/Dostoevsky), #168 (Henry James/William James)

## Close-out

For each work, check the box and annotate with either:
- ✅ if no parser issue found, with a brief note
- ⚠️ if a known limitation exists (source HTML defect, pre-existing issue family), with a note
- 🔧 if a parser fix was applied, referencing the commit or PR

Post a summary comment with structured results tables (clean passes, issues found, fixes
applied) before closing the issue.
