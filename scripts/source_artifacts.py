"""源数据契约（Spec 1）：确定性事实层 blocks.jsonl + 质量/建议报告 parse_report.json。

纯数据 + 序列化，无业务逻辑。下游（windowing/show-window/workorder）只认这里的形状。
blocks.jsonl 是 windowing 的输入与定位事实层，不是 Obsidian 页、不是 LLM 语义切分结果；
schema 预留 MinerU 的 table/equation/image 细类型，Spec 1 的 PyMuPDF 只产页粒度 text 块。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

# artifact 格式版本：blocks/parse_report 形状实质改动就 +1，折进 converted 阶段 input_hash，
# 使格式升级失效缓存、强制对任意来源重产（与 PROFILER_VERSION/WINDOWING_VERSION 同规）。
ARTIFACT_VERSION = "1"


@dataclass
class SourceBlock:
    block_id: str            # 全源单调序号 "b{seq:06d}"
    type: str                # "text" | "heading"（Spec 1 PyMuPDF 仅 text；预留 table/equation/image）
    text: str
    page: int                # 1-based
    char_start: int          # 进 source.md 的字符偏移（定位事实层）
    char_end: int
    text_level: int | None = None   # Markdown heading 的 # 级数；正文/PyMuPDF 为 None
    heading_path: str = ""          # Markdown：直接标题（与 _sections 一致，不嵌套）；PyMuPDF：""
    asset_path: str | None = None   # needs_vision 页 PNG 的 staging 相对路径；否则 None
    risk_flags: list = field(default_factory=list)
    source_ref: str = ""            # f"p{page:04d}#{block_id}"


def block_source_ref(page: int, block_id: str) -> str:
    return f"p{page:04d}#{block_id}"


def write_blocks(path, blocks) -> str:
    """把 SourceBlock（或 dict）列表写成 blocks.jsonl，返回内容 sha256。"""
    lines = []
    for b in blocks:
        d = asdict(b) if isinstance(b, SourceBlock) else dict(b)
        lines.append(json.dumps(d, ensure_ascii=False))
    text = "\n".join(lines)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_blocks(path) -> list:
    """读 blocks.jsonl 为 dict 列表（windowing 等消费方用 dict，不依赖本模块类型）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


@dataclass
class RoutingAdvice:
    recommended_backend: str
    structured_reparse_recommended: bool
    reasons: list = field(default_factory=list)
    advisory_only: bool = True             # Spec 1 恒 True
    consumed_by_auto_router: bool = False  # Spec 1 恒 False（Spec 2 被 auto 读时才置 True）


def build_parse_report(selected_backend: str, *, input_hash: str,
                       routing_advice: "RoutingAdvice", warnings=None, **extra) -> dict:
    """组装 parse_report.json（advisory-only）。强制信封常量，避免漏写/误写。

    Spec 1 不探测 MinerU：mineru_status 恒 "not_checked"，绝不写 mineru_available。
    extra：per-backend 附加字段（pymupdf: page_count/block_count/needs_vision_pages/
    risk_flag_counts；markdown: section_count/heading_count/block_count）。
    """
    report = {
        "selected_backend": selected_backend,
        "backend_policy": "contract_only",
        "artifact_version": ARTIFACT_VERSION,
        "input_hash": input_hash,
        "routing_advice": asdict(routing_advice),
        "mineru_status": "not_checked",
        "warnings": list(warnings or []),
    }
    report.update(extra)
    return report


def write_parse_report(path, report: dict) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
