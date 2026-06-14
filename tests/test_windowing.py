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
