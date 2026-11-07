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
- **Web app** `web/` adds a local Fastify/React interface for model setup, uploads, human-triggered pi 1.0.3 skill sessions, task logs and reading. Python business rules remain in the CLI; pi imports are confined to server `llm/`, `ingest/` and `skills/`. Credentials stay in the OS user configuration directory, outside the agent's readable paths. The ordinary-font image test uses PyMuPDF and the actual read tool, requiring three exact matches. Reader JSON comes from `export-site --format json [--out DIR] [--with-images]`, sharing page rendering with the offline HTML. `arbitration-status --json` and `ingest-stats --json` provide workflow data, including the registered source path. Web guides: `docs/guide/{zh,en}/`.
- Web skill sessions load the selected existing Skill and project instructions, with per-skill CLI/file permissions. Query sessions can be continued and explicitly saved; report-only skills cannot edit knowledge pages. Human answers and approvals are bound to a specific pending job/question. Skill evolution requires a clean checkout and an isolated worktree; candidate adoption is a separate human-confirmed action. The CLI's read-only `--describe-commands` supplies maintenance forms; the web explicitly classifies every command and rejects unclassified additions. ChatGPT choices come from the current account model endpoint, not the full API catalogue. Supported reasoning efforts come from pi metadata and are saved per provider/model.
- Web ingest needs one human confirmation to chain evidence arbitration, remaining preparation, writing and publication. Human decisions and errors pause it. Source-specific learning goals live in staging/<source>/learning-goal.md, separate from global wiki/_meta/purpose.md. OAuth vision verification binds to the issued ChatGPT clientId (pi 1.0.3), which survives token refresh.
- Web learning: reader-info supplies source-page images/text, honest window-level evidence, report associations and historical content-review status. study-read/study-write store review events, FSRS schedules, reading position and private annotations in the existing SQLite; JSON backups exclude operational proposals. Planned review defaults to 20 distinct questions/day and 5 new questions, preserves actual due dates, and enrolls only user-selected sources. Free practice changes scheduling only when selected. Flags create individual review_proposals; publication never closes them or certifies content quality. Rendering ==highlights== changes generated HTML only. Published-source `kb-qa` uses `content-review` begin/read/register: frozen scope, read-time revisions, structured per-page evidence/outcomes, atomic SQLite report + findings registration, reproducible Markdown/JSON reports, and unchanged legacy YAML history. Rechecks append versions; human batch acceptance requires current conclusive reviews and resolved relevant findings. Later shared-page changes preserve acceptance history and require recheck. Structural proposal checks (placeholder/topic membership/current-round accounting) supply evidence for explicit human closure; successful lint never closes them.
- A sole stale-RESUME error enters model-led digest reconciliation in the web session. After every newly completed window (U6), the model must update this source's digest (U7); only evidence reads and digest edits are allowed until handoff. U7 must actually change the digest, either after U6 or just before it in the same tool batch; old unchanged content does not count. A blocked premature next-window call does not invalidate that change. The entire tool batch completes and `next --source <id> --resume-packet` must pass before a fresh Pi session continues the same job. Rules, chapter map and full digest are reread; old images remain in saved logs rather than subsequent requests. Two unsuccessful digest turns stop the attempt; other contradictions still stop. `next --source <id> --json` supplies compact next-window ID/hash without validating the digest. When every window is finished, a DONE digest yields `phase=finish` for phase E synthesis and phase F ingest-done/lint; adding E writes to a finished window does not trigger another handoff. Finish packets include all current-round accounted paths and a separate source-attributed page list; source attribution never substitutes for accounting. The read-only files tool lists wiki and this source staging using the same controlled walker as web skills.
- The web service binds only to 127.0.0.1 with random-token and Host/Origin checks. One job runs at a time; CLI vault locks still apply. Destructive `--apply` commands require a successful same-arguments preview and the exact target name. Quota exhaustion pauses the job for manual resume; service restart resumes through `next --source <id> --resume-packet`.
- Within a human-started web ingest, an intermediate assistant reply does not finish the job: continue the same job until CLI publication, with session handoffs after validated U7 updates. Stop after three replies without completed-window or stage progress. OpenAI Responses transient connection failures before response headers retry the identical request at most twice (1s, 2s), without replaying tools; retries may consume additional quota. Each attempt retains its header deadline (currently 10 minutes by default); deadline expiry retries, while human cancellation immediately stops. The SDK outer timeout allows all attempts, and the attempt timer clears once headers arrive. OAuth refresh and account-model listing use scoped interception of their exact OpenAI endpoints, replayable form bodies, and independent 15-second attempt limits; an internal TimeoutError is not human cancellation. HTTP responses never trigger request retries. Credentials remain under the SDK refresh lock: failures retain them, and an already sent refresh may finish saving rotated tokens after its caller cancels, without further attempts. Logs contain only host, allowlisted cause-chain codes, elapsed time and attempt; stream errors use stream_code, and OAuth HTTP error bodies are withheld. Recoverable model transport errors after headers permit at most two same-session continuations per window (1s, 2s), reset only after validated U7 handoff. Pi skips errored/aborted assistant messages and never executes their tool calls; completed tool results stay in the conversation. Exhausted header retries do not trigger another round of session retries. Cancellation, HTTP/auth/quota errors, context overflow, failed pipeline state, inability to read images and lint failures do not enter transport recovery. Ordinary tool errors are returned to the model for correction in the same session; three replies without window/stage progress still stop, and successful tools are never replayed automatically. Arbitration-only jobs still end after arbitration.

