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


def test_pymupdf_backend_page_blocks_and_invariant(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz, importlib
    pb = importlib.import_module("source_backends.pymupdf_backend")
    src = tmp_path / "b.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "first page body")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "second page")
    for k in range(20):                       # 让第 2 页判难页（矢量图）
        page2.draw_line(fitz.Point(72, 100 + k * 5), fitz.Point(300, 100 + k * 5))
    doc.save(str(src)); doc.close()
    res = pb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    assert len(res.blocks) == 2
    assert all(b.type == "text" and b.text_level is None for b in res.blocks)
    # char span 不变量：slice 含该页 marker 与 block.text
    for b in res.blocks:
        seg = res.source_md[b.char_start:b.char_end]
        assert f"<!-- page {b.page} -->" in seg
        assert b.text in seg
    # 难页：第 2 页 asset_path 置位 + PNG 生成 + risk_flags
    p2 = next(b for b in res.blocks if b.page == 2)
    assert p2.asset_path == "assets/p0002.png"
    assert (tmp_path / "o" / "assets" / "p0002.png").exists()
    assert p2.risk_flags                       # 至少一个 reason
    assert 2 in res.needs_vision_pages
    assert res.report["selected_backend"] == "pymupdf"
    assert res.report["page_count"] == 2 and res.report["block_count"] == 2


def test_convert_emits_blocks_and_parse_report_md(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# Title\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "n"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    # 旧键保留
    assert res["source_md"].endswith("source.md") and res["pages"]
    assert res["chapters_path"].endswith("chapters.json")
    # 新键 + 新文件
    assert (out_dir / "blocks.jsonl").exists()
    assert (out_dir / "parse_report.json").exists()
    assert res["backend"] == "markdown"
    assert len(res["blocks_sha"]) == 64 and len(res["parse_report_sha"]) == 64


def test_converted_input_hash_includes_versions(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("x", encoding="utf-8")
    h = source_convert.converted_input_hash(src)
    import source_profile as _sp
    import source_artifacts as _sa
    assert _sp.PROFILER_VERSION in h and _sa.ARTIFACT_VERSION in h


def test_pymupdf_backend_raises_backendunavailable_when_fitz_missing(tmp_path, monkeypatch):
    # parity（Task 7b）：fitz 缺失时抛 BackendUnavailable，而非裸 ImportError/fitz 错误。
    import importlib
    import importlib.util as u
    import pytest
    pb = importlib.import_module("source_backends.pymupdf_backend")
    from source_backends import BackendUnavailable
    real = u.find_spec
    monkeypatch.setattr(u, "find_spec",
                        lambda name, *a, **k: None if name == "fitz" else real(name, *a, **k))
    src = tmp_path / "x.pdf"
    src.write_text("dummy", encoding="utf-8")
    with pytest.raises(BackendUnavailable):
        pb.convert(src, out_dir=tmp_path / "o", input_hash="h")


def test_e2e_pdf_convert_then_block_windows(tmp_path):
    # 端到端不变量（Task 12）：convert → blocks.jsonl → block windows，页标记一个不丢。
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    import importlib
    windowing = importlib.import_module("windowing")
    import source_artifacts
    src = tmp_path / "e2e.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "some readable body text on this page")
    doc.save(str(src)); doc.close()
    out_dir = tmp_path / "staging" / "e2e"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    blocks = source_artifacts.read_blocks(out_dir / "blocks.jsonl")
    # 每个 PyMuPDF block 的 char slice 含对应 <!-- page N --> marker
    for b in blocks:
        seg = md[b["char_start"]:b["char_end"]]
        assert f"<!-- page {b['page']} -->" in seg
    # block windows 聚合后不丢页标记
    ws = windowing.build_windows_from_blocks(blocks)
    covered = "".join(md[w["char_start"]:w["char_end"]] for w in ws)
    assert covered.count("<!-- page") == 3
    assert res["backend"] == "pymupdf"


# --- Spec 2 C4：dispatcher backend/policy 路由 + cache key ---

def test_select_backend_explicit_pymupdf_does_not_consume_auto():
    assert source_convert.select_backend("pdf", None, backend="pymupdf",
                                         policy="conservative") == ("pymupdf", False)


def test_select_backend_explicit_mineru():
    assert source_convert.select_backend("pdf", None, backend="mineru",
                                         policy="conservative") == ("mineru", False)


def test_select_backend_auto_md_markdown():
    assert source_convert.select_backend("md", None, backend="auto",
                                         policy="conservative") == ("markdown", True)


def test_select_backend_auto_docx_pptx_mineru():
    assert source_convert.select_backend("docx", None, backend="auto", policy="conservative")[0] == "mineru"
    assert source_convert.select_backend("pptx", None, backend="auto", policy="conservative")[0] == "mineru"


def test_select_backend_auto_normal_pdf_pymupdf():
    pages = [{"text_len": 800, "image_count": 0, "needs_vision_reason": []} for _ in range(5)]
    assert source_convert.select_backend("pdf", pages, backend="auto", policy="conservative")[0] == "pymupdf"


def test_select_backend_auto_scanned_pdf_mineru():
    pages = [{"text_len": 0, "image_count": 1, "needs_vision_reason": ["scanned-or-image"]}
             for _ in range(10)]
    assert source_convert.select_backend("pdf", pages, backend="auto", policy="conservative")[0] == "mineru"


def test_select_backend_auto_low_text_pdf_mineru():
    pages = [{"text_len": 20, "image_count": 0, "needs_vision_reason": []} for _ in range(10)]
    assert source_convert.select_backend("pdf", pages, backend="auto", policy="conservative")[0] == "mineru"


def test_select_backend_dense_conservative_pymupdf_aggressive_mineru():
    pages = [{"text_len": 800, "image_count": 0, "needs_vision_reason": ["formula"]} for _ in range(10)]
    assert source_convert.select_backend("pdf", pages, backend="auto", policy="conservative")[0] == "pymupdf"
    assert source_convert.select_backend("pdf", pages, backend="auto", policy="aggressive")[0] == "mineru"


def test_converted_input_hash_varies_by_backend_policy(tmp_path):
    src = tmp_path / "n.md"; src.write_text("x", encoding="utf-8")
    h_auto = source_convert.converted_input_hash(src, backend="auto", policy="conservative")
    h_mineru = source_convert.converted_input_hash(src, backend="mineru", policy="conservative")
    h_aggr = source_convert.converted_input_hash(src, backend="auto", policy="aggressive")
    assert h_auto != h_mineru and h_auto != h_aggr and h_mineru != h_aggr


def test_convert_mineru_required_but_unavailable_fail_closed(tmp_path, monkeypatch):
    import pytest
    import source_backends.mineru_backend as _mb
    monkeypatch.setattr(_mb, "mineru_available", lambda: False)
    src = tmp_path / "s.pdf"; src.write_text("x", encoding="utf-8")
    pages = [{"text_len": 0, "image_count": 1, "needs_vision_reason": ["scanned-or-image"]}
             for _ in range(10)]
    with pytest.raises(source_convert.BackendUnavailable):
        source_convert.convert(src, out_dir=tmp_path / "o", fmt="pdf", backend="auto",
                               mineru_policy="conservative", profile_pages=pages)


def test_convert_mineru_success_marks_consumed_by_auto_router(tmp_path, monkeypatch):
    import source_backends.mineru_backend as _mb
    monkeypatch.setattr(_mb, "mineru_available", lambda: True)
    monkeypatch.setattr(_mb, "_mineru_version", lambda: "x")

    def fake_run(src, raw_dir, *, timeout):
        import json
        from pathlib import Path as _P
        auto = _P(raw_dir) / "x" / "auto"
        auto.mkdir(parents=True, exist_ok=True)
        (auto / "x_content_list.json").write_text(
            json.dumps([{"type": "text", "text": "hi", "page_idx": 0}]), encoding="utf-8")
        return _P(raw_dir)
    monkeypatch.setattr(_mb, "_run_mineru", fake_run)
    src = tmp_path / "d.docx"; src.write_text("x", encoding="utf-8")
    res = source_convert.convert(src, out_dir=tmp_path / "o", fmt="docx", backend="auto",
                                 mineru_policy="conservative")
    assert res["backend"] == "mineru"
    import json
    rep = json.loads((tmp_path / "o" / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["routing_advice"]["consumed_by_auto_router"] is True   # auto 实际据信号路由
    assert rep["routing_advice"]["advisory_only"] is True


def test_convert_explicit_mineru_not_marked_auto_consumed(tmp_path, monkeypatch):
    import source_backends.mineru_backend as _mb
    monkeypatch.setattr(_mb, "mineru_available", lambda: True)
    monkeypatch.setattr(_mb, "_mineru_version", lambda: "x")

    def fake_run(src, raw_dir, *, timeout):
        import json
        from pathlib import Path as _P
        auto = _P(raw_dir) / "x" / "auto"
        auto.mkdir(parents=True, exist_ok=True)
        (auto / "x_content_list.json").write_text(
            json.dumps([{"type": "text", "text": "hi", "page_idx": 0}]), encoding="utf-8")
        return _P(raw_dir)
    monkeypatch.setattr(_mb, "_run_mineru", fake_run)
    src = tmp_path / "p.pdf"; src.write_text("x", encoding="utf-8")
    res = source_convert.convert(src, out_dir=tmp_path / "o", fmt="pdf", backend="mineru")
    import json
    rep = json.loads((tmp_path / "o" / "parse_report.json").read_text(encoding="utf-8"))
    assert res["backend"] == "mineru"
    assert rep["routing_advice"]["consumed_by_auto_router"] is False  # 显式指定，非 auto 消费


def _fake_run_mineru_minimal(src, raw_dir, *, timeout):
    import json
    from pathlib import Path as _P
    auto = _P(raw_dir) / "x" / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    (auto / "x_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "hi", "page_idx": 0}]), encoding="utf-8")
    return _P(raw_dir)


def test_convert_mineru_report_scan_ocr_from_profile(tmp_path, monkeypatch):
    # smoke 暴露的缺口：MinerU 报告的 scan/OCR 应据 profile（扫描件→scan/OCR True；born-digital→False）。
    import json
    import source_backends.mineru_backend as _mb
    monkeypatch.setattr(_mb, "mineru_available", lambda: True)
    monkeypatch.setattr(_mb, "_mineru_version", lambda: "x")
    monkeypatch.setattr(_mb, "_run_mineru", _fake_run_mineru_minimal)
    src = tmp_path / "s.pdf"; src.write_text("x", encoding="utf-8")
    scanned = [{"text_len": 0, "image_count": 1} for _ in range(10)]
    res = source_convert.convert(src, out_dir=tmp_path / "o1", fmt="pdf", backend="mineru",
                                 profile_pages=scanned)
    rep = json.loads((tmp_path / "o1" / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["scan_suspected"] is True and rep["ocr_used"] is True
    born = [{"text_len": 800, "image_count": 0} for _ in range(10)]
    source_convert.convert(src, out_dir=tmp_path / "o2", fmt="pdf", backend="mineru",
                           profile_pages=born)
    rep2 = json.loads((tmp_path / "o2" / "parse_report.json").read_text(encoding="utf-8"))
    assert rep2["scan_suspected"] is False and rep2["ocr_used"] is False


def test_convert_mineru_failure_writes_failed_report_and_raises(tmp_path, monkeypatch):
    import pytest
    import source_backends.mineru_backend as _mb
    monkeypatch.setattr(_mb, "mineru_available", lambda: True)

    def boom(src, raw_dir, *, timeout):
        raise _mb.MineruRunFailed("exited 1")
    monkeypatch.setattr(_mb, "_run_mineru", boom)
    src = tmp_path / "p.pdf"; src.write_text("x", encoding="utf-8")
    with pytest.raises(_mb.MineruRunFailed):
        source_convert.convert(src, out_dir=tmp_path / "o", fmt="pdf", backend="mineru")
    import json
    rep = json.loads((tmp_path / "o" / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["mineru_failed"] is True and rep["mineru_status"] == "failed"


# --- L1 解析层：classify_source（source_type + backend_reason，纯函数，additive） ---

def _native_pdf_pages(n=5):
    return [{"text_len": 800, "image_count": 0, "needs_vision_reason": []} for _ in range(n)]


def test_classify_source_markdown():
    c = source_convert.classify_source("md", [], backend="auto", policy="conservative")
    assert c["source_type"] == "markdown"
    assert "markdown" in c["backend_reason"]


def test_classify_source_docx_pptx_by_fmt():
    cd = source_convert.classify_source("docx", [], backend="auto", policy="conservative")
    cp = source_convert.classify_source("pptx", [], backend="auto", policy="conservative")
    assert cd["source_type"] == "docx" and cp["source_type"] == "pptx"
    # docx/pptx 无 profile_pages（空）仍按 fmt 派生 type
    assert "docx" in cd["backend_reason"] and "pptx" in cp["backend_reason"]


def test_classify_source_native_pdf():
    c = source_convert.classify_source("pdf", _native_pdf_pages(), backend="auto",
                                       policy="conservative")
    assert c["source_type"] == "native_pdf"
    assert c["backend_reason"]


def test_classify_source_scanned_pdf():
    pages = [{"text_len": 0, "image_count": 1, "needs_vision_reason": ["scanned-or-image"]}
             for _ in range(10)]
    c = source_convert.classify_source("pdf", pages, backend="auto", policy="conservative")
    assert c["source_type"] == "scanned_pdf"


def test_classify_source_low_text_pdf():
    # 低文本密度但不算整本扫描件（无 scanned-or-image 信号）→ low_text_pdf
    pages = [{"text_len": 20, "image_count": 0, "needs_vision_reason": []} for _ in range(10)]
    c = source_convert.classify_source("pdf", pages, backend="auto", policy="conservative")
    assert c["source_type"] == "low_text_pdf"


def test_classify_source_mixed_pdf_dense():
    # 文本充足、非扫描、但表/图/公式密集 → mixed_pdf
    pages = [{"text_len": 800, "image_count": 0, "needs_vision_reason": ["formula"]}
             for _ in range(10)]
    c = source_convert.classify_source("pdf", pages, backend="auto", policy="conservative")
    assert c["source_type"] == "mixed_pdf"


def test_classify_source_partial_scan_reason_conservative():
    # 保守策略下 30% 页 scanned-or-image（partial-scan，非整本扫描、文本充足）→ 路由 mineru，
    # backend_reason 必须如实标 partial-scan，不能误标 aggressive（策略其实是 conservative）。
    pages = ([{"text_len": 800, "image_count": 1, "needs_vision_reason": ["scanned-or-image"]}
              for _ in range(3)]
             + [{"text_len": 800, "image_count": 0, "needs_vision_reason": []} for _ in range(7)])
    c = source_convert.classify_source("pdf", pages, backend="auto", policy="conservative")
    assert c["backend_reason"] == "partial-scan pdf→mineru"
    assert "aggressive" not in c["backend_reason"]
    assert c["source_type"] == "native_pdf"      # type 看页面信号，backend 看路由——可不同，均如实


def test_classify_source_empty_pdf_pages_native_default():
    # PDF 但 profile 为空（异常/无页信息）→ 不误判扫描/低文本，保守 native_pdf
    c = source_convert.classify_source("pdf", [], backend="auto", policy="conservative")
    assert c["source_type"] == "native_pdf"


def test_classify_source_explicit_backend_reason():
    # 显式 --backend mineru 的 reason 要体现是显式选择（与 auto 路由区分）
    c = source_convert.classify_source("pdf", _native_pdf_pages(), backend="mineru",
                                       policy="conservative")
    assert "explicit" in c["backend_reason"] or "mineru" in c["backend_reason"]


def test_convert_writes_source_type_and_backend_reason_md(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# Title\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "n"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    import json
    rep = json.loads((out_dir / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["source_type"] == "markdown"
    assert rep["backend_reason"]


def test_convert_writes_source_type_pdf(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    src = tmp_path / "tiny.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Hello PDF body text long enough to be native")
    doc.save(str(src)); doc.close()
    # 先 profile 出 pages 再传给 convert（与 pipeline 一致）
    pages = source_profile.profile_source(src, fmt="pdf")
    out_dir = tmp_path / "staging" / "tiny"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf", profile_pages=pages)
    import json
    rep = json.loads((out_dir / "parse_report.json").read_text(encoding="utf-8"))
    assert rep["source_type"] in ("native_pdf", "low_text_pdf", "mixed_pdf")


# --- L2 结构还原层：block.chapter_id 映射（page→chapter，后端无关） ---

def test_convert_maps_chapter_id_markdown_single_chapter(tmp_path):
    import source_artifacts as _sa
    src = tmp_path / "n.md"
    src.write_text("# A\n\naaa\n\n## B\n\nbbb\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "n"
    source_convert.convert(src, out_dir=out_dir, fmt="md")
    blocks = _sa.read_blocks(out_dir / "blocks.jsonl")
    # markdown 单章 ch00-full（page 1）→ 每个 block 映射到该章
    assert blocks and all(b["chapter_id"] == "ch00-full" for b in blocks)


def test_convert_maps_chapter_id_pdf_by_page_range(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    import source_artifacts as _sa
    src = tmp_path / "book.pdf"
    doc = fitz.open()
    body = ("This is a native born-digital page with enough readable body text "
            "so the source profiles as a native pdf and stays on the pymupdf backend "
            "instead of being routed to mineru as a low-text pdf. ") * 4
    for _ in range(6):
        pg = doc.new_page()
        for k in range(12):
            pg.insert_text((72, 72 + k * 14), body[:90])
    doc.set_toc([[1, "Part I", 1], [2, "导论", 1], [2, "进阶", 4]])
    doc.save(str(src)); doc.close()
    pages = source_profile.profile_source(src, fmt="pdf")
    # 前提：本 fixture 须停在 pymupdf（native pdf）以拿到真 TOC 章节；否则断言无意义
    assert source_convert.select_backend("pdf", pages, backend="auto",
                                         policy="conservative")[0] == "pymupdf"
    out_dir = tmp_path / "staging" / "book"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf", profile_pages=pages)
    blocks = _sa.read_blocks(out_dir / "blocks.jsonl")
    # 建立 chapter_id → 页范围，逐 block 验证落在所标章内
    ch_by_id = {c["chapter_id"]: c for c in res["chapters"]}
    assert blocks
    for b in blocks:
        assert b["chapter_id"] in ch_by_id, f"block {b['block_id']} chapter_id 未知"
        c = ch_by_id[b["chapter_id"]]
        assert c["page_start"] <= b["page"] <= c["page_end"]
    # page 1..3 属"导论"，page 4..6 属"进阶"（按 chapters_from_toc 切分）
    p1 = next(b for b in blocks if b["page"] == 1)
    p5 = next(b for b in blocks if b["page"] == 5)
    assert ch_by_id[p1["chapter_id"]]["title"] == "导论"
    assert ch_by_id[p5["chapter_id"]]["title"] == "进阶"
