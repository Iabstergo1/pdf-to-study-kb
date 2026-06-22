---
name: kb-qa
description: Run QA / audit / coverage checks on the published knowledge base or pre-save candidates, producing a report and Review-Queue proposals. Use when the user says "run a KB QA / audit coverage / spot-check evidence / run the Q-chain / check for concept pollution". Semantic-health words (L4, contradiction, Q2 added value) belong to wiki-lint-semantic and must not be hijacked here.
---

# kb-qa — post-publish / pre-save QA report

Run broad QA over the published vault or a pre-save candidate: coverage, evidence spot-checks, formula
screenshot spot-checks, concept pollution, an ljg-qa-style Q-chain. Produces a report and Review-Queue
proposals only; it does not edit content pages.

## 1. Triggers / Non-triggers

- **Triggers:** "run a KB QA", "audit coverage", "spot-check evidence", "run the Q-chain", "check for concept pollution", "pre-save QA".
- **Non-triggers:** "semantic health check", "find contradictions", "does the comparison cover the key dimensions", "Q2 added value" belong to `wiki-lint-semantic`; handling an existing Review-Queue uses `kb-review`; read-only Q&A uses `kb-query`; a new source uses `ingest`. kb-qa and `wiki-lint-semantic` are **mutually exclusive** on triggers.

## 2. Inputs

- Scope: whole vault, a domain, a source, a query-session, or a proposed write candidate.
- Read: `wiki/index.generated.md`, `wiki/concepts/_registry.yaml`, and the relevant source/concept/topic/comparison/synthesis/lesson pages.
- May read: `pipeline-workspace/query-sessions/<run_id>/`, `wiki/Review-Queue/`, the deterministic `lint` result.

## 3. Outputs

- `pipeline-workspace/reports/kb-qa/<run_id>.md`: the QA report.
- For actionable findings, write `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md` proposals; no direct content-page edits.
- The report covers scope, the Q-chain, sampled items, findings, risk level, and the suggested follow-up skill (usually `kb-review`).

## 4. Dependencies

- CLI: `python scripts/pipeline.py status`; if needed `python scripts/pipeline.py lint --source <source_id>`.
- Protocols: `docs/skill-runtime/schema.md`, `save-back-policy.md`, `concept-resolution.md`.
- Triggers mutually exclusive with `wiki-lint-semantic`: semantic-health words are not handled here.

## 5. Persisted artifacts

- `pipeline-workspace/reports/kb-qa/<run_id>.md`.
- `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md` (only when an actionable finding exists).
- The spot-check list: sampled pages, evidence refs, and Q-chain conclusions recorded in the report.

## 6. CLI commands

```text
python scripts/pipeline.py status
python scripts/pipeline.py lint --source <source_id>
```

This skill never promotes, rolls back, or edits content pages; fixes go to `kb-review` or the matching write skill.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| QA1 scope | request + index | scope and samples | does not hijack wiki-lint-semantic triggers | report draft | scope unclear |
| QA2 status background | source/status/lint | deterministic background | does not re-implement deterministic lint | report | lint already blocked |
| QA3 Q-chain | in-scope material | question→evidence→judgement→action | every Q has an evidence path | report | evidence thin |
| QA4 spot-check | page/formula/evidence samples | spot-check results | samples and conclusions traceable | report | sample missing |
| QA5 file findings | actionable findings | Review-Queue proposal | no content-page edits | kb-qa proposal | duplicate proposal |

## 8. Failure stops / recovery

vault/index missing; scope unclear; the request is really a semantic-health check (switch to
`wiki-lint-semantic`); deterministic lint already failed with no stable QA scope; an evidence path is
missing; the user asks to edit content pages directly (switch to `kb-review` and wait for confirmation).
**Recovery:** the report + proposals are the durable output; re-run after the scope is clarified.

## 9. Acceptance criteria

- The QA report is written under `pipeline-workspace/reports/kb-qa/`.
- Every Q-chain item has a question, evidence, judgement, and a follow-up action.
- Actionable findings are written as Review-Queue proposals.
- No wiki content page was edited directly.
- None of `wiki-lint-semantic`'s exclusive triggers were handled.
