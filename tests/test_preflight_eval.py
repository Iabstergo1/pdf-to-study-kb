"""L4 调用与评测层：preflight_eval 纯函数 + check_*（确定性，零-LLM）。

每个 check_* 用合成 staging（正例 + 违例）测；evaluate 端到端组装 + summary；
pipeline preflight-eval 的 CLI（--strict 退出码/JSON 落盘）在 test_preflight_eval_cli.py 的 subprocess 测。
"""
import importlib.util
import json
from pathlib import Path

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


# ---- check_page_coverage ----

def test_check_page_coverage_ok():
    blocks = [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)]
    c = pe.check_page_coverage(blocks, page_count=2)
    assert c["name"] == "page_coverage" and c["status"] == "ok"


def test_check_page_coverage_missing_page_fails():
    blocks = [_block("b1", 1, 0, 10), _block("b3", 3, 10, 20)]  # page 2 缺
    c = pe.check_page_coverage(blocks, page_count=3)
    assert c["status"] == "fail" and c["severity"] == "high"
    assert "2" in c["detail"]


# ---- check_window_monotonic ----

def test_check_window_monotonic_ok():
    ws = [_window("w0", 0, 100, ["b1"], ps=1, pe_=1),
          _window("w1", 100, 200, ["b2"], ps=2, pe_=2)]
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "ok"


def test_check_window_monotonic_ok_with_overlap():
    # 相邻窗 overlap（后窗起点早于前窗终点）合法
    ws = [_window("w0", 0, 120, ["b1"], ps=1, pe_=1),
          _window("w1", 100, 200, ["b2"], ps=1, pe_=2)]
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "ok"


def test_check_window_monotonic_hole_fails():
    # 窗间有洞（w1 起点 > w0 终点）→ fail
    ws = [_window("w0", 0, 100, ["b1"]), _window("w1", 150, 200, ["b2"])]
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_window_monotonic_page_descending_fails():
    ws = [_window("w0", 0, 100, ["b1"], ps=5, pe_=6),
          _window("w1", 100, 200, ["b2"], ps=2, pe_=3)]  # 跨窗页倒退
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "fail"


def test_check_window_monotonic_empty_block_ids_fails():
    ws = [_window("w0", 0, 100, [])]  # block 窗 block_ids 空
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "fail"


def test_check_window_monotonic_bad_page_range_fails():
    ws = [_window("w0", 0, 100, ["b1"], ps=3, pe_=1)]  # page_start > page_end
    c = pe.check_window_monotonic(ws)
    assert c["status"] == "fail"


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

def test_check_artifact_schema_ok():
    blocks = [_block("b1", 1, 0, 10)]
    ws = [_window("w0", 0, 200, ["b1"])]
    c = pe.check_artifact_schema(_ok_report(), blocks, ws)
    assert c["name"] == "artifact_schema" and c["status"] == "ok"


def test_check_artifact_schema_unknown_source_type_fails():
    c = pe.check_artifact_schema(_ok_report(source_type="unknown"),
                                 [_block("b1", 1, 0, 10)], [_window("w0", 0, 200, ["b1"])])
    assert c["status"] == "fail" and c["severity"] == "high"
    assert "source_type" in c["detail"]


def test_check_artifact_schema_missing_backend_reason_fails():
    rep = _ok_report()
    del rep["backend_reason"]
    c = pe.check_artifact_schema(rep, [_block("b1", 1, 0, 10)], [_window("w0", 0, 200, ["b1"])])
    assert c["status"] == "fail" and "backend_reason" in c["detail"]


def test_check_artifact_schema_missing_block_chapter_id_fails():
    b = _block("b1", 1, 0, 10)
    del b["chapter_id"]
    c = pe.check_artifact_schema(_ok_report(), [b], [_window("w0", 0, 200, ["b1"])])
    assert c["status"] == "fail" and "chapter_id" in c["detail"]


def test_check_artifact_schema_missing_window_l3_fails():
    w = _window("w0", 0, 200, ["b1"])
    del w["source_refs"]                       # block 窗缺 L3 字段
    c = pe.check_artifact_schema(_ok_report(), [_block("b1", 1, 0, 10)], [w])
    assert c["status"] == "fail" and "source_refs" in c["detail"]


def test_check_artifact_schema_char_window_needs_only_minimal():
    # char-fallback 窗只需 window_id/source_id，不因缺块级 L3 字段而 schema-fail（由 window_contract 标降级）。
    c = pe.check_artifact_schema(_ok_report(), [_block("b1", 1, 0, 10)],
                                 [_char_window("w0", 0, 100)])
    assert c["status"] == "ok"


