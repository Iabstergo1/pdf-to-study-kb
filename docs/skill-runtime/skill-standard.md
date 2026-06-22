# Skill engineering standard (thin skill + thick CLI)

> The shared format for every `.claude/skills/*/SKILL.md` (Claude Code) and `.agents/skills/*/SKILL.md`
> (Codex). Source of truth: `CLAUDE.md` / `AGENTS.md` + this `docs/skill-runtime/*` directory.
> **Never reference any deleted spec / ADR.**

## Principle

**Thin skill, thick CLI.** `scripts/pipeline.py` is the only business contract: every deterministic,
recoverable, auditable, gated operation lives in the CLI, the state machine, and lint. A `SKILL.md`
does four things only: **orchestrate the CLI, surface acceptance checks, mark failure stops, and
constrain intermediate artifacts.** Business logic never goes in prose; prose mirrors the contract the
CLI already enforces so the model improvises less.

A complex skill is an **engineered workflow**, not a one-shot prompt. It must name its responsibilities,
inputs, outputs, dependencies, persisted intermediate artifacts, recovery/retry points, and acceptance
criteria — i.e. the nine-section contract below.

## Nine-section contract (every SKILL.md)

Frontmatter: `name` + `description` (the description embeds a one-line positive trigger). Body sections:

1. **Triggers / Non-triggers** — when to fire; **list Non-triggers explicitly** (requests that must NOT
   fire, e.g. "summarize this / translate" never enter the write path).
2. **Inputs** — what the user/conversation must supply + which files to read.
3. **Outputs** — what is produced (vault pages / staging artifacts / reports); **every vault write is
   `status: proposed`**.
4. **Dependencies** — which `pipeline.py` commands / other skills / `docs/skill-runtime/*` protocols /
   `references/*` the skill relies on.
5. **Persisted artifacts** — the intermediate results that must hit disk + where (`ingest_progress`,
   `staging/<src>/digest.md`, `query-sessions/`, `pipeline-workspace/reports/`).
6. **CLI commands** — the exact orchestrated commands (**business logic lives here**).
7. **Workflow** (complex skills only) — sub-units, each with inputs / outputs / acceptance / persisted
   artifact / failure stop. Complex skills split phase detail into sibling `references/*.md`; the
   SKILL.md keeps only the top-level orchestration and an index.
8. **Failure stops / recovery** — when to stop and hand back (check-write DENY / lint fail /
   `managed_by: human` conflict / lock contention / preprocessing error), and the recovery anchor
   (`pipeline.py next` + digest `## RESUME`).
9. **Acceptance criteria** — verifiable items aligned with the CLI gates: check-write ALLOW, lint pass,
   check-session, zero `page_rules` violations.

> A simple skill (e.g. kb-query) may omit section 7; the other eight are mandatory.

## references/ split for complex skills

Multi-phase skills like `ingest` keep only the nine-section **orchestration** in `SKILL.md`; phase detail
lives in sibling `references/{preflight,write-pages,synthesis,finish-lint}.md`, each organized as
inputs / outputs / acceptance / persisted / failure stop. The main file points at them by relative path
and the model loads them on demand. **Protocol keywords (workorder.yaml, resolve-concept, check-write,
window-done, status: proposed, lint, source-audit, reconciliation.json …) may live in references but must
not be lost** — tests check across `SKILL.md + references/*`.

## Three binding boundaries (v1)

1. **source-xray never writes the vault.** Reading-note / structural extraction lands in
   `pipeline-workspace/reports/source-xray/<src>.md` or `pipeline-workspace/staging/<src>/llm-notes/`,
   and is built **from published source/concept/topic content only**. It **does not preprocess, does not
   decide windows, does not decide write scope, and never creates or merges concept pages.** Only when
   the user explicitly wants it in the wiki does it hand off to `kb-save` for two-phase publish + lint.
2. **kb-qa and wiki-lint-semantic do not share triggers.** `kb-qa` is the broader post-publish /
   pre-save QA report (coverage, ljg-qa-style Q-chain, concept pollution, cross-page contradiction,
   formula/evidence spot-checks) → report + Review-Queue proposal. `wiki-lint-semantic` is the dedicated
   semantic-lint entry (L4 / contradiction / Q2). In v1 their triggers are **mutually exclusive**.
3. **Preprocessing is zero-LLM.** `source-preflight` only orchestrates and accepts
   `add-source → profile → source-convert → source-audit → windows → workorder`; it performs **no LLM
   semantic splitting / unit planning** (honoring the "no splitting" rule in CLAUDE.md / AGENTS.md).

## Test rubric (lives in tests/)

- **T1 — nine-section compliance:** walk **both `.claude/skills/*` and `.agents/skills/*`**; every
  SKILL.md (incl. references) covers the mandatory sections. Both trees are checked so the Codex side is
  never missed.
- **T2 — dual-agent parity:** the two trees have an identical skill set and byte-equivalent content,
  modulo the single per-agent truth pointer (`CLAUDE.md` ↔ `AGENTS.md` in ingest).
- **T3 — hygiene:** agent-facing files carry no dead `spec`/`ADR`/`docs/superpowers|adr|agents` pointers,
  no `pythonProject`, no `.Codex`.
- **T4 — protocol keywords intact:** key protocol words still present across `SKILL.md + references/*`.
- **T5 — source-xray guard:** its SKILL.md explicitly declares the boundary (does not preprocess / does
  not decide windows / does not decide write scope / never merges concept pages / published content only /
  does not write the vault).
