"""L4 调用与评测层：preflight_eval 纯函数 + check_*（确定性，零-LLM）。

每个 check_* 用合成 staging（正例 + 违例）测；evaluate 端到端组装 + summary；
pipeline preflight-eval 的 --strict 退出码语义另在 test_p2/p1 CLI 风格的 subprocess 测。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"
spec = importlib.util.spec_from_file_location("preflight_eval", ROOT / "scripts" / "preflight_eval.py")
pe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pe)


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


# ---- helpers：合成 staging ----

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


def _write_staging(d, *, blocks, windows, report, pages=None, assets=None):
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
    ad = d / "assets"
    ad.mkdir(exist_ok=True)
    for name in (assets or []):
        (ad / name).write_bytes(b"png")
    return d


def _ok_report(**extra):
    r = {"selected_backend": "pymupdf", "source_type": "native_pdf",
         "backend_reason": "default native pdf→pymupdf", "page_count": 2,
         "scan_suspected": False, "ocr_used": False}
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


# ---- evaluate（端到端组装 + summary） ----

def test_evaluate_all_ok(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2,
                  refs=["p0001#b1", "p0002#b2"])]
    d = _write_staging(tmp_path / "good", blocks=blocks, windows=ws,
                       report=_ok_report(), pages=[{"page": 1}, {"page": 2}])
    rep = pe.evaluate(d)
    assert rep["generated_by"] == "preflight-eval"
    assert rep["source_id"] == "good"
    assert rep["source_type"] == "native_pdf"
    assert rep["selected_backend"] == "pymupdf"
    names = {c["name"] for c in rep["checks"]}
    assert names == {"artifact_schema", "page_coverage", "window_monotonic", "window_contract",
                     "asset_traceability", "risk_signals", "orphan_blocks", "source_ref_integrity"}
    assert rep["summary"]["fail"] == 0
    assert rep["summary"]["ok"] >= 7


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
                       report=_ok_report(page_count=1, low_confidence_pages=[1]))
    rep = pe.evaluate(d)
    rs = next(c for c in rep["checks"] if c["name"] == "risk_signals")
    assert rs["status"] == "warn"
    assert rep["summary"]["warn"] >= 1


# ---- pipeline preflight-eval CLI（--source / --strict / --json + 退出码语义） ----

def _staging_under_root(root, source_id, **kw):
    d = root / "pipeline-workspace" / "staging" / source_id
    return _write_staging(d, **kw)


def test_cli_preflight_eval_ok_writes_default_json(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2, refs=["p0001#b1", "p0002#b2"])]
    _staging_under_root(tmp_path, "good", blocks=blocks, windows=ws,
                        report=_ok_report(), pages=[{"page": 1}, {"page": 2}])
    r = _run(["preflight-eval", "--source", "good"], tmp_path)
    assert r.returncode == 0, r.stderr
    out_json = tmp_path / "pipeline-workspace" / "staging" / "good" / "preflight_eval.json"
    assert out_json.exists()
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["generated_by"] == "preflight-eval" and rep["summary"]["fail"] == 0


def test_cli_preflight_eval_nonstrict_exits_zero_even_on_fail(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b3", 3, 100, 200)]  # 缺 page 2
    ws = [_window("w0", 0, 200, ["b1", "b3"], ps=1, pe_=3, refs=["p0001#b1", "p0003#b3"])]
    _staging_under_root(tmp_path, "bad", blocks=blocks, windows=ws,
                        report=_ok_report(page_count=3))
    r = _run(["preflight-eval", "--source", "bad"], tmp_path)
    assert r.returncode == 0, r.stderr            # 非 strict → 退出 0（report 标注 fail）
    assert "fail" in r.stdout.lower()


def test_cli_preflight_eval_strict_nonzero_on_fail(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b3", 3, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b3"], ps=1, pe_=3, refs=["p0001#b1", "p0003#b3"])]
    _staging_under_root(tmp_path, "bad", blocks=blocks, windows=ws,
                        report=_ok_report(page_count=3))
    r = _run(["preflight-eval", "--source", "bad", "--strict"], tmp_path)
    assert r.returncode != 0                       # strict + high/fail → 非零退出码（CI 化）


def test_cli_preflight_eval_strict_zero_when_all_ok(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2, refs=["p0001#b1", "p0002#b2"])]
    _staging_under_root(tmp_path, "good", blocks=blocks, windows=ws, report=_ok_report())
    r = _run(["preflight-eval", "--source", "good", "--strict"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_preflight_eval_custom_json_path(tmp_path):
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    _staging_under_root(tmp_path, "g", blocks=blocks, windows=ws, report=_ok_report(page_count=1))
    custom = tmp_path / "out" / "pf.json"
    r = _run(["preflight-eval", "--source", "g", "--json", str(custom)], tmp_path)
    assert r.returncode == 0, r.stderr
    assert custom.exists()


def test_cli_preflight_eval_missing_staging_errors(tmp_path):
    r = _run(["preflight-eval", "--source", "nope"], tmp_path)
    assert r.returncode != 0


def test_cli_preflight_eval_help(tmp_path):
    r = _run(["preflight-eval", "--help"], tmp_path)
    assert r.returncode == 0 and "--strict" in r.stdout and "--source" in r.stdout


def test_cli_preflight_eval_strict_nonzero_on_scanned_without_ocr(tmp_path):
    # 端到端：扫描件但 ocr_used=False（schema/字段齐全）→ strict 非零退出（OCR 契约硬规则）。
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    _staging_under_root(tmp_path, "scn", blocks=blocks, windows=ws,
                        report=_ok_report(page_count=1, source_type="scanned_pdf",
                                          scan_suspected=True, ocr_used=False))
    r = _run(["preflight-eval", "--source", "scn", "--strict"], tmp_path)
    assert r.returncode != 0
