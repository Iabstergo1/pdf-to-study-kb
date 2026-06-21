"""L4 调用与评测层：确定性预处理产物验收（零-LLM，纯函数 + check_*）。

读 staging/<source>/ 的 blocks.jsonl / windows.jsonl / parse_report.json（+ 可选 pages.jsonl
/ assets/），对预处理产物做 6 项确定性结构检查，产出可 CI 化的 JSON 报告。

**不是 RAG**：不实现 search_PDF / 向量召回 / LLM 评判；只在既有确定性产物上做结构断言。
每个 check_* 是独立纯函数（输入已解析的 blocks/windows/report），便于合成 staging 单测；
evaluate() 负责 I/O + 组装 + summary。算不出的字段保守跳过，不伪造。
"""
from __future__ import annotations

import json
from pathlib import Path

# 严重度排序：high > warn > info（strict 判定取 high）。状态：ok / warn / fail。
__all__ = ["evaluate", "check_page_coverage", "check_window_monotonic",
           "check_asset_traceability", "check_risk_signals", "check_orphan_blocks",
           "check_source_ref_integrity"]


def _check(name: str, severity: str, status: str, detail: str) -> dict:
    return {"name": name, "severity": severity, "status": status, "detail": detail}


def check_page_coverage(blocks: list, *, page_count: int) -> dict:
    """blocks 覆盖 page 1..page_count 无缺页（缺页 → high/fail）。

    page_count<=0（如 docx/pptx 无页号、空报告）→ info/ok（不适用，不伪造缺页）。"""
    pc = int(page_count or 0)
    if pc <= 0:
        return _check("page_coverage", "info", "ok", "page_count 未知/不适用，跳过缺页检查")
    covered = {int(b.get("page", 0)) for b in blocks}
    missing = [p for p in range(1, pc + 1) if p not in covered]
    if missing:
        shown = ",".join(str(p) for p in missing[:20])
        return _check("page_coverage", "high", "fail",
                      f"缺页 {len(missing)} 个（page_count={pc}）：{shown}")
    return _check("page_coverage", "high", "ok", f"page 1..{pc} 全覆盖")


def check_window_monotonic(windows: list) -> dict:
    """windows char 区间有序无洞（相邻窗 char 连续，允许 overlap）、page_start<=page_end 且
    跨窗页非降、block 窗 block_ids 非空。任一违例 → high/fail。"""
    problems: list = []
    prev_end = None          # 上窗 char_end 上界（含 overlap）
    prev_page_start = None   # 上窗 page_start（跨窗页非降判定基准）
    for w in windows:
        wid = w.get("window_id", "?")
        cs, ce = int(w.get("char_start", 0)), int(w.get("char_end", 0))
        ps, pe_ = int(w.get("page_start", 0)), int(w.get("page_end", 0))
        if ce < cs:
            problems.append(f"{wid}: char_end<char_start")
        if ps > pe_:
            problems.append(f"{wid}: page_start>page_end ({ps}>{pe_})")
        if w.get("mode") == "blocks" and not w.get("block_ids"):
            problems.append(f"{wid}: block 窗 block_ids 为空")
        if prev_end is not None and cs > prev_end:
            problems.append(f"{wid}: char 区间有洞（{cs}>{prev_end}）")
        # 跨窗页非降：本窗 page_start 不应早于上窗 page_start（允许同页 overlap）。
        if prev_page_start is not None and ps and ps < prev_page_start:
            problems.append(f"{wid}: 跨窗页倒退（page_start {ps}<{prev_page_start}）")
        prev_end = max(prev_end or 0, ce)
        if ps:
            prev_page_start = ps
    if problems:
        return _check("window_monotonic", "high", "fail", "; ".join(problems[:20]))
    return _check("window_monotonic", "high", "ok", f"{len(windows)} 窗 char/page 单调无洞")


def _staging_has(staging_dir: Path, rel: str) -> bool:
    """asset 相对路径（如 'assets/p0001.png'）在 staging 是否存在。"""
    if not rel:
        return False
    return (Path(staging_dir) / rel).exists()


def check_asset_traceability(staging_dir, blocks: list, windows: list) -> dict:
    """每个 table/image/chart block 的 asset_path 文件在 staging 存在；每个 window assets
    存在（缺失 → high/fail）。"""
    asset_types = {"table", "image", "chart"}
    missing: list = []
    for b in blocks:
        ap = b.get("asset_path")
        if (b.get("type") in asset_types or ap) and ap:
            if not _staging_has(staging_dir, ap):
                missing.append(f"block {b.get('block_id', '?')} → {ap}")
    for w in windows:
        for ap in w.get("assets", []) or []:
            if not _staging_has(staging_dir, ap):
                missing.append(f"window {w.get('window_id', '?')} → {ap}")
    if missing:
        return _check("asset_traceability", "high", "fail",
                      f"缺资产 {len(missing)} 处：" + "; ".join(missing[:20]))
    return _check("asset_traceability", "high", "ok", "全部 asset_path 在 staging 命中")


