# Command routing (decision tree + positive / counter-examples)

Architecture truth: `CLAUDE.md` / `AGENTS.md`. The command layer is `.claude/skills/<name>/SKILL.md`
(Claude Code) / `.agents/skills/<name>/SKILL.md` (Codex). **All skills are model-invocable by their
`description`**; misfires are suppressed by the counter-examples below, and data safety is enforced by the
deterministic CLI guards (orthogonal to auto-invocation). Skills can also be called manually as `/name`.

## Decision tree

- A new external source (PDF/DOCX/PPTX/MD) to ingest → `ingest` (orchestrates preprocessing → writes
  `status: proposed` → finishes with lint).
- Just run deterministic preprocessing + workorder acceptance, write no semantic pages → `source-preflight`.
- Ask about existing knowledge → `kb-query` (read-only + persists a query-session).
- Keep a query result → `kb-save` (gated by save-back-policy).
- Work the review queue → `kb-review`.
- Semantic health check → `wiki-lint-semantic`.
- QA / audit / coverage / Q-chain / evidence spot-check → `kb-qa` (triggers mutually exclusive with
  `wiki-lint-semantic`).
- Reading notes / learning-path candidates from published content → `source-xray` (writes reports only,
  not the vault).
- Post-publish retrospective on an ingested source (metrics / deviations / backlog delta) → `kb-postmortem`
  (report + recommendations only; never edits the vault or resolves proposals itself).
- The pipeline itself is stuck (lock / crashed running stage / corrupt window JSON / forward-only refuses a
  re-run) → `pipeline-doctor` (whitelisted CLI repairs only, never hand-written SQL).

## Positive examples

- "add this PDF / this book to the knowledge base", "ingest game-theory-whitepaper", "index this doc" → `ingest`
- "preprocess this PDF first", "run source-preflight", "see if it can be ingested" → `source-preflight`
- "what does the KB say about signaling games", "search my wiki" → `kb-query`
- "save that comparison into the wiki", "turn it into a synthesis" → `kb-save`
- "work the review queue" → `kb-review`; "do a semantic health check" → `wiki-lint-semantic`
- "run a KB QA", "audit coverage", "spot-check evidence", "run the Q-chain" → `kb-qa`
- "x-ray this published source", "make reading notes from published content" → `source-xray`
- "postmortem this ingest", "复盘这次入库", "how did this book's ingest go" → `kb-postmortem`
- "the pipeline is stuck", "状态机卡住了", "the lock won't release", "it won't let me re-run profile" → `pipeline-doctor`

## Counter-examples (never enter the write / ingest path)

- "Summarize this article", "explain this passage", "translate this" → a normal answer; not a wiki flow.
- "help me set up Obsidian", "fix this code bug", "answer a trivia question" → unrelated to the KB.
- "what is this PDF about?" (a question, not an ingest request) → a normal answer, unless the user says
  "add it to the knowledge base".
- `source-preflight` never does LLM semantic splitting / unit planning / writing `proposed` pages; to
  ingest, switch to `ingest`.
- `source-xray` uses published content only and writes no vault by default; to save, switch to `kb-save`.
- "semantic health check / find contradictions / comparison dimensions / Q2 added value" → those belong to
  `wiki-lint-semantic`, not `kb-qa`.
- "audit coverage / run the Q-chain" → `kb-qa`, not `kb-postmortem`; "distill this failure into the skill"
  → `skill-evolve` — `kb-postmortem` only produces recommendations, it never edits skills or resolves proposals.
- "this page's content looks wrong / bad writing" → content quality (`kb-qa` / `wiki-lint-semantic`), not
  `pipeline-doctor`; `pipeline-doctor` fixes pipeline state only, via whitelisted CLI, never raw SQL.
