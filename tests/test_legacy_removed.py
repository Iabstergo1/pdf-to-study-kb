"""旧管线下线守卫（spec §12 / ADR-0001）：确保被删除的旧路径不再回来。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_SCRIPTS = [
    "langgraph_worker.py", "unit_plan.py", "unit_context.py", "run_book.py",
    "business_db.py", "llm_provider.py", "pdf_profile.py", "ocr_surya.py",
    "surya_smoke.py", "evidence_verifier.py", "review_gate.py",
    "obsidian_indexes.py", "memory_store.py", "cost_guard.py", "web_ops.py", "serve.py",
]


def test_legacy_scripts_gone():
    leftovers = [n for n in LEGACY_SCRIPTS if (ROOT / "scripts" / n).exists()]
    assert leftovers == [], f"legacy scripts still present: {leftovers}"


def test_legacy_dirs_and_templates_gone():
    assert not (ROOT / "webapp").exists()
    assert not (ROOT / "templates" / "section-lesson.template.md").exists()
    assert not (ROOT / "templates" / "review-report.template.md").exists()


def test_pipeline_has_no_legacy_commands_and_no_toplevel_yaml():
    text = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    for legacy in ["plan-units", "run-book", "init-book", "profile-pdf",
                   "validate-unit-plan", "review-unit-plan", "langgraph"]:
        assert legacy not in text, f"legacy command remains: {legacy}"
    assert "\nimport yaml" not in text  # status/next 等保持 stdlib-only（F5）


def test_requirements_free_of_legacy_deps():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for dep in ["langgraph", "surya"]:
        assert dep not in req, f"legacy dependency remains: {dep}"
