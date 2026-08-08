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


def test_findings_open_uses_date_not_list_order(tmp_path):
    """"最新"按 date 判，不按列表末尾——台账按"新条目写在最前"书写同样成立。

    复审实证（prepush-audit-2026-08-08 F5）：实现曾逐条覆盖 last_outcome、取列表末尾，
    `date` 完全不参与；上面那条测试恰好用日期升序 fixture，钉不住。这里用**降序**写同一
    份数据：t.md 08-05 已复审关闭、overview.md 仍未决，答案必须仍是 1。
    """
    _make_vault(tmp_path)
    _write_report(tmp_path, "kb-qa/r1.md")
    _write_report(tmp_path, "kb-qa/r2.md")
    _write_ledger(tmp_path, {
        "topics/t.md": [                                     # 新条目写在最前
            {"date": "2026-08-05", "report": "kb-qa/r2.md", "outcome": "findings-reworked"},
            {"date": "2026-08-03", "report": "kb-qa/r1.md", "outcome": "findings-open"},
        ],
        "overview.md": [
            {"date": "2026-08-03", "report": "kb-qa/r1.md", "outcome": "findings-open"},
        ],
    })
    result = _run(["review-coverage"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "findings-open 的页: 1" in result.stdout


def test_content_page_traversal_has_a_single_implementation(tmp_path):
    """`pipeline.review_content_pages` 与 `legacy_revision._content_page_paths` 必须逐项相等。

    两处曾各写各的、docstring 都声称"同一口径"，实际分叉了 log.md 与 generated 两条规则
    （prepush-audit-2026-08-08 F4）。分叉的后果是"该审的页"（覆盖率分母）与"能改的页"
    （`_owned_pages` 的修订射程）不是同一集合。这里连同两条分歧样本一起钉死。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    import pipeline

    wiki = _make_vault(tmp_path)
    # 两条历史分歧样本：域内 log.md 应计入；含 "generated" 但非 .generated.md 后缀应计入。
    for rel in ("domains/statistics/log.md", "topics/generated-notes.md",
                "topics/real.generated.md"):
        page = wiki / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("---\ntype: topic\n---\n正文\n", encoding="utf-8")

    via_pipeline = pipeline.review_content_pages(tmp_path)
    via_legacy = legacy_revision._content_page_paths(wiki)
    assert via_pipeline == via_legacy, (sorted(set(via_pipeline) ^ set(via_legacy)))
    assert "domains/statistics/log.md" in via_pipeline, "域内 log.md 不该被当成顶层台账排除"
    assert "topics/generated-notes.md" in via_pipeline, "子串匹配会静默吞掉正当内容页"
    assert "topics/real.generated.md" not in via_pipeline, "*.generated.md 是派生层，应排除"
    assert "log.md" not in via_pipeline, "顶层 log.md 是 vault 级台账，应排除"
