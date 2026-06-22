---
name: wiki-lint-semantic
description: Run the judgement-requiring health checks on the KB — whether a comparison truly covers the key difference dimensions (L4), whether cross-page conclusions contradict, whether recent kb-save output adds real value (Q2) — emitting Review-Queue proposals only, never editing any wiki page. Use when the user says "do a semantic health check / check for contradictions / see whether the comparison pages are complete". Deterministic lint (L1/L2/L3/L5/L6/broken-links/duplicates) is handled by scripts/pipeline.py lint, not here.
---

# wiki-lint-semantic — semantic lint (the half the finishing CLI cannot do)

Deterministic lint (L1/L2/L3/L5/L6/broken-links/duplicates) is handled by `python scripts/pipeline.py lint`;
this skill does only the judgement-requiring part. **It emits Review-Queue proposals only and never directly
edits any wiki content page.** The execution layer is `scripts/pipeline.py`; this skill only orchestrates.

## 1. Triggers / Non-triggers

- **Triggers:** "do a semantic health check", "check for contradictions", "see whether the comparison pages are complete", "check whether the kb-save output adds value".
- **Non-triggers:** broken links / missing sections / bare evidence IDs / orphan pages and other deterministic lint (use `python scripts/pipeline.py lint`); read-only Q&A (use `kb-query`); handling an existing Review-Queue (use `kb-review`); coverage, Q-chain, formula/evidence spot-checks and other broad QA/audit requests belong to `kb-qa` — do not hijack its triggers.

## 2. Inputs

- `wiki/comparisons/**`, `wiki/topics/**`, `wiki/synthesis/**`, `wiki/overview.md`, and the relevant concept/lesson/source pages.
- Recent `kb-save` query-sessions (especially `decision.md`, `candidate_write_set.json`, `evidence_refs.json`).
- The existing `wiki/Review-Queue/`, to avoid duplicate proposals.

## 3. Outputs

- `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`.
- Each proposal carries: page path, problem type (L4 / contradiction / Q2), evidence, suggested fix direction, whether a user decision is needed.
- It **does not directly edit** comparison/topic/synthesis/concept/lesson/source content pages.

## 4. Dependencies

- CLI: may run/reference `python scripts/pipeline.py lint --source <source_id>` for the deterministic result, but does not re-implement L1/L2/L3/L5/L6/broken-link/duplicate rules.
- Protocols: `docs/skill-runtime/schema.md` (page roles / required sections), `save-back-policy.md` (Q2 save-value judgement).
- Follow-up handling goes to `kb-review`: this skill only files proposals.

## 5. Persisted artifacts

- `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`: the semantic-lint proposals.
- A chat summary: scope checked, number of findings, suggestion to handle via `kb-review`.

## 6. CLI commands

```text
python scripts/pipeline.py lint --source <source_id>
python scripts/pipeline.py status
```

These only confirm the deterministic gate / state background; semantic judgements become Review-Queue proposals, never a direct promote or content edit.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| L1 scope | request + vault index | page set to check | focus on comparison/contradiction/Q2, not broad QA | check list | vault missing |
| L2 comparison L4 | comparison pages | key-dimension gaps | covers assumptions/conditions/outcomes/costs | proposal draft | page evidence thin |
| L3 contradiction | related concept/topic/lesson | conflicting claim pairs | claims locatable under one concept/model | proposal draft | source not locatable |
| L4 Q2 | kb-save output + evidence | added-value judgement | distinguishes new synthesis from restating | proposal draft | evidence missing |
| L5 write proposals | proposal drafts | semantic-lint file | each has path/problem/evidence/fix direction | Review-Queue | duplicate proposal |

## 8. Failure stops / recovery

vault or index missing; deterministic lint already failed and blocks the semantic scope; a page lacks
evidence to judge; the finding already has an open proposal; the user asks to edit content pages directly
(switch to `kb-review` and wait for confirmation). **Recovery:** proposals are the durable output; hand off
to `kb-review`.

## 9. Acceptance criteria

- No wiki content page edited directly.
- `semantic-lint-<YYYY-MM-DD>.md` written to Review-Queue.
- Each proposal has a page path, problem description, evidence, and a suggested fix direction.
- L4/Q2/contradiction judgements do not duplicate deterministic-lint responsibilities.
- The user was prompted to handle the proposals via `kb-review`.
