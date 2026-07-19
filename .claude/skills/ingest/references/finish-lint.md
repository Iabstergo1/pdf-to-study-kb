# ingest / phase F — finish + publish gate (zero LLM)

**Inputs:** all of this source's proposed pages.
**Outputs:** promote to published + rebuild derived files, or roll back + Review-Queue.
**Persisted:** a lint line appended to `log.md`; failures to `review_proposals` + `wiki/Review-Queue/`.
**Failure stop:** when lint fails, stop and hand back.

## Steps

1. After every window is done, **do phase E first (synthesis, first-class):** update `overview.md` (every
   source) + build `topic`/`comparison`/`synthesis` as needed, into the matching window's `--writes` (detail
   in `references/synthesis.md`). **Skipping it makes lint `L7-synthesis-missing` and rolls back.**
2. Write/update `sources/<src>.md` (the source summary page; body is purpose-driven, no fixed template).
3. `python scripts/pipeline.py ingest-done --source <src>` — state advances to `ingested/proposed`, releases the vault lock.
4. **Finishing gate:** `python scripts/pipeline.py lint --source <src>`.
   - **Vault preflight（先于批检查，事务隔离）** → if *previously published* pages (any source) carry
     render-safety violations (callout type/nesting, math delimiters, empty question stems), lint blocks
     promote and queues them to `Review-Queue/vault-health-*.md`, **but the current batch is NOT rolled
     back** — proposed pages and in-place edits stay intact. Fix the old page (small human-confirmed edit,
     or reopen its source) and simply re-run `lint`; do not redo this batch's work.
   - **Pass** → proposed promotes to `published`, folds into `index.generated.md`, rebuilds `_registry.yaml` (`aliases.md` is retired — aliases stay in concept frontmatter) + the derived reading layer (knowledge graph + `quiz-index.generated.md` + `propositions.generated.md`, publish-isolated: their failure never blocks publish), and cleans this source's stale `<src>-lint-*.md` failure reports. Report which pages were published.
   - **Fail（current-batch violations only）** → in-place merges are rolled back, the violation list goes to `wiki/Review-Queue/<src>-lint-*.md`; **stop** and give the user the violations + fix suggestions (edit pages and re-run `lint`, or use kb-review). **⚠ Rollback restores in-place-merged pages (`overview.md` / existing concept merges) to their pre-edit state — after fixing the violations you MUST re-apply those in-place edits before re-running `lint`, or the "updated overview" silently reverts to the seed (happened on two consecutive books; the `overview-seed` / `source-page-missing` gates now fail-closed on this).**

## Skipping phase E → lint blocks (no longer a soft warning)

If you run windows then `ingest-done → lint` without phase E, lint reports **`L7-synthesis-missing` and
rolls back** (fail-closed when concepts exist but there is no synthesis page). **The fix is to complete
phase E before finishing.** To remediate an earlier published source that lacks synthesis:
`python scripts/pipeline.py reopen --source <src>` (rebuilds the work order against the current vault +
resets the state machine to `workorder_ready`), `ingest-start` as usual, write the overview knowledge map /
comparison / topic / synthesis (re-express needs_vision formulas as native KaTeX — never embed source images,
add worked examples to key concepts, trim redundant wikilinks to strong relations only), then
`ingest-done → lint`. Incremental lint only promotes this round's pages; existing published pages are untouched.

## Pipeline completion (not content acceptance)

`lint` passing means **structural publish only** — the order/safety/provenance gates held. It is NOT
content acceptance: lint cannot verify that page content actually comes from the source (CLAUDE.md §7's
stated audit limit). **Content acceptance requires an independent kb-qa content-fidelity pass** — run by
a different session/agent than the one that wrote the pages — and a human decision on its report. The
ingesting session reports its outcome as **"published, pending content acceptance"**; it never declares
acceptance for its own writing (2026-07-17/19 postmortems: the executor's own "acceptable" verdict was
wrong twice).

- Publish pass: `pipeline status` shows the source `lint / published`; `index.generated.md` includes the new pages (published only); **synthesis exists (no `L7-synthesis-missing`)**.
- Fail (current-batch violations): rolled back to pre-ingest, violations in Review-Queue, the source sits at `lint/failed` (the state machine allows a return to `ingest_waiting` to re-run after fixes).
- Blocked by vault preflight (old published pages' render-safety): **no rollback and no `lint/failed` state** — the batch stays proposed intact; fix the old page(s) listed in `Review-Queue/vault-health-*.md`, then simply re-run `lint`.

After publish, optionally run the `kb-postmortem` skill: proxy metrics + digest deviations + backlog delta
into one retrospective report, so this book's lessons feed the skill-evolution loop instead of evaporating.
