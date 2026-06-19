from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


chaptering = _load("chaptering")


def test_empty_toc_yields_single_whole_chapter():
    chs = chaptering.chapters_from_toc([], n_pages=50)
    assert len(chs) == 1
    assert chs[0]["page_start"] == 1 and chs[0]["page_end"] == 50
    assert chs[0]["chapter_id"]  # 非空 id


def test_chapters_cover_all_pages_contiguously():
    toc = [
        [1, "第一部分", 1],
        [2, "导论", 1],
        [2, "核心模型", 10],
        [1, "第二部分", 25],
        [2, "进阶", 25],
    ]
    chs = chaptering.chapters_from_toc(toc, n_pages=40, cut_level=2)
    # 页范围连续、覆盖 1..40、无空洞无重叠
    assert chs[0]["page_start"] == 1
    assert chs[-1]["page_end"] == 40
    for a, b in zip(chs, chs[1:]):
        assert b["page_start"] == a["page_end"] + 1, (a, b)


def test_front_matter_chapter_when_first_boundary_after_page_1():
    toc = [[2, "第一章", 5], [2, "第二章", 12]]
    chs = chaptering.chapters_from_toc(toc, n_pages=20, cut_level=2)
    assert chs[0]["page_start"] == 1 and chs[0]["page_end"] == 4  # front matter
    assert chs[1]["title"] == "第一章" and chs[1]["page_start"] == 5
    assert chs[-1]["page_end"] == 20


def test_cut_level_filters_deeper_entries():
    toc = [
        [1, "部分A", 1],
        [2, "章1", 1],
        [3, "小节1.1", 2],   # 应被 cut_level=2 忽略,不另起章
        [2, "章2", 8],
    ]
    chs = chaptering.chapters_from_toc(toc, n_pages=12, cut_level=2)
    titles = [c["title"] for c in chs]
    assert "小节1.1" not in titles
    # 章1 跨到 章2 前一页(含小节1.1 所在页)
    ch1 = next(c for c in chs if c["title"] == "章1")
    assert ch1["page_start"] == 1 and ch1["page_end"] == 7


def test_chapter_ids_unique_and_stable():
    toc = [[2, "导论", 1], [2, "导论", 10]]  # 同名也要唯一 id
    chs = chaptering.chapters_from_toc(toc, n_pages=20, cut_level=2)
    ids = [c["chapter_id"] for c in chs]
    assert len(ids) == len(set(ids)), ids


def test_page_clamping_against_out_of_range_toc():
    toc = [[2, "章", 999]]  # 越界页码须被钳到 n_pages
    chs = chaptering.chapters_from_toc(toc, n_pages=10, cut_level=2)
    assert all(1 <= c["page_start"] <= 10 and 1 <= c["page_end"] <= 10 for c in chs)