# ---- check_window_contract（char-fallback 显式降级，item 3）----

def test_check_window_contract_ok_all_block_windows():
    c = pe.check_window_contract([_window("w0", 0, 200, ["b1"])])
    assert c["status"] == "ok"


def test_check_window_contract_flags_char_fallback():
    c = pe.check_window_contract([_char_window("w0", 0, 100), _window("w1", 100, 200, ["b1"])])
    assert c["status"] == "warn" and c["severity"] == "warn"
    assert "w0" in c["detail"]


# ---- check_dual_audit（PyMuPDF + MinerU 双审验收契约）----

def test_check_dual_audit_cross_checked_ok():
    c = pe.check_dual_audit(_ok_reconciliation(), _ok_report())
    assert c["name"] == "dual_audit" and c["status"] == "ok"


def test_check_dual_audit_not_applicable_for_markdown():
    c = pe.check_dual_audit({}, _ok_report(source_type="markdown", dual_audit_required=False))
    assert c["status"] == "ok" and c["severity"] == "info"


def test_check_dual_audit_missing_reconciliation_for_pdf_fails():
    # PDF 但没跑 source-audit（无 reconciliation.json）→ high/fail（验收要求双审证据）。
    c = pe.check_dual_audit({}, _ok_report())
    assert c["status"] == "fail" and c["severity"] == "high"
    assert "reconciliation" in c["detail"] or "双审" in c["detail"]


def test_check_dual_audit_degraded_no_review_fails():
    # PyMuPDF-only（MinerU 缺）→ not dual-audited → high/fail（strict 不放行）。
    rec = _ok_reconciliation(review_status="degraded_no_review", dual_audited=False,
                             degraded=True, production_accepted=False, review_backend=None,
                             degraded_reason="mineru unavailable", missing_evidence=["mineru_review"])
    c = pe.check_dual_audit(rec, _ok_report())
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_dual_audit_review_failed_fails():
    rec = _ok_reconciliation(review_status="review_failed", dual_audited=False, degraded=True,
                             production_accepted=False, review_backend=None,
                             degraded_reason="mineru crashed", missing_evidence=["mineru_review"])
    c = pe.check_dual_audit(rec, _ok_report())
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_dual_audit_disagreements_warn():
    rec = _ok_reconciliation(disagreements=[{"page": 2, "kind": "table_presence",
                                             "primary": True, "review": False}])
    c = pe.check_dual_audit(rec, _ok_report())
    assert c["status"] == "warn" and c["severity"] == "warn"


# ---- check_evidence_bundle（双审分歧是否闭环进 LLM 读取窗口；evidence-assembly 核心门）----

def _evidence(candidates):
    return {"pages": {}, "candidates": list(candidates), "initial_needs_vision": [1],
            "reviewer_structural": list(candidates), "final_hard_pages": [1]}


def test_check_evidence_bundle_na_when_no_candidates():
    c = pe.check_evidence_bundle({"candidates": []}, [], [], [], _ok_report())
    assert c["name"] == "evidence_bundle" and c["status"] == "ok" and c["severity"] == "info"


def test_check_evidence_bundle_fails_when_unarbitrated():
    # 候选页 2 无裁决 → 分歧未闭环 → high/fail（阻断整本 ingest）。
    blocks = [_block("b2", 2, 0, 10)]
    ws = [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)]
    c = pe.check_evidence_bundle(_evidence([2]), [], blocks, ws, _ok_report())
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_evidence_bundle_ok_when_render_materialized_in_window():
    blocks = [_block("b2", 2, 0, 10, asset="assets/p0002.png")]
    ws = [_window("w0", 0, 10, ["b2"], ps=2, pe_=2, assets=["assets/p0002.png"])]
    decs = [{"page": 2, "decision": "render", "reason": "flattened fraction"}]
    c = pe.check_evidence_bundle(_evidence([2]), decs, blocks, ws, _ok_report())
    assert c["status"] == "ok" and c["severity"] == "high"


def test_check_evidence_bundle_fails_when_render_not_in_window():
    blocks = [_block("b2", 2, 0, 10, asset="assets/p0002.png")]
    ws = [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)]   # window does not carry the asset
    decs = [{"page": 2, "decision": "render", "reason": "x"}]
    c = pe.check_evidence_bundle(_evidence([2]), decs, blocks, ws, _ok_report())
    assert c["status"] == "fail"


