---
name: source-xray
description: Generate a book-breakdown reading note or synthesis-candidate report from published source/concept/topic content, writing only pipeline-workspace/reports/source-xray/ by default (not the vault). Use when the user says "x-ray this published source / book-breakdown reading notes / source-xray / generate learning-note candidates". It does not preprocess, does not decide windows, does not decide write scope, and does not create or merge concept pages.
---

# source-xray — reading-note report from a published source

Generate reading notes or synthesis candidates from published source/concept/topic content. It does not
write the vault by default; if the user wants it saved into the wiki, hand off to `kb-save` for two-phase
publish. This skill works from **published content only**.

## 1. Triggers / Non-triggers

- **Triggers:** "x-ray this published source", "book-breakdown reading notes", "source-xray", "generate learning-note candidates", "organize a learning path from published content".
- **Non-triggers:** preprocessing a new source (use `source-preflight`); ingesting a new source (use `ingest`); querying existing knowledge (use `kb-query`); saving the report into the wiki (use `kb-save`); a semantic health check (use `wiki-lint-semantic`).

## 2. Inputs

- `<src>` or a published source page path.
- Read: `wiki/sources/<src>.md`, that source's published lessons/concepts/topics/comparisons/synthesis, and `wiki/index.generated.md`.
- Published content only; if the source is not yet published, stop and suggest finishing `ingest`/`lint` first.

## 3. Outputs

- By default, write `pipeline-workspace/reports/source-xray/<src>.md`.
- The report may include: the core question, the consensus baseline, the author/source delta, the key concept map, a learning path, and synthesis candidates that could go to `kb-save`.
- It does not write `wiki/`, does not create `status: proposed` pages, does not update concept pages.

## 4. Dependencies

- Protocols: the source-xray guard in `docs/skill-runtime/skill-standard.md`, page roles in `docs/skill-runtime/schema.md`.
- Saving is handed to `kb-save` only, entering two-phase publish via a query-session / evidence_refs / decision.md.
- It does not depend on preprocessing staging and does not read an unpublished source.md as a primary basis.

## 5. Persisted artifacts

- `pipeline-workspace/reports/source-xray/<src>.md`.
- Optional: to turn the report into a save candidate, first land a query-session, then hand to `kb-save`; this skill never writes the vault directly.

## 6. CLI commands

```text
python scripts/pipeline.py status
```

This only confirms the source's publish state. There is no dedicated business CLI; this skill's output is a report file, not a vault content page.

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| X1 publish check | src/index/status | published-source judgement | published content only | report draft | source unpublished |
| X2 collect material | source + related pages | material list | paths real, with source refs | report draft | related pages missing |
| X3 structural extract | published material | core question/baseline/delta/concept map | does not change write scope, no unit planning | report | evidence thin |
| X4 candidate tagging | report content | synthesis/learning-path candidates | candidates carry evidence refs | report | drop candidates without evidence |
| X5 handoff | report | whether to switch to kb-save | does not write the vault by default | report | user asks to save → switch to kb-save |

## 8. Failure stops / recovery

source unpublished; only unpublished staging content is found; the user asks to preprocess, decide windows,
decide write scope, or create/merge concept pages; evidence thin; the user asks to write the vault directly.
**Recovery:** the report is the durable output; on a save request, hand off to `kb-save`.

## 9. Acceptance criteria

- Explicitly honored: **does not preprocess** / **does not decide windows** / **does not decide write scope** / does not create or **merge concept pages** / **published content only** / **does not write the vault** by default.
- The report is written to `pipeline-workspace/reports/source-xray/<src>.md`.
- Every synthesis or learning-path candidate carries evidence refs.
- No change under `wiki/`.
- If the user wants it saved, it was handed to `kb-save` rather than written directly.
