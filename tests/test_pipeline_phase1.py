import argparse
import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline  # noqa: E402


PHASE1_DIRS = [
    "input",
    "config",
    "pipeline-workspace/reports",
    "pipeline-workspace/staging",
    "pipeline-workspace/reviews",
    "pipeline-workspace/runs",
    "pipeline-workspace/checkpoints",
    "pipeline-workspace/state",
    "pipeline-workspace/events",
    "study-kb/Section-Lessons",
    "study-kb/Concept-Cards",
    "study-kb/Glossary",
    "study-kb/Symbols",
    "study-kb/Formula-Ledger",
    "study-kb/Claims",
    "study-kb/Questions",
    "study-kb/Review-Queue",
    "study-kb/Learning-Maps",
    "study-kb/Source-QA",
    "study-kb/Dashboards",
]


def test_ensure_dirs_creates_semantic_unit_and_legacy_dirs(tmp_path):
    book_root = tmp_path / "books" / "phase1-book"

    pipeline._ensure_dirs(book_root)

    for rel in PHASE1_DIRS:
        assert (book_root / rel).is_dir(), f"missing Phase 1 dir: {rel}"

    assert (book_root / "pipeline-workspace" / "tasks").is_dir()


def test_pipeline_help_lists_semantic_unit_commands():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pipeline.py"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    for command in [
        "profile-pdf",
        "plan-units",
        "validate-unit-plan",
        "review-unit-plan",
    ]:
        assert command in result.stdout


def test_semantic_unit_commands_defer_to_phase_modules(monkeypatch, tmp_path):
    book_root = tmp_path / "books" / "phase1-book"
    book_root.mkdir(parents=True)
    calls = []

    pdf_profile = types.ModuleType("pdf_profile")
    pdf_profile.profile_pdf_command = (
        lambda root, force=False: calls.append(("profile-pdf", root, force))
    )

    unit_plan = types.ModuleType("unit_plan")
    unit_plan.plan_units_command = (
        lambda root, force=False: calls.append(("plan-units", root, force))
    )
    unit_plan.validate_unit_plan_command = (
        lambda root: calls.append(("validate-unit-plan", root))
    )
    unit_plan.review_unit_plan_command = (
        lambda root, list_only=False: calls.append(("review-unit-plan", root, list_only))
    )

    monkeypatch.setitem(sys.modules, "pdf_profile", pdf_profile)
    monkeypatch.setitem(sys.modules, "unit_plan", unit_plan)
    monkeypatch.setattr(pipeline, "find_book_root", lambda book: book_root)

    pipeline.cmd_profile_pdf(argparse.Namespace(book="phase1-book", force=True))
    pipeline.cmd_plan_units(argparse.Namespace(book="phase1-book", force=True))
    pipeline.cmd_validate_unit_plan(argparse.Namespace(book="phase1-book"))
    pipeline.cmd_review_unit_plan(argparse.Namespace(book="phase1-book", list=True))

    assert calls == [
        ("profile-pdf", book_root, True),
        ("plan-units", book_root, True),
        ("validate-unit-plan", book_root),
        ("review-unit-plan", book_root, True),
    ]
