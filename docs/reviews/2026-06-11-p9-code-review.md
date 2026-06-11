# feat/p9-legacy-cleanup Code Review - 2026-06-11

## Scope

Reviewed `feat/p9-legacy-cleanup` against `main` after confirming the implementation lives on the local P0-P9 branch stack. No merge or push was performed.

## Summary

The branch is real and substantially implements the new architecture: legacy LangGraph/unit files are removed, new CLI commands exist, and the phase test suite passes when pytest is run with a repo-local `--basetemp`.

Do not fast-forward to `main` yet. There are merge-blocking issues in the write guard and source-scoped publishing semantics.

## Findings

### P0 - `check-write` allows path traversal outside the vault/write scope

Evidence:

- `scripts/ingest_guards.py:24-26` normalizes only backslashes and then applies a regex glob.
- `scripts/ingest_guards.py:36-38` joins `Path(vault) / rel_path` and allows nonexistent targets as `"new page"`.
- `scripts/pipeline.py:325-330` passes the uncanonicalized CLI path directly into those guards.
- Spec §9 says writes must stay inside `write_scope`, and out-of-scope writes are failures (`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md:313-316`).

Minimal repro run during review:

```powershell
D:\miniconda3\envs\pythonProject\python.exe -c "import sys; sys.path.insert(0, r'D:\pdf-to-study-kb\scripts'); import ingest_guards; print(ingest_guards.in_write_scope('domains/misc/../../outside.md', ['domains/misc/**'])); print(ingest_guards.can_overwrite(r'D:\tmp\wiki', 'domains/misc/../../outside.md', []))"
```

Observed: `True` and `(True, 'new page')`.

Impact: A slash-command user or model following `check-write` can receive `ALLOW` for a path that escapes the intended vault subtree. Fix by rejecting absolute paths, `..` segments, drive-qualified paths, and canonicalizing `vault / rel_path` with a final `relative_to(vault.resolve())` check before any allow decision.

### P1 - `lint --source X` promotes every proposed page in the vault, not X's write set

Evidence:

- `wiki_gate.collect_proposed()` scans the entire vault and returns all `status: proposed` pages (`scripts/wiki_gate.py:23-33`).
- `cmd_lint()` uses that unfiltered set for the source-specific lint run (`scripts/pipeline.py:350-369`).
- On pass, `wiki_gate.promote(vault, proposed)` flips all collected pages to `published` while only `args.source` is marked published (`scripts/pipeline.py:390-405`).

Impact: If source A is ingested but not linted, then source B is ingested and linted, B's lint can publish A's proposed pages while A's state remains `ingested/proposed`. This breaks the source-level state machine and audit trail. Fix by deriving the lint/promote candidate set from the source work order/window write set, or by recording source ownership in page frontmatter and filtering on it.

### P1 - Stale lock recovery exists in code but is not reachable from the CLI/status contract

Evidence:

- Spec says `pipeline status` should show lock holder/start time and stale heartbeat; `pipeline next` should give cleanup advice (`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md:117-120`, `:392`).
- `locks.py` implements `is_stale()` and `break_stale()` (`scripts/locks.py:60-75`).
- `cmd_status()` and `cmd_next()` only print source rows/actions and never inspect `source_locks` (`scripts/pipeline.py:471-492`).
- `ingest-start` rejects any held lock without stale handling (`scripts/pipeline.py:250-252`).

Impact: A crashed `/ingest` can leave the vault locked with no supported CLI path to diagnose or recover. Fix by surfacing lock state in `status`, adding stale advice in `next`, and adding a guarded maintenance command such as `break-lock --stale-only`.

### P2 - `windows` records the source.md hash as the windows.jsonl artifact hash

Evidence:

- `cmd_windows()` computes `ihash` from `source.md` (`scripts/pipeline.py:124-126`).
- After writing `windows.jsonl`, it records the windows artifact and stage output hash as the same `ihash` (`scripts/pipeline.py:131-136`).

Impact: The artifacts table cannot verify the actual windows file bytes, and downstream workorder idempotency can be harder to audit. Compute a separate `ohash = sha256((out / "windows.jsonl").read_bytes())` after writing and use it for the artifact/output hash.

## Verification

Commands run:

```powershell
git diff --check main...HEAD
D:\miniconda3\envs\pythonProject\python.exe scripts\pipeline.py --help
D:\miniconda3\envs\pythonProject\python.exe scripts\pipeline.py status
D:\miniconda3\envs\pythonProject\python.exe -m pytest tests -q --basetemp tmp\pytest-review
```

Results:

- `git diff --check main...HEAD`: passed.
- CLI help shows the new command set and no legacy `plan-units` / `run-book`.
- `pipeline.py status`: exits 0 with `no state db yet`.
- `pytest tests -q --basetemp tmp\pytest-review`: `133 passed in 27.87s`; one `pytest_asyncio` deprecation warning.

Direct `pytest tests -q` was also tried and failed with `PermissionError` under `C:\Users\Lenovo\AppData\Local\Temp\pytest-of-Lenovo`; this is an environment temp-dir issue, not a test assertion failure.

## Recommendation

Fix P0/P1 before fast-forwarding `main` or opening a PR. After fixes, add regression tests for path traversal, multi-source proposed-page isolation, and stale-lock CLI recovery, then rerun:

```powershell
D:\miniconda3\envs\pythonProject\python.exe -m pytest tests -q --basetemp tmp\pytest-review
git diff --check main...HEAD
```
