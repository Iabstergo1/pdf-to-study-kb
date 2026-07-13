"""pipeline preflight-eval CLI 集成（subprocess）：--strict 退出码语义 + JSON 落盘。

check_* 纯函数层在 test_preflight_eval.py（本文件拆分自它，复用其合成 staging helper）；
这里只测 CLI wiring：默认/自定义 JSON 输出、strict/非 strict 退出码、缺 staging 报错。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from test_preflight_eval import (_block, _ok_reconciliation, _ok_report, _window,
                                 _write_staging)

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _staging_under_root(root, source_id, **kw):
    d = root / "pipeline-workspace" / "staging" / source_id
    return _write_staging(d, **kw)


def test_cli_preflight_eval_ok_writes_default_json(tmp_path):
    blocks = [_block("b1", 1, 0, 100), _block("b2", 2, 100, 200)]
    ws = [_window("w0", 0, 200, ["b1", "b2"], ps=1, pe_=2, refs=["p0001#b1", "p0002#b2"])]
    _staging_under_root(tmp_path, "good", blocks=blocks, windows=ws,
                        report=_ok_report(), pages=[{"page": 1}, {"page": 2}],
                        reconciliation=_ok_reconciliation())
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
    _staging_under_root(tmp_path, "good", blocks=blocks, windows=ws, report=_ok_report(),
                        reconciliation=_ok_reconciliation())
    r = _run(["preflight-eval", "--source", "good", "--strict"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_preflight_eval_strict_fails_pdf_without_dual_audit(tmp_path):
    # 端到端：PDF 源但缺 reconciliation.json（未跑 source-audit）→ strict fail-closed（双审契约）。
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    _staging_under_root(tmp_path, "nd", blocks=blocks, windows=ws, report=_ok_report(page_count=1))
    r = _run(["preflight-eval", "--source", "nd", "--strict"], tmp_path)
    assert r.returncode != 0


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


def test_cli_preflight_eval_strict_nonzero_on_scanned_without_ocr(tmp_path):
    # 端到端：扫描件但 ocr_used=False（schema/字段齐全）→ strict 非零退出（OCR 契约硬规则）。
    blocks = [_block("b1", 1, 0, 200)]
    ws = [_window("w0", 0, 200, ["b1"], ps=1, pe_=1, refs=["p0001#b1"])]
    _staging_under_root(tmp_path, "scn", blocks=blocks, windows=ws,
                        report=_ok_report(page_count=1, source_type="scanned_pdf",
                                          scan_suspected=True, ocr_used=False))
    r = _run(["preflight-eval", "--source", "scn", "--strict"], tmp_path)
    assert r.returncode != 0
