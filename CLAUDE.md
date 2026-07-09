# PDF to Study KB — Claude Code project truth

> This file is the **single source of truth for Claude Code** (Codex reads `AGENTS.md`; the two are
> content-equivalent and drive the same CLI). Runtime protocols live in `docs/skill-runtime/*` (loaded by
> skills on demand). Older design docs were deleted — **do not work from deleted docs.**

## 1. What this is

Compile multi-format sources (PDF/DOCX/PPTX/MD) **through conversation** into one local, cross-domain,
concept-navigated Obsidian study knowledge base (llm-wiki). A **deterministic, zero-LLM Python CLI**
guarantees reproducibility, observability, and safety; the **only LLM is a human-triggered conversational
skill** that does the high-value writing and cross-page merging. The output is a knowledge web, not a
translation: concepts/topics lead; lessons are an optional, downgraded layer (theme-named, never a chapter
recap) and the wiki is not shaped by the source TOC — the reader should never sense the original document.

## 2. Architecture (two layers + two agents)

- **Deterministic layer** `scripts/pipeline.py` (zero LLM): preprocessing + post-write lint gate + index
  rebuild + a single business SQLite state machine / locks. **All business logic lives here**, covered by `tests/`.
- **Orchestration layer** `.claude/skills/<name>/SKILL.md` (Claude) / `.agents/skills/<name>/SKILL.md`
  (Codex): natural-language orchestration, **no business Python** — it shells the same CLI.
- Both agents share one `pipeline.py` and one `wiki/` vault.

```text
preprocess (zero-LLM CLI):  add-source → profile → source-convert → source-audit →[ auto-arbitration: agent decides, CLI materializes ]→ windows → workorder
same session (the LLM): read chapters.json (whole-book map) → per-chapter content-routing table into digest (advisory type→writing-approach, deviations logged; ingest references/content-routing.md, a skill-evolve living document) + source.md / hard-page images
                        → write status:proposed pages (hard-page source images are read as evidence, re-expressed natively — never embedded)
                        → concept resolution → synthesis layer
finish (zero LLM):      lint → promote(proposed→published) or rollback + Review-Queue → rebuild index/registry (aliases.md retired — aliases in concept frontmatter) + knowledge graph v2.0 (graph-data.generated.json → force-directed offline knowledge-graph HTML, click-node-opens-Obsidian via obsidian://; zero-LLM Louvain communities; rebuild-graph CLI + lint hook; publish-isolated — graph failure never blocks publish; canvas removed — topic_membership lives in graph_model and powers the A2 concept-coverage gate) + quiz-index.generated.md (zero-LLM review entry: published [!question] stems + back-links, no answers; rebuild-quiz CLI + lint hook, publish-isolated; lint also soft-warns on unanswered questions and cleans this source's stale Review-Queue lint reports on success) + propositions.generated.md (zero-LLM claims registry: published named **命题（…）** statements + back-links, name-as-anchor / no numbering; rebuild-propositions CLI + lint hook, publish-isolated; in-domain duplicate names soft-warned)
```

## 3. Core constraints

