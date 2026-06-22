"""source-convert dispatcher（Spec 1 + Spec 2）：选后端（fmt/backend/policy）→ 调后端 →
落盘 source.md + blocks.jsonl + chapters.json + parse_report.json + assets/，返回旧键超集 dict。

后端在 source_backends/；本文件不含解析业务，只做选后端 + 持久化 + 拼返回 dict。
扫描件 fail-closed 边界在 pipeline.cmd_source_convert；MinerU 运行失败时落最小失败 report 并抛出
（不静默回退 PyMuPDF）。routing_advice 是 advisory；仅 auto 实际据信号路由时置 consumed_by_auto_router。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_profile
import source_artifacts
import thresholds  # 路由阈值单一真值（env 可覆盖）
from source_backends import get_backend, get_backend_by_name, BackendUnavailable  # noqa: F401

__all__ = ["convert", "converted_input_hash", "select_backend", "classify_source",
           "BackendUnavailable"]

# 低文本/密集阈值见 thresholds.LOW_TEXT_MEAN / thresholds.DENSE_RATIO（env 可覆盖）。
# 密集/扫描信号取值（profile 已算的 per-page needs_vision_reason）
_DENSE_FLAGS = {"formula", "formula-borderline", "table", "vector-figure", "caption"}


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _scan_or_low_text(pages) -> bool:
    """扫描/纯图像/低文本密度（auto 据此把 PDF 路由给 MinerU）。复用 profile 已算信号，不改 profile。"""
    if not pages:
        return False
    if source_profile.is_scanned_source(pages):
        return True
    n = len(pages)
    mean_text = sum(p.get("text_len", 0) for p in pages) / n
    scan_ratio = sum(1 for p in pages
                     if "scanned-or-image" in (p.get("needs_vision_reason") or [])) / n
    return mean_text < thresholds.LOW_TEXT_MEAN or scan_ratio >= thresholds.DENSE_RATIO


def _dense(pages) -> bool:
    """公式/表格/图片密集 born-digital（仅 aggressive 据此路由 MinerU；conservative 只写 advice）。"""
    if not pages:
        return False
    n = len(pages)
    dense = sum(1 for p in pages
                if _DENSE_FLAGS & set(p.get("needs_vision_reason") or [])) / n
    return dense >= thresholds.DENSE_RATIO


def _assign_chapter_ids(blocks, chapters) -> None:
    """就地给每个 block 设 chapter_id：block.page 落入某章 [page_start, page_end] → 该章 id。

    后端无关：blocks 是 SourceBlock 实例（write_blocks 前），chapters 是 chaptering 的 dict 列表。
    章节连续无空洞（chapters_from_toc 保证），但稳健起见逐章区间判定，落不到 → 保持 ""。"""
    spans = [(int(c.get("page_start", 0)), int(c.get("page_end", 0)), c.get("chapter_id", ""))
             for c in (chapters or [])]
    for b in blocks:
        page = getattr(b, "page", None)
        if page is None:
            continue
        for ps, pe, cid in spans:
            if ps <= page <= pe:
                b.chapter_id = cid
                break


def select_backend(fmt, profile_pages, *, backend, policy):
    """确定性选后端 → (backend_name, consumed_by_auto_router)。

    - 显式 --backend pymupdf/mineru：直返，consumed=False（非 auto 消费）。
    - auto：md→markdown；docx/pptx→mineru；扫描/低文本 pdf→mineru；
      aggressive 下密集 born-digital pdf→mineru；其余 pdf→pymupdf。consumed=True。
    """
    if backend == "pymupdf":
        return "pymupdf", False
    if backend == "mineru":
        return "mineru", False
    if backend != "auto":
        raise BackendUnavailable(f"unknown --backend: {backend}")
    if fmt == "md":
        return "markdown", True
    if fmt in ("docx", "pptx"):
        return "mineru", True
    if fmt == "pdf":
        pages = profile_pages or []
        if _scan_or_low_text(pages):
            return "mineru", True
        if policy == "aggressive" and _dense(pages):
            return "mineru", True
        return "pymupdf", True
    raise BackendUnavailable(f"no backend for fmt={fmt}")


def classify_source(fmt, profile_pages, *, backend, policy) -> dict:
    """L1 解析层：确定性派生 source_type + backend_reason（纯函数，零 LLM、不调后端）。

    source_type ∈ {native_pdf, scanned_pdf, low_text_pdf, mixed_pdf, docx, pptx, markdown}：
    - fmt==md → markdown；fmt==docx → docx；fmt==pptx → pptx（profile_pages 为空也按 fmt）。
    - fmt==pdf：扫描件 → scanned_pdf；否则 mean_text<thresholds.LOW_TEXT_MEAN → low_text_pdf；
      否则密集（表/图/公式）→ mixed_pdf；否则 → native_pdf。pages 为空 → 保守 native_pdf。
    - 未知 fmt → source_type="unknown"（不伪造；与 select_backend 的 fail-closed 分工，不抛错）。

    backend_reason：短串，解释为何选实际后端。显式 --backend 体现 "explicit"，auto 路由体现信号。
    与 select_backend 同源派生（同一 _scan_or_low_text/_dense 信号），但**不改其返回签名**。
    """
    name, _consumed = select_backend(fmt, profile_pages, backend=backend, policy=policy)
    pages = profile_pages or []

    if fmt == "md":
        source_type = "markdown"
    elif fmt == "docx":
        source_type = "docx"
    elif fmt == "pptx":
        source_type = "pptx"
    elif fmt == "pdf":
        if pages and source_profile.is_scanned_source(pages):
            source_type = "scanned_pdf"
        elif pages and (sum(p.get("text_len", 0) for p in pages) / len(pages)) < thresholds.LOW_TEXT_MEAN:
            source_type = "low_text_pdf"
        elif _dense(pages):
            source_type = "mixed_pdf"
        else:
            source_type = "native_pdf"
    else:
        source_type = "unknown"

    # backend_reason：先按显式选择，再按 auto 路由的实际依据组织短串。
    if backend in ("pymupdf", "mineru"):
        backend_reason = f"explicit --backend={backend}"
    elif name == "markdown":
        backend_reason = "md→markdown"
    elif fmt in ("docx", "pptx"):
        backend_reason = f"fmt={fmt}→mineru"
    elif source_type == "scanned_pdf":
        backend_reason = "scanned pdf→mineru"
    elif source_type == "low_text_pdf":
        backend_reason = "low-text pdf→mineru"
    elif name == "mineru" and _scan_or_low_text(pages):  # 保守策略：partial-scan（scan_ratio≥阈值）
        backend_reason = "partial-scan pdf→mineru"
    elif name == "mineru":  # 仅剩 aggressive 策略下密集 born-digital
        backend_reason = "aggressive+dense pdf→mineru"
    elif source_type == "mixed_pdf":
        backend_reason = "dense pdf→pymupdf (conservative)"
    elif source_type == "native_pdf":
        backend_reason = "default native pdf→pymupdf"
    else:
        backend_reason = f"fmt={fmt}→{name}"

    return {"source_type": source_type, "backend_reason": backend_reason}


def converted_input_hash(raw_path, *, backend: str = "auto", policy: str = "conservative") -> str:
    """converted 阶段缓存键（单一真值，pipeline 与 convert 共用）：raw sha + PROFILER_VERSION +
    ARTIFACT_VERSION + 请求的 backend + policy + MINERU_ADAPTER_VERSION——使切换 backend/policy
    不复用彼此产物（state_store 不误判 converted up-to-date）。"""
    from source_backends import mineru_backend
    raw = Path(raw_path).read_bytes()
    return (hashlib.sha256(raw).hexdigest() + ":" + source_profile.PROFILER_VERSION
            + ":" + source_artifacts.ARTIFACT_VERSION
            + ":" + str(backend) + ":" + str(policy)
            + ":" + mineru_backend.MINERU_ADAPTER_VERSION
            + ":" + thresholds.fingerprint())   # 覆盖检测/路由阈值即失效缓存、强制重算


def convert(src_path, *, out_dir, fmt: str, backend: str = "auto",
            mineru_policy: str = "conservative", profile_pages=None) -> dict:
    """返回旧键超集 + 新键（blocks_path/blocks_sha/parse_report_path/parse_report_sha/backend）。
    未传 backend 等价 auto（向后兼容 Spec 1 调用）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name, consumed = select_backend(fmt, profile_pages, backend=backend, policy=mineru_policy)
    ihash = converted_input_hash(src_path, backend=backend, policy=mineru_policy)
    be = get_backend_by_name(name)                       # 未知/未装后端：fail-closed（BackendUnavailable）
    try:
        res = be.convert(src_path, out_dir=out_dir, input_hash=ihash)
    except Exception as e:
        from source_backends import mineru_backend
        if isinstance(e, mineru_backend.MineruRunFailed):
            # MinerU 运行失败：落最小失败 report 供审计，再抛出（pipeline fail-closed，不静默回退）。
            source_artifacts.write_parse_report(
                out_dir / "parse_report.json", mineru_backend.failed_report(ihash, str(e)))
        raise

    # 仅 auto 实际据信号路由时标记 consumed_by_auto_router；显式 --backend 不算。
    if isinstance(res.report.get("routing_advice"), dict):
        res.report["routing_advice"]["consumed_by_auto_router"] = bool(consumed)
    # MinerU 报告的 scan/OCR 信号由 profile 派生（MinerU pipeline auto-method 对扫描/图像页走 OCR）：
    # 整本扫描件 → scan_suspected/ocr_used=True；born-digital → False。MinerU 输出未显式暴露 per-page
    # parse type，故用 profile 的 is_scanned_source 作准确近似，避免扫描件报告里 scan/OCR 恒为 False。
    if name == "mineru" and isinstance(res.report, dict):
        scanned = source_profile.is_scanned_source(profile_pages or [])
        res.report["scan_suspected"] = scanned
        res.report["ocr_used"] = scanned

    # L1：写确定性 source_type + backend_reason（与选定后端同源派生，additive，零 LLM）。
    if isinstance(res.report, dict):
        cls = classify_source(fmt, profile_pages, backend=backend, policy=mineru_policy)
        res.report["source_type"] = cls["source_type"]
        res.report["backend_reason"] = cls["backend_reason"]
        # dual-audit 契约：PDF 类（*_pdf）验收要求 PyMuPDF + MinerU 双审（reconciliation.json 兑现，
        # 见 source_audit）；md/docx/pptx 非 PDF → False。preflight check_dual_audit 据此判适用范围。
        res.report["dual_audit_required"] = cls["source_type"].endswith("_pdf")

    # L2：用 res.chapters 的页范围给每个 block 映射 chapter_id（后端无关，统一）。
    # 三后端都返回 res.chapters（pymupdf TOC / markdown ch00-full / mineru heading），映射通用；
    # block.page 落某章 [page_start, page_end] → 该 chapter_id；落不到任何章 → 保持 ""（不伪造）。
    _assign_chapter_ids(res.blocks, res.chapters)

    source_md = out_dir / "source.md"
    source_md.write_text(res.source_md, encoding="utf-8")
    blocks_path = out_dir / "blocks.jsonl"
    blocks_sha = source_artifacts.write_blocks(blocks_path, res.blocks)
    chapters_json = json.dumps(res.chapters, ensure_ascii=False, indent=2)
    chapters_path = out_dir / "chapters.json"
    chapters_path.write_text(chapters_json, encoding="utf-8")
    report_path = out_dir / "parse_report.json"
    report_sha = source_artifacts.write_parse_report(report_path, res.report)

    return {
        "source_md": str(source_md),
        "sha256": _sha256_text(res.source_md),
        "assets_dir": str(out_dir / "assets"),
        "pages": res.pages,
        "needs_vision_pages": res.needs_vision_pages,
        "chapters": res.chapters,
        "chapters_path": str(chapters_path),
        "chapters_sha": _sha256_text(chapters_json),
        "blocks_path": str(blocks_path),
        "blocks_sha": blocks_sha,
        "parse_report_path": str(report_path),
        "parse_report_sha": report_sha,
        "backend": res.report["selected_backend"],
    }
