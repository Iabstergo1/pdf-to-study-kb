---
name: kb-postmortem
description: Post-publish retrospective on one ingested source — collect deterministic proxy metrics (ingest-stats), refresh the failure-signal backlog with increment context (snapshot the old backlog.yaml BEFORE skill-mine, then diff), read the digest routing deviations, and write one standardized report with recommendations only (skill-evolve triggers, gate/truth updates, proposals-resolve dry-run commands). Use when the user says "postmortem this ingest / 复盘这次入库 / how did this book's ingest go / ingest retrospective for <source>". Only for a source already published; "run a KB QA / audit coverage" is kb-qa, "semantic health check" is wiki-lint-semantic, and "distill this failure into the skill" is skill-evolve — none of those trigger this.
---

# kb-postmortem — post-publish ingest retrospective (report-only)

After a source publishes, turn the manual "lessons learned" pass into a standard step: deterministic
proxy metrics + digest deviations + backlog delta → one report. **Recommendations only — every follow-up
action (skill-evolve, gate edits, `proposals-resolve --apply`) is decided by the human.**
Project truth: `AGENTS.md`.

## 1. Triggers / Non-triggers

- **Triggers:** "postmortem <source>", "复盘这次入库", "how did this ingest go", "ingest retrospective / review this book's ingest".
- **Non-triggers (never fire):**
  - "run a KB QA / audit coverage / spot-check evidence / run the Q-chain" → `kb-qa`.
  - "semantic health check / contradictions / L4 / Q2 added value" → `wiki-lint-semantic`.
  - "distill this failure into the skill / evolve a skill / handle backlog item N" → `skill-evolve` (this skill only **recommends** it).
  - "work the review queue item by item" → `kb-review`.
  - the source is not published yet → finish the ingest (lint gate) first; this skill never runs mid-ingest.

## 2. Inputs

- `source_id` — must show `lint/published` in `status`.
- Proxy metrics via `ingest-stats` (windows, stage durations and reruns, lint failures ≈ rollbacks, pages estimate, violations by kind). Token/cost numbers are not available — never invent them.
- `pipeline-workspace/staging/<src>/digest.md`: the per-chapter content-routing table + logged deviations.
- The old `pipeline-workspace/skill-evolution/backlog.yaml` — **read and snapshot it BEFORE running `skill-mine`**: skill-mine overwrites the whole file, so mining first destroys the increment context.
- Residue in `wiki/Review-Queue/` for this source.

## 3. Outputs

One standardized report `pipeline-workspace/reports/postmortem/<src>-<YYYY-MM-DD>.md` with four sections:

1. **Proxy metrics** (from `ingest-stats`; honest scope — no invented token/cost data).
2. **Digest routing deviations** (from `digest.md`; absent → marked degraded).
3. **Backlog delta** (old snapshot vs refreshed) + skill-evolve trigger recommendations (recurring signatures with `count` >= 2 only).
4. **Gate / `AGENTS.md` update recommendations.**

Plus a suggested-commands list: `proposals-resolve` **dry-runs** for signatures this ingest's fixes
covered. **Recommendations only — this skill never executes them.**

## 4. Dependencies

- CLI: `status`, `ingest-stats`, `skill-mine`; `proposals-resolve` appears in suggestions only (human confirms and runs `--apply`).
- `staging/<src>/digest.md` (may be absent for md fast-path sources → degraded, noted in the report).
- Truth: `AGENTS.md`. No new LLM judgement enters the CLI.

## 5. Persisted artifacts

- The report `pipeline-workspace/reports/postmortem/<src>-<YYYY-MM-DD>.md` (the old-backlog snapshot is embedded in its delta section).
- **No vault page is written; no DB row is changed** (`skill-mine` only rewrites the derived `backlog.yaml`).

## 6. CLI commands

```bash
python scripts/pipeline.py status                                  # W1: confirm lint/published
python scripts/pipeline.py ingest-stats --source <src> --json      # W2: proxy metrics
# W3: FIRST copy/read pipeline-workspace/skill-evolution/backlog.yaml (old state), THEN:
python scripts/pipeline.py skill-mine                              # refresh (overwrites backlog.yaml)
# suggested only — never auto-run by this skill:
python scripts/pipeline.py proposals-resolve --signature <kind> --source <src>   # dry-run first
```

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| W1 confirm published | `status` | source is `lint/published` | otherwise stop | — | not published |
| W2 metrics | `ingest-stats --json` | metrics section | numbers come from the state db, none invented | report draft | non-zero exit |
| W3 backlog delta | old backlog.yaml snapshot → `skill-mine` → diff | delta section | **old file snapshotted before mining** | snapshot in report | mine fails |
| W4 digest deviations | `staging/<src>/digest.md` | deviations section | absent → degraded note, continue | report draft | — |
| W5 write report | W2–W4 | `reports/postmortem/<src>-<date>.md` | four sections present | the report | — |
| W6 recommendations | delta + deviations | skill-evolve / gate / `proposals-resolve` suggestions | commands are suggestions only, count>=2 rule | report section | — |

## 8. Failure stops / recovery

- Source not `lint/published` → stop and point to the finishing gate (`lint`) or `kb-review` for pending violations.
- `ingest-stats` exits non-zero → stop, report the error verbatim.
- `digest.md` missing (md fast-path source) → do **not** stop; mark the deviations section degraded.
- Never run `proposals-resolve --apply`, `skill-adopt`, or any vault/DB write from this skill.
- **Recovery:** the report is idempotent — re-running the retrospective overwrites the same day's file.

## 9. Acceptance criteria

- [ ] Report exists at `pipeline-workspace/reports/postmortem/<src>-<YYYY-MM-DD>.md` with the four sections.
- [ ] The old backlog was snapshotted **before** `skill-mine` ran (the delta section shows old vs new).
- [ ] Zero vault writes, zero DB changes; `proposals-resolve` appears only as suggested dry-run commands.
- [ ] skill-evolve recommendations name only recurring signatures (`count` >= 2).