def test_check_evidence_bundle_fails_on_needs_human():
    blocks = [_block("b2", 2, 0, 10)]
    ws = [_window("w0", 0, 10, ["b2"], ps=2, pe_=2)]
    decs = [{"page": 2, "decision": "needs_human", "reason": "ambiguous table/figure"}]
    c = pe.check_evidence_bundle(_evidence([2]), decs, blocks, ws, _ok_report())
    assert c["status"] == "fail"


def test_check_evidence_bundle_na_for_markdown():
    c = pe.check_evidence_bundle(_evidence([2]), [], [], [],
                                 _ok_report(source_type="markdown", dual_audit_required=False))
    assert c["status"] == "ok" and c["severity"] == "info"


# ---- check_risk_coverage（soft 证据风险是否记录进窗口；观测，不阻断）----

def test_check_risk_coverage_ok_when_soft_recorded():
    ev = {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}}
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1)]
    ws[0]["risk_flags"] = ["reading_order_risk"]                  # 已写进窗口
    c = pe.check_risk_coverage(ev, ws, _ok_report())
    assert c["name"] == "risk_coverage" and c["status"] == "ok" and c["severity"] == "info"


def test_check_risk_coverage_warn_when_uncovered():
    ev = {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}}
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1)]            # 默认 risk_flags=[] → 未记录
    c = pe.check_risk_coverage(ev, ws, _ok_report())
    assert c["status"] == "warn" and c["severity"] == "warn"


def test_check_risk_coverage_na_for_markdown():
    ev = {"soft_risk_pages": [1], "risk_flags_by_page": {"1": ["reading_order_risk"]}}
    c = pe.check_risk_coverage(ev, [], _ok_report(source_type="markdown", dual_audit_required=False))
    assert c["status"] == "ok" and c["severity"] == "info"


def test_check_risk_coverage_na_when_no_soft_risk():
    c = pe.check_risk_coverage({"soft_risk_pages": []}, [], _ok_report())
    assert c["status"] == "ok" and c["severity"] == "info"


def test_check_risk_coverage_warn_when_only_some_pages_covered():
    # per-page（非全书）：page1 窗带 flag、page2 窗不带 → page2 未覆盖 → warn。
    ev = {"soft_risk_pages": [1, 2],
          "risk_flags_by_page": {"1": ["reading_order_risk"], "2": ["reading_order_risk"]}}
    w1 = _window("w0", 0, 100, ["b1"], ps=1, pe_=1)
    w1["risk_flags"] = ["reading_order_risk"]
    w2 = _window("w1", 100, 200, ["b2"], ps=2, pe_=2)   # risk_flags=[]（page2 未带）
    c = pe.check_risk_coverage(ev, [w1, w2], _ok_report())
    assert c["status"] == "warn" and "2" in c["detail"]


def test_check_risk_coverage_warn_when_covering_window_missing_one_flag():
    # 该页有两类 soft，覆盖窗只带一类 → warn（另一类未覆盖；要求全部 soft flag 都被覆盖窗携带）。
    ev = {"soft_risk_pages": [1],
          "risk_flags_by_page": {"1": ["heading_structure_risk", "reading_order_risk"]}}
    w1 = _window("w0", 0, 100, ["b1"], ps=1, pe_=1)
    w1["risk_flags"] = ["reading_order_risk"]            # 缺 heading_structure_risk
    c = pe.check_risk_coverage(ev, [w1], _ok_report())
    assert c["status"] == "warn"


def test_check_risk_coverage_ok_when_each_page_window_carries_its_flags():
    # 每页的覆盖窗都带全该页 soft flags → ok。
    ev = {"soft_risk_pages": [1, 2],
          "risk_flags_by_page": {"1": ["reading_order_risk"], "2": ["reading_order_risk"]}}
    w1 = _window("w0", 0, 100, ["b1"], ps=1, pe_=1)
    w1["risk_flags"] = ["reading_order_risk"]
    w2 = _window("w1", 100, 200, ["b2"], ps=2, pe_=2)
    w2["risk_flags"] = ["reading_order_risk"]
    c = pe.check_risk_coverage(ev, [w1, w2], _ok_report())
    assert c["status"] == "ok" and c["severity"] == "info"


# ---- check_risk_signals ----

def test_check_risk_signals_info_when_clean():
    c = pe.check_risk_signals(_ok_report(), low_confidence_pages=[])
    assert c["name"] == "risk_signals" and c["status"] == "ok" and c["severity"] == "info"


