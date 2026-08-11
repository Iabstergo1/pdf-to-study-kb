---
name: ingest
description: End-to-end add a new external source (PDF/DOCX/PPTX/Markdown), deterministically adopt an already-populated Obsidian vault baseline, or register selective reuse from another published pipeline vault. Use for "add/ingest/index/weave this source", "adopt/onboard this existing vault", or "reuse this published source". Normal ingest preprocesses and writes proposed pages; legacy adoption and external-vault reuse are separate zero-LLM branches. Read-only requests like "summarize/explain/translate this" must never trigger it.
---

# ingest — weave, adopt, or reuse a source (normal ingest is the only LLM write step)

You are the maintainer of the knowledge base. Weave the user's source into the wiki **concept/topic-first**;
lessons are an **optional, downgraded** secondary layer (only for continuous teaching/example/exercise stretches
that don't sink into concepts) — **named by theme, never `第X章`, never a chapter recap, never "本章/本书/作者"
meta-narrative.** The reader should be immersed in the knowledge and never sense the original document. Work under
the work-order transaction protocol the whole way. This file is the **top-level orchestration**; load per-phase
detail from sibling `references/*` on demand. Project truth: `AGENTS.md` / `CLAUDE.md`. Engineering format: `docs/skill-runtime/skill-standard.md`.

If the request is to adopt an already-populated vault, take the dedicated zero-LLM `adopt-vault` branch in §6
**before** any work-order, window or LLM writing step; that branch does not use the normal ingest transaction protocol.
If the request is to register selective reuse from another pipeline vault whose source is already published, take
the zero-LLM `reuse-source` branch in §6; target merge pages/source_refs must already exist and are never written by it.

> **Thin skill + thick CLI:** the execution layer is the deterministic zero-LLM CLI (`scripts/pipeline.py`);
> this skill carries no business code, only orchestrates it. `<src>` = this source's source_id; run commands
> from the project root with the study-kb interpreter (on Windows: pwsh + `$env:PYTHONUTF8=1`).

## 1. Triggers / Non-triggers

- **Triggers:** "add this book/PDF to the KB", "ingest \<source\>", "index this document", "weave this file into the wiki", "adopt/onboard this existing Obsidian vault", "reuse this source from the published vault".
- **Non-triggers (never fire):** "summarize this", "explain this", "translate this", "answer a trivia question", "what is this PDF about" (a question, not an ingest request).

## 2. Inputs

- The user gives: file path `<path>`, domain `<domain>`; format `<fmt>` is inferred from the extension
  (pdf/md/docx/pptx); `<src>` is derived from the filename (lowercase, hyphenated). **Confirm `<src>` and
  `<domain>` once with the user.**
- For existing-vault adoption, instead require `<src>`, `<title>`, `<domain>`, a baseline archive path and its
  independently recorded 64-hex SHA-256. Confirm all five inputs; do not silently calculate a new expected hash
  and treat it as prior baseline evidence.
- For cross-vault reuse, require `<src>/<title>/<domain>`, the current PDF path + independently expected SHA-256,
  a disjoint read-only `<origin-root>` + matching `<origin-source>`, and an explicit mapping JSON (v1 or v2 —
  both supported). The mapping's covered origin-concept set must equal the set the origin actually owns, so
  nothing is missed, duplicated or double-mapped; target count and the non-empty/zero-mapping split are the
  mapping's own shape, not a gate. A v2 mapping adds a symmetric `topic_targets` dimension so topic pages that
  legitimately aggregate this source can declare it; unlike concepts, origin topics need **not** be covered
  exhaustively — only referenced ones must exist and must not be referenced twice. Non-empty targets in either
  dimension already carry this source_ref, while explicit zero-mapping targets must not.
  This branch verifies the merge; it never performs it. Existing v1 mappings and frozen v1 evidence
  need no migration: a v1 run is byte-identical to before.
- Read: `wiki/_meta/purpose.md` **first — it is the authority on writing style, structure, depth and
  terminology** (the user's learning goals / teaching preference). The deterministic layer only guards
  order/safety/provenance; **form is purpose-driven, not template-driven.** Then read
  `docs/skill-runtime/{schema,concept-resolution}.md`, `templates/*` (suggested scaffolds, not mandatory
  skeletons), and the phase references.

## 3. Outputs

- Normal-ingest vault writes are always `status: proposed` + `managed_by: pipeline`: lessons / concepts / topics /
  comparisons / synthesis / `sources/<src>.md` / `overview.md`.
- Adoption exception: `adopt-vault --apply` adds only a deterministic canonical `sources/<src>.md` with
  `format: legacy-vault`, `status: published`, plus immutable evidence/state; it never automatically rewrites
  existing knowledge pages.
- Reuse exception: `reuse-source --apply` likewise adds a deterministic canonical source page with
  `format: external-vault-reuse`, `status: published`, plus immutable origin/mapping/first-target evidence and
  `reused/published` state; it never writes any mapped target page.
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
- Adoption branch only: `pipeline-workspace/adoptions/<src>/manifest.json` + verbatim page bytes under `files/`,
  one canonical source page and one `adoption_evidence` artifact; no staging/workorder/window ledgers.
- Reuse branch only: `pipeline-workspace/reuses/<src>/{manifest.json,mapping.json,origin-state.json,origin-files/**,target-files/**}`,
  one canonical source page and one `reuse_evidence` artifact; no staging/workorder/window ledgers.
- Reuse reseal only: `pipeline-workspace/reuse-reseals/<src>/<operation-id>/` keeps the canonical
  transition, old/new source bytes and the full old evidence generation; exact retries reuse this archive.

## 6. CLI commands (orchestration order)

```text
legacy vault (zero LLM)  adopt-vault --source <src> --title <title> --domain <domain> --baseline-archive <archive> --baseline-sha256 <sha256> [--apply]
published source reuse (zero LLM)  reuse-source --source <src> --title <title> --domain <domain> --path <pdf> --sha256 <sha256> --origin-root <root> --origin-source <src> --mapping <mapping.json> [--expect-concepts N --expect-topics N] [--apply]
reuse evidence v1→v2 reseal (zero LLM)  reseal-source --source <src> --mapping <mapping-v2.json> --from-manifest-sha256 <old-manifest-sha256> [--apply]
preprocess + auto-arbitration  init-vault → add-source → profile → source-convert → source-audit →[ arbitration-status → if pending: agent arbitrates queue → arbitration-apply ]→ windows → workorder
start / per-window (LLM)  ingest-start → read chapters.json (build whole-book understanding) → write per-chapter content-routing table into digest (advisory; references/content-routing.md)
                          →[ in chapter order: window-start → show-window → write pages per routing orientation (read hard-page source images as evidence; re-express natively — never embed them; deviations logged) → window-done --writes ]×N
synthesis (LLM)           phase E: update overview + build topic/comparison/synthesis (into some window's --writes) — first-class, lint blocks if missing
finish (zero LLM)         ingest-done → lint
incremental reopen        reopen → ingest-start →[ per-window backfill ]→ ingest-done → lint
```

> **legacy-vault adoption:** run the command first without `--apply`; this is a strict byte-zero-write dry-run
> that proves the pre-adoption ZIP and live adoptable-page set/bytes match. Legacy `wiki_gate` content debt is
> warning-only here—the hard stop is limited to safety/readability/published status and archive/evidence/source/
> state/ledger integrity. After explicit review, rerun with `--apply`. It takes the vault lock, writes immutable
> evidence + the canonical source page, rebuilds derived artifacts, and only then records `legacy-vault` as
> `adopted/published`, with all three ingest ledgers at zero. A fully verified exact repeat is a whole-tree byte
> no-op. Later live-page evolution only reports `post-adoption-live-drift`; it never rewrites the historical
> manifest/evidence or fails adoption. Archive, evidence, source-page, adoption metadata or state drift still
> fails closed. Use `vault-lint` / `graph-lint` afterward to pay down the warning-only legacy content debt.

> **published-source reuse:** before both dry-run and apply, set `PYTHONDONTWRITEBYTECODE=1` and invoke
> `python -B scripts/pipeline.py reuse-source ...`; the origin may also be this CLI code repository, so ordinary
> imports are not allowed to update its `scripts/__pycache__`. Run without `--apply` first. The origin state DB
> must use a non-WAL rollback journal and have no `-wal/-shm` sidecar. The plan must report the expected PDF hash,
> read-only origin `lint/published` state, canonical source page, the published concept/topic counts it found, and
> a mapping (v1 or v2) whose covered origin-concept set equals the origin's own set (hence exactly once each).
> Target count is the mapping's shape, not a gate; `--expect-concepts` / `--expect-topics` are optional
> confirmations. A v2 mapping also reports its `topic_targets` dimension — topic coverage is deliberately partial,
> so only referenced origin topics must exist and must not repeat. Non-empty target pages in either dimension must
> already carry this source_ref; zero-mapping targets must not. Replaying frozen v1 evidence stays a byte no-op;
> mixing a v1 evidence set with a v2 mapping (or vice versa) fails closed rather than silently upgrading. Apply takes only the target vault lock, freezes origin state/pages,
> raw mapping and the first target merge bytes, rebuilds every derived layer, then records
> `external-vault-reuse` as `reused/published`; all three ingest ledgers remain zero. Exact repeat is whole-tree
> byte/mtime no-op only after registry/index/graph/quiz/propositions are recomputed and verified; missing or corrupt
> derived output is rebuilt under the target lock. Later target live evolution is `post-reuse-target-live-drift`
> warning-only; origin/PDF/mapping/
> evidence/source/state drift fails closed. The temporary mapping path is not identity: after success, replay with
> the immutable evidence `mapping.json` and remove the temporary input.

> **reuse evidence reseal:** existing v1 evidence normally needs no migration. Use the independent
> `reseal-source` only when a legitimate topic attribution cannot be represented by frozen v1 evidence; ordinary
> `reuse-source` must never enter this branch. Set `PYTHONDONTWRITEBYTECODE=1` and use `python -B`; the same
> read-only origin and non-WAL checks still apply. Run without `--apply` first. The old evidence must pass its full
> manifest/file/live-origin/PDF validation. Source/domain/title/format/evidence version/PDF/origin are derived from
> that manifest and have no override flags; concept targets, counts and `target_pages` must also remain identical.
> Only a non-empty v2 `topic_targets` dimension and its derived evidence may change. Apply holds the target vault
> lock, durably stages one deterministic operation, demotes state to `reused/running`, archives the whole v1
> evidence generation, activates v2 evidence, replaces the source page only if its bytes still equal the old
> canonical page, rebuilds derived artifacts, then atomically changes reused-stage/artifact hashes and republishes.
> A crash at any boundary rolls forward by rerunning the exact command; published state exists only with a matching
> old or new generation, and an exact completed repeat is a whole-tree byte/mtime no-op. Do not use reseal to cover
> damaged evidence or to retire a source. Known limitation: `retract-source` does not support `adopted/published`
> or `reused/published` terminal states; do not call it for either branch. If an OS-level kill leaves a stale vault
> lock, follow the normal `unlock` protocol before rerunning the same reseal operation.

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
`managed_by: human` page conflict; cross-domain promotion candidate; the vault lock is held; **you cannot
actually open the route-B page images** (see below — stop, do not write from the linearized text).

> **Vision is a hard prerequisite, not a nice-to-have.** If `show-window` lists `route-b-assets` / an
> `assets=…png` header and you cannot actually view those files, **stop and tell the user this source
> needs a vision-capable agent** — do not continue from `source.md` alone. Writing a hard page without
> seeing its image produces a page that passes every deterministic gate while resting on evidence you
> never read, and only a page-by-page kb-qa against the source can catch it. The same applies to phase
> A.5: never decide `ignore` on a `figure_missing_asset` packet whose `page_image` you could not open —
> choose `render` (costs one image) or `needs_human`. **Recovery:**
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
- Writing: every page follows `check-write → edit` (existing-page ALLOW atomically preserves the first baseline;
  `window-done` and `lint` verify it), page_rules self-check 0 violations, every non-source page in a
  `window-done --writes`.
- Synthesis (phase E mandatory): overview updated (not a bare link list) + at least one topic/comparison/synthesis, all in `--writes`; otherwise `lint` reports `L7-synthesis-missing` and rolls back.
- Finish: `lint` passes (promoted into the index), or failures land in `Review-Queue/` and the round is rolled back.
- Adoption alternative: dry-run reports `byte-zero-write`; integrity violations must be zero, while legacy
  `wiki_gate` content debt may remain visible as warnings. Apply rebuilds derived artifacts before recording
  `adopted/published`, preserves existing knowledge-page bytes, and reports all three ingest-ledger counts as zero.
  A fully verified exact rerun is a whole-tree byte no-op; later live-page drift is warning-only and leaves the
  historical manifest/evidence unchanged, while archive/evidence/source/adoption-state drift remains a hard stop.
- Reuse alternative: dry-run is byte-zero-write and proves origin read-only published truth, PDF SHA, every
  concept/topic page hash, the set-equal exactly-once mapping and the zero-mapping non-attributions. Apply preserves every
  existing target-page byte, rebuilds derived artifacts before `reused/published`, and keeps all three ingest
  ledgers at zero. Exact replay (including from evidence mapping) is whole-tree byte no-op; target live drift is
  warning-only, while origin/mapping/evidence/source/reuse-state drift is a hard stop.
- Reporting: quote the promoted count from `lint` separately; use `ingest-stats --json`
  `page_inventory.total/by_type` for the complete delivered inventory — never use `pages_estimate` as the delivery total.
