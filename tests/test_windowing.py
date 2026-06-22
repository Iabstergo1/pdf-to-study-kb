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


def test_char_fallback_windows_marked_degraded():
    # char-fallback 窗缺 block-aware 结构 → 显式标 degraded（不当正常成功；dual-audit 契约）。
    md = "y " * 2000
    ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=0)
    assert ws and all(w.get("degraded") is True for w in ws)


def test_block_windows_not_degraded():
    md = "# A\n\naaa\n"
    blocks = [{"block_id": "b1", "type": "heading", "text": md, "page": 1, "char_start": 0,
               "char_end": len(md), "text_level": 1, "heading_path": "A", "asset_path": None,
               "risk_flags": [], "source_ref": "p0001#b1", "chapter_id": "ch00-full"}]
    ws = windowing.build_windows_from_blocks(blocks)
    assert ws and all(w.get("degraded") is False for w in ws)   # block-aware 窗非降级


def test_windowing_version_bumped():
    assert windowing.WINDOWING_VERSION == "5"


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


def _blk(bid, typ, cs, ce, *, page=1, rf=None, eid=""):
    return {"block_id": bid, "type": typ, "text": "x", "page": page, "char_start": cs,
            "char_end": ce, "text_level": None, "heading_path": "S", "asset_path": None,
            "risk_flags": rf or [], "source_ref": f"p{page:04d}#{bid}", "chapter_id": "",
            "element_id": eid}


def test_block_windows_long_table_not_split():
    # C2 长表不切：含原子块的 section，超大表块整块独占一窗（不被 token 预算切到两窗）。
    tlen = 6000
    blocks = [_blk("b000001", "text", 0, 20),
              _blk("b000002", "table", 20, 20 + tlen, rf=["table"], eid="t0001")]
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=300, max_tokens=400,
                                             overlap_tokens=50)
    tab_ws = [w for w in ws if "b000002" in w["block_ids"]]
    assert len(tab_ws) == 1                                  # 表块只在一个窗（未被切开）
    tw = tab_ws[0]
    assert tw["char_start"] <= 20 and tw["char_end"] >= 20 + tlen   # 窗 char 区间完整包住整张表
    assert tw["block_ids"] == ["b000002"]                    # 超大表独占其窗


def test_block_windows_small_table_stays_inline_with_text():
    # 小表 + 文本（section 短）→ 整块打包进同一窗（不因含原子块就碎片化）。
    blocks = [_blk("b000001", "text", 0, 30), _blk("b000002", "table", 30, 60, rf=["table"], eid="t0001"),
              _blk("b000003", "text", 60, 90)]
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=1000, max_tokens=2000)
    assert len(ws) == 1 and ws[0]["block_ids"] == ["b000001", "b000002", "b000003"]


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


def test_attach_block_meta_adds_source_refs_and_chapter_ids():
    blocks = [
        {"block_id": "b000001", "type": "text", "text": "a", "page": 1,
         "char_start": 0, "char_end": 10, "text_level": None, "heading_path": "",
         "asset_path": None, "risk_flags": [], "source_ref": "p0001#b000001",
         "chapter_id": "ch01-intro"},
        {"block_id": "b000002", "type": "text", "text": "b", "page": 2,
         "char_start": 10, "char_end": 20, "text_level": None, "heading_path": "",
         "asset_path": None, "risk_flags": [], "source_ref": "p0002#b000002",
         "chapter_id": "ch02-body"},
    ]
    w = {}
    windowing._attach_block_meta(w, blocks, 0, 20)
    assert w["source_refs"] == ["p0001#b000001", "p0002#b000002"]
    assert w["chapter_ids"] == ["ch01-intro", "ch02-body"]


def test_attach_block_meta_chapter_ids_dedup_sorted_and_skip_empty():
    blocks = [
        {"block_id": "b1", "type": "text", "text": "a", "page": 1, "char_start": 0,
         "char_end": 5, "heading_path": "", "asset_path": None, "risk_flags": [],
         "source_ref": "p0001#b1", "chapter_id": "ch02"},
        {"block_id": "b2", "type": "text", "text": "b", "page": 1, "char_start": 5,
         "char_end": 10, "heading_path": "", "asset_path": None, "risk_flags": [],
         "source_ref": "p0001#b2", "chapter_id": "ch01"},
        {"block_id": "b3", "type": "text", "text": "c", "page": 1, "char_start": 10,
         "char_end": 15, "heading_path": "", "asset_path": None, "risk_flags": [],
         "source_ref": "p0001#b3", "chapter_id": ""},  # 空章 id 不计入
    ]
    w = {}
    windowing._attach_block_meta(w, blocks, 0, 15)
    assert w["chapter_ids"] == ["ch01", "ch02"]   # 去重排序，跳过空


