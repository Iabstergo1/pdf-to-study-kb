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


def test_code_page_not_flagged_as_formula():
    # 通用回归：Python 代码页（REPL >>>、转义 \x \u、变量名 s1/t2、^ 位运算）不得误判为公式页，
    # 否则代码密集书（Python Cookbook）会把成片代码页误渲为 route B 公式 PNG。
    code = (">>> s1 = 'Spicy Jalape\\u00f1o'\n>>> s2 = 'Spicy Jalapen\\u0303o'\n"
            ">>> data = b'\\x00\\x12V'\n>>> int.from_bytes(data, 'little')\n"
            "def f(x1, x2):\n    return x1 ^ x2\n")
    assert source_profile.looks_like_code(code) is True
    n = source_profile.count_formula_symbols(code)
    assert n < 12, f"code page formula score too high: {n}"
    assert source_profile.needs_vision(
        {"text_len": len(code), "formula_symbols": n, "image_count": 0}) is False


def test_real_math_page_still_flagged():
    # 真公式页（希腊字母 / 真减号 − / 上下标 / ∑∫≥≤√∇±）仍须判 needs_vision（不被代码抑制误伤）。
    math = ("一阶条件 π1 = (a−c)q1 − b q1² − b q1 q2，∂π/∂q1 = 0，求得 q* = (a−c)/3b；"
            "∑ x_i ≥ 0，∫ f dx，α β γ δ ≤ ≥ ≠ ∇ √ ± ∞ ∈")
    assert source_profile.looks_like_code(math) is False
    n = source_profile.count_formula_symbols(math)
    assert n >= 12, f"real math score too low: {n}"
    assert source_profile.needs_vision(
        {"text_len": 400, "formula_symbols": n, "image_count": 0}) is True


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


# --- Stage 1 高召回 needs_vision(矢量图/表/标题/边缘公式)+ 可审计 reason ---

def test_vector_figure_flagged_by_drawings():
    # 矢量图页:无公式、无内嵌栅格图,但矢量路径多(反应函数图/流程图)
    page = {"text_len": 800, "formula_symbols": 0, "image_count": 0, "n_draw": 30, "n_tables": 0}
    assert source_profile.needs_vision(page) is True
    assert "vector-figure" in source_profile.needs_vision_reasons(page)


def test_table_page_flagged():
    page = {"text_len": 800, "formula_symbols": 0, "image_count": 0, "n_draw": 0, "n_tables": 1}
    assert source_profile.needs_vision(page) is True
    assert "table" in source_profile.needs_vision_reasons(page)


def test_caption_page_flagged():
    # 图标题命中(get_drawings 漏的小图靠标题兜)
    p = source_profile.profile_page(5, "图 4.1 古诺均衡的反应函数图解\n正文若干。", image_count=0)
    assert p["has_caption"] is True
    assert p["needs_vision"] is True
    assert "caption" in p["needs_vision_reason"]


def test_subthreshold_formula_now_flagged():
    # 旧阈值 12 漏掉的 6-11 区间真公式页,现按 >=6 召回
    page = {"text_len": 800, "formula_symbols": 8, "image_count": 0, "n_draw": 0, "n_tables": 0}
    assert source_profile.needs_vision(page) is True


def test_plain_text_with_header_rules_not_flagged():
    # 纯文字页只有页眉/页脚线(少量 draw)+ 无表无标题 → 不截(治旧面积信号假阳)
    page = {"text_len": 900, "formula_symbols": 1, "image_count": 0, "n_draw": 6, "n_tables": 0}
    assert source_profile.needs_vision(page) is False


def test_reason_recorded_in_profile_page():
    p = source_profile.profile_page(1, "纯散文一段,没有公式。", image_count=0)
    assert p["needs_vision"] is False
    assert p["needs_vision_reason"] == []
    assert {"n_draw", "n_tables", "has_caption", "is_code"} <= set(p.keys())


def test_pdf_vector_drawing_page_rendered(tmp_path):
    # 端到端:含矢量图(无标题词,纯测 n_draw 接线)的 PDF 页应被判难页并渲染 PNG
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    src = tmp_path / "fig.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "示意页正文，无标题词。")
    for k in range(20):
        page.draw_line(fitz.Point(72, 100 + k * 5), fitz.Point(300, 100 + k * 5))
    doc.save(str(src)); doc.close()
    out_dir = tmp_path / "staging" / "fig"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    assert 1 in res["needs_vision_pages"]
    assert (out_dir / "assets" / "p0001.png").exists()
    assert "vector-figure" in res["pages"][0]["needs_vision_reason"]
