"""Architecture-contract guards (see CLAUDE.md / AGENTS.md).

Two halves:
1. Deprecated *orchestration / hard-pipeline* designs stay removed — they must never come back:
   LangGraph worker, plan-units, run-book / init-book flow, dual business SQLite, and the old
   Surya hard-OCR pipeline (replaced by MinerU as a *structural backend*).
2. The current required architecture is present: PyMuPDF extraction + MinerU structural review
   (dual-audit), reconciliation artifact, and the strict preflight gate.

NOTE: this is NOT an "anti-OCR / anti-structured-parsing" guard. Structured parsing via MinerU is
now a REQUIRED part of PDF acceptance (see source_audit / source-audit / check_dual_audit). We ban
the deprecated *orchestration*, not the presence of OCR/structured parsing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Removed scripts that encoded deprecated orchestration / hard-pipeline designs. The Surya pair
# (ocr_surya.py / surya_smoke.py) is the old *hard* OCR pipeline — deprecated in favour of MinerU
# as a structural backend; banning the scripts bans the design, not structured parsing itself.
LEGACY_SCRIPTS = [
    "langgraph_worker.py", "unit_plan.py", "unit_context.py", "run_book.py",
    "business_db.py", "llm_provider.py", "pdf_profile.py", "ocr_surya.py",
    "surya_smoke.py", "evidence_verifier.py", "review_gate.py",
    "obsidian_indexes.py", "memory_store.py", "cost_guard.py", "web_ops.py", "serve.py",
]


def test_legacy_scripts_gone():
    leftovers = [n for n in LEGACY_SCRIPTS if (ROOT / "scripts" / n).exists()]
    assert leftovers == [], f"deprecated scripts still present: {leftovers}"


def test_legacy_dirs_and_templates_gone():
    assert not (ROOT / "webapp").exists()
    assert not (ROOT / "templates" / "section-lesson.template.md").exists()
    assert not (ROOT / "templates" / "review-report.template.md").exists()


def test_pipeline_has_no_legacy_commands_and_no_toplevel_yaml():
    text = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    for legacy in ["plan-units", "run-book", "init-book", "profile-pdf",
                   "validate-unit-plan", "review-unit-plan", "langgraph"]:
        assert legacy not in text, f"deprecated command remains: {legacy}"
    assert "\nimport yaml" not in text  # status/next 等保持 stdlib-only（F5）


def test_single_business_sqlite_only():
    # 旧设计是"双 SQLite"（状态库 + business_db）。现在只有单一状态库（state_store），business_db 已删。
    assert not (ROOT / "scripts" / "business_db.py").exists()
    assert (ROOT / "scripts" / "state_store.py").exists()


def test_requirements_free_of_legacy_orchestration_deps():
    # LangGraph 永不重新引入（CLAUDE.md / AGENTS.md 核心约束：纯确定性 CLI + 状态机，无图编排框架）。
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "langgraph" not in req, "deprecated dependency remains: langgraph"


# ---- 当前必备架构存在（PyMuPDF 抽取 + MinerU structural review 双审）----

def test_dual_audit_architecture_present():
    # MinerU 是 PDF 验收的 REQUIRED structural reviewer（不是被禁的；是双审的一半）。
    assert (ROOT / "scripts" / "source_backends" / "mineru_backend.py").exists()
    assert (ROOT / "scripts" / "source_audit.py").exists(), "缺 dual-audit reviewer 模块 source_audit.py"
    audit_text = (ROOT / "scripts" / "source_audit.py").read_text(encoding="utf-8")
    assert "reconcile" in audit_text and "DualAuditUnavailable" in audit_text


def test_pipeline_exposes_source_audit_command():
    text = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    assert "source-audit" in text and "cmd_source_audit" in text


def test_preflight_enforces_dual_audit():
    text = (ROOT / "scripts" / "preflight_eval.py").read_text(encoding="utf-8")
    assert "check_dual_audit" in text, "preflight 须有 dual-audit 验收检查（strict fail-closed 的依据）"
