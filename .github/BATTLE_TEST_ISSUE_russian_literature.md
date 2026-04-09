# GitHub Issue: Battle test all works of Chekhov, Gogol, and Turgenev

**Labels:** `test`

---

## Issue Title

Battle test all works of Chekhov, Gogol, and Turgenev

## Issue Body

Run a full live battle test of the CLI and parsing functionality for each work below.

## Selection criteria

* ALL English-language Project Gutenberg works by Anton Chekhov, Nikolai Gogol,
  and Ivan Turgenev.
* Includes all available editions, alternate translations, and short pieces.
* Shared anthologies that feature these authors are listed at the end.
* PG 55351 (The Three Sisters) is outside the English text catalog and excluded.

## Works (56 total)

### Anton Chekhov (27 works)

#### Plays (4)

- [ ] The Sea-Gull — PG 1754
- [ ] Uncle Vanya: Scenes from Country Life in Four Acts — PG 1756
- [ ] Ivanoff: A Play — PG 1755
- [ ] Swan Song — PG 1753

#### Play Collections (1)

- [ ] Plays by Anton Chekhov, Second Series — PG 7986

#### Novel (1)

- [ ] The Shooting Party — PG 73729

#### Short Story Collections (17)

- [ ] Project Gutenberg Compilation of Short Stories by Chekhov — PG 57333
- [ ] The Lady with the Dog and Other Stories — PG 13415
- [ ] The Bet, and Other Stories — PG 55283
- [ ] The Duel and Other Stories — PG 13505
- [ ] The Cook's Wedding and Other Stories — PG 13417
- [ ] The Schoolmistress, and Other Stories — PG 1732
- [ ] The Wife, and Other Stories — PG 1883
- [ ] The Black Monk, and Other Stories — PG 55307
- [ ] Love, and Other Stories — PG 13414
- [ ] The Darling and Other Stories — PG 13416
- [ ] The Horse-Stealers and Other Stories — PG 13409
- [ ] The Witch, and Other Stories — PG 1944
- [ ] The Bishop and Other Stories — PG 13419
- [ ] The Party and Other Stories — PG 13413
- [ ] Russian Silhouettes: More Stories of Russian Life — PG 66790
- [ ] The House with the Mezzanine and Other Stories — PG 27411
- [ ] The Schoolmaster and Other Stories — PG 13412

#### Short Stories (standalone) (2)

- [ ] The Chorus Girl and Other Stories — PG 13418
- [ ] The Slanderer — PG 23055

#### Epistolary / Non-fiction (2)

- [ ] Letters of Anton Chekhov to His Family and Friends — PG 6408
- [ ] Note-Book of Anton Chekhov — PG 12494

### Nikolai Gogol (6 works)

#### Novel (2)

- [ ] Dead Souls — PG 1081
- [ ] Home Life in Russia, Volumes 1 and 2 [Dead Souls, alternate translation] — PG 58070

#### Play (1)

- [ ] The Inspector-General — PG 3735

#### Story Collections (3)

- [ ] Taras Bulba, and Other Tales — PG 1197
- [ ] The Mantle, and Other Stories — PG 36238
- [ ] Cossack Tales — PG 58409

### Ivan Turgenev (23 works)

#### Novels (7)

- [ ] Fathers and Sons — PG 47935
- [ ] Fathers and Children [alternate translation] — PG 30723
- [ ] Virgin Soil — PG 2466
- [ ] On the Eve: A Novel — PG 6902
- [ ] Rudin: A Novel — PG 6900
- [ ] Smoke — PG 40813
- [ ] A House of Gentlefolk — PG 5721

#### Alternate Translation (1)

- [ ] Liza; Or, "A Nest of Nobles" [alternate translation of A House of Gentlefolk] — PG 12194

#### Sketches (2)

- [ ] A Sportsman's Sketches, Volume 1 — PG 8597
- [ ] A Sportsman's Sketches, Volume 2 — PG 8744

#### Novellas / Short Works (3)

- [ ] The Torrents of Spring — PG 9911
- [ ] A Nobleman's Nest — PG 25771
- [ ] Annouchka: A Tale — PG 39427

#### Short Story Collections (7)

- [ ] The Jew and Other Stories — PG 8696
- [ ] First Love, and Other Stories — PG 56878
- [ ] A Desperate Character and Other Stories — PG 8871
- [ ] The Diary of a Superfluous Man, and Other Stories — PG 41201
- [ ] The Diary of a Superfluous Man, and Other Stories [alternate edition] — PG 9615
- [ ] A Reckless Character, and Other Stories — PG 15994
- [ ] Knock, Knock, Knock and Other Stories — PG 7120

#### Novellas / Misc (2)

- [ ] A Lear of the Steppes, etc. — PG 52642
- [ ] The Rendezvous — PG 23056

#### Prose Poetry (1)

- [ ] Dream Tales and Prose Poems — PG 8935

### Shared Anthologies (2)

- [ ] Best Russian Short Stories — PG 13437
- [ ] Stories by Foreign Authors: Russian — PG 5741

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

## References

- `.claude/skills/gutenbit-live-battle-test/references/kei-17-corpus.md`
- `tests/test_battle.py`
- `AGENTS.md`

## Close-out

For each work, check the box and annotate with either:
- ✅ if no parser issue found, with a brief note
- ⚠️ if a known limitation exists (source HTML defect, pre-existing issue family), with a note
- 🔧 if a parser fix was applied, referencing the commit or PR

Post a summary comment with structured results tables (clean passes, issues found, fixes
applied) before closing the issue.
