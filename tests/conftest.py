"""Centralised test-tier marking.

Applies pytest markers per test file so the marker policy lives in one place
instead of editing every contract test module. See
``pipeline-workspace/reports/test-audit-2026-06-25.md`` for the rationale
(layered execution: fast local checks, targeted subsystem checks, full
deterministic gate before release / real-book validation).

Files not listed here carry no tier marker and are picked up by the default
``-m "not slow and not realbook"`` daily run.
"""
import os

import pytest

# filename -> markers to apply to every test collected from that file.
_FILE_MARKERS = {
    "test_conversion_backend_cli.py": ("cli", "slow"),
    "test_lint_republish_cli.py": ("cli", "slow"),
    "test_ingest_orchestration_cli.py": ("cli", "slow"),
    "test_skill_evolution.py": ("skill", "slow"),
    "test_preprocessing_cli.py": ("cli",),
    "test_concept_promotion_cli.py": ("cli",),
    "test_query_session_cli.py": ("cli",),
    "test_vault_init_cli.py": ("cli",),
    "test_command_docs.py": ("skill",),
    "test_skill_standard.py": ("skill",),
}


def pytest_collection_modifyitems(items):
    for item in items:
        filename = os.path.basename(item.location[0])
        for marker in _FILE_MARKERS.get(filename, ()):
            item.add_marker(getattr(pytest.mark, marker))
