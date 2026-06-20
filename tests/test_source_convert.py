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


def test_caption_page_flagged_when_visual_signal_present():
    # 真图题(行首"图 4.2")+ 视觉信号(n_draw)→ 仍触发 caption；caption 不再单凭文本触发
    p = source_profile.profile_page(77, "图 4.2 古诺均衡的反应函数图解\n正文若干。",
                                    image_count=0, n_draw=14, n_tables=0)
    assert p["has_caption"] is True
    assert p["needs_vision"] is True
    assert "caption" in p["needs_vision_reason"]


def test_caption_compound_word_and_prose_not_flagged():
    # p0006 目录 "分析框架地图 57"：地图≠图题（图前接 CJK）且页无视觉信号 → 不触发 caption
    toc = source_profile.profile_page(6, "分析框架地图 57\n博弈论的故事 60\n", image_count=0,
                                      n_draw=0, n_tables=0)
    assert toc["has_caption"] is False
    assert "caption" not in toc["needs_vision_reason"]
    # p0027 普通句 "代表10块钱"：代表≠表题（表前接 CJK）
    prose = source_profile.profile_page(27, "这里的 10 代表10块钱的收益。", image_count=0,
                                        n_draw=0, n_tables=0)
    assert "caption" not in prose["needs_vision_reason"]
    # p0179/p0180 写作示例 "图1/表1"：散文提到图表、页无真实图表 → 不触发
    ex = source_profile.profile_page(179, "撰写论文时，图1 应在正文首次提及处附近。", image_count=0,
                                     n_draw=0, n_tables=0)
    assert "caption" not in ex["needs_vision_reason"]


def test_equation_lines_recover_subthreshold_formula():
    # p0042 类：拍平后残留符号低，但有多行方程（域无关 = 信号）→ 召回 formula-borderline
    text = "对目标函数求一阶导数：\nMR = a - 2*b*q\nMC = c\n令 MR = MC 解得 q* = (a-c)/(2*b)"
    p = source_profile.profile_page(42, text, image_count=0, n_draw=6, n_tables=0)
    assert p["formula_symbols"] < 12
    assert p["needs_vision"] is True
    assert "formula-borderline" in p["needs_vision_reason"] or "formula" in p["needs_vision_reason"]


def test_prose_without_equations_not_flagged():
    # 纯散文（无方程行、无网格、无符号）→ 不召回（防过召回到普通正文）
    p = source_profile.profile_page(3, "这一章讲博弈论的发展简史与人物故事，没有公式或图表。",
                                    image_count=0, n_draw=2, n_tables=0)
    assert p["needs_vision"] is False
    assert p["needs_vision_reason"] == []


def test_numeric_grid_recovers_payoff_matrix():
    # p0164：find_tables 漏（n_tables=0），但数字网格（≥3 行数字为主）→ 召回 table（域无关，不靠"支付矩阵"关键词）
    grid = "策略 合作 背叛\n合作 3,3 0,5\n背叛 5,0 1,1\n"
    p = source_profile.profile_page(164, grid, image_count=0, n_draw=0, n_tables=0)
    assert "table" in p["needs_vision_reason"]


def test_normal_prose_not_numeric_grid():
    p = source_profile.profile_page(3, "正文一段散文，没有任何数字网格或矩阵结构。", image_count=0)
    assert "table" not in p["needs_vision_reason"]


def test_toc_section_numbers_not_numeric_grid():
    # 真书回归暴露的过召回：目录抽取后层级编号(3.3.2)/页码(35)单独成行 + 点导线 → 不得当 table
    toc = source_profile.profile_page(
        6, "3.3.2\n关键一步：收益函数\n35\n3.4.1\n成本的两大组成\n38\n"
           "5.1.1\n古诺(Cournot) 模型 . . . . . . . . . 72\n", image_count=0, n_draw=0)
    assert "table" not in toc["needs_vision_reason"]
    prose = source_profile.profile_page(
        179, "你的文档可以由图1、表1、图2、表2 搭建逻辑框架，在正文首次提及处附近放置。",
        image_count=0, n_draw=8)
    assert "table" not in prose["needs_vision_reason"]


