from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


source_profile = _load("source_profile")


def test_needs_vision_high_formula():
    page = {"text_len": 800, "formula_symbols": 25, "image_count": 0}
    assert source_profile.needs_vision(page) is True


def test_needs_vision_blank_image_page():
    page = {"text_len": 10, "formula_symbols": 0, "image_count": 3}
    assert source_profile.needs_vision(page) is True


def test_plain_text_page_no_vision():
    page = {"text_len": 1500, "formula_symbols": 1, "image_count": 0}
    assert source_profile.needs_vision(page) is False


def test_formula_symbol_count_detects_latex_and_greek():
    n = source_profile.count_formula_symbols(r"设 $\alpha$ 与 $\sum_{i} x_i^2$，则 ∫ f dx ≥ 0")
    assert n >= 4


def test_profile_source_md_single_page(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# T\n\nbody\n", encoding="utf-8")
    pages = source_profile.profile_source(src, fmt="md")
    assert len(pages) == 1 and pages[0]["page"] == 1 and "needs_vision" in pages[0]


source_convert = _load("source_convert")


def test_markdown_passthrough(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Title\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "note"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    assert "# Title" in md
    assert res["source_md"].endswith("source.md")
    assert res["pages"]  # 至少一段 profile


def test_unknown_backend_raises(tmp_path):
    src = tmp_path / "x.xyz"
    src.write_text("z", encoding="utf-8")
    try:
        source_convert.convert(src, out_dir=tmp_path / "o", fmt="xyz")
        assert False, "should raise"
    except source_convert.BackendUnavailable:
        pass


def test_text_pdf_backend_or_skips_when_pymupdf_missing(tmp_path):
    # 有 pymupdf 时跑文本 PDF；没有则适配器报 unavailable（两种都算契约成立）
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest
        pytest.skip("pymupdf not installed")
    # 用 fitz 造一个最小单页 PDF
    import fitz
    src = tmp_path / "tiny.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF body text")
    doc.save(str(src))
    doc.close()
    out_dir = tmp_path / "staging" / "tiny"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    assert "Hello PDF" in md
    assert res["pages"][0]["page"] == 1