def test_build_windows_from_blocks_sets_source_id_and_chapter_title():
    md_p1 = "<!-- page 1 -->\n\nintro\n"
    md = md_p1 + "<!-- page 2 -->\n\nbody\n"
    blocks = [
        {"block_id": "b000001", "type": "text", "text": "intro", "page": 1,
         "char_start": 0, "char_end": len(md_p1), "heading_path": "", "asset_path": None,
         "risk_flags": [], "source_ref": "p0001#b000001", "chapter_id": "ch01-intro"},
        {"block_id": "b000002", "type": "text", "text": "body", "page": 2,
         "char_start": len(md_p1), "char_end": len(md), "heading_path": "", "asset_path": None,
         "risk_flags": [], "source_ref": "p0002#b000002", "chapter_id": "ch01-intro"},
    ]
    chapters = [{"chapter_id": "ch01-intro", "title": "导论", "page_start": 1, "page_end": 2}]
    ws = windowing.build_windows_from_blocks(blocks, source_id="book", chapters=chapters,
                                             target_tokens=1000, max_tokens=2000, overlap_tokens=0)
    assert len(ws) == 1
    w = ws[0]
    assert w["source_id"] == "book"
    assert w["chapter_title"] == "导论"        # page_start=1 落入 ch01-intro
    assert w["chapter_ids"] == ["ch01-intro"]
    assert w["source_refs"] == ["p0001#b000001", "p0002#b000002"]


def test_build_windows_from_blocks_chapter_title_empty_when_no_match():
    blocks = [
        {"block_id": "b1", "type": "text", "text": "x", "page": 9, "char_start": 0,
         "char_end": 10, "heading_path": "", "asset_path": None, "risk_flags": [],
         "source_ref": "p0009#b1", "chapter_id": ""},
    ]
    chapters = [{"chapter_id": "ch01", "title": "A", "page_start": 1, "page_end": 3}]
    ws = windowing.build_windows_from_blocks(blocks, source_id="s", chapters=chapters,
                                             target_tokens=1000, max_tokens=2000)
    assert ws[0]["chapter_title"] == ""        # page 9 不在任何章 → ""
    assert ws[0]["source_id"] == "s"


def test_build_windows_from_blocks_defaults_no_source_or_chapters():
    # 向后兼容：不传 source_id/chapters → source_id="" + chapter_title=""，仍带 source_refs/chapter_ids
    md = "# A\n\naaa\n"
    blocks = [{"block_id": "b1", "type": "heading", "text": md, "page": 1, "char_start": 0,
               "char_end": len(md), "text_level": 1, "heading_path": "A", "asset_path": None,
               "risk_flags": [], "source_ref": "p0001#b1", "chapter_id": "ch00-full"}]
    ws = windowing.build_windows_from_blocks(blocks)
    assert ws[0]["source_id"] == "" and ws[0]["chapter_title"] == ""
    assert ws[0]["chapter_ids"] == ["ch00-full"]
    assert ws[0]["source_refs"] == ["p0001#b1"]


def test_build_windows_char_fallback_unaffected_by_new_kwargs():
    # char-fallback build_windows 不接 source_id/chapters（那是 block 窗才有）；其形状不变
    md = "# A\n\naaa\n"
    ws = windowing.build_windows(md)
    assert "source_refs" not in ws[0] and "chapter_title" not in ws[0]


def test_block_windows_mineru_table_equation_image_metadata():
    # MinerU 风格细类型块 → window 的 contains/risk_flags/assets 覆盖 table/equation/image
    def blk(bid, typ, cs, ce, rf, asset=None):
        return {"block_id": bid, "type": typ, "text": typ, "page": 1, "char_start": cs,
                "char_end": ce, "text_level": (1 if typ == "heading" else None),
                "heading_path": "T", "asset_path": asset, "risk_flags": rf}
    blocks = [blk("b000001", "heading", 0, 10, []), blk("b000002", "text", 10, 20, []),
              blk("b000003", "table", 20, 30, ["table"]),
              blk("b000004", "equation", 30, 40, ["equation"]),
              blk("b000005", "image", 40, 50, ["image"], asset="assets/fig1.jpg")]
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=1000, max_tokens=2000)
    assert len(ws) == 1
    w = ws[0]
    assert set(w["contains"]) == {"heading", "text", "table", "equation", "image"}
    assert set(w["risk_flags"]) == {"table", "equation", "image"}
    assert w["assets"] == ["assets/fig1.jpg"]
