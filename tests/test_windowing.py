from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windowing", ROOT / "scripts" / "windowing.py")
windowing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windowing)


def test_splits_by_top_headings():
    md = "# A\n\naaa\n\n# B\n\nbbb\n"
    ws = windowing.build_windows(md, target_tokens=1000, max_tokens=2000, overlap_tokens=0)
    paths = [w["heading_path"] for w in ws]
    assert paths == ["A", "B"]
    assert all(w["window_id"] for w in ws)


def test_oversize_section_subsplit_with_overlap():
    body = "x " * 3000  # ~6000 chars ~1500 tokens
    md = f"# Big\n\n{body}\n"
    ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    assert len(ws) >= 3
    assert all(w["heading_path"] == "Big" for w in ws)
    # overlap：后一窗起点早于前一窗终点
    assert ws[1]["char_start"] < ws[0]["char_end"]


def test_no_heading_fallback_token_slices():
    md = "y " * 2000
    ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=0)
    assert len(ws) >= 2
    assert all(w["heading_path"] == "" for w in ws)


def test_deterministic_same_input_same_output():
    md = "# A\n\n" + ("z " * 1000)
    a = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    b = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    assert a == b


def test_page_marker_disables_heading_split_for_code_comments():
    # 通用回归：PDF 抽取文本（有 <!-- page N --> 页标记）里的 `#` 多为代码注释，
    # 绝不能当 markdown 标题切，否则代码密集书会碎成数百微窗。
    md = ("<!-- page 1 -->\n\n这一节讲解。\n# Example use on a file\ncode_line_1\n"
          "# Find keys in common\ncode_line_2\n\n<!-- page 2 -->\n\n更多正文内容。\n")
    ws = windowing.build_windows(md, target_tokens=1000, max_tokens=2000, overlap_tokens=0)
    assert all(w["heading_path"] == "" for w in ws), "页标记源不得按 # 切段"
    assert len(ws) == 1, f"短文本应整体成一窗而非按代码注释碎片化，实得 {len(ws)}"


def test_window_ids_stable_and_unique():
    md = "# A\n\naaa\n\n# B\n\nbbb\n"
    ws = windowing.build_windows(md)
    ids = [w["window_id"] for w in ws]
    assert len(ids) == len(set(ids))


def test_page_char_ranges_basic():
    md = "<!-- page 1 -->\n\nAAA\n\n<!-- page 2 -->\n\nBBB\n"
    r = windowing.page_char_ranges(md)
    assert set(r.keys()) == {1, 2}
    s1, e1 = r[1]
    assert md[s1:e1].startswith("<!-- page 1 -->")
    assert "AAA" in md[s1:e1]
    s2, e2 = r[2]
    assert e2 == len(md)


def test_build_windows_has_chars_mode():
    md = "# A\n\naaa\n"
    ws = windowing.build_windows(md)
    assert all(w["mode"] == "chars" for w in ws)


def test_windowing_version_bumped():
    assert windowing.WINDOWING_VERSION == "3"


def _md_blocks():
    # 复刻 markdown backend 的 section 块（heading_path = 直接标题）
    return [
        {"block_id": "b000001", "type": "heading", "text": "# A\n\naaa\n",
         "page": 1, "char_start": 0, "char_end": 9, "text_level": 1,
         "heading_path": "A", "asset_path": None, "risk_flags": []},
        {"block_id": "b000002", "type": "heading", "text": "# B\n\nbbb\n",
         "page": 1, "char_start": 9, "char_end": 18, "text_level": 1,
         "heading_path": "B", "asset_path": None, "risk_flags": []},
    ]


def test_block_windows_md_split_by_heading():
    ws = windowing.build_windows_from_blocks(_md_blocks(), target_tokens=1000,
                                             max_tokens=2000, overlap_tokens=0)
    assert [w["heading_path"] for w in ws] == ["A", "B"]
    assert all(w["mode"] == "blocks" for w in ws)
    assert ws[0]["block_ids"] == ["b000001"] and ws[1]["block_ids"] == ["b000002"]


def _pdf_blocks():
    # 两页 PyMuPDF 页块，heading_path 全 ""，第 2 页含公式难页 asset
    md_p1 = "<!-- page 1 -->\n\nintro text\n"
    md = md_p1 + "<!-- page 2 -->\n\nformula page\n"
    return md, [
        {"block_id": "b000001", "type": "text", "text": "intro text",
         "page": 1, "char_start": 0, "char_end": len(md_p1),
         "text_level": None, "heading_path": "", "asset_path": None, "risk_flags": []},
        {"block_id": "b000002", "type": "text", "text": "formula page",
         "page": 2, "char_start": len(md_p1), "char_end": len(md),
         "text_level": None, "heading_path": "", "asset_path": "assets/p0002.png",
         "risk_flags": ["formula"]},
    ]


def test_block_windows_pdf_pages_not_fragmented():
    _md, blocks = _pdf_blocks()
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=1000,
                                             max_tokens=2000, overlap_tokens=0)
    assert len(ws) == 1                       # 短 2 页合并为 1 窗，绝不按页/标题碎片化
    w = ws[0]
    assert w["heading_path"] == "" and w["mode"] == "blocks"
    assert w["page_start"] == 1 and w["page_end"] == 2
    assert w["block_ids"] == ["b000001", "b000002"]
    assert w["assets"] == ["assets/p0002.png"]
    assert w["risk_flags"] == ["formula"]
    assert w["contains"] == ["text"]


def test_block_windows_oversize_block_subsplit():
    big = "z" * 12000  # ~3000 tokens
    blocks = [{"block_id": "b000001", "type": "text", "text": big, "page": 1,
               "char_start": 0, "char_end": len(big), "text_level": None,
               "heading_path": "", "asset_path": None, "risk_flags": []}]
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=300, max_tokens=400,
                                             overlap_tokens=50)
    assert len(ws) >= 2
    assert all(w["mode"] == "blocks" and w["block_ids"] == ["b000001"] for w in ws)
    assert ws[1]["char_start"] < ws[0]["char_end"]   # overlap


def test_block_windows_md_equivalent_to_char_windows():
    # 关键等价性：md 块窗与今天 char 窗在 heading_path / char 区间上一致
    md = "# A\n\n" + ("z " * 1000) + "\n# B\n\nbbb\n"
    sections = windowing._sections(md)
    blocks = []
    for i, (path, s, e) in enumerate(sections):
        first = md[s:e].splitlines()[0] if md[s:e].strip() else ""
        m = windowing._HEADING.match(first)
        blocks.append({"block_id": f"b{i + 1:06d}",
                       "type": "heading" if m else "text", "text": md[s:e],
                       "page": 1, "char_start": s, "char_end": e,
                       "text_level": (len(m.group(1)) if m else None),
                       "heading_path": path, "asset_path": None, "risk_flags": []})
    char_ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    block_ws = windowing.build_windows_from_blocks(blocks, target_tokens=300,
                                                   max_tokens=400, overlap_tokens=50)
    assert [(w["heading_path"], w["char_start"], w["char_end"]) for w in char_ws] == \
           [(w["heading_path"], w["char_start"], w["char_end"]) for w in block_ws]
