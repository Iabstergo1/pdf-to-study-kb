# PDF to Study KB — Codex project truth

> This file is the **single source of truth for Codex** (Claude Code reads `CLAUDE.md`; the two are
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
- **Orchestration layer** `.agents/skills/<name>/SKILL.md` (Codex) / `.claude/skills/<name>/SKILL.md`
  (Claude): natural-language orchestration, **no business Python** — it shells the same CLI.
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
5. **Overwrite protection:** writing an existing page requires "in work-order snapshot + `managed_by != human` + hash match"; otherwise refuse and emit a proposal. `check-write` atomically preserves that page's first pre-edit baseline (repeated calls never replace it); `resolve-concept` does the same before it mutates an existing concept. `window-done` and `lint` reject current-round edits to work-order-existing pages without a verified baseline (`prewrite-snapshot-missing`), so "edit first, snapshot later" cannot be laundered into compliance. Work orders snapshot every existing in-scope concept/lesson/topic/comparison/synthesis plus overview/log/this source ledger. **Never silently edit a human-owned page.** **Write scope is narrow on both sides of the domain line (G3):** a source writes `domains/<own>/{concepts,lessons}/**` only — the synthesis layer (topic/comparison/synthesis) and the `sources/<src>.md` ledger live at the vault top level, never under a domain. (The former `domains/<own>/**` wildcard hollowed out the explicit `sources/<src>.md` grant: a duplicate ledger page under the domain collided on the graph's `source:<id>` node id.)
6. **Fail-closed lint** (form is no longer policed — order/safety/provenance is): broken links, orphan pages (unaccounted ownership), duplicate `canonical_id`, per-type frontmatter incompleteness (non-source pages need `source_refs`), a published body embedding a source image (`source-image-embed`), a body H1 duplicating the filename (`title-duplicate-h1`), an over-short concept/topic/comparison (`content-too-short`), unfilled placeholders, **render-safety** violations — unknown callout type (nested levels included), a same-depth callout head inside an open block (`callout-nested-malformed`, Obsidian renders it as literal text so folded answers leak), non-Obsidian math delimiters `\(`/`\[` (`math-delimiter-nonobsidian`), an empty question stem (`question-stem-empty`) — a concept batch with `overview.md` still the unfilled init-vault seed (`overview-seed`) or without a `sources/<src>.md` ledger page (`source-page-missing`), a proposed **non-source** page (concept/lesson/topic/comparison/synthesis/overview) not in the **current-round** write ledger (`unaccounted-write`: a window's `--writes` recorded this round for ingest — the round is an explicit counter on the work order (bumped by workorder/reopen/re-preprocessing, kept across lint retries) and every read/write row is stamped with it at record time, so an old round never accounts for a new one even on same-second collisions, and only finished window rows account; the **session-scoped** `candidate_write_set.json` for kb-save via `lint --source kb-save --session <run_id>` — historical/unsaved sessions never account; `source_refs` decides which source's lint owns a page, **never** substitutes for accounting), a duplicate or misplaced source ledger page (`source-page-duplicate` / `source-page-misplaced` — same `source_id` twice collides on the graph's `source:<id>` node id), a window that wrote pages this round without a this-round `show-window` read record (`window-unread-write`; `window-done` fail-fasts the same rule and also reconciles `--writes` against disk), a first full ingest that did not read **100% of the windows** this round (`windows-unread` — empty-write skip windows must be read too), and a proposed or ledgered path outside the work order's `write_scope` (`write-scope-violation` — `check-write` is caller-invoked and skippable, so lint re-checks) — any one blocks publish. Table-cell wikilinks use Obsidian-escaped `[[path\|alias]]` (lint accepts the escape; a bare `|` shreds the rendered table and now hard-blocks as `table-wikilink-pipe`). **Vault preflight is transaction-isolated from the batch:** render-safety violations on *previously published* pages block promote and are queued (deduped by rule+path+content-hash, owned by the offending page's source) but never roll back the current batch — only current-batch violations trigger the snapshot rollback, whose restored in-place edits are printed/queued for re-application. `vault-lint` runs the same published∪proposed render-safety scan standalone (CI-able). Render-safety scope = known, deterministic, high-confidence rendering traps; unknown traps enter via the postmortem→legislation loop, not speculative rules. **Section titles are NOT policed (D-4); source images never appear in a published body (D-1).**

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

LLM capability = `.agents/skills/{ingest,kb-query,kb-save,kb-review,kb-qa,kb-postmortem,pipeline-doctor,wiki-lint-semantic,source-preflight,source-xray,skill-evolve}/SKILL.md`,
all model-invocable by `description` (misfires suppressed by negative samples; data safety enforced by the
CLI guards, orthogonal to auto-invocation). `skill-evolve` is **skill self-evolution**: recurring lint
failures (`skill-mine` clusters them into `backlog.yaml` — open proposals only, each cluster carries
`last_seen`; fixed signals retire via `proposals-resolve`, default dry-run) become bounded edits to a skill, gated by
`skill-gate` (pytest + dual-tree parity + gate-integrity; candidates may only touch the two skill trees)
and merged only by a human `skill-adopt`. Protocols: `docs/skill-runtime/{routing,schema,concept-resolution,save-back-policy,skill-standard}.md`.

## 6. Dual-agent collaboration (Codex + Claude)

- **One ingest per vault at a time** (`source_locks`). Codex and Claude **must not ingest the same vault
  concurrently**; reclaim a crashed lock with `python scripts/pipeline.py unlock`.
- **The shared CLI is the only contract:** both agents call only `scripts/pipeline.py`; **business logic
  changes go there**, never duplicated in skills. If you change CLI behavior, keep both skill trees consistent.
- **Resume anchor = `pipeline.py next` + the digest `## RESUME` block** (no session-level hook): after an
  interruption say "continue" or let `scripts/resume-ingest.ps1` resume from the next unfinished window.
  `next --source <src> --resume-packet` emits the structured `RESUME_PACKET v1` (ledger-decided next window,
  write boundary, digest RESUME, resume-critical contract excerpt) that `resume-ingest.ps1` writes to
  `tmp/resume-packet.txt` and points the headless session at (single-line prompt — multiline args break
  Windows `.cmd` shims); it is **fail-closed** on state contradictions (stale RESUME, missing digest/workorder)
  and is a resume-experience hardening, not a safety boundary — the end-of-line lint gate remains the only guarantee.
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
  duplicating the filename; concept/topic/comparison not over-short; render-safety clean (callout types,
  callout nesting, `$…$`/`$$…$$` math delimiters, non-empty question stems — same scan re-checks published
  pages as a transaction-isolated vault preflight); **every non-source page**
  (concept/lesson/topic/comparison/synthesis/overview) must be accounted in the write ledger — a window's
  `--writes` (ingest) or the query-session `candidate_write_set.json` (kb-save); `source_refs` decides
  ownership, never accounting (`unaccounted-write`); the source ledger page is unique and lives only at
  `sources/<src>.md` (`source-page-duplicate` / `source-page-misplaced` — a second page with the same
  `source_id` collides on the graph's `source:<id>` node id and fail-hards `rebuild-graph`, which is
  publish-isolated and so would rot the graph silently); the write ledger is **round-scoped** (round = an
  explicit counter on the work order, bumped by workorder/reopen/re-preprocessing, kept across lint retries;
  reads/writes are stamped with the round at record time, so same-second collisions cannot leak an old round
  into a new one, and only **finished** window rows account);
  **a window that wrote pages must have been read via `show-window` in the same round** (`window-unread-write`;
  `window-done` fail-fasts the same rule + reconciles `--writes` against disk); a **first full ingest must
  read 100% of the windows** (`windows-unread`, empty-write skips included); lint re-checks `write_scope`
  on every proposed/ledgered path (`write-scope-violation` — `check-write` alone is caller-invoked and skippable).
- **Retraction is evidence-first:** `retract-source` (dry-run default) exports an evidence package (page
  bytes + SHA256 manifest + every DB ledger row) and verifies it **before** deleting a source's exclusively
  owned pages, then purges ledgers, resets state, and rebuilds all derived layers. Shared-ref and
  human-managed pages are reported, never deleted. Disposal must never destroy its own audit trail.
- **Reading evidence is the one thing the CLI can only audit, never enforce.** It can force the flow to be
  run; it cannot force the LLM to actually read the source (nor rule out reads that bypass `show-window`).
  `window_reads` is the machine-checkable proxy — pages written from pretrained knowledge plus the
  `chapters.json` chapter map are *formally* flawless, and only the read ledger gives them away.
  Deliberately **not** policed as gates: `started_at == finished_at` "instant windows" (writing need not
  happen between start/done, so it false-positives — surfaced instead as the `ingest-stats`
  `instant_write_windows` soft signal), and `source_refs` section numbers against `chapters.json`
  (title formats differ per book — game-theory/pro-git carry no numbering at all, and `chapters.json`
  under-collects deep headings, so it would false-positive on published books; it once produced a false
  "fabricated sections" audit verdict).

## 8. Windows / PowerShell tooling

On Windows, prefer the native tools (Glob/Grep/Read/Edit — no path issues). To run commands, call `pwsh`
(PowerShell 7) + the study-kb interpreter directly; do not drive PowerShell through Git Bash. Set
`$env:PYTHONUTF8=1` before Python (CJK sources/paths).

**Testing tiers (markers).** The suite is layered by pytest marker (registered in `pytest.ini`; the
single file→tier registry is `tests/_tiering.py` `FILE_TIERS`, applied and **fail-closed guarded** by
`tests/conftest.py` — an unregistered new test file, a stale entry, or `fast` combined with a heavier
tier aborts collection, so nothing silently drops out of the daily tier. Rationale + audit in
`pipeline-workspace/reports/test-audit-2026-07-13.md`): `fast` (positive whitelist = the daily tier,
pure-function/direct-module tests), `cli`, `slow`, `skill`, `realbook` (reserved layer, no tests yet).
Do **not** treat full `pytest tests` as the per-edit default — run the fast tier for ordinary edits,
the full gate before publish/refactor. Always pass a fresh `--basetemp` (a prior run can leave a locked
`pytest-of-Lenovo` temp dir that blocks default cleanup).

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests -q -m fast --basetemp=$bt        # daily tier, seconds (counts drift; pytest --collect-only is the truth)
python -m pytest tests/test_doctor_cli.py -q --basetemp=$bt   # targeted subsystem run when touching that CLI
python -m pytest tests -q --basetemp=$bt                # full gate ~3 min before publish/refactor
```

## 9. Authority & do-not-reintroduce

- **This file = Codex's project truth;** `CLAUDE.md` = Claude's (equivalent). On conflict, prefer the safer
  behavior and sync both.
- `docs/skill-runtime/*` = skill runtime protocols (keep accurate, load on demand).
- Old design docs were deleted — do not work from them. **Do not reintroduce LangGraph / dual business
  SQLite / plan-units / per-unit isolated generation / the Surya hard-OCR pipeline** (guarded by
  `tests/test_legacy_removed.py`). MinerU structured parsing is required, not banned — the ban is on the
  deprecated orchestration, not on OCR/structured parsing.
- Write execution/fix/review reports to project files (e.g. `pipeline-workspace/reports/`); in chat give a
  one-line pointer, not large dumps.
