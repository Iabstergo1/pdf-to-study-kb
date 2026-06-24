# ingest / phase A — deterministic preprocessing (zero LLM, re-runnable, idempotent skip)

**Inputs:** `<src>` / `<domain>` / `<path>` / `<fmt>`.
**Outputs:** `staging/<src>/{source.md, blocks.jsonl, chapters.json, parse_report.json, reconciliation.json, evidence.json, arbitration/queue.json, windows.jsonl, workorder.yaml}` + hard-page PNGs.
**Persisted:** the staging artifacts above + SQLite stage state.
**Failure stop:** any step errors → stop and report; never skip ahead.

## Steps

1. If `wiki/` is absent: `python scripts/pipeline.py init-vault` (idempotent, never overwrites existing files).
2. Run in order (each idempotent; unchanged input prints `[skip]`):
   - `python scripts/pipeline.py add-source --source <src> --domain <domain> --path <path> --fmt <fmt>`
   - `python scripts/pipeline.py profile --source <src>`
   - `python scripts/pipeline.py source-convert --source <src>`
   - `python scripts/pipeline.py source-audit --source <src>` (PDF dual-audit: MinerU reviews PyMuPDF → `reconciliation.json` + `evidence.json` + `arbitration/queue.json`; add `--strict` for production / strict acceptance, fail-closed if MinerU is unavailable)
   - **auto-arbitration (when the dual-audit found un-closed disagreements):** `arbitration-status` → if pending, arbitrate the queue → `arbitration-apply` — see `references/arbitrate.md`. This runs automatically before windows; an un-closed disagreement blocks strict acceptance.
   - `python scripts/pipeline.py windows --source <src>`
   - `python scripts/pipeline.py workorder --source <src>`
3. Read `pipeline-workspace/staging/<src>/workorder.yaml` — it defines your entire write boundary
   (`write_scope`), the registry hash, and page snapshots. **No work order, no phase B.**

## Acceptance (must hold before phase B)

- `workorder.yaml` is generated and `write_scope` covers `domains/<domain>/**` etc.
- **Dual-audit (PDF):** `reconciliation.json` exists (from `source-audit`). Strict acceptance requires
  `dual_audited=true`; otherwise `preflight-eval` reports a `dual_audit` failure (PyMuPDF thresholds are
  deliberately broad and are not a single source of truth — PyMuPDF-only output is not production-accepted).
- **Evidence bundle closed (PDF):** every dual-audit disagreement candidate has been arbitrated and
  materialized — `preflight-eval`'s `check_evidence_bundle` is green (no un-arbitrated / un-materialized /
  pending `needs_human`); the windows ingest reads carry the source images for arbitrated pages.
- **Evidence-risk recorded (PDF):** `evidence.json` carries per-page `risk_flags` — hard (`formula_text_loss`
  / `formula_undetected` / `table_linearization` / `figure_missing_asset`) become arbitration candidates when
  the page has no visual asset; soft (`reading_order_risk` / `heading_structure_risk`) are written into the
  window's `risk_flags` deterministically and only observed by `check_risk_coverage` (never blocking).
  `source.md` stays the **primary extracted text** — risk is recorded beside it, never by rewriting it.
- **needs_vision sane:** `source-convert`'s hard-page count should not be 0 (a book with formulas/figures
  should flag some pages); 0 on such a source is suspicious — review.
- **Hard pages (route B):** `source-convert` marks hard pages (formula / vector figure / table / caption,
  high recall) with `[info]` and renders a full-page PNG each. Confirm the PNGs exist and `pages.jsonl`
  carries `needs_vision_reason`.
- **Window coverage:** `windows.jsonl` char ranges cover all of `source.md` (no large gaps).
