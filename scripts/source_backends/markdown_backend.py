"""Markdown 后端（Spec 1）：原文即 source.md（顺读视图）；按 _sections 出 section-level 块。

复刻现有 Markdown 行为，不引入新解析策略。heading_path 与 windowing._sections 一致
（直接标题、不嵌套），保证 block 窗 ≈ char 窗等价。section 块的 text = 该段完整 Markdown
（heading 行 + 正文），绝不拆走正文；source_md[char_start:char_end] == block.text。
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_profile
import chaptering
import windowing
import source_artifacts as sa


def convert(src_path, *, out_dir, input_hash: str):
    md = Path(src_path).read_text(encoding="utf-8")
    pages = [source_profile.profile_page(1, md, image_count=0)]
    chapters = chaptering.chapters_from_toc([], n_pages=1)
    blocks = []
    heading_count = 0
    for i, (path, s, e) in enumerate(windowing._sections(md)):
        seg = md[s:e]
        first = seg.splitlines()[0] if seg.strip() else ""
        m = windowing._HEADING.match(first)
        if m:
            heading_count += 1
        block_id = f"b{i + 1:06d}"
        blocks.append(sa.SourceBlock(
            block_id=block_id, type="heading" if m else "text", text=seg,
            page=1, char_start=s, char_end=e,
            text_level=(len(m.group(1)) if m else None), heading_path=path,
            risk_flags=[], source_ref=sa.block_source_ref(1, block_id)))
    advice = sa.RoutingAdvice(recommended_backend="markdown",
                              structured_reparse_recommended=False)
    report = sa.build_parse_report("markdown", input_hash=input_hash, routing_advice=advice,
                                   section_count=len(blocks), heading_count=heading_count,
                                   block_count=len(blocks))
    return sa.BackendResult(source_md=md, blocks=blocks, chapters=chapters,
                            pages=pages, report=report, needs_vision_pages=[])
