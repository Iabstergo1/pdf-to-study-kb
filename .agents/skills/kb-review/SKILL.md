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
- Append `> handled: <conclusion>` to a processed Review-Queue item, or note that it still awaits the user.

## 4. Dependencies

- CLI: `status`, `lint`, `promote-concept`, `rebuild-registry`; if needed, return to `ingest` or the target source's lint loop.
- Protocols: `docs/skill-runtime/schema.md`, `concept-resolution.md`, `save-back-policy.md`.
- Human-page protection stays top priority: a human page is edited by the user, never auto-overwritten by the skill.

## 5. Persisted artifacts

- The handled-markers on `wiki/Review-Queue/*.md`.
- If the user confirms a fix: the matching proposed page, the registry-derived rebuild, or a new proposal.
- The machine `review_proposals` stays the ledger; this skill does not hand-write the database.

## 6. CLI commands

```text
python scripts/pipeline.py status
python scripts/pipeline.py lint --source <source_id>
python scripts/pipeline.py promote-concept --id <canonical_id>
python scripts/pipeline.py rebuild-registry
```

Run vault-changing commands (e.g. `promote-concept` / page fixes / marking handled) only after user confirmation.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| R1 collect queue | Review-Queue + status | pending list | files and ledger aligned | — | queue missing |
| R2 classify | one item | lint/promotion/coverage/semantic/overwrite class | class maps to a fix path | analysis draft | type unclear |
| R3 suggest | item + related pages | fix/reject/promote suggestion | states risk, affected pages, commands | chat output | evidence thin |
| R4 user confirm | user decision | execute or reject | no target edit without confirmation | Review-Queue mark | human-page conflict |
| R5 verify loop | fix result | lint/rebuild/check result | the command passes or re-enqueues on failure | new proposal/mark | verify fails |

## 8. Failure stops / recovery

User has not confirmed; target `managed_by: human`; cross-domain promotion semantics unclear; a homonym
promotion; a lint fix would exceed the write scope; `promote-concept` or `rebuild-registry` fails; an item
lacks evidence. **Recovery:** unresolved items stay in `Review-Queue` with their state; re-run the matching
`lint` after fixes.

## 9. Acceptance criteria

- Every pending item has a class, a suggestion, and a user-decision state.
- No target vault page edited without user confirmation.
- A promotion-candidate has been judged "semantic reuse vs homonym".
- After a confirmed promotion, `promote-concept` + `rebuild-registry` were run.
- After a lint-violation fix, the matching `lint` was re-run; failures stay in Review-Queue.
- No human page was auto-overwritten.
