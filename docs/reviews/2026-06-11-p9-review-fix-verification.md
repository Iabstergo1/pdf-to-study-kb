# P9 Review Fix Verification - 2026-06-11

## Scope

Re-verified the uncommitted fixes for `docs/reviews/2026-06-11-p9-code-review.md` on branch `feat/p9-legacy-cleanup`. No commit, merge, or push was performed.

## Verified Fixes

- P0 path traversal: fixed. `domains/misc/../../outside.md` no longer matches `domains/misc/**`, and `can_overwrite()` rejects it as unsafe.
- P1 stale lock CLI: fixed at command-surface level. `unlock` appears in help, `status`/`next` inspect lock state, and tests cover stale-only unlock.
- P2 windows hash: fixed. The windows artifact now records the `windows.jsonl` byte hash while retaining source.md hash as stage input hash.
- Regression suite: targeted and full tests pass when using repo-local `--basetemp`.

## Remaining Finding

### P1 - `lint --source X` can publish source state while leaving unowned proposed pages behind

The source-scoping fix correctly avoids promoting pages that do not belong to the current source. However, `cmd_lint()` still treats an empty scoped set as success and marks the source as `published`.

Evidence:

- `scripts/pipeline.py:371-382` collects all proposed pages, filters to pages belonging to `args.source`, and only prints `[skip]` for unowned proposed pages.
- `scripts/pipeline.py:383-405` computes the lint input hash from the filtered set and completes the source lint stage even when the filtered set is empty.
- `templates/overview.md` is seeded as `status: proposed` and has no source ownership field. If it is not included in the window write set, `lint --source X` skips it and can still publish source X.

Manual repro run during verification:

```powershell
# In a temp STUDY_KB_ROOT, create source note, run through ingest-start,
# create wiki/topics/unowned.md as status: proposed with no source/source_refs,
# run ingest-done, then lint --source note.
```

Observed lint output:

```text
[skip] proposed 页不归属 'note'，留待其所属 source 收尾: overview.md
[skip] proposed 页不归属 'note'，留待其所属 source 收尾: topics/unowned.md
[OK] lint passed: promoted 0 pages; index/registry/aliases rebuilt; source published
```

Observed status:

```text
note                         misc           lint             published
```

The unowned page remained `status: proposed`.

Impact: This is still a source lifecycle/audit inconsistency. A source can become `published` even though proposed pages produced during that ingest were skipped due to missing ownership/write-set accounting. This weakens the new "window-done --writes is necessary" contract because forgetting the write set does not fail the source; it silently publishes zero or a partial set.

Recommended fix: treat skipped unowned proposed pages as a blocking condition when running `lint --source X`, unless they are explicitly known to belong to another source. A conservative version is:

- If any proposed page lacks ownership and is not in the current source write set, fail lint and create a Review-Queue item explaining that ownership/write_set is missing.
- If the current source has zero scoped proposed pages, fail lint unless the command has an explicit maintenance flag.
- Add a regression test for "unowned proposed page + lint source" expecting non-zero exit and source status `failed`, not `published`.

## Verification Commands

```powershell
D:\miniconda3\envs\pythonProject\python.exe -m pytest tests/test_ingest_guards.py tests/test_p1_cli.py tests/test_p4_cli.py tests/test_p6_cli.py -q --basetemp tmp\pytest-review-targeted
D:\miniconda3\envs\pythonProject\python.exe -m pytest tests -q --basetemp tmp\pytest-review-full
git diff --check
```

Results:

- Targeted tests: `21 passed in 24.95s`.
- Full tests: `138 passed in 30.18s`.
- `git diff --check`: passed.
- One `pytest_asyncio` deprecation warning remains.

## Recommendation

Do not commit/merge yet. Fix the remaining lint ownership/status issue, then rerun the same targeted and full verification commands.
