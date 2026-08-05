---
name: kb-review
description: Work the Review-Queue and review_proposals items one by one (lint failure lists, cross-domain promotion candidates, overwrite-protection-rejected change proposals), giving analysis and fix suggestions; the final accept/reject is the user's. Use when the user says "work the review queue / look at the pending items / what's in the Review-Queue / walk me through the review backlog".
---

# kb-review — Review-Queue processing

Work `wiki/Review-Queue/` and `review_proposals` items one by one. By default it only analyzes and
suggests; **the final accept/reject is the user's.** The execution layer is `scripts/pipeline.py`; this
skill only orchestrates, surfaces acceptance, and marks failure stops.

## 1. Triggers / Non-triggers

- **Triggers:** "work the review queue", "look at the pending items", "what's in the Review-Queue", "walk me through the review backlog", "handle the lint failures / cross-domain promotions / overwrite proposals".
- **Non-triggers:** ingesting a new source (use `ingest`); read-only queries (use `kb-query`); saving a query result (use `kb-save`); a whole-vault semantic check (use `wiki-lint-semantic`); never accept a proposal or edit a human page without user confirmation.

## 2. Inputs

- `wiki/Review-Queue/*.md`: lint failure lists, `promotion-*.md`, `*-proposal.md`, semantic-lint reports.
- The machine ledger: the `review_proposals` table, via `python scripts/pipeline.py status` or related CLI state.
- Relevant vault pages, the concept registry, source state.

## 3. Outputs

- For each item: a classification, the risk, a suggested fix, and whether a user decision is needed.
- After the user confirms, execute the matching fix/promotion/marking; without confirmation, do not edit the target.
  For an `adopted/published` legacy page, prepare a `revise-adopted` sidecar operation instead of
  fabricating an ingest workorder or editing live first.
- Append `> handled: <conclusion>` to a processed Review-Queue item, or note that it still awaits the user.

## 4. Dependencies

- CLI: `status`, `lint`, `revise-adopted`, `promote-concept`, `rebuild-registry`; if needed, return to
  `ingest` or the target source's lint loop.
- Protocols: `docs/skill-runtime/schema.md`, `concept-resolution.md`, `save-back-policy.md`.
- Human-page protection stays top priority: a human page is edited by the user, never auto-overwritten by the skill.

## 5. Persisted artifacts

- The handled-markers on `wiki/Review-Queue/*.md`.
- If the user confirms a fix: the matching proposed page, the registry-derived rebuild, or a new proposal.
- The machine `review_proposals` stays the ledger; this skill does not hand-write the database.

## 6. CLI commands

```text
python scripts/pipeline.py status
python scripts/pipeline.py revise-adopted --source <legacy_source> --request <revision-request.yaml>
python scripts/pipeline.py revise-adopted --source <legacy_source> --request <revision-request.yaml> --apply
python scripts/pipeline.py lint --source <source_id>
python scripts/pipeline.py promote-concept --id <canonical_id>
python scripts/pipeline.py rebuild-registry
python scripts/pipeline.py proposals-resolve --signature <kind> [--source <src>] --all-matching --apply
```

Run vault-changing commands (e.g. `promote-concept` / page fixes / marking handled) only after user confirmation.
After a fix is verified, retire the matching `review_proposals` rows with `proposals-resolve` (dry-run
first, then `--apply` after user confirmation) so the skill-mine backlog stops counting fixed signals.

For adopted legacy pages, **write authorization is not content attribution**. The revision request must
name each target, reason, expiry, and structured HTTPS evidence; the credential freezes the current live
page. Edit only the generated `candidate/files/` copies, never the live pages. `source_refs` and identity
frontmatter stay unchanged; external support is added through the credentialed `citations`. The first
`--apply` prepares the operation, and a later identical `--apply` commits only after full overlay lint.
Before preparation, declared citation-removal SHA values must be a subset of the current live page
citations; an unknown SHA fails closed before sidecar creation and reports the available source/URL summaries.
If the operation is `committing`, recover that operation before any ordinary lint. Do not use
`revise-adopted` for human-managed pages or as a substitute for a real book ingest.

To build the removal list mechanically instead of hand-writing digests, run the read-only exporter
(zero writes, no `--request` needed):

```text
python scripts/pipeline.py revise-adopted --source <legacy_source> --emit-removal-sha <page>
```

It prints every citation on the page as `sha256  source=...  title=...  url=...`.
The request schema also accepts two optional, audited fields:

- page-level `frontmatter_updates.aliases.remove: [...]` declares a controlled change of an immutable
  frontmatter key (currently only `aliases`). The authorization records both the expected post-update
  identity and the pre-update identity; the candidate must match the declared update — declaring
  without applying, or applying without declaring, both fail closed;
- evidence `citation.url` is optional when `citation.source` is a registered source_id
  (state `sources` table or `sources/<id>.md`); a URL, when present, must still be HTTPS.
  `evidence` may be an empty list when the change is a pure citation removal or a body/meta
  edit with no new citations; the operation cannot complete until the candidate is genuinely
  edited (a byte-identical candidate stays in the prepared/waiting state), and `reason` is
  the only justification record in that case.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| R1 collect queue | Review-Queue + status | pending list | files and ledger aligned | — | queue missing |
| R2 classify | one item | lint/promotion/coverage/semantic/overwrite class | class maps to a fix path | analysis draft | type unclear |
| R3 suggest | item + related pages | fix/reject/promote suggestion | states risk, affected pages, commands | chat output | evidence thin |
| R4 user confirm | user decision | execute or reject | no target edit without confirmation; adopted legacy edits use a bounded revise-adopted request | Review-Queue mark / sidecar operation | human-page conflict |
| R5 verify loop | fix result | lint/rebuild/check result | the command passes or re-enqueues on failure | new proposal/mark | verify fails |

## 8. Failure stops / recovery

User has not confirmed; target `managed_by: human`; cross-domain promotion semantics unclear; a homonym
promotion; a lint fix would exceed the write scope; `promote-concept` or `rebuild-registry` fails; an item
lacks evidence; an adopted revision request is expired; an operation is already `committing`; a revert
target has changed since its recorded post. **Recovery:** unresolved items stay in `Review-Queue` with
their state. Abort an uncommitted prepared operation, recover a committing operation with its frozen
manifest, or use a new forward `mode: edit` after later live drift; then re-run the matching lint.

## 9. Acceptance criteria

- Every pending item has a class, a suggestion, and a user-decision state.
- No target vault page edited without user confirmation.
- A promotion-candidate has been judged "semantic reuse vs homonym".
- After a confirmed promotion, `promote-concept` + `rebuild-registry` were run.
- After a lint-violation fix, the matching `lint` was re-run; failures stay in Review-Queue.
- Adopted legacy fixes used `revise-adopted`; their citations match the approved evidence and their
  `source_refs` were not changed by the authorization path.
- No human page was auto-overwritten.
