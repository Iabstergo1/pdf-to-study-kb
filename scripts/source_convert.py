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
from source_backends import get_backend, get_backend_by_name, BackendUnavailable  # noqa: F401

__all__ = ["convert", "converted_input_hash", "select_backend", "BackendUnavailable"]

_LOW_TEXT_MEAN = 100
_DENSE_RATIO = 0.30
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
    return mean_text < _LOW_TEXT_MEAN or scan_ratio >= _DENSE_RATIO


def _dense(pages) -> bool:
    """公式/表格/图片密集 born-digital（仅 aggressive 据此路由 MinerU；conservative 只写 advice）。"""
    if not pages:
        return False
    n = len(pages)
    dense = sum(1 for p in pages
                if _DENSE_FLAGS & set(p.get("needs_vision_reason") or [])) / n
    return dense >= _DENSE_RATIO


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


def converted_input_hash(raw_path, *, backend: str = "auto", policy: str = "conservative") -> str:
    """converted 阶段缓存键（单一真值，pipeline 与 convert 共用）：raw sha + PROFILER_VERSION +
    ARTIFACT_VERSION + 请求的 backend + policy + MINERU_ADAPTER_VERSION——使切换 backend/policy
    不复用彼此产物（state_store 不误判 converted up-to-date）。"""
    from source_backends import mineru_backend
    raw = Path(raw_path).read_bytes()
    return (hashlib.sha256(raw).hexdigest() + ":" + source_profile.PROFILER_VERSION
            + ":" + source_artifacts.ARTIFACT_VERSION
            + ":" + str(backend) + ":" + str(policy)
            + ":" + mineru_backend.MINERU_ADAPTER_VERSION)


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