```text
preprocess (zero-LLM CLI):  add-source → profile → source-convert → source-audit →[ auto-arbitration: agent decides, CLI materializes ]→ windows → workorder
skill session(s) (the LLM): read chapters.json (whole-book map) → per-chapter content-routing table into digest (advisory type→writing-approach, deviations logged; ingest references/content-routing.md, a skill-evolve living document) + source.md / hard-page images
                        → write status:proposed pages (hard-page source images are read as evidence, re-expressed natively — never embedded)
                        → concept resolution → synthesis layer
finish (zero LLM):      lint → promote(proposed→published) or rollback + Review-Queue → rebuild index/registry (aliases.md retired — aliases in concept frontmatter) + knowledge graph v2.0 (graph-data.generated.json → force-directed offline knowledge-graph HTML, click-node-opens-Obsidian via obsidian://; zero-LLM Louvain communities; rebuild-graph CLI + lint hook; publish-isolated — graph failure never blocks publish; canvas removed — topic_membership lives in graph_model and powers the A2 concept-coverage gate) + quiz-index.generated.md (zero-LLM review entry: published [!question] stems + back-links, no answers; rebuild-quiz CLI + lint hook, publish-isolated; lint also soft-warns on unanswered questions and cleans this source's stale Review-Queue lint reports on success) + propositions.generated.md (zero-LLM claims registry: published named **命题（…）** statements + back-links, name-as-anchor / no numbering; rebuild-propositions CLI + lint hook, publish-isolated; in-domain duplicate names soft-warned) + anki-export.generated.tsv (zero-LLM Anki export: published [!question] stems + success descendant answers + back-links, duplicate stems deterministically disambiguated + soft-warned; export-anki CLI + lint hook, publish-isolated; Anki remains an optional export; the local Web app also schedules personal review with FSRS in the existing SQLite via study-read/study-write, never in wiki page content) + source-images.generated.md (zero-LLM hard-page original-image index: page-level entries link each written knowledge page to the source page images that produced it via windows.jsonl assets ⋈ the current round's finished window write-set; sources without a usable write-set degrade to source-level and are explicitly flagged, never disguised as page-level; rebuild-source-images CLI + lint hook, publish-isolated; ordinary markdown links only, never embeds source images) + `pipeline-workspace/exports/site/study-kb.html` (zero-LLM self-contained offline static site via export-site CLI; manual distribution action — NOT a vault derived layer, NOT in the publish hook / _DERIVED / derived_violations / retract rebuilds; callout structure via page_rules.parse_callouts, Markdown via markdown-it-py, math via vendored KaTeX; P4 reading shell via site_render.py/site_layout.py/site_data.py/site_media.py — navigation-domain Explorer (overview / 跨域综合 / real domains), in-page TOC, breadcrumbs, prev/next, backlinks, hover previews, graph-data-driven full/local graph honouring the shared 500/1200 degradation thresholds, quiz/proposition views, source-pages panel for concept/topic/comparison/synthesis, Obsidian URI via wiki_gate, light/dark theme, title/body weighted search, domain/type/source filters, alias retrieval, reading-progress memory, responsive drawers; graph-data copy strips generated_at; images excluded by default, `--with-images` now emits a directory with relative original-image/thumbnail links via PyMuPDF, no base64 inline)
```

