---
name: skill-evolve
description: Distill a recurring ingest/lint failure into a bounded improvement to one skill — read the skill-mine backlog → write a bounded SKILL.md edit on an isolated branch → run skill-gate (pytest + dual-tree parity + gate-integrity) → skill-stage the candidate, leaving skill-adopt to a human. Use when the user says "distill this failure into the skill / evolve a skill / let a skill self-improve / handle skill backlog item N". Only for improving this project's own skills; "summarize this / explain this / translate this / add this book to the KB (that is ingest)" must never trigger it.
---

# skill-evolve — make a skill steadier under a gate (the only LLM, human-triggered)

Distill a **recurring** failure into a **bounded improvement** to one skill; whether it's correct is judged
by **deterministic tests**, and release is decided by a **human**. This is the human-triggered "only LLM
action" under the core constraints — **never an unattended batch run.** Project truth: `CLAUDE.md` / `AGENTS.md`.

## 1. Triggers / Non-triggers

- **Triggers:** the user wants to distill a recurring failure into a skill — "evolve a skill / handle backlog item N / write this lesson into the skill / let a skill self-improve".
- **Non-triggers (never fire):**
  - "add this book / PDF to the KB" → that is **ingest**, not a skill edit.
  - "summarize this / explain this / translate this / query X in the KB" → read-only or ingest/kb-query, unrelated to editing a skill.
  - a one-off, non-reproducing failure (backlog `count` = 1) → not worth a skill edit.
  - anything that would need editing `tests/` or `pipeline.py` gate logic to "pass" → out of bounds, refuse.

## 2. Inputs

- The `skill-mine` output `pipeline-workspace/skill-evolution/backlog.yaml`: each entry has `signature / count / sources / sample_reason / last_seen`. Only `open` proposals are counted — signatures already fixed and retired via `proposals-resolve` drop out; use `last_seen` to judge how fresh a signal is.
- The user-named backlog entry (by `signature` or index).
- The target skill's `SKILL.md` (both trees) + failure-sample context (`review_proposals` `reason` / Review-Queue items).

## 3. Outputs

- A **bounded edit** to a **single** skill: only a section or two of that `SKILL.md`; **edit both trees in sync, keep them byte-equivalent**.
- A candidate on an isolated branch + the `skill-stage`-registered proposal `pipeline-workspace/skill-evolution/candidates/<id>/proposal.diff`.
- **Not published directly:** a candidate is semantically `proposed`; only a human `skill-adopt` merges it into both trees (stage→adopt is the two-phase-publish analogue).

## 4. Dependencies

- CLI: `skill-mine` (read backlog), `skill-gate` (deterministic gate), `skill-stage` (register a proposal), `skill-adopt` (human accept).
- Isolation: a git branch / worktree (candidate isolated from mainline).
- Truth: `CLAUDE.md` / `AGENTS.md` (the core constraints, especially that this is the human-triggered only-LLM action).
- It **does not** depend on any LLM-judge / training backend / rollout-replay.

## 5. Persisted artifacts

All under the gitignored workspace `pipeline-workspace/skill-evolution/`:
- `backlog.yaml` (skill-mine output, the input).
- `candidates/<id>/proposal.diff` (skill-stage output, for human review).
- `audit.jsonl` (staged / adopted / rejected "dead-end" negatives, append-only).

## 6. CLI commands (all business logic here)

```bash
python scripts/pipeline.py skill-mine                       # failure signals → backlog.yaml
# a human reads the backlog and picks a count>=2 (recurring) signature
git switch -c skill-cand/<id>                               # isolated branch
#   write the bounded SKILL.md edit on that branch (both trees in sync, byte-equivalent)
python scripts/pipeline.py skill-gate  --candidate <id>     # pytest + dual-tree parity + gate-integrity
python scripts/pipeline.py skill-stage --candidate <id>     # green → register the proposal, mainline untouched
#   report proposal.diff to the human, await confirmation
python scripts/pipeline.py skill-adopt --candidate <id>     # human-triggered: re-run the gate + commit both trees
```

