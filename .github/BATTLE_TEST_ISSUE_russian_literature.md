# GitHub Issue: Battle test 15 works of Chekhov, Gogol, and Turgenev

**Labels:** `test`

---

## Issue Title

Battle test all works of Chekhov, Gogol, and Turgenev

## Issue Body

Run a full live battle test of the CLI and parsing functionality for each work below.

## Selection criteria

* English-language Project Gutenberg works only.
* Prioritize major, well-known works of literature.
* Each work is confirmed absent from existing GitHub battle-test issues,
  `tests/test_battle.py`, and the kei-17-corpus reference.
* Breadth across authors, periods, and structural styles.

## Works (15 total)

### Anton Chekhov (5 works)

- [ ] The Sea-Gull — PG 1754
- [ ] Uncle Vanya: Scenes from Country Life in Four Acts — PG 1756
- [ ] Ivanoff: A Play — PG 1755
- [ ] Plays by Anton Chekhov, Second Series — PG 7986
- [ ] Letters of Anton Chekhov to His Family and Friends — PG 6408

### Nikolai Gogol (5 works)

- [ ] Dead Souls — PG 1081
- [ ] The Inspector-General — PG 3735
- [ ] Taras Bulba, and Other Tales — PG 1197
- [ ] The Mantle, and Other Stories — PG 36238
- [ ] Cossack Tales — PG 58409

### Ivan Turgenev (5 works)

- [ ] Fathers and Sons — PG 47935
- [ ] A Sportsman's Sketches — PG 8597
- [ ] Virgin Soil — PG 2466
- [ ] On the Eve: A Novel — PG 6902
- [ ] Dream Tales and Prose Poems — PG 8935

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
