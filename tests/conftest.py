"""Centralised test-tier marking + fail-closed tier registry / sandbox guards.

Markers are applied per test file from the single registry in ``tests/_tiering.py``
(FILE_TIERS), so the tier policy lives in one place. The daily tier is the
**positive whitelist** ``-m fast``; the full deterministic gate runs with no -m.
See ``pipeline-workspace/reports/test-audit-2026-07-13.md`` (P0) for the rationale.

Fail-closed guard: every ``tests/test_*.py`` file MUST be registered with a
primary tier. An unregistered new file, a stale registry entry, an unknown tier
name, or ``fast`` combined with a heavier tier aborts collection — a new test
file can never silently drop out of the frequent ``-m fast`` run.

Second fail-closed guard (``tests/_sandbox.py``): an explicit ``--basetemp`` must stay inside this
repository's ``tmp/`` or the system temp area. pytest **wipes** basetemp wholesale, so a stray
``$PWD``-relative path pointed at somebody else's workspace is a data hazard, not just litter.
The same hook turns off bytecode writing for the whole test process tree, so subprocess CLI tests
never leave ``__pycache__`` behind in whatever directory they happen to run against.

Third fail-closed guard (same module): the collected set must live entirely under ``tests/``.
``pytest.ini`` declares the boundary (``testpaths`` / ``norecursedirs``); this hook is the runtime
backstop for a run that pulls in leftover ``tmp/`` basetemps alongside the real suite — those carry
the skill-evolution fixtures' prop file ``tests/test_ok.py``, and a second copy of that basename
makes pytest abort the whole run with ``import file mismatch``.

**Reach, stated honestly:** this conftest is only loaded when something under ``tests/`` is
collected, so ``pytest tmp/whatever`` on its own never reaches it. That is fine — the property we
protect is "the *suite* is exactly tests/", and the suite always includes this directory. The
declarative ini boundary is what keeps a bare run out of ``tmp/`` in the first place;
``tests/test_collection_boundary_cli.py`` asserts that end-to-end against a real subprocess.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

import _sandbox
import _tiering

REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config):
    problems = _sandbox.basetemp_violations(
        getattr(config.option, "basetemp", None), REPO_ROOT, [tempfile.gettempdir()])
    if problems:
        raise pytest.UsageError(
            "test sandbox violations (fail-closed; tests/_sandbox.py):\n  " + "\n  ".join(problems))
    # 子进程 CLI 测试都是 env={**os.environ, ...}，所以在这里设一次即可覆盖整棵进程树：
    # 被测 CLI 不会在它当轮操作的工作区里落下 __pycache__。
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True


def pytest_collection_modifyitems(items):
    tests_dir = Path(__file__).resolve().parent
    # item.location[0] 是相对 rootdir 的路径，从别处调用时 abspath 会算错；用绝对路径。
    problems = _sandbox.collected_outside_tests(
        {getattr(item, "path", None) or item.fspath for item in items}, tests_dir)
    if problems:
        raise pytest.UsageError(
            "test collection boundary violations (fail-closed; tests/_sandbox.py):\n  "
            + "\n  ".join(problems))
    problems = _tiering.registry_violations(
        (p.name for p in tests_dir.glob("test_*.py")), _tiering.FILE_TIERS)
    if problems:
        raise pytest.UsageError(
            "test tier registry violations (fail-closed; tests/_tiering.py):\n  "
            + "\n  ".join(problems))
    for item in items:
        filename = os.path.basename(item.location[0])
        for marker in _tiering.FILE_TIERS.get(filename, ()):
            item.add_marker(getattr(pytest.mark, marker))
