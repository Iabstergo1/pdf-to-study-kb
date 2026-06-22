---
name: source-preflight
description: Run the deterministic preprocessing chain on a new external source and accept its staging artifacts, without writing any semantic wiki pages. Use when the user says "preprocess this PDF first / run source-preflight / build the source profile first / see if it can be ingested". Only the zero-LLM acceptance gate for add-source, profile, source-convert, source-audit, windows, workorder — no book-splitting, summarizing, semantic unit planning, or vault writes.
---

# source-preflight — source preprocessing acceptance gate (zero semantic LLM)

Run the deterministic CLI preprocessing chain on a candidate source and decide whether its staging
artifacts are good enough to enter `ingest`. This skill is a thin wrapper: it only orchestrates
`scripts/pipeline.py` and does **no LLM semantic splitting / unit planning / page writing**. Project truth:
`CLAUDE.md` / `AGENTS.md`. Engineering format: `docs/skill-runtime/skill-standard.md`.

## 1. Triggers / Non-triggers

- **Triggers:** "preprocess this PDF first", "run source-preflight", "build the source profile first", "see if it can be ingested", "just run up to workorder".
- **Non-triggers:** "add to the KB / index it" with page writing (use `ingest`); "summarize/split this book" without ingesting (use `source-xray`, published content only); "query the wiki" (use `kb-query`); "translate/explain" (a normal answer).

## 2. Inputs

- The user gives: file path `<path>`, domain `<domain>`; `<fmt>` inferred from the extension (pdf/md/docx/pptx); `<src>` derived from the filename. Confirm `<src>` and `<domain>` once.
- Read: the **zero-LLM preprocessing constraint** in `CLAUDE.md` / `AGENTS.md`, and `docs/skill-runtime/schema.md` (to understand the workorder write boundary).

## 3. Outputs

- `pipeline-workspace/staging/<src>/{source.md, blocks.jsonl, chapters.json, parse_report.json, reconciliation.json, windows.jsonl, workorder.yaml, preflight_eval.json}` + hard-page PNGs / figure assets.
- An optional deterministic report `pipeline-workspace/reports/source-preflight/<src>.md`: from `parse_report.json` + `reconciliation.json` show backend, dual-audit status (dual_audited / degraded / disagreements), OCR, table/equation/image counts, discarded (header/footer) count, warnings, and an ingest recommendation; plus CLI status, page count, needs_vision pages, degraded warnings, window coverage, workorder `write_scope`. **No semantic summary.**
- `preflight-eval`'s deterministic JSON (`preflight_eval.json`): page coverage, window monotonicity, table/image/chart asset + source_ref traceability, the **dual-audit gate** (`check_dual_audit`), scan/OCR & low-confidence pages, orphan blocks — reads existing artifacts only, no LLM; `--strict` returns non-zero on a high/fail (CI-able).
- No semantic wiki content pages, no `status: proposed` pages, no concept-page updates.

## 4. Dependencies

- CLI: `init-vault`, `add-source`, `profile`, `source-convert`, `source-audit`, `windows`, `workorder`, `preflight-eval`, `status`.
- The actual ingest is handed to `ingest`; this skill does not inline ingest phases B/C/D/E/F.
- Protocols: `docs/skill-runtime/skill-standard.md` and `docs/skill-runtime/schema.md`.

## 5. Persisted artifacts

- `pipeline-workspace/staging/<src>/source.md`, `reconciliation.json`, `windows.jsonl`, `workorder.yaml`.
- `pipeline-workspace/staging/<src>/assets/pXXXX.png` (needs_vision pages).
- `pipeline-workspace/reports/source-preflight/<src>.md` (if written, deterministic facts only).

## 6. CLI commands

```text
python scripts/pipeline.py init-vault
python scripts/pipeline.py add-source --source <src> --domain <domain> --path <path> --fmt <fmt>
python scripts/pipeline.py profile --source <src>
python scripts/pipeline.py source-convert --source <src>
python scripts/pipeline.py source-audit --source <src> [--strict]   # PDF dual-audit: MinerU reviews PyMuPDF → reconciliation.json
python scripts/pipeline.py windows --source <src>
python scripts/pipeline.py workorder --source <src>
python scripts/pipeline.py preflight-eval --source <src> [--strict]
python scripts/pipeline.py status
```

Each step is idempotent; on any error, stop — do not skip. A PDF must run `source-audit` first (PyMuPDF
thresholds are deliberately broad and are not a single source of truth); strict acceptance requires the
dual-audit to pass, and MinerU unavailable in strict mode is fail-closed. In dev you may omit `source-audit`,
but `preflight-eval` then flags the `dual_audit` check as degraded — that output is **not production-accepted**.
`preflight-eval` reads existing staging only, zero LLM; `--strict` exits non-zero on a high/fail (a hard gate before switching to ingest).

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| P1 confirm source | path/domain/src/fmt | confirmed 4-tuple | src/domain clear | — | user not confirmed |
| P2 run the chain | 4-tuple | source/profile/convert/audit/windows/workorder | each step succeeds or idempotent-skips | staging + SQLite | any step errors |
| P3 accept artifacts | staging | ingest-ready judgement | workorder.yaml exists, windows cover source.md, `reconciliation.json` present, preflight-eval no high/fail | report draft | workorder missing / preflight-eval high |
| P4 dual-audit + formula check | source-audit + source-convert output | dual_audit + needs_vision/PNG record | PDF dual-audited (or degraded recorded); formula pages have a full-page PNG (route B) | report | not dual-audited in strict / formula page unrendered |
| P5 handoff | workorder + report | next-step suggestion | a clear "ingest-ready" or a blocker list | report | user asks to write pages → switch to ingest |

## 8. Failure stops / recovery

Path missing; unsupported fmt; any CLI step fails; `source-convert` missing backend; PDF dual-audit
fail-closed in strict (MinerU unavailable); `windows.jsonl` does not cover the full text; `workorder.yaml`
not generated; a formula risk page has no PNG; `preflight-eval --strict` high/fail (non-zero exit); the user
asks for LLM unit planning or semantic splitting. **Recovery:** every step is idempotent — fix the cause and
re-run from the failed step; `pipeline status` shows where it stopped.

## 9. Acceptance criteria

- No semantic wiki pages written, no `status: proposed` content pages.
- `source.md`, `reconciliation.json`, `windows.jsonl`, `workorder.yaml` exist.
- `workorder.yaml` contains `write_scope` and the registry hash.
- PDF dual-audit recorded (`reconciliation.json` `dual_audited=true`, or a degraded/blocker recorded).
- needs_vision pages have a PNG, or a blocker is recorded.
- `preflight-eval` checks have no high/fail (`--strict` exit 0), or a blocker is recorded.
- The report contains deterministic facts only — no semantic summary / chapter interpretation.