## 3. Core constraints

1. **Preprocessing & finishing are deterministic** (zero-LLM CLI). The only LLM in preprocessing is the agent's **auto-arbitration of dual-audit disagreements** inside the skill flow (structured decisions only; the CLI materializes them) — never an LLM inside the CLI, never unattended batch, never a full-book scan. The writing LLM is the human-triggered ingest skill.
2. **No splitting:** the LLM never plans/approves semantic units; long sources are read via deterministic processing windows (TOC / heading / page / token sliding window).
3. **Concept dedup:** every concept create/update goes through the single `resolve-concept`; a unique exact name, supplied alias or explicit `canonical_id` match merges, **never creates a duplicate**. All terms participate (each prefers home domain over shared); conflicting matches stop before snapshots or writes. A derived-ID collision alone refuses creation, never infers semantic equivalence. The CLI resolves once, snapshots the selected page, then merges that same page. Support concepts use the source's **application domain** (e.g. cosine similarity in LLM retrieval → `llm`); cross-domain home routing is allowed only by the actual work order (methodology → `research-method`, narrowly at `domains/<home>/concepts/**`). Do not create statistics/optimization domains merely from disciplinary labels or alter the work order to bypass a denial. `_registry.yaml` is derived — skills never hand-write it; **`aliases.md` is retired**, aliases live only in the concept page's `aliases:` frontmatter.
4. **Two-phase publish:** skills only write `status: proposed`; the finishing gate promotes to `published` and indexes it; failure rolls back (`pipeline-workspace/snapshots/`) + enqueues to `Review-Queue/`.
5. **Overwrite protection:** writing an existing page requires "in work-order snapshot + `managed_by != human` + hash match"; otherwise refuse and emit a proposal. `check-write` atomically preserves that page's first pre-edit baseline (repeated calls never replace it); `resolve-concept` does the same before it mutates an existing concept and checks the target scope before creating a new skeleton. `sync-overview-sources --apply` also preserves the overview baseline when an ingest holds the vault lock; web ingest checks it before calling the command. `window-done` and `lint` reject current-round edits to work-order-existing pages without a verified baseline (`prewrite-snapshot-missing`), so "edit first, snapshot later" cannot be laundered into compliance. Work orders snapshot every existing in-scope concept/lesson/topic/comparison/synthesis plus overview/log/this source ledger. **Never silently edit a human-owned page.** **Write scope is narrow on both sides of the domain line (G3):** a source writes `domains/<own>/{concepts,lessons}/**` only — the synthesis layer (topic/comparison/synthesis) and the `sources/<src>.md` ledger live at the vault top level, never under a domain. (The former `domains/<own>/**` wildcard hollowed out the explicit `sources/<src>.md` grant: a duplicate ledger page under the domain collided on the graph's `source:<id>` node id.)
6. **Fail-closed lint** (form is no longer policed — order/safety/provenance is): broken links, orphan pages (unaccounted ownership), duplicate `canonical_id`, per-type frontmatter incompleteness (non-source pages need `source_refs`), a published body embedding a source image (`source-image-embed`), a body H1 duplicating the filename (`title-duplicate-h1`), an over-short concept/topic/comparison (`content-too-short`), unfilled placeholders, **render-safety** violations — unknown callout type (nested levels included), a same-depth callout head inside an open block (`callout-nested-malformed`, Obsidian renders it as literal text so folded answers leak), non-Obsidian math delimiters `\(`/`\[` (`math-delimiter-nonobsidian`), an empty question stem (`question-stem-empty`) — a concept batch with `overview.md` still the unfilled init-vault seed (`overview-seed`) or without a `sources/<src>.md` ledger page (`source-page-missing`), **the current round's own** source ledger page not linked from `overview.md` (`overview-source-unlinked` — **ingest phase E only**: `overview.md`'s *only* write channel is the ingest `write_scope`, since `revise-adopted`'s `_safe_rel` rejects it and `kb-save` is new-page-only, so gating a kb-save batch on it would demand an edit with no legitimate channel — constraint 7; the mechanical fix is `sync-overview-sources --apply`. **Scope is the round's own source, not the whole vault**: the gap's cause is "every ingest forgets its own book", and `cmd_lint` rolls back indiscriminately, so a vault-wide scope would let books 1..N-1's debt roll back book N's batch (including `overview.md`'s own in-place edit this round) — the same mis-scoping L7-synthesis-missing cost a rework round for. Other sources' debt drops to the soft warning `overview_unlinked_other_sources`. The rule covers source ledger links rather than topics because a fail-closed rule needs a **deterministic remedy that can compute the whole correct answer**: the set of `type: source` pages is machine-enumerable, whereas "which section of overview should this topic be introduced in, and in what words" has no deterministic answer, so any machine fix there would be manufacturing content), a proposed **non-source** page (concept/lesson/topic/comparison/synthesis/overview) not in the **current-round** write ledger (`unaccounted-write`: a window's `--writes` recorded this round for ingest — the round is an explicit counter on the work order (bumped by workorder/reopen/re-preprocessing, kept across lint retries) and every read/write row is stamped with it at record time, so an old round never accounts for a new one even on same-second collisions, and only finished window rows account; the **session-scoped** `candidate_write_set.json` for kb-save via `lint --source kb-save --session <run_id>` — historical/unsaved sessions never account; `source_refs` decides which source's lint owns a page, **never** substitutes for accounting), a duplicate or misplaced source ledger page (`source-page-duplicate` / `source-page-misplaced` — same `source_id` twice collides on the graph's `source:<id>` node id), a window that wrote pages this round without a this-round `show-window` read record (`window-unread-write`; `window-done` fail-fasts the same rule and also reconciles `--writes` against disk), a first full ingest that did not read **100% of the windows** this round (`windows-unread` — empty-write skip windows must be read too), and a proposed or ledgered path outside the work order's `write_scope` (`write-scope-violation` — `check-write` is caller-invoked and skippable, so lint re-checks) — any one blocks publish. Table-cell wikilinks use Obsidian-escaped `[[path\|alias]]` (lint accepts the escape; a bare `|` shreds the rendered table and now hard-blocks as `table-wikilink-pipe`). **Vault preflight is transaction-isolated from the batch:** render-safety violations on *previously published* pages block promote and are queued (deduped by rule+path+content-hash, owned by the offending page's source) but never roll back the current batch — only current-batch violations trigger the snapshot rollback, whose restored in-place edits are printed/queued for re-application. `vault-lint` runs the same published∪proposed render-safety scan standalone (CI-able). Render-safety scope = known, deterministic, high-confidence rendering traps; unknown traps enter via the postmortem→legislation loop, not speculative rules. **Section titles are NOT policed (D-4); source images never appear in a published body (D-1).**

