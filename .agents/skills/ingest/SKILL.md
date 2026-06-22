---
name: ingest
description: End-to-end add a new external source (PDF/DOCX/PPTX/Markdown) to the study knowledge base — deterministic preprocessing → read the whole source and write status:proposed pages + concept resolution → finish with lint and publish. Use when the user says "add this book/PDF to the KB / ingest <source> / index this document / weave this file into the wiki". Only for ingesting a new external source; read-only requests like "summarize this / explain this / translate this / answer a trivia question" must never trigger it.
---

# ingest — weave a whole source into the wiki (the only LLM write step; top-level orchestration)

You are the maintainer of the knowledge base. Weave the user's source into the wiki **concept/topic-first**
(lessons follow the source TOC as a secondary layer), under the work-order transaction protocol the whole
way. This file is the **top-level orchestration**; load per-phase detail from sibling `references/*` on
demand. Project truth: `AGENTS.md`. Engineering format: `docs/skill-runtime/skill-standard.md`.

> **Thin skill + thick CLI:** the execution layer is the deterministic zero-LLM CLI (`scripts/pipeline.py`);
> this skill carries no business code, only orchestrates it. `<src>` = this source's source_id; run commands
> from the project root with the study-kb interpreter (on Windows: pwsh + `$env:PYTHONUTF8=1`).

## 1. Triggers / Non-triggers

- **Triggers:** "add this book/PDF to the KB", "ingest \<source\>", "index this document", "weave this file into the wiki".
- **Non-triggers (never fire):** "summarize this", "explain this", "translate this", "answer a trivia question", "what is this PDF about" (a question, not an ingest request).

## 2. Inputs

- The user gives: file path `<path>`, domain `<domain>`; format `<fmt>` is inferred from the extension
  (pdf/md/docx/pptx); `<src>` is derived from the filename (lowercase, hyphenated). **Confirm `<src>` and
  `<domain>` once with the user.**
- Read: `wiki/_meta/purpose.md` (the user's learning goals / teaching preference, a global writing bias
  across page-writing and synthesis; default if absent), `docs/skill-runtime/{schema,concept-resolution}.md`,
  `templates/*`, and the phase references.

## 3. Outputs

- Vault writes are always `status: proposed` + `managed_by: pipeline`: lessons / concepts / topics /
  comparisons / synthesis / `sources/<src>.md` / `overview.md`.
- Derived files (`_registry.yaml` / `aliases.md` / `index.generated.md`) are **not written by this skill** —
  the finishing CLI rebuilds them.

## 4. Dependencies

- CLI: `scripts/pipeline.py` (commands per phase).
- Protocols: `docs/skill-runtime/schema.md` (page types / required sections), `concept-resolution.md` (resolution).
- Phase references: `references/preflight.md`, `references/write-pages.md`, `references/synthesis.md`, `references/finish-lint.md`.

## 5. Persisted artifacts

- `pipeline-workspace/staging/<src>/`: `source.md`, `blocks.jsonl`, `chapters.json` (deterministic chapter
  map / navigation spine), `reconciliation.json` (PyMuPDF×MinerU dual-audit), `windows.jsonl`,
  `workorder.yaml`, hard-page PNGs, `digest.md` (cross-window rolling digest with a `## RESUME` block).
- `ingest_progress` (per-window accounting, machine state). Rollback snapshots in `pipeline-workspace/snapshots/`.

## 6. CLI commands (orchestration order)

```text
preprocess (zero LLM)  init-vault → add-source → profile → source-convert → source-audit → windows → workorder
start / per-window (LLM)  ingest-start → read chapters.json (build whole-book understanding)
                          →[ in chapter order: window-start → show-window → write pages (hard pages embed source images by type) → window-done --writes ]×N
synthesis (LLM)           phase E: update overview + build topic/comparison/synthesis (into some window's --writes) — first-class, lint blocks if missing
finish (zero LLM)         ingest-done → lint
incremental reopen        reopen → ingest-start →[ per-window backfill ]→ ingest-done → lint
```

> **Backend selection / dual-audit / reading windows:** `source-convert` defaults to `--backend auto` —
> Markdown / born-digital PDF take the lightweight PyMuPDF path; scanned / low-text PDF, DOCX / PPTX take
> MinerU (fail-closed if absent, never a silent fallback). **`source-audit` runs the MinerU structural
> review of every PDF and writes `reconciliation.json`** (PyMuPDF thresholds are deliberately broad and are
> not a single source of truth); production / strict acceptance requires the dual-audit to pass.
> When writing each window, **read it via `show-window`** (output carries heading_path / page range /
> block_ids / risk_flags / assets); **do not guess ranges from `source.md` char offsets.** Block-mode
> (MinerU / structured) pages keep traceable `block_ids` / `source_refs` / `assets`.

> **reopen (incremental backfill of a published source):** to add synthesis / formula source images /
> worked examples to an already-finished source, first `python scripts/pipeline.py reopen --source <src>` —
> it rebuilds the work order against the current vault and resets the state machine to `workorder_ready`;
> then `ingest-start` as usual. lint only promotes this round's new/edited `proposed` pages; existing
> `published` pages stay. New synthesis pages have no `source:` owner, so **account for them in some
> window's `--writes`** or they are flagged orphan and blocked.

## 7. Workflow (load references on demand)

| Phase | File | Responsibility |
|---|---|---|
| A preprocess | `references/preflight.md` | deterministic chain + dual-audit acceptance (needs_vision / degraded warnings / reconciliation / window coverage) |
| B+C+D per-window writing | `references/write-pages.md` | start guard + **read chapters.json for whole-book understanding** + per-window sub-units U1–U7 + embed source images by type + writing discipline + lint hard rules |
| E synthesis | `references/synthesis.md` | incremental overview/topic/comparison/synthesis |
| F finish | `references/finish-lint.md` | ingest-done + lint promote/rollback + derived rebuild |

## 8. Failure stops / recovery

Any preprocessing step errors; `check-write` DENY (out of scope / overwrite protection); lint fails;
`managed_by: human` page conflict; cross-domain promotion candidate; the vault lock is held. **Recovery:**
after an interruption, re-read `chapters.json` + the digest `## RESUME` block and resume from the next
unfinished window (`pipeline.py next` is the machine anchor); otherwise auto-advance and report progress.

## 9. Acceptance criteria

- Preprocess: `workorder.yaml` generated; `ingest-start` took the lock + the stale-registry check passed;
  for PDFs, `source-audit` produced `reconciliation.json` and strict `preflight-eval` would pass the dual-audit.
- Writing: every page `check-write` ALLOW, page_rules self-check 0 violations, every non-source page in a `window-done --writes`.
- Synthesis (phase E mandatory): overview updated (not a bare link list) + at least one topic/comparison/synthesis, all in `--writes`; otherwise `lint` reports `L7-synthesis-missing` and rolls back.
- Finish: `lint` passes (promoted into the index), or failures land in `Review-Queue/` and the round is rolled back.