## 7. Workflow

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| E1 mine | review_proposals | backlog.yaml entries (count≥2) | recurring signature only | backlog.yaml | nothing recurring |
| E1.5 route | one signature + failure samples | legislation-routing verdict (three written answers) | only "semantic judgment / thin orchestration" proceeds to E2 | routing answers in the hand-off report / proposal | routed to another layer → stop + report |
| E2 bounded edit | one signature + target SKILL.md | a 1–2 section edit, both trees | byte-equivalent across trees | branch worktree | edit needs tests/pipeline changes |
| E3 gate | candidate id | gate result | gate-integrity PASS + pytest green | — | gate red |
| E4 stage | green candidate | proposal.diff + audit entry | mainline untouched | candidates/<id>/ + audit.jsonl | — |
| E5 adopt (human) | proposal | both-tree commit | gate re-run passes | git commit + audit | gate red on re-run |

### E1.5 legislation routing (mandatory, before any edit)

A skill edit is the **last resort, not the default output** — the most valuable outcome of this skill is
often "do not edit the skill; sink the fix into the runtime". Classify the failure before touching any
SKILL.md:

| Failure class | Belongs in (NOT a skill edit) |
|---|---|
| deterministically reproducible & machine-checkable (state / order / ownership / accounting / format / render-safety) | parser / state machine / gate / schema in the CLI runtime |
| induced by a runtime prompt surface (CLI output, template seed, RESUME text) | that prompt surface + its contract test |
| a missing deterministic capability | a new CLI primitive |
| calling judgment / writing strategy / when-to-invoke / how-to-phrase | **the skill — the only class that proceeds to E2** |
| not yet reducible to any of the above | postmortem / QA / Review-Queue / human |

Every candidate must answer three questions **in writing** before E2:

1. What is the minimal reproduction?
2. Why can't a deterministic runtime check block or detect it?
3. Why does it belong in the skill rather than a runtime prompt surface?

If any answer routes away from the skill: **stop skill-evolve and report which layer should receive the
fix** — that hand-off report is a successful outcome of this skill, not a failure. (This skill still never
edits `tests/` or `pipeline.py` itself; routing to runtime means handing the signal back to a human /
normal development flow, not implementing it here.)

## 8. Failure stops / recovery

- `skill-gate` red → stop, do not stage:
  - **gate-integrity:** the candidate touched anything outside the two skill trees (especially `tests/`) → stop immediately. That is out-of-bounds / gaming its own gate.
  - **pytest red** (incl. dual-tree parity T2) → stop; paste the failure back, log a "dead-end" negative in audit, rewrite or abandon.
- the backlog entry's `count` = 1 (not reproducing) → not worth it, stop.
- E1.5 routes the failure to runtime / prompt surface / new primitive / human → stop, report the target
  layer, do not edit any SKILL.md.
- it would require editing `tests/` or gate logic to pass → never do it, stop and hand back.
- `skill-adopt` is always human-triggered; this skill never auto-adopts. **Recovery:** the audit.jsonl trail records every staged/rejected attempt.

## 9. Acceptance criteria

- [ ] The candidate diff only touches `.claude/skills/` and `.agents/skills/` (`skill-gate` gate-integrity PASS).
- [ ] Dual-tree byte-equivalence holds (pytest T2 green).
- [ ] `pytest tests` all green (`skill-gate` PASS).
- [ ] The E1.5 routing answers (three questions) are recorded, and the failure genuinely remains
      "semantic judgment or thin orchestration" — anything determinable was routed to the runtime instead.
- [ ] The edit is bounded (a section or two) and targets that backlog `signature`.
- [ ] The proposal is `skill-stage`-d; `audit.jsonl` has a record; `skill-adopt` is left to a human.
