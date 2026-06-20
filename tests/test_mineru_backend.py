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
