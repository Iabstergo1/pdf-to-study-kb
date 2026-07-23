"""L4 调用与评测层：preflight_eval 纯函数 + check_*（确定性，零-LLM）。

每个 check_* 用合成 staging（正例 + 违例）测；evaluate 端到端组装 + summary；
pipeline preflight-eval 的 CLI（--strict 退出码/JSON 落盘）在 test_preflight_eval_cli.py 的 subprocess 测。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preflight_eval", ROOT / "scripts" / "preflight_eval.py")
pe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pe)


# ---- helpers：合成 staging（test_preflight_eval_cli.py 复用）----

def _block(bid, page, cs, ce, *, typ="text", asset=None, rf=None, chapter="ch00-full"):
    return {"block_id": bid, "type": typ, "text": "x", "page": page,
            "char_start": cs, "char_end": ce, "text_level": None, "heading_path": "",
            "asset_path": asset, "risk_flags": rf or [],
            "source_ref": f"p{page:04d}#{bid}", "chapter_id": chapter}


def _window(wid, cs, ce, block_ids, *, ps=1, pe_=1, refs=None, assets=None):
    return {"window_id": wid, "mode": "blocks", "heading_path": "", "char_start": cs,
            "char_end": ce, "overlap_before": 0, "block_ids": block_ids,
            "page_start": ps, "page_end": pe_, "token_estimate": max(1, (ce - cs) // 4),
            "contains": ["text"], "assets": assets or [], "risk_flags": [],
            "source_id": "s", "chapter_title": "", "chapter_ids": ["ch00-full"],
            "source_refs": refs if refs is not None else [f"p{ps:04d}#{b}" for b in block_ids]}


def _write_staging(d, *, blocks, windows, report, pages=None, assets=None, reconciliation=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "blocks.jsonl").write_text(
        "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks), encoding="utf-8")
    (d / "windows.jsonl").write_text(
        "\n".join(json.dumps(w, ensure_ascii=False) for w in windows), encoding="utf-8")
    (d / "parse_report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (d / "source.md").write_text("x" * 200, encoding="utf-8")
    if pages is not None:
        (d / "pages.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pages), encoding="utf-8")
    if reconciliation is not None:
        (d / "reconciliation.json").write_text(
            json.dumps(reconciliation, ensure_ascii=False), encoding="utf-8")
    ad = d / "assets"
    ad.mkdir(exist_ok=True)
    for name in (assets or []):
        (ad / name).write_bytes(b"png")
    return d


def _ok_report(**extra):
    r = {"selected_backend": "pymupdf", "source_type": "native_pdf",
         "backend_reason": "default native pdf→pymupdf", "page_count": 2,
         "scan_suspected": False, "ocr_used": False, "dual_audit_required": True}
    r.update(extra)
    return r


def _ok_reconciliation(**extra):
    # 一份"双审通过"的 reconciliation（native_pdf，PyMuPDF primary + MinerU 复读一致）。
    r = {"generated_by": "source-audit", "source_id": "s", "source_type": "native_pdf",
         "primary_backend": "pymupdf", "review_backend": "mineru",
         "review_status": "cross_checked", "dual_audited": True, "production_accepted": True,
         "degraded": False, "degraded_reason": "", "mineru_status": "used", "input_hash": "h",
         "page_count_primary": 2, "page_count_review": 2, "pages_cross_checked": [1, 2],
         "agreements": 2, "disagreements": [], "missing_evidence": []}
    r.update(extra)
    return r


def _char_window(wid, cs, ce):
    # char-fallback 降级窗：只有 source_id，缺 L3 块级字段（page/chapter/source_refs/block_ids）。
    return {"window_id": wid, "mode": "chars", "heading_path": "", "char_start": cs,
            "char_end": ce, "overlap_before": 0, "source_id": "s"}


# ---- 决策表 helpers（parametrize 专用；del-case 构造 + detail 或断言）----

def _report_without(key):
    r = _ok_report(); del r[key]; return r


def _block_without(key):
    b = _block("b1", 1, 0, 10); del b[key]; return b


def _window_without(key):
    w = _window("w0", 0, 200, ["b1"]); del w[key]; return w


def _block_with_ref(ref):
    b = _block("b1", 1, 0, 10); b["source_ref"] = ref; return b


def _win_rf(wid, cs, ce, block_ids, ps, pe_, rf):
    w = _window(wid, cs, ce, block_ids, ps=ps, pe_=pe_); w["risk_flags"] = rf; return w


def _assert_check(c, name, status, severity=None, detail=None):
    # 统一断言：name/status 恒查；severity/detail 给定才查；detail 支持 tuple(任一命中)。
    assert c["name"] == name
    assert c["status"] == status
    if severity is not None:
        assert c["severity"] == severity
    if detail is not None:
        alts = detail if isinstance(detail, tuple) else (detail,)
        assert any(a in c["detail"] for a in alts), f"{c['detail']!r} 不含 {alts} 任一"


# ---- check_page_coverage ----

_PAGE_COVERAGE_CASES = [
    ("ok", [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)], 2, "ok", None, None),
    ("missing_page_2", [_block("b1", 1, 0, 10), _block("b3", 3, 10, 20)], 3, "fail", "high", "2"),
]


@pytest.mark.parametrize("cid,blocks,page_count,status,severity,detail", _PAGE_COVERAGE_CASES,
                         ids=[c[0] for c in _PAGE_COVERAGE_CASES])
def test_check_page_coverage(cid, blocks, page_count, status, severity, detail):
    c = pe.check_page_coverage(blocks, page_count=page_count)
    _assert_check(c, "page_coverage", status, severity, detail)


# ---- check_window_monotonic ----

_MONOTONIC_CASES = [
    ("ok_sequential", [_window("w0", 0, 100, ["b1"], ps=1, pe_=1),
                       _window("w1", 100, 200, ["b2"], ps=2, pe_=2)], "ok", None),
    ("ok_with_overlap", [_window("w0", 0, 120, ["b1"], ps=1, pe_=1),
                         _window("w1", 100, 200, ["b2"], ps=1, pe_=2)], "ok", None),
    ("hole_between_windows", [_window("w0", 0, 100, ["b1"]),
                              _window("w1", 150, 200, ["b2"])], "fail", "high"),
    ("cross_window_page_descending", [_window("w0", 0, 100, ["b1"], ps=5, pe_=6),
                                      _window("w1", 100, 200, ["b2"], ps=2, pe_=3)], "fail", None),
    ("empty_block_ids", [_window("w0", 0, 100, [])], "fail", None),
    ("bad_page_range", [_window("w0", 0, 100, ["b1"], ps=3, pe_=1)], "fail", None),
]


@pytest.mark.parametrize("cid,windows,status,severity", _MONOTONIC_CASES,
                         ids=[c[0] for c in _MONOTONIC_CASES])
def test_check_window_monotonic(cid, windows, status, severity):
    c = pe.check_window_monotonic(windows)
    _assert_check(c, "window_monotonic", status, severity)


# ---- check_asset_traceability ----

def test_check_asset_traceability_ok(tmp_path):
    blocks = [_block("b1", 1, 0, 10, typ="image", asset="assets/p0001.png")]
    ws = [_window("w0", 0, 200, ["b1"], assets=["assets/p0001.png"])]
    d = _write_staging(tmp_path / "s", blocks=blocks, windows=ws, report=_ok_report(),
                       assets=["p0001.png"])
    c = pe.check_asset_traceability(d, blocks, ws)
    assert c["status"] == "ok"


def test_check_asset_traceability_missing_block_asset_fails(tmp_path):
    blocks = [_block("b1", 1, 0, 10, typ="image", asset="assets/missing.png")]
    ws = [_window("w0", 0, 200, ["b1"])]
    d = _write_staging(tmp_path / "s", blocks=blocks, windows=ws, report=_ok_report())
    c = pe.check_asset_traceability(d, blocks, ws)
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_asset_traceability_missing_window_asset_fails(tmp_path):
    blocks = [_block("b1", 1, 0, 10)]
    ws = [_window("w0", 0, 200, ["b1"], assets=["assets/ghost.png"])]
    d = _write_staging(tmp_path / "s", blocks=blocks, windows=ws, report=_ok_report())
    c = pe.check_asset_traceability(d, blocks, ws)
    assert c["status"] == "fail"


def test_check_asset_traceability_image_without_asset_fails(tmp_path):
    # 视觉块（image/chart）无 asset_path：内容即图，缺图=丢内容 → high/fail（修死逻辑）。
    blocks = [_block("b1", 1, 0, 10, typ="image", asset=None)]
    ws = [_window("w0", 0, 200, ["b1"])]
    d = _write_staging(tmp_path / "s", blocks=blocks, windows=ws, report=_ok_report())
    c = pe.check_asset_traceability(d, blocks, ws)
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_asset_traceability_table_html_without_asset_ok(tmp_path):
    # table 无 asset 但有 HTML 文本 → 可追溯（HTML 兜底）→ ok。
    blocks = [_block("b1", 1, 0, 10, typ="table", asset=None)]
    blocks[0]["text"] = "<table><tr><td>a</td></tr></table>"
    ws = [_window("w0", 0, 200, ["b1"])]
    d = _write_staging(tmp_path / "s", blocks=blocks, windows=ws, report=_ok_report())
    c = pe.check_asset_traceability(d, blocks, ws)
    assert c["status"] == "ok"


# ---- check_artifact_schema（四层必备字段契约，item 2）----

_SCHEMA_CASES = [
    ("ok", _ok_report(), [_block("b1", 1, 0, 10)], [_window("w0", 0, 200, ["b1"])],
     "ok", None, None),
    ("unknown_source_type", _ok_report(source_type="unknown"),
     [_block("b1", 1, 0, 10)], [_window("w0", 0, 200, ["b1"])], "fail", "high", "source_type"),
    ("missing_backend_reason", _report_without("backend_reason"),
     [_block("b1", 1, 0, 10)], [_window("w0", 0, 200, ["b1"])], "fail", None, "backend_reason"),
    ("missing_block_chapter_id", _ok_report(),
     [_block_without("chapter_id")], [_window("w0", 0, 200, ["b1"])], "fail", None, "chapter_id"),
    ("missing_window_l3_source_refs", _ok_report(),
     [_block("b1", 1, 0, 10)], [_window_without("source_refs")], "fail", None, "source_refs"),
    ("char_window_minimal_ok", _ok_report(),
     [_block("b1", 1, 0, 10)], [_char_window("w0", 0, 100)], "ok", None, None),
]


@pytest.mark.parametrize("cid,report,blocks,windows,status,severity,detail", _SCHEMA_CASES,
                         ids=[c[0] for c in _SCHEMA_CASES])
def test_check_artifact_schema(cid, report, blocks, windows, status, severity, detail):
    # char-fallback 窗只需 window_id/source_id，不因缺块级 L3 字段而 schema-fail（由 window_contract 标降级）。
    c = pe.check_artifact_schema(report, blocks, windows)
    _assert_check(c, "artifact_schema", status, severity, detail)


# ---- check_window_contract（char-fallback 显式降级，item 3）----

_WINDOW_CONTRACT_CASES = [
    ("ok_all_block_windows", [_window("w0", 0, 200, ["b1"])], "ok", None, None),
    ("flags_char_fallback", [_char_window("w0", 0, 100), _window("w1", 100, 200, ["b1"])],
     "warn", "warn", "w0"),
]


@pytest.mark.parametrize("cid,windows,status,severity,detail", _WINDOW_CONTRACT_CASES,
                         ids=[c[0] for c in _WINDOW_CONTRACT_CASES])
def test_check_window_contract(cid, windows, status, severity, detail):
    c = pe.check_window_contract(windows)
    _assert_check(c, "window_contract", status, severity, detail)


# ---- check_dual_audit（PyMuPDF + MinerU 双审验收契约）----

_DUAL_AUDIT_CASES = [
    ("cross_checked_ok", _ok_reconciliation(), _ok_report(), "ok", None, None),
    ("na_for_markdown", {}, _ok_report(source_type="markdown", dual_audit_required=False),
     "ok", "info", None),
    ("missing_reconciliation_for_pdf", {}, _ok_report(), "fail", "high", ("reconciliation", "双审")),
    ("degraded_no_review", _ok_reconciliation(
        review_status="degraded_no_review", dual_audited=False, degraded=True,
        production_accepted=False, review_backend=None, degraded_reason="mineru unavailable",
        missing_evidence=["mineru_review"]), _ok_report(), "fail", "high", None),
    ("review_failed", _ok_reconciliation(
        review_status="review_failed", dual_audited=False, degraded=True,
        production_accepted=False, review_backend=None, degraded_reason="mineru crashed",
        missing_evidence=["mineru_review"]), _ok_report(), "fail", "high", None),
    ("disagreements_warn", _ok_reconciliation(disagreements=[
        {"page": 2, "kind": "table_presence", "primary": True, "review": False}]),
     _ok_report(), "warn", "warn", None),
]


@pytest.mark.parametrize("cid,recon,report,status,severity,detail", _DUAL_AUDIT_CASES,
                         ids=[c[0] for c in _DUAL_AUDIT_CASES])
def test_check_dual_audit(cid, recon, report, status, severity, detail):
    c = pe.check_dual_audit(recon, report)
    _assert_check(c, "dual_audit", status, severity, detail)


# ---- check_evidence_bundle（双审分歧是否闭环进 LLM 读取窗口；evidence-assembly 核心门）----

def _evidence(candidates):
    return {"pages": {}, "candidates": list(candidates), "initial_needs_vision": [1],
            "reviewer_structural": list(candidates), "final_hard_pages": [1]}


_EVIDENCE_CASES = [
    ("na_when_no_candidates", {"candidates": []}, [], [], [], _ok_report(), "ok", "info"),
    # 候选页 2 无裁决 → 分歧未闭环 → high/fail（阻断整本 ingest）。
    ("unarbitrated_candidate_fail", _evidence([2]), [], [_block("b2", 2, 0, 10)],
     [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)], _ok_report(), "fail", "high"),
    ("render_materialized_in_window_ok", _evidence([2]),
     [{"page": 2, "decision": "render", "reason": "flattened fraction"}],
     [_block("b2", 2, 0, 10, asset="assets/p0002.png")],
     [_window("w0", 0, 10, ["b2"], ps=2, pe_=2, assets=["assets/p0002.png"])],
     _ok_report(), "ok", "high"),
    ("render_not_in_window_fail", _evidence([2]),
     [{"page": 2, "decision": "render", "reason": "x"}],
     [_block("b2", 2, 0, 10, asset="assets/p0002.png")],
     [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)], _ok_report(), "fail", None),
    ("needs_human_fail", _evidence([2]),
     [{"page": 2, "decision": "needs_human", "reason": "ambiguous table/figure"}],
     [_block("b2", 2, 0, 10)], [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)],
     _ok_report(), "fail", None),
    ("na_for_markdown", _evidence([2]), [], [], [],
     _ok_report(source_type="markdown", dual_audit_required=False), "ok", "info"),
]


@pytest.mark.parametrize("cid,evidence,decisions,blocks,windows,report,status,severity",
                         _EVIDENCE_CASES, ids=[c[0] for c in _EVIDENCE_CASES])
def test_check_evidence_bundle(cid, evidence, decisions, blocks, windows, report, status, severity):
    c = pe.check_evidence_bundle(evidence, decisions, blocks, windows, report)
    _assert_check(c, "evidence_bundle", status, severity)


# ---- check_risk_coverage（soft 证据风险是否记录进窗口；观测，不阻断；per-page 非全书）----

_RISK_COVERAGE_CASES = [
    # 已写进窗口 → ok
    ("soft_recorded_ok",
     {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}},
     [_win_rf("w0", 0, 200, ["b1"], 1, 1, ["reading_order_risk"])], _ok_report(),
     "ok", "info", None),
    # 默认 risk_flags=[] → 未记录 → warn
    ("uncovered_warn",
     {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}},
     [_window("w0", 0, 200, ["b1"], ps=1, pe_=1)], _ok_report(), "warn", "warn", None),
    ("na_for_markdown",
     {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}},
     [], _ok_report(source_type="markdown", dual_audit_required=False), "ok", "info", None),
    ("na_when_no_soft_risk", {"soft_risk_pages": []}, [], _ok_report(), "ok", "info", None),
    # page1 窗带 flag、page2 窗不带 → page2 未覆盖 → warn，detail 提及页 2
    ("some_pages_uncovered_warn",
     {"soft_risk_pages": [1, 2],
      "risk_flags_by_page": {"1": ["reading_order_risk"], "2": ["reading_order_risk"]}},
     [_win_rf("w0", 0, 100, ["b1"], 1, 1, ["reading_order_risk"]),
      _window("w1", 100, 200, ["b2"], ps=2, pe_=2)], _ok_report(), "warn", None, "2"),
    # 该页两类 soft，覆盖窗只带一类 → warn（要求全部 soft flag 都被覆盖窗携带）
    ("covering_window_missing_one_flag_warn",
     {"soft_risk_pages": [1],
      "risk_flags_by_page": {"1": ["heading_structure_risk", "reading_order_risk"]}},
     [_win_rf("w0", 0, 100, ["b1"], 1, 1, ["reading_order_risk"])], _ok_report(), "warn", None, None),
    # 每页的覆盖窗都带全该页 soft flags → ok
    ("each_page_window_carries_flags_ok",
     {"soft_risk_pages": [1, 2],
      "risk_flags_by_page": {"1": ["reading_order_risk"], "2": ["reading_order_risk"]}},
     [_win_rf("w0", 0, 100, ["b1"], 1, 1, ["reading_order_risk"]),
      _win_rf("w1", 100, 200, ["b2"], 2, 2, ["reading_order_risk"])], _ok_report(),
     "ok", "info", None),
]


@pytest.mark.parametrize("cid,evidence,windows,report,status,severity,detail", _RISK_COVERAGE_CASES,
                         ids=[c[0] for c in _RISK_COVERAGE_CASES])
def test_check_risk_coverage(cid, evidence, windows, report, status, severity, detail):
    c = pe.check_risk_coverage(evidence, windows, report)
    _assert_check(c, "risk_coverage", status, severity, detail)


# ---- check_risk_signals ----

_RISK_SIGNALS_CASES = [
    ("clean_info", _ok_report(), [], "ok", "info", None),
    ("low_confidence_warn", _ok_report(scan_suspected=True, ocr_used=True), [3, 5],
     "warn", "info", ("scan_suspected", "3")),
    # item 4 硬规则：扫描件/疑似扫描但 ocr_used=False → 内容可能被当文本悄悄丢失 → high/fail。
    ("scanned_without_ocr_fail",
     _ok_report(source_type="scanned_pdf", scan_suspected=True, ocr_used=False), [],
     "fail", "high", "ocr_used=False"),
    # 扫描件正确走了 OCR → 不触发硬规则。
    ("scanned_with_ocr_ok",
     _ok_report(source_type="scanned_pdf", scan_suspected=True, ocr_used=True), [],
     "ok", None, None),
]


@pytest.mark.parametrize("cid,report,low_confidence_pages,status,severity,detail", _RISK_SIGNALS_CASES,
                         ids=[c[0] for c in _RISK_SIGNALS_CASES])
def test_check_risk_signals(cid, report, low_confidence_pages, status, severity, detail):
    c = pe.check_risk_signals(report, low_confidence_pages=low_confidence_pages)
    _assert_check(c, "risk_signals", status, severity, detail)


# ---- check_orphan_blocks ----

_ORPHAN_CASES = [
    ("none_all_windowed", [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)],
     [_window("w0", 0, 200, ["b1", "b2"])], "ok", None, None),
    # b2/b3 未进任何窗 → warn，detail 计 2 个孤儿
    ("two_orphans_warn",
     [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20), _block("b3", 3, 20, 30)],
     [_window("w0", 0, 200, ["b1"])], "warn", "warn", "2"),
]


@pytest.mark.parametrize("cid,blocks,windows,status,severity,detail", _ORPHAN_CASES,
                         ids=[c[0] for c in _ORPHAN_CASES])
def test_check_orphan_blocks(cid, blocks, windows, status, severity, detail):
    c = pe.check_orphan_blocks(blocks, windows)
    _assert_check(c, "orphan_blocks", status, severity, detail)


# ---- check_source_ref_integrity ----

_SOURCE_REF_CASES = [
    ("ok", [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)],
     [_window("w0", 0, 200, ["b1", "b2"], refs=["p0001#b1", "p0002#b2"])], "ok", None),
    ("bad_block_ref", [_block_with_ref("WRONG")],
     [_window("w0", 0, 200, ["b1"], refs=["WRONG"])], "fail", "high"),
    ("empty_ref", [_block_with_ref("")],
     [_window("w0", 0, 200, ["b1"], refs=[""])], "fail", None),
    # window 覆盖 b1,b2 但 source_refs 只给 b1 的 → 不覆盖
    ("window_missing_ref", [_block("b1", 1, 0, 10), _block("b2", 1, 10, 20)],
     [_window("w0", 0, 200, ["b1", "b2"], refs=["p0001#b1"])], "fail", None),
]


@pytest.mark.parametrize("cid,blocks,windows,status,severity", _SOURCE_REF_CASES,
                         ids=[c[0] for c in _SOURCE_REF_CASES])
def test_check_source_ref_integrity(cid, blocks, windows, status, severity):
    c = pe.check_source_ref_integrity(blocks, windows)
    _assert_check(c, "source_ref_integrity", status, severity)


# ---- check_detection_distribution（检测分布观测，proposal 2）----

_DETECTION_CASES = [
    ("no_pages_ok", [], "ok", "info", None),
    ("normal_ratio_ok", [{"needs_vision": i < 2} for i in range(10)], "ok", None, None),  # 20% 难页
    ("over_recall_warn", [{"needs_vision": True} for _ in range(10)], "warn", "warn", "过召回"),  # 100%
]


@pytest.mark.parametrize("cid,pages,status,severity,detail", _DETECTION_CASES,
                         ids=[c[0] for c in _DETECTION_CASES])
def test_check_detection_distribution(cid, pages, status, severity, detail):
    c = pe.check_detection_distribution(pages)
    _assert_check(c, "detection_distribution", status, severity, detail)


def test_evaluate_includes_detection_distribution_warn(tmp_path):
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    d = _write_staging(tmp_path / "d", blocks=blocks, windows=ws, report=_ok_report(page_count=1),
                       pages=[{"page": i, "needs_vision": True} for i in range(1, 11)],
                       reconciliation=_ok_reconciliation(page_count_primary=1, page_count_review=1,
                                                         pages_cross_checked=[1], agreements=1))
    rep = pe.evaluate(d)
    dd = next(c for c in rep["checks"] if c["name"] == "detection_distribution")
    assert dd["status"] == "warn"
    assert rep["summary"]["warn"] >= 1 and rep["summary"]["fail"] == 0    # 观测 warn 不阻断


# ---- check_content_retention（读 report.content_dropped：归一漏读源正文 = 内容静默丢失）----

def _mineru_report(**extra):
    r = {"selected_backend": "mineru", "source_type": "docx",
         "backend_reason": "docx→mineru primary", "page_count": 1, "content_dropped": 0}
    r.update(extra)
    return r


_CONTENT_RETENTION_CASES = [
    # 归一漏读（content_dropped>0，如列表 list_items 未读）→ high/fail
    ("dropped_one_fail", _mineru_report(content_dropped=1), "fail", "high"),
    ("dropped_many_fail", _mineru_report(content_dropped=3), "fail", "high"),
    # mineru 无漏读 → high/ok
    ("no_drop_ok", _mineru_report(content_dropped=0), "ok", "high"),
    # 非 mineru 报告无 content_dropped 字段 → 不适用 info/ok（真空白页/扫描页不会误伤）
    ("no_field_not_applicable", _ok_report(), "ok", "info"),
]


@pytest.mark.parametrize("cid,report,status,severity", _CONTENT_RETENTION_CASES,
                         ids=[c[0] for c in _CONTENT_RETENTION_CASES])
def test_check_content_retention(cid, report, status, severity):
    c = pe.check_content_retention(report)
    _assert_check(c, "content_retention", status, severity)


def test_evaluate_mineru_content_dropped_fails(tmp_path):
    # mineru 报告 content_dropped>0（归一漏读源正文）→ evaluate 汇总 fail（strict 会阻断）。
    blocks = [_block("b1", 1, 0, 100)]
    ws = [_window("w0", 0, 100, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    d = _write_staging(tmp_path / "mn", blocks=blocks, windows=ws,
                       report=_mineru_report(page_count=1, dual_audit_required=False, content_dropped=1))
    rep = pe.evaluate(d)
    cr = next(c for c in rep["checks"] if c["name"] == "content_retention")
    assert cr["status"] == "fail" and cr["severity"] == "high"
    assert rep["summary"]["fail"] >= 1


def test_evaluate_mineru_blank_page_not_flagged(tmp_path):
    # 真空白页：mineru 产空 text 块但 content_dropped=0 → content_retention 不误伤（扫描/空白页保护）。
    blocks = [{**_block("b1", 1, 0, 100), "text": "正文"}, {**_block("b2", 2, 100, 120), "text": ""}]
    ws = [_window("w0", 0, 120, ["b1", "b2"], ps=1, pe_=2, refs=["p0001#b1", "p0002#b2"])]
    d = _write_staging(tmp_path / "blank", blocks=blocks, windows=ws,
                       report=_mineru_report(page_count=2, dual_audit_required=False, content_dropped=0))
    rep = pe.evaluate(d)
    cr = next(c for c in rep["checks"] if c["name"] == "content_retention")
    assert cr["status"] == "ok"          # 空 text 块存在但 content_dropped=0 → 不判丢失


# ---- evaluate（端到端组装 + summary） ----

def test_evaluate_all_ok(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2,
                  refs=["p0001#b1", "p0002#b2"])]
    d = _write_staging(tmp_path / "good", blocks=blocks, windows=ws,
                       report=_ok_report(), pages=[{"page": 1}, {"page": 2}],
                       reconciliation=_ok_reconciliation())
    rep = pe.evaluate(d)
    assert rep["generated_by"] == "preflight-eval"
    assert rep["source_id"] == "good"
    assert rep["source_type"] == "native_pdf"
    assert rep["selected_backend"] == "pymupdf"
    names = {c["name"] for c in rep["checks"]}
    assert names == {"artifact_schema", "page_coverage", "window_monotonic", "window_contract",
                     "asset_traceability", "risk_signals", "orphan_blocks", "source_ref_integrity",
                     "detection_distribution", "dual_audit", "evidence_bundle", "risk_coverage",
                     "content_retention"}
    assert rep["summary"]["fail"] == 0
    assert rep["summary"]["ok"] >= 9


def test_evaluate_flags_missing_page_as_fail(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b3", 3, 100, 200)]  # page 2 缺
    ws = [_window("w0", 0, 200, ["b1", "b3"], ps=1, pe_=3,
                  refs=["p0001#b1", "p0003#b3"])]
    d = _write_staging(tmp_path / "bad", blocks=blocks, windows=ws,
                       report=_ok_report(page_count=3))
    rep = pe.evaluate(d)
    assert rep["summary"]["fail"] >= 1
    pc = next(c for c in rep["checks"] if c["name"] == "page_coverage")
    assert pc["status"] == "fail"


def test_evaluate_low_confidence_pages_warn(tmp_path):
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    d = _write_staging(tmp_path / "lc", blocks=blocks, windows=ws,
                       report=_ok_report(page_count=1, low_confidence_pages=[1]),
                       reconciliation=_ok_reconciliation(page_count_primary=1, page_count_review=1,
                                                         pages_cross_checked=[1], agreements=1))
    rep = pe.evaluate(d)
    rs = next(c for c in rep["checks"] if c["name"] == "risk_signals")
    assert rs["status"] == "warn"
    assert rep["summary"]["warn"] >= 1


def test_evaluate_includes_dual_audit_ok(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2, refs=["p0001#b1", "p0002#b2"])]
    d = _write_staging(tmp_path / "da", blocks=blocks, windows=ws, report=_ok_report(),
                       reconciliation=_ok_reconciliation())
    rep = pe.evaluate(d)
    da = next(c for c in rep["checks"] if c["name"] == "dual_audit")
    assert da["status"] == "ok"


def test_evaluate_pdf_without_reconciliation_flags_dual_audit_fail(tmp_path):
    # PDF 源跑了预处理但没跑 source-audit（无 reconciliation.json）→ dual_audit high/fail。
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    d = _write_staging(tmp_path / "nd", blocks=blocks, windows=ws, report=_ok_report(page_count=1))
    rep = pe.evaluate(d)
    da = next(c for c in rep["checks"] if c["name"] == "dual_audit")
    assert da["status"] == "fail" and da["severity"] == "high"
    assert rep["summary"]["fail"] >= 1
