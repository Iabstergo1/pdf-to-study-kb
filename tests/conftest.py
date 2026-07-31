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

Fourth fail-closed guard (same module): ``STUDY_KB_ROOT`` decides where the CLI thinks the knowledge
base lives — unset, ``pipeline._workspace_root()`` falls back to the **repo root**. Individual CLI
tests point it at their own ``tmp_path``, but that is a scattered convention, not a guarantee. So
``pytest_configure`` rejects any incoming value outside the temp sandbox (``unsafe STUDY_KB_ROOT for
tests``) and then hands the whole session its **own** temporary workspace root, before collection.
Rejecting rather than silently overriding is deliberate: a caller who exported a real vault root
must learn that the command itself was dangerous.

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


_SESSION_WORKSPACE: tempfile.TemporaryDirectory | None = None


def _install_isolated_study_kb_root(config):
    """collection **之前**把 ``STUDY_KB_ROOT`` 收口到本次 session 独占的临时根。

    必须在 ``pytest_configure`` 做，不能只靠 autouse 的 ``tmp_path`` fixture：fixture 要等到用例
    执行阶段才建立，而模块 import 与 collection hook 早已跑过——那时若有代码触到 workspace，
    fixture 来不及。

    调用方传进来的真实路径**拒绝而非静默覆盖**：静默覆盖会让"这条命令有数据风险"这件事无声消失。
    """
    global _SESSION_WORKSPACE
    problems = _sandbox.study_kb_root_violations(
        os.environ.get("STUDY_KB_ROOT"), REPO_ROOT, [tempfile.gettempdir()])
    if problems:
        raise pytest.UsageError(
            "unsafe STUDY_KB_ROOT for tests (fail-closed; tests/_sandbox.py):\n  "
            + "\n  ".join(problems))
    # ignore_cleanup_errors：Windows 上子进程可能还攥着句柄；清不掉就把目录留在系统临时区，
    # **绝不**因此去删别的路径。清理只针对本次 session 自己创建的这一个精确目录。
    _SESSION_WORKSPACE = tempfile.TemporaryDirectory(
        prefix="study-kb-testroot-", ignore_cleanup_errors=True)
    os.environ["STUDY_KB_ROOT"] = _SESSION_WORKSPACE.name
    config.add_cleanup(_SESSION_WORKSPACE.cleanup)


def pytest_configure(config):
    problems = _sandbox.basetemp_violations(
        getattr(config.option, "basetemp", None), REPO_ROOT, [tempfile.gettempdir()])
    if problems:
        raise pytest.UsageError(
            "test sandbox violations (fail-closed; tests/_sandbox.py):\n  " + "\n  ".join(problems))
    _install_isolated_study_kb_root(config)
    # 子进程 CLI 测试都是 env={**os.environ, ...}，所以在这里设一次即可覆盖整棵进程树：
    # 被测 CLI 不会在它当轮操作的工作区里落下 __pycache__，也不会回落到仓库根当知识库。
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
