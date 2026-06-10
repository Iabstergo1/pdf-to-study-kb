"""确定性 processing windows（spec §3.1）：按标题切，超长按 token 滑窗 + overlap。纯函数、无 I/O。"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


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
    for path, s, e in _sections(md):
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
