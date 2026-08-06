"""review-coverage：内容页口径 + 台账检查（只读；未登记不判失败）。"""
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, workspace):
    env = {**os.environ, "STUDY_KB_ROOT": str(workspace), "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-B", str(PIPELINE), *args], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", env=env)


def _make_vault(tmp_path):
    wiki = tmp_path / "wiki"
    for rel in ("domains/statistics/concepts/a.md", "topics/t.md",
                "synthesis/s.md", "comparisons/c.md", "overview.md"):
        page = wiki / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("---\ntype: concept\nstatus: published\n---\n正文\n",
                        encoding="utf-8")
    for rel in ("Review-Queue/x.md", "sources/y.md", "concepts/_registry.yaml",
                "graph/g.md", "index.generated.md", "log.md"):
        page = wiki / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("x", encoding="utf-8")
    return wiki


def _write_ledger(tmp_path, pages):
    path = tmp_path / "wiki" / "Review-Queue" / "content-review-ledger.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "pages": pages},
                                   allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def _write_report(tmp_path, rel):
    path = tmp_path / "pipeline-workspace" / "reports" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# dummy\n", encoding="utf-8")
    return path


def test_content_page_count_excludes_dirs_and_generated(tmp_path):
    """内容页口径：排除 Review-Queue/sources/concepts/graph、log.md 与 *.generated.*。"""
    _make_vault(tmp_path)
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "内容页 5" in result.stdout
    assert "台账未建立" in result.stdout
    assert "未登记 5" in result.stdout


def test_missing_ledger_is_not_error(tmp_path):
    """台账缺失不是 error：输出"未登记全部"并正常退出（未登记不判失败）。"""
    _make_vault(tmp_path)
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0
    assert "台账未建立" in result.stdout


def test_ledger_ref_to_missing_page_is_error(tmp_path):
    _make_vault(tmp_path)
    _write_ledger(tmp_path, {"nope.md": [{
        "date": "2026-08-06", "report": "kb-qa/dummy.md", "outcome": "no-findings"}]})
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode != 0
    assert "台账条目指向不存在的页: nope.md" in result.stdout + result.stderr


def test_ledger_ref_to_missing_report_is_error(tmp_path):
    _make_vault(tmp_path)
    _write_ledger(tmp_path, {"topics/t.md": [{
        "date": "2026-08-06", "report": "kb-qa/missing.md", "outcome": "no-findings"}]})
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode != 0
    assert "台账 report 指向不存在的报告: topics/t.md: kb-qa/missing.md" in \
        result.stdout + result.stderr


def test_ledger_reports_coverage_and_findings_open(tmp_path):
    _make_vault(tmp_path)
    _write_report(tmp_path, "kb-qa/r.md")
    _write_ledger(tmp_path, {
        "topics/t.md": [{"date": "2026-08-06", "report": "kb-qa/r.md",
                         "outcome": "no-findings"}],
        "overview.md": [{"date": "2026-08-06", "report": "kb-qa/r.md",
                         "outcome": "findings-open"}],
    })
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "内容页 5" in result.stdout
    assert "已登记 2   未登记 3   覆盖率 40.0%" in result.stdout
    assert "findings-open 的页: 1" in result.stdout
    assert "domains/statistics/concepts   1" in result.stdout


def test_unregistered_is_not_failure(tmp_path):
    """未登记页只输出清单，不判失败（不能把覆盖率当门禁）。"""
    _make_vault(tmp_path)
    _write_report(tmp_path, "kb-qa/r.md")
    _write_ledger(tmp_path, {"topics/t.md": [{
        "date": "2026-08-06", "report": "kb-qa/r.md", "outcome": "no-findings"}]})
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0
    assert "未登记 4" in result.stdout
    assert "[FAIL]" not in result.stdout + result.stderr


def test_findings_open_counts_only_latest_entry(tmp_path):
    """findings-open 语义：只算该页最新一条 entry 为 findings-open 的页（复审后关闭的不算）。"""
    _make_vault(tmp_path)
    _write_report(tmp_path, "kb-qa/r1.md")
    _write_report(tmp_path, "kb-qa/r2.md")
    _write_ledger(tmp_path, {
        "topics/t.md": [
            {"date": "2026-08-03", "report": "kb-qa/r1.md", "outcome": "findings-open"},
            {"date": "2026-08-05", "report": "kb-qa/r2.md", "outcome": "findings-reworked"},
        ],
        "overview.md": [
            {"date": "2026-08-03", "report": "kb-qa/r1.md", "outcome": "findings-open"},
        ],
    })
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "findings-open 的页: 1" in result.stdout
