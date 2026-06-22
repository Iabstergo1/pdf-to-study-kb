---
name: kb-save
description: Save a synthesis/comparison/learning-path/self-test candidate from an existing query-session into the wiki as status:proposed (gated, two-phase publish). Use when, after a query, the user says "save that comparison/conclusion into the wiki / form a synthesis / save to the KB / keep this as a note". One-off facts, plain explanations, and restating an existing page are not saved.
---

# kb-save — explicit save (the second step of the query → save loop)

Acts on an existing query-session: check the save admission gate first, then write qualifying candidates as
`status: proposed` pages or a Review-Queue proposal. The execution layer is `scripts/pipeline.py`; this
skill only orchestrates, surfaces acceptance, and marks failure stops.

## 1. Triggers / Non-triggers

- **Triggers:** "save that comparison/conclusion into the wiki", "form a synthesis", "save to the KB", "keep this as a note", "write that query-session back".
- **Non-triggers:** one-off facts, plain explanations, translation, source-less speculation, restating an existing page; a direct write request with no query-session (first switch to `kb-query` or ask for a run_id); a new external source (use `ingest`).

## 2. Inputs

- `<run_id>`: the user-named query-session, or the most recent `kb-query` run_id.
- Read: `pipeline-workspace/query-sessions/<run_id>/{question.md,answer.md,related_pages.json,candidate_write_set.json,evidence_refs.json}`.
- Read: `docs/skill-runtime/save-back-policy.md`, `docs/skill-runtime/schema.md`, `docs/skill-runtime/concept-resolution.md`, and the relevant vault pages.

## 3. Outputs

- Below the admission gate: refuse clearly with a reason; write nothing.
- Above the gate: write/update `topics/**`, `comparisons/**`, `synthesis/**`, relevant concept pages, `overview.md`, `log.md`; all `status: proposed` + `managed_by: pipeline`.
- Overwrite-protection DENY or a human-page conflict: write `wiki/Review-Queue/<page>-proposal.md`, do not edit the target.
- Update the query-session: record what was actually written + evidence, and write `decision.md`.

## 4. Dependencies

- CLI: `resolve-concept`, `check-write`, `snapshot-page`, `check-session --saved`; the finishing publish is decided by `lint`.
- Protocols: `save-back-policy.md` (admission gate), `schema.md` (page structure), `concept-resolution.md` (concept resolution).
- Write discipline matches `ingest`: concepts merge on hit, never duplicate; never hand-write derived files.

## 5. Persisted artifacts

- `pipeline-workspace/query-sessions/<run_id>/decision.md`: why saved / which pages written / which evidence cited / why no existing concept was polluted.
- `candidate_write_set.json`: updated to the pages actually/intended written.
- `evidence_refs.json`: completed with the evidence actually used.
- vault proposed pages or `wiki/Review-Queue/*-proposal.md`.

## 6. CLI commands

```text
python scripts/pipeline.py resolve-concept --mention "<mention>" --domain <domain> [--alias "<alias>"] [--ref-source <source_id> --ref-sections "<sections>"]
python scripts/pipeline.py check-write --source kb-save --path <vault-rel-path>
python scripts/pipeline.py snapshot-page --source kb-save --path <vault-rel-path>
python scripts/pipeline.py check-session --id <run_id> --saved
```

After saving, prompt the user to run `python scripts/pipeline.py lint --source kb-save` (or the project's current finishing gate); do not bypass two-phase publish.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| S1 read session | run_id | session content + related pages | required files present, JSON parses | — | session missing |
| S2 admission | session + save-back-policy | save/refuse decision | at least one gate condition holds and evidence_refs non-empty | `decision.md` draft | below the gate |
| S3 resolve concepts | candidate concepts | canonical_id + concept page | only via resolve-concept, merge on hit | concept frontmatter | registry corrupt |
| S4 write proposed | candidate pages | proposed pages or a proposal | write only after check-write ALLOW; snapshot before overwrite | vault / Review-Queue | check-write DENY |
| S5 session self-check | write result | check-session --saved result | passes the saved-mode contract | full session dir | self-check fails |
| S6 publish prompt | proposed pages | lint handoff note | user knows lint decides promotion | chat summary | on lint failure hand to kb-review |

## 8. Failure stops / recovery

query-session missing or incomplete; `evidence_refs.json` empty; below `save-back-policy`; `check-write`
DENY; target `managed_by: human`; concept registry corrupt; `check-session --saved` fails; the user asks to
overwrite a human page. **Recovery:** keep `decision.md` + the session as the durable record; a DENY becomes
a `Review-Queue` proposal handed to `kb-review`.

## 9. Acceptance criteria

- When not saving, no change under `wiki/`, with a stated reason.
- When saving, every written page is `status: proposed` + `managed_by: pipeline`.
- Concepts only via `resolve-concept`, no duplicate canonical_id.
- `decision.md` written; `candidate_write_set.json` / `evidence_refs.json` updated.
- `python scripts/pipeline.py check-session --id <run_id> --saved` passes.
- Prompted the finishing `lint` to decide promotion; a lint failure goes to `kb-review`.