def test_vision_tier_must_nice_none():
    strong = source_profile.profile_page(
        26, "σ_i = (p1, p2)，∑_{j} p_j = 1 且 p_j ≥ 0，α β γ δ ∈ ≤ ≥ ≠ ∇ √ ± ∞",
        image_count=0, n_draw=7, n_tables=0)
    assert strong["needs_vision"] is True
    assert strong["vision_tier"] == "must"
    border = source_profile.profile_page(
        42, "对目标函数求导：\nMR = a - 2*b*q\n令 q* = (a-c)/(2*b)", image_count=0, n_draw=6)
    assert border["vision_tier"] == "nice"
    plain = source_profile.profile_page(3, "只讲研究背景，没有公式、图表或这种结构。", image_count=0, n_draw=2)
    assert plain["needs_vision"] is False
    assert plain["vision_tier"] == "none"


def test_symbolic_matrix_recovered_by_matrix_word_and_structure():
    # p0164 类：符号化支付矩阵（用变量非数字，数字网格抓不到），含"矩阵"通用词 + 结构证据(n_draw≥6) → table
    text = "双方的支付结构可表示为如下矩阵：\n−CA, V−CD\nV, −L\n0, −CD\n0, 0"
    p = source_profile.profile_page(164, text, image_count=0, n_draw=9, n_tables=0)
    assert p["has_matrix_word"] is True
    assert "table" in p["needs_vision_reason"]


def test_matrix_word_without_structure_not_flagged():
    # 纯文本"讨论矩阵思想"无结构证据（无图、公式符号不足）→ 不召回（避免纯文本误报）
    p = source_profile.profile_page(3, "本节讨论矩阵思想在分析中的意义，不含任何表或图。",
                                    image_count=0, n_draw=0, n_tables=0)
    assert p["has_matrix_word"] is True
    assert "table" not in p["needs_vision_reason"]
    assert p["needs_vision"] is False


def test_is_scanned_source_true_for_full_scan():
    pages = [{"text_len": 0, "image_count": 1} for _ in range(100)]
    assert source_profile.is_scanned_source(pages) is True


def test_is_scanned_source_false_for_few_scanned_in_normal_pdf():
    pages = ([{"text_len": 800, "image_count": 0} for _ in range(90)]
             + [{"text_len": 0, "image_count": 1} for _ in range(10)])
    assert source_profile.is_scanned_source(pages) is False


def test_is_scanned_source_false_empty():
    assert source_profile.is_scanned_source([]) is False


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


def test_pdf_chapters_from_toc_emitted(tmp_path):
    # convert 应据 PDF 书签目录产出确定性章节计划 chapters.json
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    src = tmp_path / "book.pdf"
    doc = fitz.open()
    for _ in range(6):
        doc.new_page().insert_text((72, 72), "page text")
    doc.set_toc([[1, "Part I", 1], [2, "导论", 1], [2, "进阶", 4]])
    doc.save(str(src)); doc.close()
    out_dir = tmp_path / "staging" / "book"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    assert (out_dir / "chapters.json").exists()
    titles = [c["title"] for c in res["chapters"]]
    assert "导论" in titles and "进阶" in titles
    assert res["chapters"][-1]["page_end"] == 6
    assert res["chapters_sha"]


def test_md_chapters_whole_book(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# T\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "n"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    assert (out_dir / "chapters.json").exists()
    assert len(res["chapters"]) == 1


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


# --- Spec 1：source_backends 拆分（Task 5/6） ---
import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts"))


def test_markdown_backend_section_blocks(tmp_path):
    import importlib
    mb = importlib.import_module("source_backends.markdown_backend")
    src = tmp_path / "n.md"
    src.write_text("# A\n\naaa\n\n## B\n\nbbb\n", encoding="utf-8")
    res = mb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    # 块为 section 级，heading 块带 text_level/heading_path，text 含整段
    headings = [b for b in res.blocks if b.type == "heading"]
    assert any(b.heading_path == "A" and b.text_level == 1 for b in headings)
    a_block = next(b for b in res.blocks if b.heading_path == "A")
    assert "aaa" in a_block.text                     # 正文未被丢
    assert res.source_md[a_block.char_start:a_block.char_end] == a_block.text  # 逐字一致
    assert res.report["selected_backend"] == "markdown"
    assert res.report["routing_advice"]["recommended_backend"] == "markdown"
    assert res.report["section_count"] >= 2
    assert res.needs_vision_pages == []
