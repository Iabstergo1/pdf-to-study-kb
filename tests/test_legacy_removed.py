"""Architecture-contract guards (see CLAUDE.md / AGENTS.md).

Negative architecture regression guard: deprecated *orchestration / hard-pipeline* designs stay
removed — LangGraph worker, plan-units, run-book / init-book flow, dual business SQLite, and the
old Surya hard-OCR pipeline (replaced by MinerU as a *structural backend*). Two guards:
1. removed artifacts (scripts / dirs / templates / legacy command dir) never come back;
2. deprecated orchestration commands / deps stay out of pipeline.py + requirements.

NOTE: this is NOT an "anti-OCR / anti-structured-parsing" guard — structured parsing via MinerU is
a REQUIRED part of PDF acceptance. We ban the deprecated *orchestration*, not OCR/structured parsing.
The presence of the current dual-audit architecture is proven by behaviour tests (test_source_audit,
test_mineru_backend, test_conversion_backend_cli source-audit CLI, test_preflight_eval check_dual_audit),
so the old string/module-presence positives were dropped here (§7 dedup).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Removed scripts that encoded deprecated orchestration / hard-pipeline designs. The Surya pair
# (ocr_surya.py / surya_smoke.py) is the old *hard* OCR pipeline — deprecated in favour of MinerU
# as a structural backend; banning the scripts bans the design, not structured parsing itself.
# business_db.py = the old second business SQLite (dual-SQLite design); now single state_store only.
LEGACY_SCRIPTS = [
    "langgraph_worker.py", "unit_plan.py", "unit_context.py", "run_book.py",
    "business_db.py", "llm_provider.py", "pdf_profile.py", "ocr_surya.py",
    "surya_smoke.py", "evidence_verifier.py", "review_gate.py",
    "obsidian_indexes.py", "memory_store.py", "cost_guard.py", "web_ops.py", "serve.py",
]


def test_deprecated_artifacts_stay_removed():
    # 统一 removed-artifacts guard（合并自 legacy_scripts_gone / dirs_and_templates_gone /
    # single_business_sqlite 的 business_db 负半 / command_docs 的 .claude/commands 迁入）。
    leftovers = [n for n in LEGACY_SCRIPTS if (ROOT / "scripts" / n).exists()]
    assert leftovers == [], f"deprecated scripts still present: {leftovers}"
    assert not (ROOT / "webapp").exists()
    assert not (ROOT / "templates" / "section-lesson.template.md").exists()
    assert not (ROOT / "templates" / "review-report.template.md").exists()
    # 命令层已迁到 .claude/skills/；旧 .claude/commands/ 目录不得复活（迁自 test_command_docs.py）。
    assert not (ROOT / ".claude/commands").exists(), "legacy .claude/commands/ should be deleted"


def test_deprecated_orchestration_absent_from_pipeline_and_deps():
    # 统一 forbidden-deps/commands guard（合并自 no_legacy_commands_and_no_toplevel_yaml /
    # requirements_free_of_legacy_orchestration_deps）。LangGraph 永不重新引入（纯确定性 CLI + 状态机）。
    text = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    for legacy in ["plan-units", "run-book", "init-book", "profile-pdf",
                   "validate-unit-plan", "review-unit-plan", "langgraph"]:
        assert legacy not in text, f"deprecated command remains: {legacy}"
    assert "\nimport yaml" not in text  # status/next 等保持 stdlib-only（F5）
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "langgraph" not in req, "deprecated dependency remains: langgraph"
