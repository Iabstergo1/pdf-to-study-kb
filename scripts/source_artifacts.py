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
# v2: SourceBlock 增 chapter_id（page→章映射）+ parse_report 增 source_type/backend_reason。
# v3: SourceBlock 增 element_id（table→t{n} / image·chart→f{n}，稳定 id；跨页表片段共享）。
# v4: parse_report 增 dual_audit_required；新增 reconciliation.json 契约（PyMuPDF + MinerU 双审）。
ARTIFACT_VERSION = "4"


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
    chapter_id: str = ""            # block.page 落入的章（chapters.json 的 chapter_id）；落不到 → ""
    element_id: str = ""            # 稳定元素 id：table→"t{n}"、image/chart→"f{n}"；跨页表片段共享；其余 ""


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
                       routing_advice: "RoutingAdvice", warnings=None,
                       consumed_by_auto_router: bool = False,
                       dual_audit_required: bool = False, **extra) -> dict:
    """组装 parse_report.json（advisory-only）。强制信封常量，避免漏写/误写。

    强制 advisory 契约：`routing_advice.advisory_only` 永远 True（即使调用方误传 False）；
    `consumed_by_auto_router` 默认 False，**仅** auto router 实际据 advice/信号做路由时（Spec 2）
    才由调用方显式置 True。
    `dual_audit_required`：本源是否要求 PyMuPDF + MinerU 双审（PDF 类恒 True；严格验收靠
    reconciliation.json 兑现，见 source_audit）。默认 False（md/docx/pptx 等非 PDF）。
    mineru_status 默认 "not_checked"（Spec 1）；MinerU backend 可经 extra 覆盖为 "used"/失败值。
    extra：per-backend 附加字段（pymupdf: page_count/block_count/needs_vision_pages/
    risk_flag_counts；markdown: section_count/heading_count/block_count；mineru: 见 mineru_backend）。
    """
    ra = asdict(routing_advice)
    ra["advisory_only"] = True
    ra["consumed_by_auto_router"] = bool(consumed_by_auto_router)
    report = {
        "selected_backend": selected_backend,
        "backend_policy": "contract_only",
        "artifact_version": ARTIFACT_VERSION,
        "input_hash": input_hash,
        "routing_advice": ra,
        "mineru_status": "not_checked",
        "dual_audit_required": bool(dual_audit_required),
        "warnings": list(warnings or []),
    }
    report.update(extra)
    return report


def write_parse_report(path, report: dict) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── dual-audit reconciliation 契约（source_audit 产出 / preflight 验收的事实层）──────
# reconciliation.json 是 PyMuPDF（primary 抽取）与 MinerU（structural reviewer）一次确定性
# 互检的可审计记录：哪个后端给了哪类证据、对了哪些页、哪里不一致、是否被接受、是否降级。
# 本模块只定义形状 + 序列化（零业务逻辑）；互检比对逻辑在 source_audit.py。

_RECONCILIATION_REQUIRED = (
    "source_id", "source_type", "primary_backend", "review_backend", "review_status",
    "dual_audited", "production_accepted", "degraded", "mineru_status",
    "page_count_primary")


def build_reconciliation_report(*, source_id: str, source_type: str, primary_backend: str,
                                review_backend, review_status: str, dual_audited: bool,
                                production_accepted: bool, degraded: bool, mineru_status: str,
                                input_hash: str, page_count_primary: int,
                                degraded_reason: str = "", page_count_review=None,
                                pages_cross_checked=None, agreements: int = 0,
                                disagreements=None, missing_evidence=None) -> dict:
    """组装 reconciliation.json。强制必备字段齐全、非 vague——验收要用的字段不得缺省成
    "unknown"/None（review_backend 在"无审"时确为 None，是显式语义而非缺省占位）。

    review_status ∈ {cross_checked, degraded_no_review, review_failed}；
    primary_backend ∈ {pymupdf, mineru, markdown}；review_backend ∈ {mineru, None}。
    disagreements: list[{page, kind, primary, review}]；missing_evidence: list[str]。
    """
    return {
        "generated_by": "source-audit",
        "source_id": source_id,
        "source_type": source_type,
        "primary_backend": primary_backend,
        "review_backend": review_backend,
        "review_status": review_status,
        "dual_audited": bool(dual_audited),
        "production_accepted": bool(production_accepted),
        "degraded": bool(degraded),
        "degraded_reason": degraded_reason,
        "mineru_status": mineru_status,
        "input_hash": input_hash,
        "page_count_primary": int(page_count_primary),
        "page_count_review": page_count_review,
        "pages_cross_checked": list(pages_cross_checked or []),
        "agreements": int(agreements),
        "disagreements": list(disagreements or []),
        "missing_evidence": list(missing_evidence or []),
    }


def write_reconciliation(path, report: dict) -> str:
    """落盘 reconciliation.json，返回内容 sha256（确定性，与 parse_report 同序列化规约）。"""
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BackendResult:
    """后端 → dispatcher 的内部交接（非落盘形状；dispatcher 据此写 artifact 并拼返回 dict）。"""
    source_md: str            # source.md 全文（LLM 顺读视图）
    blocks: list              # list[SourceBlock]
    chapters: list            # chaptering 输出（dict 列表）
    pages: list               # 逐页 profile（dict 列表）
    report: dict              # build_parse_report 产出
    needs_vision_pages: list  # 难页页号
