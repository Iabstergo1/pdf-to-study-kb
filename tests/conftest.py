"""Centralised test-tier marking + fail-closed tier registry guard.

Markers are applied per test file from the single registry in ``tests/_tiering.py``
(FILE_TIERS), so the tier policy lives in one place. The daily tier is the
**positive whitelist** ``-m fast``; the full deterministic gate runs with no -m.
See ``pipeline-workspace/reports/test-audit-2026-07-13.md`` (P0) for the rationale.

Fail-closed guard: every ``tests/test_*.py`` file MUST be registered with a
primary tier. An unregistered new file, a stale registry entry, an unknown tier
name, or ``fast`` combined with a heavier tier aborts collection — a new test
file can never silently drop out of the frequent ``-m fast`` run.
"""
import os
from pathlib import Path

import pytest

import _tiering


def pytest_collection_modifyitems(items):
    tests_dir = Path(__file__).resolve().parent
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
