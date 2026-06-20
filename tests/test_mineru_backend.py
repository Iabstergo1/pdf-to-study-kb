from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_backends
from source_backends import mineru_backend as mb
from source_backends import BackendUnavailable


def test_mineru_convert_fail_closed_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mineru_available", lambda: False)
    src = tmp_path / "x.pdf"
    src.write_text("dummy", encoding="utf-8")
    with pytest.raises(BackendUnavailable) as ei:
        mb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    assert "requirements-mineru" in str(ei.value)


def test_get_backend_by_name_mineru():
    assert source_backends.get_backend_by_name("mineru") is mb


def _fake_content_list():
    return [
        {"type": "text", "text": "Chapter 1", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "intro paragraph", "page_idx": 0},
        {"type": "header", "text": "running header", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>a</td></tr></table>", "page_idx": 1},
        {"type": "equation", "text": "E=mc^2", "text_format": "latex", "page_idx": 1},
        {"type": "image", "img_path": "images/fig1.jpg", "img_caption": ["Figure 1"], "page_idx": 2},
        {"type": "footer", "text": "page 3", "page_idx": 2},
    ]


def test_normalize_content_list_types_and_discard(tmp_path):
    assets_src = tmp_path / "raw_images"
    assets_src.mkdir()
    (assets_src / "fig1.jpg").write_bytes(b"\xff\xd8fakejpg")
    blocks, discarded = mb.normalize_content_list(
        _fake_content_list(), assets_src_dir=assets_src, assets_out_dir=tmp_path / "o" / "assets")
    assert discarded == 2                       # header + footer 丢弃并计数
    assert [b.type for b in blocks] == ["heading", "text", "table", "equation", "image"]
    # page 统一 1-based
    assert blocks[0].page == 1 and blocks[3].page == 2 and blocks[4].page == 3
    assert blocks[0].source_ref == f"p0001#{blocks[0].block_id}"
    # 风险标记
    assert blocks[2].risk_flags == ["table"]
    assert blocks[3].risk_flags == ["equation"]
    assert blocks[4].risk_flags == ["image"]
    # 图片 asset 复制进 staging assets（相对路径）
    assert blocks[4].asset_path == "assets/fig1.jpg"
    assert (tmp_path / "o" / "assets" / "fig1.jpg").exists()
    # heading 带 text_level/heading_path，正文继承 heading_path（同段，避免标题与正文分裂）
    assert blocks[0].text_level == 1 and blocks[0].heading_path == "Chapter 1"
    assert blocks[1].heading_path == "Chapter 1"
    # header/footer 不进正文块
    assert all("header" not in (b.text or "") for b in blocks)


def test_normalize_handles_chart_as_image(tmp_path):
    # MinerU 3.4.0 真实存在 type='chart'（ContentType.CHART）；归一为 image（带图 asset + risk）。
    assets_src = tmp_path / "raw"
    (assets_src / "images").mkdir(parents=True)
    (assets_src / "images" / "c1.jpg").write_bytes(b"\xff\xd8jpg")
    items = [{"type": "chart", "img_path": "images/c1.jpg", "chart_caption": ["Chart 1"], "page_idx": 0}]
    blocks, _ = mb.normalize_content_list(items, assets_src_dir=assets_src,
                                          assets_out_dir=tmp_path / "o" / "assets")
    assert blocks[0].type == "image" and blocks[0].risk_flags == ["image"]
    assert blocks[0].asset_path == "assets/c1.jpg"
    assert "Chart 1" in blocks[0].text


def test_render_source_md_assigns_char_spans(tmp_path):
    blocks, _ = mb.normalize_content_list(_fake_content_list(),
                                          assets_src_dir=tmp_path, assets_out_dir=tmp_path / "a")
    md = mb.render_source_md(blocks)
    for b in blocks:
        seg = md[b.char_start:b.char_end]
        assert f"block:{b.block_id}" in seg     # 块注释在切片内
    assert "".join(md[b.char_start:b.char_end] for b in blocks) == md   # 连续覆盖


def test_build_mineru_report_counts(tmp_path):
    blocks, discarded = mb.normalize_content_list(_fake_content_list(),
                                                  assets_src_dir=tmp_path, assets_out_dir=tmp_path / "a")
    rep = mb.build_mineru_report(blocks, input_hash="h", discarded_count=discarded)
    assert rep["selected_backend"] == "mineru"
    assert rep["mineru_status"] == "used" and rep["mineru_backend"] == "pipeline"
    assert rep["block_count"] == 5 and rep["heading_count"] == 1
    assert rep["table_count"] == 1 and rep["equation_count"] == 1 and rep["image_count"] == 1
    assert rep["discarded_count"] == 2
    assert rep["routing_advice"]["advisory_only"] is True
    assert rep["routing_advice"]["consumed_by_auto_router"] is False


class _FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_run_mineru_uses_isolated_python_runner_pipeline(tmp_path, monkeypatch):
    import subprocess
    import sys
    captured = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (captured.__setitem__("cmd", cmd), _FakeProc())[1])
    mb._run_mineru(tmp_path / "x.pdf", tmp_path / "raw", timeout=10)
    cmd = [str(c) for c in captured["cmd"]]
    assert cmd[0] == sys.executable                 # 隔离子进程跑 python（非 mineru CLI、非主进程 do_parse）
    assert cmd[1].endswith("mineru_runner.py")
    assert "--backend" in cmd and cmd[cmd.index("--backend") + 1] == "pipeline"   # 强制 pipeline
    assert not any("vlm" in c for c in cmd)
    assert not any("hybrid" in c for c in cmd)