7. **A gate must never demand an edit where no legitimate edit exists** — such a rule does not enforce
   quality, it **manufactures content**. Before adding or widening any fail-closed rule, ask: *in the
   narrowest legitimate scenario this rule fires, what does it force someone to write?* If the honest
   answer is "something they would otherwise have no reason to write", the rule is mis-scoped. Two
   instances cost a full rework round each: `broken-link` ordered "create the missing page" first, and
   an ingest duly invented an entire page for a concept its source never mentions; `L7-synthesis-missing`
   judged per batch rather than per source, so a rework that only corrected four clauses in existing
   concept pages was told to edit an already-complete synthesis layer. Fix the scope (5d6a7ea), never
   satisfy such a gate by writing filler.

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
  **The lock covers the vault's ingest, not the repo working tree** — two sessions editing `CLAUDE.md`
  or a skill tree at the same time will silently overwrite each other, and nothing warns them. Observed
  2026-08-12: a kb-review session found 8 tracked files modified by a concurrent session and had to prove
  by mtime that they were not its own. Discipline, not mechanism: one session touching this repo at a time,
  `git status` before starting cross-session work, and **close Obsidian before any independent audit** (a
  live editor rewrote 6 vault files' mtime mid-audit and contaminated that evidence line).
- **The shared CLI is the only contract:** both agents call only `scripts/pipeline.py`; **business logic
  changes go there**, never duplicated in skills. If you change CLI behavior, keep both skill trees consistent.
- **Resume anchor = `pipeline.py next` + the digest `## RESUME` block** (no session-level hook): after an
  interruption say "continue" or let `scripts/resume-ingest.ps1` resume from the next unfinished window.
  `next --source <src> --resume-packet` emits the structured `RESUME_PACKET v1` (ledger-decided next window,
  write boundary, digest RESUME, resume-critical contract excerpt) that `resume-ingest.ps1` writes to
  `tmp/resume-packet.txt` and points the headless session at (single-line prompt — multiline args break
  Windows `.cmd` shims); it is **fail-closed** on state contradictions (stale RESUME, missing digest/workorder)
  and is a resume-experience hardening, not a safety boundary — the end-of-line lint gate remains the only guarantee.
  A missing digest permits `phase=initialize` only before any window reads/starts and with every writable
  Markdown page unchanged from the workorder snapshot; the agent then reads the chapter map and creates
  its initial routing/digest. Lost digests after activity or page changes still fail closed.
- **Interpreter = the project `study-kb` conda env** (`conda create -n study-kb python=3.12`, then
  `requirements.txt`: PyMuPDF / PyYAML / markdown-it-py / fsrs / pytest; optional MinerU per `requirements.txt` / `scripts/install_mineru.py`).
- **Generated state is not git:** `wiki/` and `pipeline-workspace/` are gitignored per-machine runtime state.

## 7. Capability boundaries

- **Format coverage:** `pdf` = PyMuPDF extraction + MinerU dual-audit (strict required, fail-closed if
  absent); `md` = fast path; `docx`/`pptx` and scanned/low-text PDF = MinerU primary (`--backend auto`
  routes, `--backend mineru` forces; fail-closed if absent).
- **Each book's ingest is a paid LLM operation**, not import-and-go; the project ships with an empty vault.
- **The ingest agent MUST be multimodal (vision-capable) — this is a hard requirement, not a preference.**
  Route B hands the writing LLM **rendered page images** as its evidence: `show-window` emits an
  `assets=…png` header of **paths**, and phase A.5's arbitration packet likewise ships a `page_image`
  path. A text-only model cannot open either, does not error, and silently degrades to writing from
  the linearized `source.md`. The exposure is not marginal — measured on a real large scanned textbook:
  **279/348 pages (80%) were hard pages, and 223 of them carried a figure-class signal that has no
  textual equivalent** (only 36 were formula/table-only, where MinerU's LaTeX/HTML could in principle
  substitute for the image). Such a run passes **every** deterministic gate — form, links, accounting,
  provenance, render-safety — while resting on evidence the writer never saw; only a page-by-page kb-qa
  against the source can find it (§7 "reading evidence can be audited, never enforced"). **Check the
  exposure before spending:** `preflight-eval --strict` prints `detection_distribution … needs_vision
  N/M=X%` — that ratio is the share of the book the agent must be able to *see*. Deliberately **not**
  a CLI gate: capability cannot be probed from inside the pipeline, and a self-declared capability flag
  would be a lie waiting to happen.
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
  **`overview.md` is permanent vault infrastructure:** every successful `--apply` guarantees it exists.
  If it is exclusively owned by the retracted source and non-human, the old page goes into the evidence
  package, is deleted, then **reseeded verbatim from `templates/overview.md`** (published, `managed_by:
  pipeline`, carrying no retracted `source_refs`); if it is shared or `managed_by: human` it is kept
  **byte-for-byte** and only reported for manual de-referencing; if it was already missing, apply seeds
  it. Reseed happens **before** derived-layer rebuild and **never** satisfies "overview must exist" by
  keeping old source content. init-vault and retract-source share one idempotent `_seed_overview` helper
  (never overwrites an existing page). dry-run shows `overview.md: keep|reseed` and writes nothing.
- **Reading evidence is the one thing the CLI can only audit, never enforce.** It can force the flow to be
  run; it cannot force the LLM to actually read the source (nor rule out reads that bypass `show-window`).
  `window_reads` is the machine-checkable proxy — pages written from pretrained knowledge plus the
  `chapters.json` chapter map are *formally* flawless, and only the read ledger gives them away.
  Deliberately **not** policed as gates: `started_at == finished_at` "instant windows" (writing need not
  happen between start/done, so it false-positives — surfaced instead as the `ingest-stats`
  `instant_write_windows` soft signal), and `source_refs` section numbers against `chapters.json`
  (title formats differ per book — some published sources carry no numbering at all, and `chapters.json`
  under-collects deep headings, so it would false-positive on published books; it once produced a false
  "fabricated sections" audit verdict).
- **An export/derived layer is accepted by diffing it against its source — never by counting.**
  Counting successes is not acceptance: a run reporting "5507 math nodes rendered" hid that 32 of them
  were destroyed. **Counting failures is not acceptance either** — the same run reported
  `.katex-error = 0` while every formula containing `<` had already been swallowed by the HTML parser
  *before* KaTeX ran, so the error counter never saw a broken formula. The only judgement that holds is
  **item-by-item source-vs-artifact comparison**: extract every unit from the source, extract every unit
  from the rendered/exported artifact, and assert the source set is fully contained in the artifact set.
  Report `source count / intact count / lost count`, and require lost = 0.
  **Item-by-item comparison means full coverage: a random sample is not a comparison.**
  Three rounds cost one rework
  each before this was written down (P2 page-level attribution, P3 markdown eating math, P3 HTML
  swallowing math). Applies to every export surface: Anki TSV, source-image index, static site.
- **Restoring protected content must re-apply the target format's escaping and character encoding.** Both
  export layers protect math from the surrounding processor, then restore it — and shipped bugs because
  the restore step forgot the target's contract: Anki `#html:true` and static-site HTML needed
  `<`/`>`/`&` escaped; B1 graph-view HTML needed UTF-8 bytes reconstructed from `atob()`'s latin-1 binary
  string before assigning to iframe `srcdoc`. These are one defect class, now three instances and three
  rework rounds. When you write a protect→process→restore pipeline, the restore step owns both the target
  format's escaping contract and its character-encoding contract; state both in the module docstring.
- **Acceptance must run against the artifact surface the user actually touches.** Decoding an embedded
  base64 blob in Python is not the same as opening its iframe in a browser. B1's graph view was reported
  accepted from decoded intermediate data while the browser-side `atob` path corrupted all non-ASCII
  text. For iframes, frames, rendered DOM, exported files, and similar nested surfaces, verify the inner
  document or final artifact directly, not its pre-restore payload.

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
the full gate before publish/refactor.

**`--basetemp` is optional and sandbox-guarded.** The default (system temp `pytest-of-<user>/pytest-<n>`,
keeps the last three runs) is already safe — pass `--basetemp` only when a prior run left a locked temp dir
that blocks default cleanup, and then anchor it at **this repo**, never at `$PWD`. `tests/_sandbox.py` +
`conftest.pytest_configure` fail-closed if an explicit basetemp escapes `<repo>/tmp/**` or the system temp
area, because pytest **wipes basetemp wholesale**: a `$PWD`-relative path run from another workspace once
wrote ~13k files / 53 MB into it. The same hook exports `PYTHONDONTWRITEBYTECODE=1` for the whole test
process tree, so subprocess CLI tests leave no `__pycache__` in the workspace under test.

```powershell
$env:PYTHONUTF8=1
python -m pytest tests -q -m fast        # daily tier, seconds (counts drift; pytest --collect-only is the truth)
python -m pytest tests/test_doctor_cli.py -q   # targeted subsystem run when touching that CLI
python -m pytest tests -q                # full gate ~3 min before publish/refactor
# only if the default temp dir is locked — absolute, repo-anchored, never $PWD:
python -m pytest tests -q --basetemp="<repo-root>\tmp\pt-$(Get-Random)"
```

**`STUDY_KB_ROOT` must never point at a real vault while running pytest.** `_workspace_root()` trusts
that variable unconditionally and falls back to the **repo root** when it is unset, so a test that
forgets to override it writes a real `wiki/` / SQLite / `staging/`. `tests/conftest.py` therefore
fail-closes **before collection**: an incoming value outside the temp sandbox (system temp or
`<repo>/tmp/`) aborts the run with `unsafe STUDY_KB_ROOT for tests` — it is rejected, not silently
overridden, because the caller needs to learn that their command was dangerous. A clean session is
then handed its **own** temporary workspace root, inherited by every subprocess; individual tests may
still override it to their `tmp_path`. **Just don't set it when running tests.** A real
`STUDY_KB_ROOT=<vault>` belongs only to manual acceptance commands run *outside* pytest, and those
stay **dry-run by default** unless the task explicitly authorises `--apply`.

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
