---
name: pipeline-doctor
description: Diagnose and safely repair a stuck ingest pipeline using only whitelisted CLI commands — stale vault locks, crashed running stages, corrupt window-done JSON, a forward-only state machine that refuses to re-run preprocessing. Use when the user says "the pipeline is stuck / 状态机卡住了 / the lock won't release / window-done keeps failing / it won't let me re-run profile / diagnose the pipeline". Never for content quality (kb-qa / wiki-lint-semantic), ingesting a source (ingest), post-publish retrospectives (kb-postmortem), or editing skills (skill-evolve); it never hand-writes SQL or edits the SQLite file directly.
---

# pipeline-doctor — symptom → diagnosis → safe repair (CLI only)

Recurring pipeline breakages used to be fixed by hand-editing the SQLite state db. This skill replaces
that with a fixed recipe table: every repair goes through a whitelisted `scripts/pipeline.py` command, so
every state change stays auditable. **Never hand-written SQL, never direct edits to the db file.**
Project truth: `CLAUDE.md`.

## 1. Triggers / Non-triggers

- **Triggers:** "the pipeline is stuck", "状态机卡住了", "the vault lock won't release", "window-done reports a JSON error", "it won't let me re-run profile / re-profile the source", "diagnose the pipeline / what state is the pipeline in".
- **Non-triggers (never fire):**
  - content quality / coverage / audit → `kb-qa`; semantic health → `wiki-lint-semantic`.
  - adding a book / writing pages → `ingest`; post-publish retrospective → `kb-postmortem`.
  - "distill this failure into the skill" → `skill-evolve`.
  - anything that would require raw SQL / editing the SQLite file → refuse and explain the CLI path.

## 2. Inputs

- The symptom as described by the user + any command stderr.
- `status` / `next` output (per-source stage/status + next human action).
- If windows are involved: `ingest-stats --source <src>` for a quick read of window states.

## 3. Outputs

- A diagnosis (which recipe row matched) and the executed safe repair, or an explicit hand-back when no
  recipe matches. Destructive steps (`reset-source --apply`) always show their dry-run plan first and wait
  for the user's confirmation.

## 4. Dependencies

- CLI whitelist: `status`, `next`, `unlock`, `fail`, `window-fail`, `reopen`, `reset-source`,
  `preflight-eval`, `arbitration-status`, `lint`, `ingest-stats`, `window-done --writes-file`.
- Truth: `CLAUDE.md`. **Never hand-written SQL; never edit the db file; never delete state by hand.**

## 5. Persisted artifacts

- None by default. Every repair lands as auditable rows written by the CLI itself
  (`source_stage_runs` gains `reset` / `reopened` / `failed` marker rows; locks table via `unlock`).

## 6. CLI commands

```bash
python scripts/pipeline.py status                       # always start here
python scripts/pipeline.py next                         # derived next human action
python scripts/pipeline.py unlock [--ttl N]             # stale vault lock (live locks are refused)
python scripts/pipeline.py fail --source <s> --stage <st> --error "<why>"    # crashed running stage
python scripts/pipeline.py window-fail --source <s> --window <w> --error "<why>"
python scripts/pipeline.py window-done --source <s> --window <w> --writes-file <path.json>
python scripts/pipeline.py reset-source --source <s> --to <stage>            # dry-run plan
python scripts/pipeline.py reset-source --source <s> --to <stage> --apply    # after user confirms
python scripts/pipeline.py reopen --source <s>          # finished source, incremental additions
```

## 7. Workflow (recipe table)

| Symptom | Diagnose with | Safe repair | Never |
|---|---|---|---|
| vault lock won't release, no ingest is live | `status` + the lock line | `unlock` (refuses live heartbeats — if it refuses, the ingest is alive: resume it instead) | breaking a live lock |
| a stage sits at `running` after a crash | `status` | `fail --source --stage --error` → retry the stage | deleting the run row |
| a window sits at `running` / wrong writes | `ingest-stats` | `window-fail` then redo the window, or `window-done --writes-file` | editing ingest_progress |
| `window-done --writes` dies on quote-stripped JSON (conda run) | the error message itself | put the array in a UTF-8 file → `window-done --writes-file <path>` | retrying the same shell quoting |
| preprocessing must be re-run but every stage says `[skip]` (forward-only) | `status` + `preflight-eval` | `reset-source --to <stage>` dry-run → user confirms → `--apply` → re-run the chain | hand-deleting stage runs |
| a published source needs incremental additions | `status` | `reopen --source` (state back to workorder_ready) | reset-source into the ingest segment |
| lint failed and rolled back | Review-Queue report | fix pages (re-apply in-place edits!) → `lint` again | editing published pages blind |
| arbitration queue unclear | `arbitration-status` | follow the ingest skill's arbitrate flow | inventing decisions |
| staging eats too much disk | `staging-clean --source <s>` (dry-run report) | user reviews the three-class list → `--apply` (guarded: published + assets synced) | deleting staging files by hand |

## 8. Failure stops / recovery

- No recipe row matches → stop, report exactly what `status`/`next` show, hand back to the user.
- A live (heartbeating) lock is never broken; `unlock` refusing is a signal to resume, not to force.
- `reset-source --apply` only after the user has seen the dry-run plan and confirmed.
- If a repair command itself errors → stop and show the error verbatim; do not improvise around a guard —
  the guards (InvalidTransition, lock checks, C3 JSON validation) are the protocol, not obstacles.

## 9. Acceptance criteria

- [ ] Every state change went through a whitelisted CLI command (auditable in `source_stage_runs` / locks).
- [ ] No raw SQL, no direct db-file edits, no guard bypassed.
- [ ] Destructive repairs (`reset-source --apply`) were user-confirmed with the dry-run plan shown first.
- [ ] The pipeline is unstuck (verified via `status` / `next`), or the case was explicitly handed back.
