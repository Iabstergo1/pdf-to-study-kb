"""确定性 processing windows（spec §3.1）：按标题切，超长按 token 滑窗 + overlap。纯函数、无 I/O。

对"任意来源"稳健：真 Markdown 源按 `#` 标题切段；PDF/DOCX 等抽取出的 source.md 含
`<!-- page N -->` 页标记而无真标题——其中的 `#` 多半是**代码注释**（如 Python `# comment`），
绝不能当标题切（否则代码密集书会被切成数百微窗）。故检测到页标记即关闭 `#` 标题分段，
退化为整源 token 滑窗（与无标题源一致），由 overlap + rolling digest 维持跨窗连续性。
"""
from __future__ import annotations

import re

# 窗口算法版本：切分逻辑每次实质改动就 +1，折进 windowed 阶段 input_hash 使缓存失效。
# v2: 页标记存在时关闭 `#` 标题分段（修代码注释被误当标题导致的过度碎片化）。
WINDOWING_VERSION = "3"  # v3: 增 block-aware windows（build_windows_from_blocks）；窗口加 mode 字段（保留 v2 防碎片化）。

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_MARKER = re.compile(r"(?m)^<!-- page \d+ -->\s*$")


def _est_tokens(text: str) -> int:
    # 粗略：~4 字符/token；确定性即可，不依赖外部分词器
    return max(1, len(text) // 4)


def _sections(md: str):
    """切成 (heading_path, char_start, char_end) 段；首个 heading 前的前言归 ""。"""
    lines = md.splitlines(keepends=True)
    secs, cur_path, cur_start, pos = [], "", 0, 0
    for ln in lines:
        m = _HEADING.match(ln.rstrip("\n"))
        if m:
            if pos > cur_start:
                secs.append((cur_path, cur_start, pos))
            cur_path, cur_start = m.group(2).strip(), pos
        pos += len(ln)
    if pos > cur_start:
        secs.append((cur_path, cur_start, pos))
    return secs


_PAGE_NUM = re.compile(r"<!-- page (\d+) -->")


def page_char_ranges(md: str) -> dict:
    """source.md 各 `<!-- page N -->` 页的 char 区间 {page: (start, end)}（含 marker，覆盖整页段）。
    与 pipeline.show-window 的页范围计算同源，是 PyMuPDF page block 的唯一定位真值。"""
    markers = [(int(m.group(1)), m.start()) for m in _PAGE_NUM.finditer(md)]
    ranges = {}
    for i, (page, start) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else len(md)
        ranges[page] = (start, end)
    return ranges


def _slice_section(s: int, e: int, *, target_tokens: int, max_tokens: int,
                   overlap_tokens: int):
    """把一个 section [s,e) 切成 (c0, c1, overlap_before) 子窗；char 与 block 两个构建器共用。
    与旧逻辑逐字等价：≤max 一窗；否则按 token(≈char) 滑窗 + overlap。"""
    if max(1, (e - s) // 4) <= max_tokens:
        yield (s, e, 0)
        return
    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    step = max(1, target_chars - overlap_chars)
    p = s
    while p < e:
        w_end = min(e, p + target_chars)
        yield (p, w_end, overlap_chars if p > s else 0)
        if w_end >= e:
            break
        p += step


def _win(idx: int, path: str, c0: int, c1: int, overlap_before: int, mode: str = "chars") -> dict:
    return {
        "window_id": f"w{idx:04d}",
        "mode": mode,
        "heading_path": path,
        "char_start": c0,
        "char_end": c1,
        "overlap_before": overlap_before,
    }


def build_windows(md: str, *, target_tokens: int = 2000, max_tokens: int = 4000,
                  overlap_tokens: int = 200) -> list[dict]:
    """char 窗（fallback / legacy）。行为与旧版一致，仅窗口新增 mode="chars"。

    PDF 抽取文本（有页标记、`#` 实为代码注释）：不按标题切，整源当一段 token 滑窗。
    """
    out: list[dict] = []
    idx = 0
    sections = [("", 0, len(md))] if _PAGE_MARKER.search(md) else _sections(md)
    for path, s, e in sections:
        for c0, c1, ov in _slice_section(s, e, target_tokens=target_tokens,
                                         max_tokens=max_tokens, overlap_tokens=overlap_tokens):
            out.append(_win(idx, path, c0, c1, ov))
            idx += 1
    return out
