---
name: ingest
description: End-to-end add a new external source (PDF/DOCX/PPTX/Markdown) to the study knowledge base — deterministic preprocessing → read the whole source and write status:proposed pages + concept resolution → finish with lint and publish. Use when the user says "add this book/PDF to the KB / ingest <source> / index this document / weave this file into the wiki". Only for ingesting a new external source; read-only requests like "summarize this / explain this / translate this / answer a trivia question" must never trigger it.
---

# ingest — weave a whole source into the wiki (the only LLM write step; top-level orchestration)

You are the maintainer of the knowledge base. Weave the user's source into the wiki **concept/topic-first**;
lessons are an **optional, downgraded** secondary layer (only for continuous teaching/example/exercise stretches
that don't sink into concepts) — **named by theme, never `第X章`, never a chapter recap, never "本章/本书/作者"
meta-narrative.** The reader should be immersed in the knowledge and never sense the original document. Work under
the work-order transaction protocol the whole way. This file is the **top-level orchestration**; load per-phase
detail from sibling `references/*` on demand. Project truth: `CLAUDE.md`. Engineering format: `docs/skill-runtime/skill-standard.md`.

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
- Read: `wiki/_meta/purpose.md` **first — it is the authority on writing style, structure, depth and
  terminology** (the user's learning goals / teaching preference). The deterministic layer only guards
  order/safety/provenance; **form is purpose-driven, not template-driven.** Then read
  `docs/skill-runtime/{schema,concept-resolution}.md`, `templates/*` (suggested scaffolds, not mandatory
  skeletons), and the phase references.

## 3. Outputs

- Vault writes are always `status: proposed` + `managed_by: pipeline`: lessons / concepts / topics /
  comparisons / synthesis / `sources/<src>.md` / `overview.md`.
- Derived files (`_registry.yaml` / `index.generated.md`) are **not written by this skill** — the finishing
  CLI rebuilds them. **`aliases.md` is retired** (B2): English aliases live only in the concept page's
  `aliases:` frontmatter (Obsidian reads them natively for search/autocomplete).

## 4. Dependencies

- CLI: `scripts/pipeline.py` (commands per phase).
- Protocols: `docs/skill-runtime/schema.md` (page types / per-type frontmatter contract; **section titles are
  no longer mandatory — structure is purpose-driven**), `concept-resolution.md` (resolution + home-domain routing).
- Phase references: `references/preflight.md`, `references/arbitrate.md`, `references/content-routing.md`, `references/write-pages.md`, `references/synthesis.md`, `references/finish-lint.md`.

## 5. Persisted artifacts

- `pipeline-workspace/staging/<src>/`: `source.md`, `blocks.jsonl`, `chapters.json` (deterministic chapter
  map / navigation spine), `reconciliation.json` + `evidence.json` (PyMuPDF×MinerU dual-audit + per-page
  evidence model), `arbitration/{queue,decisions,audit}.json`, `windows.jsonl`,
  `workorder.yaml`, hard-page PNGs, `digest.md` (cross-window rolling digest with a `## RESUME` block).
- `ingest_progress` (per-window accounting, machine state). Rollback snapshots in `pipeline-workspace/snapshots/`.

## 6. CLI commands (orchestration order)

```text
preprocess + auto-arbitration  init-vault → add-source → profile → source-convert → source-audit →[ arbitration-status → if pending: agent arbitrates queue → arbitration-apply ]→ windows → workorder
start / per-window (LLM)  ingest-start → read chapters.json (build whole-book understanding) → write per-chapter content-routing table into digest (advisory; references/content-routing.md)
                          →[ in chapter order: window-start → show-window → write pages per routing orientation (read hard-page source images as evidence; re-express natively — never embed them; deviations logged) → window-done --writes ]×N
synthesis (LLM)           phase E: update overview + build topic/comparison/synthesis (into some window's --writes) — first-class, lint blocks if missing
finish (zero LLM)         ingest-done → lint
incremental reopen        reopen → ingest-start →[ per-window backfill ]→ ingest-done → lint
```

> **Backend selection / dual-audit / reading windows:** `source-convert` defaults to `--backend auto` —
> Markdown / born-digital PDF take the lightweight PyMuPDF path; scanned / low-text PDF, DOCX / PPTX take
> MinerU (fail-closed if absent, never a silent fallback). **`source-audit` runs the MinerU structural
> review of every PDF and writes `reconciliation.json`** (PyMuPDF thresholds are deliberately broad and are
> not a single source of truth); production / strict acceptance requires the dual-audit to pass. **When the
> dual-audit flags a structural page PyMuPDF missed, the auto-arbitration sub-step (`references/arbitrate.md`)
> automatically decides render/ignore/needs_human and the CLI materializes it into the windows — an un-closed
> disagreement blocks strict acceptance.**
> When writing each window, **read it via `show-window`** (output carries heading_path / page range /
> block_ids / risk_flags / assets); **do not guess ranges from `source.md` char offsets.** Block-mode
> (MinerU / structured) pages keep traceable `block_ids` / `source_refs` / `assets`.

> **reopen (incremental backfill of a published source):** to add synthesis / native KaTeX re-expressions
> of formula pages / worked examples to an already-finished source, first
> `python scripts/pipeline.py reopen --source <src>` — it rebuilds the work order against the current vault
> and resets the state machine to `workorder_ready`; then `ingest-start` as usual. lint only promotes this
> round's new/edited `proposed` pages; existing `published` pages stay. New topic/comparison/synthesis/
> overview pages carry `source_refs` for **ownership**, but ownership never substitutes for **accounting**
> — put them in some window's `--writes` or lint blocks them as `unaccounted-write`.

## 7. Workflow (load references on demand)

| Phase | File | Responsibility |
|---|---|---|
| A preprocess | `references/preflight.md` | deterministic chain + dual-audit acceptance (needs_vision / degraded warnings / reconciliation / window coverage) |
| A.5 auto-arbitration | `references/arbitrate.md` | when the dual-audit flags un-closed disagreements, the agent auto-decides render/ignore/needs_human (structured only); the CLI materializes → the windows carry the assets |
| B0 content routing | `references/content-routing.md` | after reading chapters.json, route each chapter to a content type (理论/方法/案例/参考/观点) → per-chapter `## 路由表` in digest; **advisory** — deviations written as `[routing-deviation]` markers (revision evidence for skill-evolve); purpose.md supreme |
| B+C+D per-window writing | `references/write-pages.md` | start guard + **read chapters.json for whole-book understanding** + per-window sub-units U1–U7 + read source images as evidence & re-express natively (never embed) + writing discipline + lint hard rules |
| E synthesis | `references/synthesis.md` | incremental overview/topic/comparison/synthesis |
| F finish | `references/finish-lint.md` | ingest-done + lint promote/rollback + derived rebuild |

## 8. Failure stops / recovery

Any preprocessing step errors; `check-write` DENY (out of scope / overwrite protection); lint fails;
`managed_by: human` page conflict; cross-domain promotion candidate; the vault lock is held. **Recovery:**
after an interruption, re-read `chapters.json` + the digest `## RESUME` block, **and re-read
`references/write-pages.md` before writing any page** — an interrupted session has lost the writing
contracts (prose organization, self-test nesting, accounting), and a fresh page's seed scaffold never
substitutes for the contract file; then resume from the next unfinished window (`pipeline.py next` is
the machine anchor; `next --source <src> --resume-packet` hands you the structured RESUME_PACKET —
ledger-decided next window + write boundary + digest RESUME + resume-critical excerpt — and fail-closes
on a stale RESUME instead of emitting a half-true packet); otherwise auto-advance and report progress.

## 9. Acceptance criteria

> Scope: these are **pipeline completion** criteria (structure / order / safety / provenance-accounting).
> Content acceptance is not this session's call: it requires an independent kb-qa content-fidelity pass
> plus a human decision on its report (`references/finish-lint.md`); the ingesting session reports
> "published, pending content acceptance" and never declares acceptance for its own writing.

- Preprocess: `workorder.yaml` generated; `ingest-start` took the lock + the stale-registry check passed;
  for PDFs, `source-audit` produced `reconciliation.json` + `evidence.json`, every dual-audit disagreement was
  arbitrated + materialized, and strict `preflight-eval` passes both `dual_audit` and `check_evidence_bundle`
  (the windows carry the source images for arbitrated pages).
- Writing: every page `check-write` ALLOW, page_rules self-check 0 violations, every non-source page in a `window-done --writes`.
- Synthesis (phase E mandatory): overview updated (not a bare link list) + at least one topic/comparison/synthesis, all in `--writes`; otherwise `lint` reports `L7-synthesis-missing` and rolls back.
- Finish: `lint` passes (promoted into the index), or failures land in `Review-Queue/` and the round is rolled back.