1. **Preprocessing & finishing are deterministic** (zero-LLM CLI). The only LLM in preprocessing is the agent's **auto-arbitration of dual-audit disagreements** inside the skill flow (structured decisions only; the CLI materializes them) — never an LLM inside the CLI, never unattended batch, never a full-book scan. The writing LLM is the human-triggered ingest skill.
2. **No splitting:** the LLM never plans/approves semantic units; long sources are read via deterministic processing windows (TOC / heading / page / token sliding window).
3. **Concept dedup:** every concept create/update goes through the single `resolve-concept`; a `canonical_id` hit merges, **never creates a duplicate**. Concepts resolve to their **home domain** (methodology → `research-method`, not the source's domain; cross-domain writes are narrowly pre-authorized to `domains/<home>/concepts/**`). `_registry.yaml` is derived — skills never hand-write it; **`aliases.md` is retired**, aliases live only in the concept page's `aliases:` frontmatter.
4. **Two-phase publish:** skills only write `status: proposed`; the finishing gate promotes to `published` and indexes it; failure rolls back (`pipeline-workspace/snapshots/`) + enqueues to `Review-Queue/`.
5. **Overwrite protection:** writing an existing page requires "in work-order snapshot + `managed_by != human` + hash match"; otherwise refuse and emit a proposal. **Never silently edit a human-owned page.**
6. **Fail-closed lint** (form is no longer policed — order/safety/provenance is): broken links, orphan pages (unaccounted ownership), duplicate `canonical_id`, per-type frontmatter incompleteness (non-source pages need `source_refs`), a published body embedding a source image (`source-image-embed`), a body H1 duplicating the filename (`title-duplicate-h1`), an over-short concept/topic/comparison (`content-too-short`), unfilled placeholders, unknown callout type (nested levels included), a concept batch with `overview.md` still the unfilled init-vault seed (`overview-seed`) or without a `sources/<src>.md` ledger page (`source-page-missing`) — any one blocks publish. Table-cell wikilinks use Obsidian-escaped `[[path\|alias]]` (lint accepts the escape; a bare `|` shreds the rendered table and now hard-blocks as `table-wikilink-pipe`). On lint failure the rollback prints/queues the exact list of restored in-place edits — re-apply them before re-running. **Section titles are NOT policed (D-4); source images never appear in a published body (D-1).**

## 4. PDF preprocessing contract (PyMuPDF + MinerU dual-audit)

PyMuPDF is the **fast extraction/profiling path**. Its `needs_vision` thresholds are deliberately broad
and **must not be trusted as a single source of truth**. **MinerU is a required structural reviewer for
strict PDF acceptance, not an optional fallback.** `source-audit` runs MinerU to re-read each PDF, does a
deterministic per-page cross-check against PyMuPDF, and writes an auditable `reconciliation.json` (which
backend produced which evidence, pages cross-checked, disagreements, accepted/degraded, missing evidence).

- **strict / production:** every PDF must pass the dual-audit; MinerU unavailable or failed → **fail-closed**
  (no silent fallback to PyMuPDF). Enforced by `preflight-eval --strict` (`check_dual_audit`).
- **non-strict / dev:** PyMuPDF-only may run for fast local iteration, but `reconciliation.json` marks it
  `degraded / not dual-audited`; it **cannot** satisfy strict acceptance.
- Scanned / low-text PDF, DOCX, PPTX use MinerU as the **primary** parser. All backends normalize to one
  artifact set: `source.md + blocks.jsonl + chapters.json + parse_report.json + reconciliation.json + assets/`.
- MinerU runs `pipeline` backend only (CLI always `-b pipeline`; vlm/hybrid disabled) — fits a ~4 GB GPU.
  Install via `python scripts/install_mineru.py` (kept install-optional so the dev path stays lightweight;
  production must install it).
- **Evidence-assembly loop (disagreements close into the windows the next LLM reads):** `source-audit` emits
  `evidence.json` + an arbitration queue for pages where MinerU found structure PyMuPDF missed. The skill flow
  **auto-arbitrates** (agent decides render/ignore/needs_human — structured only) and `arbitration-apply`
  materializes (renders the page, sets needs_vision), so the windows carry the source image. `preflight-eval
  --strict` `check_evidence_bundle` blocks any un-closed disagreement — acceptance asks "is the LLM's input
  complete", not "did dual-audit run".

## 5. Command layer (skills, model-invocable)

LLM capability = `.claude/skills/{ingest,kb-query,kb-save,kb-review,kb-qa,kb-postmortem,pipeline-doctor,wiki-lint-semantic,source-preflight,source-xray,skill-evolve}/SKILL.md`,
all model-invocable by `description` (misfires suppressed by negative samples; data safety enforced by the
CLI guards, orthogonal to auto-invocation). `skill-evolve` is **skill self-evolution**: recurring lint
failures (`skill-mine` clusters them into `backlog.yaml` — open proposals only, each cluster carries
`last_seen`; fixed signals retire via `proposals-resolve`, default dry-run) become bounded edits to a skill, gated by
`skill-gate` (pytest + dual-tree parity + gate-integrity; candidates may only touch the two skill trees)
and merged only by a human `skill-adopt`. Protocols: `docs/skill-runtime/{routing,schema,concept-resolution,save-back-policy,skill-standard}.md`.

## 6. Dual-agent collaboration (Claude + Codex)

- **One ingest per vault at a time** (`source_locks`). Claude and Codex **must not ingest the same vault
  concurrently**; reclaim a crashed lock with `python scripts/pipeline.py unlock`.
- **The shared CLI is the only contract:** both agents call only `scripts/pipeline.py`; **business logic
  changes go there**, never duplicated in skills. If you change CLI behavior, keep both skill trees consistent.
- **Resume anchor = `pipeline.py next` + the digest `## RESUME` block** (no session-level hook): after an
  interruption say "continue" or let `scripts/resume-ingest.ps1` resume from the next unfinished window.
- **Interpreter = the project `study-kb` conda env** (`conda create -n study-kb python=3.12`, then
  `requirements.txt`: PyMuPDF / PyYAML / pytest; optional MinerU per `requirements.txt` / `scripts/install_mineru.py`).
- **Generated state is not git:** `wiki/` and `pipeline-workspace/` are gitignored per-machine runtime state.

## 7. Capability boundaries

- **Format coverage:** `pdf` = PyMuPDF extraction + MinerU dual-audit (strict required, fail-closed if
  absent); `md` = fast path; `docx`/`pptx` and scanned/low-text PDF = MinerU primary (`--backend auto`
  routes, `--backend mineru` forces; fail-closed if absent).
- **Each book's ingest is a paid LLM operation**, not import-and-go; the project ships with an empty vault.
- **Lint hard rules (form is not policed; order/safety/provenance is):** wikilinks must be full
  vault-relative paths (not Obsidian basenames); **no mandatory section titles** (D-4); per-type frontmatter
  complete (non-source pages carry `source_refs`); **no source image in a published body** (D-1); no body H1
  duplicating the filename; concept/topic/comparison not over-short; non-source pages
  (topic/comparison/synthesis/overview) must be accounted for in some window's `--writes`.

## 8. Windows / PowerShell tooling

On Windows, prefer the native tools (Glob/Grep/Read/Edit — no path issues). To run commands, call `pwsh`
(PowerShell 7) + the study-kb interpreter directly; do not drive PowerShell through Git Bash. Set
`$env:PYTHONUTF8=1` before Python (CJK sources/paths).

**Testing tiers (markers).** The suite is layered by pytest marker (registered in `pytest.ini`, applied
per-file in `tests/conftest.py`; rationale + audit in `pipeline-workspace/reports/test-audit-2026-06-25.md`):
`fast` (default), `cli`, `slow`, `skill`, `realbook`. Do **not** treat full `pytest tests` as the per-edit
default — run the fast tier for ordinary edits, the full gate before publish/refactor. Always pass a fresh
`--basetemp` (a prior run can leave a locked `pytest-of-Lenovo` temp dir that blocks default cleanup).

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests -q -m "not slow and not realbook" --basetemp=$bt   # daily ~37s / 511 tests
python -m pytest tests -q --basetemp=$bt                                  # full gate ~157s / 584 tests
```

## 9. Authority & do-not-reintroduce

- **This file = Claude's project truth;** `AGENTS.md` = Codex's (equivalent). On conflict, prefer the safer
  behavior and sync both.
- `docs/skill-runtime/*` = skill runtime protocols (keep accurate, load on demand).
- Old design docs were deleted — do not work from them. **Do not reintroduce LangGraph / dual business
  SQLite / plan-units / per-unit isolated generation / the Surya hard-OCR pipeline** (guarded by
  `tests/test_legacy_removed.py`). MinerU structured parsing is required, not banned — the ban is on the
  deprecated orchestration, not on OCR/structured parsing.
- Write execution/fix/review reports to project files (e.g. `pipeline-workspace/reports/`); in chat give a
  one-line pointer, not large dumps.
