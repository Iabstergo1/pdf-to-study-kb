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
WINDOWING_VERSION = "2"

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


def build_windows(md: str, *, target_tokens: int = 2000, max_tokens: int = 4000,
                  overlap_tokens: int = 200) -> list[dict]:
    out: list[dict] = []
    idx = 0
    # PDF 抽取文本（有页标记、`#` 实为代码注释）：不按标题切，整源当一段 token 滑窗。
    sections = [("", 0, len(md))] if _PAGE_MARKER.search(md) else _sections(md)
    for path, s, e in sections:
        seg = md[s:e]
        if _est_tokens(seg) <= max_tokens:
            out.append(_win(idx, path, s, e, 0))
            idx += 1
            continue
        # 超长：按 token（≈char）滑窗 + overlap
        target_chars = target_tokens * 4
        overlap_chars = overlap_tokens * 4
        step = max(1, target_chars - overlap_chars)
        p = s
        while p < e:
            w_end = min(e, p + target_chars)
            out.append(_win(idx, path, p, w_end, overlap_chars if p > s else 0))
            idx += 1
            if w_end >= e:
                break
            p += step
    return out


def _win(idx: int, path: str, c0: int, c1: int, overlap_before: int) -> dict:
    return {
        "window_id": f"w{idx:04d}",
        "heading_path": path,
        "char_start": c0,
        "char_end": c1,
        "overlap_before": overlap_before,
    }