def check_risk_signals(report: dict, *, low_confidence_pages: list) -> dict:
    """汇报 scan_suspected / ocr_used / low_confidence_pages（信息性）。
    low_confidence_pages 非空 → warn（不阻断，仅提示人复核）；否则 ok。"""
    scan = bool(report.get("scan_suspected"))
    ocr = bool(report.get("ocr_used"))
    lcp = list(low_confidence_pages or [])
    detail = f"scan_suspected={scan}, ocr_used={ocr}, low_confidence_pages={lcp[:20]}"
    if lcp:
        return _check("risk_signals", "info", "warn", detail)
    return _check("risk_signals", "info", "ok", detail)


def check_orphan_blocks(blocks: list, windows: list) -> dict:
    """blocks 的 block_id 未进任何 window.block_ids → 孤儿（warn + 计数）。"""
    in_windows: set = set()
    for w in windows:
        in_windows.update(w.get("block_ids", []) or [])
    orphans = [b.get("block_id", "?") for b in blocks if b.get("block_id") not in in_windows]
    if orphans:
        shown = ",".join(orphans[:20])
        return _check("orphan_blocks", "warn", "warn",
                      f"{len(orphans)} 个 block 未进任何窗：{shown}")
    return _check("orphan_blocks", "warn", "ok", "无孤儿 block")


def check_source_ref_integrity(blocks: list, windows: list) -> dict:
    """每 block source_ref==f"p{page:04d}#{block_id}" 且非空；每 window source_refs 覆盖其
    block_ids（不符 → high/fail）。"""
    problems: list = []
    ref_by_id: dict = {}
    for b in blocks:
        bid = b.get("block_id", "")
        page = int(b.get("page", 0))
        expected = f"p{page:04d}#{bid}"
        ref = b.get("source_ref", "")
        ref_by_id[bid] = ref
        if not ref:
            problems.append(f"block {bid}: source_ref 为空")
        elif ref != expected:
            problems.append(f"block {bid}: source_ref={ref!r} != {expected!r}")
    for w in windows:
        refs = set(w.get("source_refs", []) or [])
        for bid in w.get("block_ids", []) or []:
            want = ref_by_id.get(bid)
            if want and want not in refs:
                problems.append(f"window {w.get('window_id', '?')}: 未覆盖 block {bid} 的 source_ref")
    if problems:
        return _check("source_ref_integrity", "high", "fail", "; ".join(problems[:20]))
    return _check("source_ref_integrity", "high", "ok", "source_ref 全部规范且被窗覆盖")


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def evaluate(staging_dir) -> dict:
    """对一个 staging/<source>/ 目录跑 6 项检查，返回 CI 化 JSON 报告（纯函数 + I/O 读取）。

    形状：{source_id, source_type, selected_backend, generated_by:"preflight-eval",
           checks:[...], summary:{ok,warn,fail}}。
    """
    d = Path(staging_dir)
    blocks = _read_jsonl(d / "blocks.jsonl")
    windows = _read_jsonl(d / "windows.jsonl")
    report = {}
    rp = d / "parse_report.json"
    if rp.exists():
        report = json.loads(rp.read_text(encoding="utf-8"))

    # page_count：优先 parse_report；缺则用 blocks 的最大 page（保守，不伪造缺页）。
    page_count = int(report.get("page_count") or 0)
    if page_count <= 0 and blocks:
        page_count = max((int(b.get("page", 0)) for b in blocks), default=0)

    checks = [
        check_page_coverage(blocks, page_count=page_count),
        check_window_monotonic(windows),
        check_asset_traceability(d, blocks, windows),
        check_risk_signals(report, low_confidence_pages=report.get("low_confidence_pages", [])),
        check_orphan_blocks(blocks, windows),
        check_source_ref_integrity(blocks, windows),
    ]
    summary = {
        "ok": sum(1 for c in checks if c["status"] == "ok"),
        "warn": sum(1 for c in checks if c["status"] == "warn"),
        "fail": sum(1 for c in checks if c["status"] == "fail"),
    }
    return {
        "source_id": d.name,
        "source_type": report.get("source_type", "unknown"),
        "selected_backend": report.get("selected_backend", "unknown"),
        "generated_by": "preflight-eval",
        "checks": checks,
        "summary": summary,
    }
