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
WINDOWING_VERSION = "5"  # v5: 含原子块(table/image/chart)的 section 整块打包（长表不切，任何块不切到两窗）。

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_MARKER = re.compile(r"(?m)^<!-- page \d+ -->\s*$")


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
        # char-fallback（mode="chars"）缺 block-aware 结构 → 显式标 degraded（不当正常成功；
        # preflight check_window_contract 据 mode 报降级，此旗标使降级在窗 artifact 上直接可见）。
        "degraded": mode == "chars",
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


def _sections_from_blocks(blocks: list) -> list:
    """把有序 blocks 按「连续同 heading_path」聚成 section (path, char_start, char_end)。
    Markdown 各块 heading_path 互异 → 各自成段（= _sections）；PyMuPDF 全空 → 合并为一段
    （无 heading 块 → 不按页/标题碎片化，保 v2/v5 防碎片化语义）。"""
    secs: list = []
    for b in blocks:
        path = b.get("heading_path", "")
        if secs and secs[-1][0] == path:
            p, s, _e = secs[-1]
            secs[-1] = (p, s, b["char_end"])
        else:
            secs.append((path, b["char_start"], b["char_end"]))
    return secs


def _attach_block_meta(w: dict, blocks: list, c0: int, c1: int) -> None:
    """给窗口回挂块元数据：与窗 char 区间 [c0,c1) 有交叠的块。"""
    inwin = [b for b in blocks if not (b["char_end"] <= c0 or b["char_start"] >= c1)]
    w["block_ids"] = [b["block_id"] for b in inwin]
    pages = [b["page"] for b in inwin]
    w["page_start"] = min(pages) if pages else 0
    w["page_end"] = max(pages) if pages else 0
    w["token_estimate"] = max(1, (c1 - c0) // 4)
    w["contains"] = sorted({b["type"] for b in inwin})
    w["assets"] = [b["asset_path"] for b in inwin if b.get("asset_path")]
    flags: set = set()
    for b in inwin:
        flags.update(b.get("risk_flags") or [])
    w["risk_flags"] = sorted(flags)
    # L3：窗内 blocks 的 source_ref（与 block_ids 对齐顺序）+ chapter_id 去重排序（跳过空）。
    w["source_refs"] = [b.get("source_ref", "") for b in inwin]
    w["chapter_ids"] = sorted({b.get("chapter_id") for b in inwin if b.get("chapter_id")})


def _chapter_title_for_page(page: int, chapters: list) -> str:
    """page 落入某章 [page_start, page_end] → 该章标题；落不到 → ""（L3 chapter_title 查询）。"""
    for c in chapters or []:
        if int(c.get("page_start", 0)) <= page <= int(c.get("page_end", 0)):
            return c.get("title", "") or ""
    return ""


# 原子块：内容不可在窗间切分（长表不切；图/图表是单一视觉单元）。
_ATOMIC_TYPES = {"table", "image", "chart"}


def _pack_blocks(sec_blocks: list, *, target_chars: int, max_chars: int):
    """整块打包（任何块不切）：累加整块到「加下一块会超 target」就开新窗；单块超 max 也独占一窗
    （不切）。用于含原子块的 section —— 长表/大图绝不被切到两窗。返回 (c0, c1, overlap=0)。"""
    out: list = []
    cur: list = []
    for b in sec_blocks:
        if cur and (b["char_end"] - cur[0]["char_start"]) > target_chars:
            out.append((cur[0]["char_start"], cur[-1]["char_end"], 0))
            cur = [b]
        else:
            cur.append(b)
    if cur:
        out.append((cur[0]["char_start"], cur[-1]["char_end"], 0))
    return out


def build_windows_from_blocks(blocks: list, *, source_id: str = "", chapters=None,
                              target_tokens: int = 2000, max_tokens: int = 4000,
                              overlap_tokens: int = 200) -> list[dict]:
    """block-aware windows：按 section 切，再回挂块元数据（block_ids/page 范围/contains/assets/
    risk_flags/source_refs/chapter_ids）。L3：每窗注入 source_id；按 page_start 查 chapter_title。

    含原子块(table/image/chart)的 section → 整块打包（长表不切）；纯文本 section → 与 char 窗
    共用 _slice_section 保等价（含超大文本块仍按 token 滑窗切，与历史行为一致）。"""
    out: list[dict] = []
    idx = 0
    for path, s, e in _sections_from_blocks(blocks):
        sec_blocks = [b for b in blocks if not (b["char_end"] <= s or b["char_start"] >= e)]
        if any(b.get("type") in _ATOMIC_TYPES for b in sec_blocks):
            slices = _pack_blocks(sec_blocks, target_chars=target_tokens * 4,
                                  max_chars=max_tokens * 4)
        else:
            slices = _slice_section(s, e, target_tokens=target_tokens,
                                    max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        for c0, c1, ov in slices:
            w = _win(idx, path, c0, c1, ov, mode="blocks")
            _attach_block_meta(w, blocks, c0, c1)
            w["source_id"] = source_id
            w["chapter_title"] = _chapter_title_for_page(w["page_start"], chapters)
            out.append(w)
            idx += 1
    return out
