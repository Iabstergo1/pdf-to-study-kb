# ingest / phase A.5 — auto-arbitration of dual-audit disagreements (agent decides, CLI materializes)

**This runs automatically when the dual-audit finds an un-closed structural disagreement** — no separate
human trigger, no per-page human review. `source-audit` (zero LLM) detects "MinerU found a formula / table /
figure on a page PyMuPDF never flagged for vision (so no source image exists)" and writes an arbitration
queue. If the queue is non-empty, you (the agent) **must** arbitrate it inline before windows/acceptance.
You output **structured decisions only** — you never edit `source.md` / `blocks.jsonl` / `windows.jsonl` and
never scan the whole book; the deterministic CLI materializes your decisions.

**Inputs:** `staging/<src>/evidence.json` (per-page model) + `staging/<src>/arbitration/queue.json` (one
minimal packet per candidate: `pymupdf_text_excerpt`, `mineru_structural`, `page_image`, `disagreement_kinds`,
`risk_flags`).
**Outputs:** `staging/<src>/arbitration/decisions.json` (structured) → materialized blocks/pages/assets.
**Persisted:** decisions.json + append-only `arbitration/audit.jsonl`. **Failure stop:** an un-arbitrated or
un-materialized candidate, or a pending `needs_human`, blocks strict acceptance.

## Evidence-risk candidates (what becomes a packet)

A candidate is any page carrying a HARD evidence risk AND no visual asset — generalized from "MinerU found
structure PyMuPDF missed" to source.md quality collapse. Hard risks (→ packet, you arbitrate): `formula_text_loss`
(both detect a formula but PyMuPDF text is fragmented), `formula_undetected`, `table_linearization` (a table
flattened to linear text), `figure_missing_asset`. Soft risks (`reading_order_risk`, `heading_structure_risk`)
are written into the window's `risk_flags` deterministically by the CLI — **not** arbitrated, never blocking
(preflight `check_risk_coverage` only observes them). Each packet's `risk_flags` tells you the risk type.

## Steps (automatic)

1. `python scripts/pipeline.py arbitration-status --source <src>` — if `pending=0` there is nothing to
   arbitrate (no LLM call); skip to windows. If pending, continue.
2. Read `arbitration/queue.json`. For **each** packet, look at the `page_image` (the rendered page),
   `pymupdf_text_excerpt`, and `mineru_structural`, and decide — answering only these questions:
   1. does this disagreement affect downstream KB writing? 2. does the next LLM need the page's visual asset?
   3. should the page's risk flags be updated? 4. must the read window carry this page's asset?
   5. can the disagreement be ignored? 6. must it go to a human?
3. Write `arbitration/decisions.json`: `{"decisions": [ {page, disagreement_kinds, decision, affects_kb_writing,
   needs_visual_asset, risk_flags, window_must_carry_asset, reason} ]}`. `decision ∈ render | ignore | needs_human`;
   `reason` is mandatory (especially for `ignore`). Decide per signal, **never by page number**.
4. `python scripts/pipeline.py arbitration-apply --source <src>` — deterministic: `render` → renders the page
   PNG into `assets/` + sets the block's `asset_path` + risk_flags + `needs_vision`; `ignore` → records the
   reason; `needs_human` → marks blocked. Idempotent + audited.
5. Then run `windows` (it builds AFTER materialization, so the windows carry the new visual assets).
   `windows` is **fail-closed on un-closed disagreements**: if a candidate is un-arbitrated / a `render` is
   un-materialized / a `needs_human` is pending, it refuses to build (dev escape hatch `--dev-bypass` marks
   the output degraded — never for strict acceptance). So you cannot skip this sub-step and still get windows.

## Decision guidance

- **render** — MinerU found a formula/table/figure PyMuPDF flattened or missed and it carries content the next
  LLM must get right (e.g. a fraction extracted as `MPL w = MPK r`, a chapter-opening framework table). The
  page becomes a route-B hard page so its window carries the source image.
- **ignore** — a decorative rule, a header/footer artifact, or a structure already covered elsewhere; give a
  concrete reason (recorded for audit).
- **needs_human** — genuinely ambiguous and high-impact (you cannot tell from the packet whether it is a real
  table/figure). Blocks strict acceptance until a human resolves it.

## Resolving needs_human

`needs_human` keeps `windows` and strict acceptance fail-closed. To close it, a human/agent picks a legal
decision: `python scripts/pipeline.py arbitration-resolve --source <src> --page <n> --decision render|ignore
--reason "<why>"` (`--reason` mandatory, appended to `audit.jsonl`). Then re-run `arbitration-apply` → `windows`
→ `preflight-eval`. `arbitration-status` lists the pending needs_human pages; until each is resolved the gates
stay fail-closed.

## Acceptance

After arbitration + apply + windows, `preflight-eval --strict` `check_evidence_bundle` must be green: every
candidate has a decision, every `render` is materialized into a window asset, no pending `needs_human`, every
`ignore` has a reason. An un-closed disagreement blocks the whole-book ingest.
