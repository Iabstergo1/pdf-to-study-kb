"""PyMuPDF 后端（Spec 1）：page-granularity 块（coarse），不模拟 layout、不推断 heading。

复刻现有 PDF 行为：source.md 仍是页标记 + 纯文本（顺读视图）；难页（needs_vision）渲染整页
PNG（route B）。每页一个 type=text 块，char span 覆盖整页段（含 marker，由 windowing.page_char_ranges
派生，是唯一定位真值）；难页块写 asset_path + risk_flags。routing_advice 由本层聚合 profile 已有
per-page 信号得出（advisory-only），不改 source_profile、不检测 MinerU。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_profile
import chaptering
import windowing
import source_artifacts as sa
from source_backends import BackendUnavailable

# routing_advice 阈值（advisory，Spec 2 再校准）
_LOW_TEXT_MEAN = 100
_DENSE_RATIO = 0.30


def _routing_advice(pages: list) -> sa.RoutingAdvice:
    """聚合 profile 已有 per-page 信号 → 描述性建议（advisory-only，无人据此路由）。"""
    n = len(pages) or 1

    def ratio(flag):
        return sum(1 for p in pages if flag in (p.get("needs_vision_reason") or [])) / n

    reasons = []
    if ratio("scanned-or-image") >= _DENSE_RATIO:
        reasons.append("scan_suspected")
    if sum(p.get("text_len", 0) for p in pages) / n < _LOW_TEXT_MEAN:
        reasons.append("low_text_density")
    dense = sum(1 for p in pages
                if {"formula", "formula-borderline", "table"}
                & set(p.get("needs_vision_reason") or [])) / n
    if dense >= _DENSE_RATIO:
        reasons.append("table_or_formula_dense")
    rec = "mineru" if reasons else "pymupdf"
    return sa.RoutingAdvice(recommended_backend=rec,
                            structured_reparse_recommended=bool(reasons), reasons=reasons)


def convert(src_path, *, out_dir, input_hash: str):
    if importlib.util.find_spec("fitz") is None:    # parity：缺 fitz 时给清晰的 BackendUnavailable
        raise BackendUnavailable("PyMuPDF (fitz) not installed; pip install pymupdf 或安装 study-kb 依赖")
    import fitz  # PyMuPDF
    out_dir = Path(out_dir)
    assets_dir = out_dir / "assets"
    doc = fitz.open(str(src_path))
    parts, pages, page_texts = [], [], []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text()
        page_texts.append(text)
        sig = source_profile.visual_signals(page)
        prof = source_profile.profile_page(i + 1, text, image_count=sig["image_count"],
                                           n_draw=sig["n_draw"], n_tables=sig["n_tables"])
        pages.append(prof)
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}\n")
    source_md = "".join(parts).strip() + "\n"
    ranges = windowing.page_char_ranges(source_md)

    blocks, needs_vision_pages, risk_counts = [], [], {}
    for i in range(len(doc)):
        page_no = i + 1
        prof = pages[i]
        s, e = ranges[page_no]
        flags = list(prof.get("needs_vision_reason") or [])
        for f in flags:
            risk_counts[f] = risk_counts.get(f, 0) + 1
        asset_path = None
        if prof.get("needs_vision"):
            needs_vision_pages.append(page_no)
            assets_dir.mkdir(parents=True, exist_ok=True)
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(3, 3))
            pix.save(str(assets_dir / f"p{page_no:04d}.png"))
            asset_path = f"assets/p{page_no:04d}.png"
        block_id = f"b{page_no:06d}"
        blocks.append(sa.SourceBlock(
            block_id=block_id, type="text", text=page_texts[i].strip(),
            page=page_no, char_start=s, char_end=e, text_level=None, heading_path="",
            asset_path=asset_path, risk_flags=flags,
            source_ref=sa.block_source_ref(page_no, block_id)))
    chapters = chaptering.chapters_from_toc(doc.get_toc(), len(doc))
    doc.close()

    if needs_vision_pages:
        print(f"[info] source-convert(pymupdf)：{len(needs_vision_pages)} 个难页已渲染整页 PNG"
              f"（route B 读图保真：公式写 KaTeX、图嵌原图、表 markdown+源图）。", file=sys.stderr)

    report = sa.build_parse_report(
        "pymupdf", input_hash=input_hash, routing_advice=_routing_advice(pages),
        page_count=len(pages), block_count=len(blocks),
        needs_vision_pages=needs_vision_pages, risk_flag_counts=risk_counts)
    return sa.BackendResult(source_md=source_md, blocks=blocks, chapters=chapters,
                            pages=pages, report=report, needs_vision_pages=needs_vision_pages)
