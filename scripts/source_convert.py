"""source-convert dispatcher（Spec 1）：按 fmt 选后端，落盘 source.md + blocks.jsonl +
chapters.json + parse_report.json + assets/，返回 ConvertResult（旧键超集 + 新键）。

后端在 source_backends/；本文件不含解析业务，只做选后端 + 持久化 + 拼返回 dict。
source.md 是 LLM 顺读视图；blocks.jsonl 是定位事实层；两者由后端同源产出。
扫描件 fail-closed 边界在 pipeline.cmd_source_convert（进入本 dispatcher 之前），本文件不重复。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_profile
import source_artifacts
from source_backends import get_backend, BackendUnavailable  # 对外重新导出 BackendUnavailable

__all__ = ["convert", "converted_input_hash", "BackendUnavailable"]


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def converted_input_hash(raw_path) -> str:
    """converted 阶段缓存键（单一真值，pipeline 与 convert 共用）：
    raw sha + PROFILER_VERSION（连带难页 PNG）+ ARTIFACT_VERSION（artifact 形状）。"""
    raw = Path(raw_path).read_bytes()
    return (hashlib.sha256(raw).hexdigest() + ":" + source_profile.PROFILER_VERSION
            + ":" + source_artifacts.ARTIFACT_VERSION)


def convert(src_path, *, out_dir, fmt: str) -> dict:
    """返回旧键 + 新键（blocks_path/blocks_sha/parse_report_path/parse_report_sha/backend）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = get_backend(fmt)                       # 未知 fmt → BackendUnavailable
    ihash = converted_input_hash(src_path)
    res = backend.convert(src_path, out_dir=out_dir, input_hash=ihash)

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
