# ingest / phase F — finish + publish gate (zero LLM)

**Inputs:** all of this source's proposed pages.
**Outputs:** promote to published + rebuild derived files, or roll back + Review-Queue.
**Persisted:** a lint line appended to `log.md`; failures to `review_proposals` + `wiki/Review-Queue/`.
**Failure stop:** when lint fails, stop and hand back.

## Steps

1. After every window is done, **do phase E first (synthesis, first-class):** update `overview.md` (every
   source) + build `topic`/`comparison`/`synthesis` as needed, into the matching window's `--writes` (detail
   in `references/synthesis.md`). **Skipping it makes lint `L7-synthesis-missing` and rolls back.**
2. Write/update `sources/<src>.md` (the source summary page, template `templates/source.md`).
3. `python scripts/pipeline.py ingest-done --source <src>` — state advances to `ingested/proposed`, releases the vault lock.
4. **Finishing gate:** `python scripts/pipeline.py lint --source <src>`.
   - **Pass** → proposed promotes to `published`, folds into `index.generated.md`, rebuilds `_registry.yaml` (`aliases.md` is retired — aliases stay in concept frontmatter). Report which pages were published.
   - **Fail** → in-place merges are rolled back, the violation list goes to `wiki/Review-Queue/<src>-lint-*.md`; **stop** and give the user the violations + fix suggestions (edit pages and re-run `lint`, or use kb-review).

## Skipping phase E → lint blocks (no longer a soft warning)

If you run windows then `ingest-done → lint` without phase E, lint reports **`L7-synthesis-missing` and
rolls back** (fail-closed when concepts exist but there is no synthesis page). **The fix is to complete
phase E before finishing.** To remediate an earlier published source that lacks synthesis:
`python scripts/pipeline.py reopen --source <src>` (rebuilds the work order against the current vault +
resets the state machine to `workorder_ready`), `ingest-start` as usual, write the overview knowledge map /
comparison / topic / synthesis (re-express needs_vision formulas as native KaTeX — never embed source images,
add worked examples to key concepts, trim redundant wikilinks to strong relations only), then
`ingest-done → lint`. Incremental lint only promotes this round's pages; existing published pages are untouched.

## Acceptance

- Pass: `pipeline status` shows the source `lint / published`; `index.generated.md` includes the new pages (published only); **synthesis exists (no `L7-synthesis-missing`)**.
- Fail: rolled back to pre-ingest, violations in Review-Queue, the source sits at `lint/failed` (the state machine allows a return to `ingest_waiting` to re-run after fixes).
