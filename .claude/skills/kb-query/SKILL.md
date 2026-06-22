---
name: kb-query
description: Read-only query of the existing study knowledge base, persisting a query-session (without writing any vault file). Use when the user asks "what does the KB say about X / search my wiki / what was that Y I learned / has Z ever been covered". Read-only; to keep a conclusion in the wiki, follow up explicitly with kb-save.
---

# kb-query — read-only query + query-session persistence

Answer the user's questions about existing KB content. **Read-only: it writes no file under `wiki/`**, but
it must persist a query-session for later `kb-save` and for audit. The execution layer is
`scripts/pipeline.py`; this skill only orchestrates, surfaces acceptance, and constrains artifacts.

## 1. Triggers / Non-triggers

- **Triggers:** "what does the KB say about X", "search my wiki", "what was that Y I learned", "has Z ever been covered".
- **Non-triggers:** ingesting a new source (use `ingest`); writing a query result back (use `kb-save`); working the Review-Queue (use `kb-review`); a semantic health check (use `wiki-lint-semantic`); plain summary/translate/explain of external text (a normal answer — no query-session unless the user explicitly queries the wiki).

## 2. Inputs

- The user's question; optional domain, concept name, source name, page path, or time range.
- Read: `wiki/index.generated.md`, `wiki/concepts/_registry.yaml`, and the relevant concept/topic/comparison/synthesis/source/lesson pages.
- If the answer might be worth saving, read `docs/skill-runtime/save-back-policy.md` to decide whether to emit a save candidate.

## 3. Outputs

- An in-chat answer citing the relevant vault pages (wikilinks) and source location (source §section / page).
- The query-session files under `pipeline-workspace/query-sessions/<run_id>/`.
- It does not write `wiki/`, does not touch `log.md`, does not promote, does not create proposed pages.

## 4. Dependencies

- Protocols: `docs/skill-runtime/save-back-policy.md` (save-candidate admission), `docs/skill-runtime/schema.md` (page types).
- CLI: `scripts/pipeline.py check-session --id <run_id>` to self-check the query-session.
- Saving is handed to `kb-save` only; this skill does not inline save logic.

## 5. Persisted artifacts

Under `pipeline-workspace/query-sessions/<run_id>/`:

- `question.md`: the original question.
- `answer.md`: this answer.
- `related_pages.json`: the list of vault page paths involved.
- `candidate_write_set.json`: pages worth saving (synthesis/comparison/learning-path candidates), else `[]`.
- `evidence_refs.json`: `[{"source": "...", "sections": ["..."]}]`; `[]` if no source evidence.

## 6. CLI commands

```text
python scripts/pipeline.py check-session --id <run_id>
```

Run `check-session` after writing the query-session; on failure, fix the session files and do not treat a failed session as a save basis.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| Q1 locate material | question + index/registry | relevant page list | paths exist, prefer published content | `related_pages.json` draft | vault/index missing |
| Q2 answer | relevant page bodies | answer with wikilinks / source location | no source-less claims; separate established vs inferred | `answer.md` draft | say so if evidence is thin |
| Q3 save-candidate judgement | answer + save-back-policy | candidate_write_set/evidence_refs | plain explanation / one-off fact → `[]` | JSON files | empty if candidate has no evidence |
| Q4 session self-check | session dir | check-session result | `check-session` passes | full query-session dir | self-check fails |

## 8. Failure stops / recovery

`wiki/index.generated.md` or registry missing; relevant pages missing; query-session write fails;
`check-session` fails; the user asks to write the wiki mid-query (stop this skill, switch to `kb-save`);
never emit a save candidate without source evidence. **Recovery:** re-run `check-session` after fixing the
session files; the session dir is the durable handoff to `kb-save`.

## 9. Acceptance criteria

- No change under `wiki/`.
- The five query-session files exist and the JSON parses.
- Paths in `related_pages.json` really exist.
- `candidate_write_set.json` is `[]` for plain explanation / translation / one-off facts.
- `python scripts/pipeline.py check-session --id <run_id>` passes.
