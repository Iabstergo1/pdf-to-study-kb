import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _unit(uid, pages, **over):
    u = {
        "unit_id": uid,
        "title": f"标题 {uid}",
        "unit_type": "concept",
        "include": True,
        "source_scope": {"pages": pages},
        "extraction_method": "text",
        "formula_risk": "low",
        "planner_confidence": "high",
        "review_status": "pending",
        "output_targets": ["section-lesson"],
        "summary": f"{uid} 的一句话摘要",
    }
    u.update(over)
    return u


def _make_book(tmp_path, monkeypatch, units, total_pages=4, final=False):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "books" / "demo"
    (root / "config").mkdir(parents=True)
    (root / "input").mkdir()
    (root / "input" / "x.pdf").write_bytes(b"%PDF-1.4 test")
    (root / "config" / "book-profile.yaml").write_text(
        yaml.dump({"book_id": "demo", "title": "演示书"}, allow_unicode=True), encoding="utf-8")
    (root / "config" / "pdf-profile.yaml").write_text(
        yaml.dump({"source_pdf": "x.pdf", "total_pages": total_pages, "pages": []}, allow_unicode=True), encoding="utf-8")
    plan = {"book_id": "demo", "total_pages": total_pages, "units": units}
    (root / "config" / "semantic-unit-plan.candidates.yaml").write_text(
        yaml.dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if final:
        (root / "config" / "semantic-unit-plan.yaml").write_text(
            yaml.dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return root


def test_list_books_and_status_stage(tmp_path, monkeypatch):
    import web_ops
    _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 4])])
    books = web_ops.list_books()
    assert [b["book_id"] for b in books] == ["demo"]
    assert books[0]["stage"] == "planned"  # 有候选、无正式规划
    st = web_ops.book_status("demo")
    assert st["candidates"]["total"] == 1
    assert st["has"]["candidates"] and not st["has"]["final_plan"]


def test_plan_ops_accept_skip_edit_merge(tmp_path, monkeypatch):
    import web_ops
    _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 2]), _unit("U-001-02", [3, 4])])

    # accept
    r = web_ops.apply_plan_op("demo", "accept", "U-001-01")
    assert r["units"][0]["review_status"] == "accepted"
    # edit title
    r = web_ops.apply_plan_op("demo", "edit_title", "U-001-02", {"title": "新标题"})
    assert r["units"][1]["title"] == "新标题" and r["units"][1]["review_status"] == "edited"
    # edit pages
    r = web_ops.apply_plan_op("demo", "edit_pages", "U-001-02", {"pages": "3-4"})
    assert r["units"][1]["pages"] == [3, 4]
    # skip
    r = web_ops.apply_plan_op("demo", "skip", "U-001-01", {"reason": "目录"})
    assert r["units"][0]["include"] is False and r["units"][0]["review_status"] == "skipped"


def test_merge_prev_keeps_page_coverage(tmp_path, monkeypatch):
    import web_ops
    _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 2]), _unit("U-001-02", [3, 4])])
    r = web_ops.apply_plan_op("demo", "merge_prev", "U-001-02")
    # 合并后只剩 1 个 unit，覆盖 1-4（关键回归：区间合并不能丢中间页）
    assert len(r["units"]) == 1
    assert web_ops.unit_plan.expand_pages(r["units"][0]["pages"]) == [1, 2, 3, 4]


def test_auto_resolve_and_finalize(tmp_path, monkeypatch):
    import web_ops
    _make_book(tmp_path, monkeypatch, [
        _unit("U-001-01", [1, 2]),                                   # 纯文字低风险高置信 → 自动接受
        _unit("U-001-02", [3, 4], include=False, skip_reason="目录"),  # include=false+skip_reason → 自动跳过
    ])
    r = web_ops.auto_resolve_candidates("demo")
    assert r["auto_accepted"] == 1 and r["auto_skipped"] == 1
    fin = web_ops.finalize_plan("demo")
    assert fin["ok"] is True
    assert (tmp_path / "books" / "demo" / "config" / "semantic-unit-plan.yaml").exists()


def test_skip_then_accept_restores_finalizable_plan(tmp_path, monkeypatch):
    """回归：先 skip（清空 output_targets）再 accept（重新 include），accept 须复原
    output_targets，否则 finalize 校验报 include=true 缺 section-lesson。"""
    import web_ops
    _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 4])])
    web_ops.apply_plan_op("demo", "skip", "U-001-01", {"reason": "误判"})
    r = web_ops.apply_plan_op("demo", "accept", "U-001-01")
    assert r["units"][0]["include"] is True
    fin = web_ops.finalize_plan("demo")
    assert fin["ok"] is True, fin


def test_finalize_blocks_on_unreviewed(tmp_path, monkeypatch):
    import web_ops
    _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 4], formula_risk="high", extraction_method="hybrid")])
    fin = web_ops.finalize_plan("demo")
    assert fin["ok"] is False and fin["reason"] == "unreviewed"


def test_kb_tree_file_and_escape(tmp_path, monkeypatch):
    import web_ops
    root = _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 4])])
    lessons = root / "study-kb" / "Section-Lessons"
    lessons.mkdir(parents=True)
    (lessons / "U-001-01.md").write_text("# 讲义\n\n正文", encoding="utf-8")
    tree = web_ops.kb_tree("demo")
    sec = next(c for c in tree["categories"] if c["category"] == "Section-Lessons")
    assert sec["files"] == ["U-001-01.md"]
    assert "讲义" in web_ops.kb_file("demo", "Section-Lessons/U-001-01.md")["markdown"]
    import pytest
    with pytest.raises(web_ops.WebError):
        web_ops.kb_file("demo", "../../../config/book-profile.yaml")


def test_review_queue_list_and_draft_roundtrip(tmp_path, monkeypatch):
    import web_ops
    root = _make_book(tmp_path, monkeypatch, [_unit("U-001-01", [1, 4])], final=True)
    (root / "study-kb" / "Review-Queue").mkdir(parents=True)
    (root / "study-kb" / "Review-Queue" / "U-001-01.md").write_text(
        "---\ntype: review-queue\nmanaged_by: pipeline\n---\n\n- reason: evidence_missing\n", encoding="utf-8")
    staging = root / "pipeline-workspace" / "staging" / "U-001-01"
    staging.mkdir(parents=True)
    (staging / "section-lesson-draft.md").write_text("# 草稿\n\n正文 [E-1]", encoding="utf-8")

    items = web_ops.list_review_queue("demo")
    assert items == [{"unit_id": "U-001-01", "reason": "evidence_missing", "has_draft": True}]
    web_ops.save_unit_draft("demo", "U-001-01", "# 改过的草稿\n\n新正文")
    assert "改过" in web_ops.get_unit_draft("demo", "U-001-01")["draft"]


def test_slugify_and_safe_id():
    import web_ops
    assert web_ops.slugify("博弈论 Game Theory!") == "game-theory"
    import pytest
    with pytest.raises(web_ops.WebError):
        web_ops._safe_book_id("../evil")