def test_mineru_runner_calls_do_parse_pipeline(tmp_path):
    from source_backends import mineru_runner
    calls = {}

    def fake_do_parse(out, names, pdfs, langs, backend="pipeline", parse_method="auto"):
        calls.update(out=out, names=names, backend=backend, n=len(pdfs), method=parse_method)
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4 dummy")
    mineru_runner.run(str(src), str(tmp_path / "o"), backend="pipeline", _do_parse=fake_do_parse)
    assert calls["backend"] == "pipeline" and calls["n"] == 1 and calls["names"] == ["x"]


def test_mineru_runner_rejects_non_pipeline(tmp_path):
    from source_backends import mineru_runner
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF dummy")
    with pytest.raises(SystemExit):
        mineru_runner.run(str(src), str(tmp_path / "o"), backend="vlm-engine",
                          _do_parse=lambda *a, **k: None)


def test_mineru_available_and_version_via_metadata(monkeypatch):
    import importlib.metadata as md
    monkeypatch.delenv("MINERU_DISABLE", raising=False)
    monkeypatch.setattr(md, "version", lambda name: "9.9.9")
    assert mb.mineru_available() is True
    assert mb._mineru_version() == "9.9.9"


def test_mineru_disable_env_forces_unavailable(monkeypatch):
    monkeypatch.setenv("MINERU_DISABLE", "1")
    assert mb.mineru_available() is False


def test_run_mineru_nonzero_raises(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _FakeProc(returncode=2, stderr="boom"))
    with pytest.raises(mb.MineruRunFailed):
        mb._run_mineru(tmp_path / "x.pdf", tmp_path / "raw", timeout=10)


def _fake_run_mineru_writes_output(tmp_path):
    import json
    def fake(src, raw_dir, *, timeout):
        auto = Path(raw_dir) / "x" / "auto"
        (auto / "images").mkdir(parents=True, exist_ok=True)
        (auto / "images" / "fig1.jpg").write_bytes(b"\xff\xd8jpg")
        (auto / "x_content_list.json").write_text(json.dumps(_fake_content_list()), encoding="utf-8")
        return Path(raw_dir)
    return fake


def test_convert_success_with_fake_mineru_output(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mineru_available", lambda: True)
    monkeypatch.setattr(mb, "_mineru_version", lambda: "x.y.z")
    monkeypatch.setattr(mb, "_run_mineru", _fake_run_mineru_writes_output(tmp_path))
    res = mb.convert(tmp_path / "x.pdf", out_dir=tmp_path / "o", input_hash="h")
    assert res.report["selected_backend"] == "mineru" and res.report["mineru_status"] == "used"
    assert res.report["mineru_version"] == "x.y.z" and res.report["mineru_backend"] == "pipeline"
    assert [b.type for b in res.blocks] == ["heading", "text", "table", "equation", "image"]
    assert (tmp_path / "o" / "assets" / "fig1.jpg").exists()
    assert "<!-- block:" in res.source_md
    assert res.needs_vision_pages == [2, 3]     # table/equation 在 p2，image 在 p3


def test_convert_propagates_run_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mineru_available", lambda: True)
    def boom(src, raw_dir, *, timeout):
        raise mb.MineruRunFailed("exited 1")
    monkeypatch.setattr(mb, "_run_mineru", boom)
    with pytest.raises(mb.MineruRunFailed):
        mb.convert(tmp_path / "x.pdf", out_dir=tmp_path / "o", input_hash="h")


def test_e2e_mineru_convert_to_block_windows(tmp_path, monkeypatch):
    # C10 端到端（mock MinerU）：docx --backend auto → mineru → artifact → block windows 风险元数据
    import importlib
    import json
    import source_convert
    windowing = importlib.import_module("windowing")
    import source_artifacts
    monkeypatch.setattr(mb, "mineru_available", lambda: True)
    monkeypatch.setattr(mb, "_mineru_version", lambda: "x")
    monkeypatch.setattr(mb, "_run_mineru", _fake_run_mineru_writes_output(tmp_path))
    src = tmp_path / "doc.docx"
    src.write_text("x", encoding="utf-8")
    res = source_convert.convert(src, out_dir=tmp_path / "o", fmt="docx", backend="auto")
    assert res["backend"] == "mineru"
    blocks = source_artifacts.read_blocks(tmp_path / "o" / "blocks.jsonl")
    ws = windowing.build_windows_from_blocks(blocks)
    flags = set()
    for w in ws:
        flags.update(w.get("risk_flags") or [])
    assert {"table", "equation", "image"} <= flags          # 风险类型进窗
    assert any(w.get("assets") for w in ws)                  # 图片 asset 进窗
    rep = json.loads((tmp_path / "o" / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["mineru_status"] == "used" and rep["table_count"] == 1 and rep["image_count"] == 1
    assert rep["routing_advice"]["consumed_by_auto_router"] is True