def test_check_risk_signals_warn_on_low_confidence():
    c = pe.check_risk_signals(_ok_report(scan_suspected=True, ocr_used=True),
                              low_confidence_pages=[3, 5])
    assert c["status"] == "warn" and c["severity"] == "info"
    assert "scan_suspected" in c["detail"] or "3" in c["detail"]


def test_check_risk_signals_scanned_without_ocr_fails():
    # item 4 硬规则：扫描件/疑似扫描但 ocr_used=False → 内容可能被当文本悄悄丢失 → high/fail。
    c = pe.check_risk_signals(_ok_report(source_type="scanned_pdf", scan_suspected=True,
                                         ocr_used=False), low_confidence_pages=[])
    assert c["status"] == "fail" and c["severity"] == "high"
    assert "ocr_used=False" in c["detail"]


def test_check_risk_signals_scanned_with_ocr_ok():
    # 扫描件正确走了 OCR → 不触发硬规则。
    c = pe.check_risk_signals(_ok_report(source_type="scanned_pdf", scan_suspected=True,
                                         ocr_used=True), low_confidence_pages=[])
    assert c["status"] == "ok"


# ---- check_orphan_blocks ----

def test_check_orphan_blocks_none():
    blocks = [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)]
    ws = [_window("w0", 0, 200, ["b1", "b2"])]
    c = pe.check_orphan_blocks(blocks, ws)
    assert c["status"] == "ok"


def test_check_orphan_blocks_warns_and_counts():
    blocks = [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20), _block("b3", 3, 20, 30)]
    ws = [_window("w0", 0, 200, ["b1"])]  # b2/b3 未进任何窗
    c = pe.check_orphan_blocks(blocks, ws)
    assert c["status"] == "warn" and c["severity"] == "warn"
    assert "2" in c["detail"]  # 2 个孤儿


# ---- check_source_ref_integrity ----

def test_check_source_ref_integrity_ok():
    blocks = [_block("b1", 1, 0, 10), _block("b2", 2, 10, 20)]
    ws = [_window("w0", 0, 200, ["b1", "b2"],
                  refs=["p0001#b1", "p0002#b2"])]
    c = pe.check_source_ref_integrity(blocks, ws)
    assert c["status"] == "ok"


def test_check_source_ref_integrity_bad_block_ref_fails():
    bad = _block("b1", 1, 0, 10)
    bad["source_ref"] = "WRONG"
    c = pe.check_source_ref_integrity([bad], [_window("w0", 0, 200, ["b1"], refs=["WRONG"])])
    assert c["status"] == "fail" and c["severity"] == "high"


def test_check_source_ref_integrity_empty_ref_fails():
    bad = _block("b1", 1, 0, 10)
    bad["source_ref"] = ""
    c = pe.check_source_ref_integrity([bad], [_window("w0", 0, 200, ["b1"], refs=[""])])
    assert c["status"] == "fail"


def test_check_source_ref_integrity_window_missing_ref_fails():
    blocks = [_block("b1", 1, 0, 10), _block("b2", 1, 10, 20)]
    # window 覆盖 b1,b2 但 source_refs 只给 b1 的 → 不覆盖
    ws = [_window("w0", 0, 200, ["b1", "b2"], refs=["p0001#b1"])]
    c = pe.check_source_ref_integrity(blocks, ws)
    assert c["status"] == "fail"


# ---- check_detection_distribution（检测分布观测，proposal 2）----

def test_check_detection_distribution_no_pages_ok():
    c = pe.check_detection_distribution([])
    assert c["name"] == "detection_distribution" and c["status"] == "ok" and c["severity"] == "info"


def test_check_detection_distribution_normal_ratio_ok():
    pages = [{"needs_vision": i < 2} for i in range(10)]      # 20% 难页 → 正常 ok
    c = pe.check_detection_distribution(pages)
    assert c["status"] == "ok"


def test_check_detection_distribution_warn_when_over_recall():
    pages = [{"needs_vision": True} for _ in range(10)]       # 100% 难页 → 疑过召回 warn
    c = pe.check_detection_distribution(pages)
    assert c["status"] == "warn" and c["severity"] == "warn" and "过召回" in c["detail"]


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
                     "detection_distribution", "dual_audit", "evidence_bundle", "risk_coverage"}
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


# ---- pipeline preflight-eval CLI（--source / --strict / --json + 退出码语义） ----