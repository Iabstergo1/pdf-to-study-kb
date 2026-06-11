# Refactor Completion Audit - 2026-06-11

## Scope

Audited the current checkout at `D:\pdf-to-study-kb` after the reported Claude Code refactor. The audit compared the current `HEAD` against `origin/main`, read the authority chain (`CLAUDE.md` -> spec -> ADR -> plans -> domain), checked the actual CLI/source/tests, and ran available verification.

## Executive Conclusion

Current repository state does **not** show P0-P7 implementation as completed. The tracked changes ahead of `origin/main` are documentation/requirements changes only. The active CLI and tests still describe and exercise the old semantic-unit/LangGraph pipeline.

The test suite passes, but that is not evidence that the new architecture is implemented, because the passing tests are mostly for the legacy pipeline and the target P0-P7 source/test files are absent from the tracked tree.

## Findings

### P0 - P0-P7 implementation is absent from the tracked source tree

The spec requires an end-to-end path: `profile -> source-convert -> windows + work order -> /ingest -> lint` (`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md:297`) and phases P0-P7 (`:311-319`). P0 specifically requires source-level state tables, `pipeline status`/`next`, locks, snapshots, and recoverability (`:311`).

Current `scripts/pipeline.py` still exposes only the legacy commands:

- `init-book`
- `profile-pdf`
- `plan-units`
- `validate-unit-plan`
- `review-unit-plan`
- `run-book`

Evidence:

- `scripts/pipeline.py:5-10` documents the old command set and "unit LangGraph" flow.
- `scripts/pipeline.py:196-245` registers only old subcommands.
- Running `python scripts\pipeline.py status` fails with `invalid choice: 'status'`.
- Expected target files are not present: `scripts/state_store.py`, `scripts/source_convert.py`, `scripts/windowing.py`, `scripts/workorder.py`, `scripts/concept_store.py`, `scripts/promotion.py`, `scripts/wiki_gate.py`, `scripts/snapshots.py`, `scripts/locks.py`, `scripts/ingest_guards.py`, `tests/test_state_store.py`, `tests/test_p1_cli.py`, `tests/test_p2_cli.py`, `tests/test_p4_cli.py`, `tests/test_p5_cli.py`, `tests/test_p6_cli.py`, `tests/test_p7_cli.py`.
- `git diff --stat origin/main...HEAD` shows only docs/requirements changes.

Impact: Treating this checkout as "all plans complete" would send the next agent onto a false baseline. The implementation should be considered not landed in this worktree.

### P0 - Legacy LangGraph/unit pipeline remains the active implementation

The authority documents say to remove semantic unit planning and LangGraph/checkpointer dependencies (`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md:58`, `:280-282`). ADR-0001 records the decision to remove LangGraph/checkpointer and use deterministic CLI + single business SQLite instead (`docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md:15`).

Current code still actively routes through the old design:

- `scripts/pipeline.py:222-225` registers `run-book` as a unit LangGraph flow with `--executor langgraph-worker`.
- `scripts/run_book.py:27` says it is the semantic LangGraph flow entrypoint.
- `scripts/run_book.py:36` tells users to run `plan-units -> validate-unit-plan -> review-unit-plan`.
- `scripts/run_book.py:189` uses `pipeline-workspace/checkpoints/langgraph.sqlite`.
- `scripts/langgraph_worker.py:497-499` imports and builds `StateGraph`.
- `scripts/business_db.py:74` points the business DB helper at `pipeline-workspace/checkpoints/langgraph.sqlite`.
- `requirements.txt:11-12` still installs `langgraph` and `langgraph-checkpoint-sqlite`.

Impact: Users and agents can still run the deprecated architecture as the only implemented path. This directly conflicts with the claimed completion of the no-LangGraph refactor.

### P1 - Passing tests give false confidence for the new architecture

Verification command passed:

```powershell
$env:PYTHONUTF8='1'; D:\miniconda3\envs\pythonProject\python.exe -m pytest -q --basetemp tmp\pytest-audit
```

Result: `88 passed in 23.39s` with one `pytest_asyncio` deprecation warning.

However, the tracked tests still target the old pipeline:

- `tests/test_pipeline_phase1.py` checks `plan-units`.
- `tests/test_run_book_semantic.py` imports `langgraph_worker`.
- `tests/test_unit_graph.py` imports `langgraph_worker` and expects `langgraph.sqlite`.

There are no tracked tests for the target commands or P0-P7 contracts (`state_store`, source conversion, work orders, promotion, wiki gate, locks, snapshots, ingest progress).

Impact: The green test run proves the old code has not broken; it does not prove the refactor was completed.

### P1 - `docs/superpowers/plans/` does not contain P0-P7 implementation plans

The only tracked plan file is:

```text
docs/superpowers/plans/2026-06-09-documentation-authority-chain.md
```

That plan explicitly states it "only changes documentation" and does not touch `scripts/` (`docs/superpowers/plans/2026-06-09-documentation-authority-chain.md:7`). It also states that completing this plan merely makes the repo safe to enter P0 (`:477`).

Impact: The repository does not contain the claimed sequence of P0-P7 implementation plans. If those plans were executed elsewhere, their source changes and plan artifacts are missing from this checkout.

### P2 - Documentation authority-chain plan has stale or self-defeating verification instructions

The plan's terminal verification asks for no matches of deleted document filenames, but the plan itself still contains those filenames (`docs/superpowers/plans/2026-06-09-documentation-authority-chain.md:417`). The same plan also says the expected docs tree includes `docs/agents/{domain,issue-tracker,triage-labels}.md` (`:456`), while the execution adjustment says `issue-tracker.md` and `triage-labels.md` were intentionally deleted (`:13`).

Impact: A future agent following the plan literally will get contradictory verification results and may attempt to restore intentionally deleted files.

### P3 - Planned branch workflow was not followed

The plan says to create a branch before committing (`docs/superpowers/plans/2026-06-09-documentation-authority-chain.md:11`), but the current state is `main...origin/main [ahead 2]`.

Impact: Low technical risk, but it makes review/rollback harder and differs from the written workflow.

## Verification Performed

- `git status --short --branch`: current branch is `main...origin/main [ahead 2]`; no uncommitted tracked changes.
- `git diff --stat origin/main...HEAD`: 10 files changed, all documentation/requirements/deletions.
- `git diff --check origin/main...HEAD`: no whitespace errors.
- `D:\miniconda3\envs\pythonProject\python.exe -m pytest -q --basetemp tmp\pytest-audit`: 88 passed.
- `D:\miniconda3\envs\pythonProject\python.exe scripts\pipeline.py --help`: shows only legacy command set.
- `D:\miniconda3\envs\pythonProject\python.exe scripts\pipeline.py status`: fails, `status` is not a valid command.
- Target P0-P7 files checked with `Test-Path`: expected source/test files are absent.

## Recommended Next Step

Do not continue from the assumption that P0-P7 are complete. First determine whether the Claude Code implementation landed in another branch/worktree or was lost before commit. If it exists elsewhere, bring that branch/worktree into review. If not, restart from P0 with a strict gate: each phase must leave tracked source files, tracked tests, and a passing phase-specific verification command before moving to the next plan.
