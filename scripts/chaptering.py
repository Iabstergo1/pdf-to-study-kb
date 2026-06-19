"""确定性章节派生（Stage 2）：从 PDF 书签目录（`doc.get_toc()`）切出章节单元。纯函数、无 I/O。

章节单元是**确定性、可审计、可重放**的读取/写作单元：`chapter_id` + 页范围（+ 源页可算 hash）。
LLM **不定义**章节单元（避免重蹈已删除的 plan-units / 逐 unit 孤立生成）；LLM 的全书 map 只作
逐章深写的共享上下文。无 TOC 的源退化为整书一章（由上层决定是否回退到窗口读取）。
"""
from __future__ import annotations

import re

# 切分逻辑每次实质改动就 +1，折进章节阶段 input_hash 使缓存失效（与 windowing/profiler 同规）。
CHAPTERING_VERSION = "1"


def _slug(title: str, idx: int) -> str:
    s = re.sub(r"\s+", "-", (title or "").strip())
    s = re.sub(r"[^\w一-鿿-]", "", s)  # 保留字母数字下划线 + CJK + 连字符
    s = s.strip("-").lower()
    return f"ch{idx:02d}-{s[:40]}" if s else f"ch{idx:02d}"


def chapters_from_toc(toc, n_pages: int, *, cut_level: int = 2) -> list[dict]:
    """toc：`[[level, title, page(1-based)], ...]`（PyMuPDF `get_toc()` 格式）。

    在 `level == cut_level` 的条目处切章（默认 L2 = 章；L1=部分、L3+ =小节都不切，
    避免 L1 部分与其首个 L2 章同页时产生退化章 / 破坏连续性）；每章覆盖
    `[page_start, page_end]`，连续无空洞。首个边界晚于第 1 页则前置 front-matter 章；
    无匹配层级（空 TOC 或该层级无条目）→ 整书一章。页码越界自动钳进 `[1, n_pages]`。
    """
    n_pages = max(1, int(n_pages))
    boundaries = [(int(lvl), title, max(1, min(int(pg), n_pages)))
                  for lvl, title, pg in (toc or []) if int(lvl) == cut_level]
    boundaries.sort(key=lambda b: b[2])  # 按起始页稳定排序

    if not boundaries:
        return [{"index": 0, "chapter_id": "ch00-full", "title": "(whole)",
                 "level": 0, "page_start": 1, "page_end": n_pages}]

    chapters: list[dict] = []
    if boundaries[0][2] > 1:
        chapters.append({"index": 0, "chapter_id": "ch00-front-matter", "title": "(front matter)",
                         "level": 0, "page_start": 1, "page_end": boundaries[0][2] - 1})

    for i, (lvl, title, pg) in enumerate(boundaries):
        nxt = boundaries[i + 1][2] if i + 1 < len(boundaries) else n_pages + 1
        idx = len(chapters)
        chapters.append({"index": idx, "chapter_id": _slug(title, idx),
                         "title": (title or "").strip(), "level": lvl,
                         "page_start": pg, "page_end": max(pg, min(nxt - 1, n_pages))})
    return chapters
