"""L4 调用与评测层：确定性预处理产物验收（零-LLM，纯函数 + check_*）。

读 staging/<source>/ 的 blocks.jsonl / windows.jsonl / parse_report.json（+ 可选 pages.jsonl
/ assets/），对预处理产物做 6 项确定性结构检查，产出可 CI 化的 JSON 报告。

**不是 RAG**：不实现 search_PDF / 向量召回 / LLM 评判；只在既有确定性产物上做结构断言。
每个 check_* 是独立纯函数（输入已解析的 blocks/windows/report），便于合成 staging 单测；
evaluate() 负责 I/O + 组装 + summary。算不出的字段保守跳过，不伪造。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thresholds  # 观测阈值（env 可覆盖）
import source_audit  # PDF_TYPES（双审适用范围单一真值）
import arbitration   # 分歧闭环验收（check_closure）

# 严重度排序：high > warn > info（strict 判定取 high）。状态：ok / warn / fail。
__all__ = ["evaluate", "check_artifact_schema", "check_page_coverage",
           "check_window_monotonic", "check_window_contract", "check_asset_traceability",
           "check_dual_audit", "check_evidence_bundle", "check_risk_coverage", "check_risk_signals",
           "check_orphan_blocks", "check_source_ref_integrity", "check_detection_distribution"]

# 四层必备字段契约（artifact contract gate）：
_REQUIRED_REPORT = ("source_type", "selected_backend", "backend_reason")   # L1
_REQUIRED_BLOCK = ("block_id", "type", "page", "source_ref", "chapter_id")  # L2（键存在即可，值可空）
_REQUIRED_WINDOW = ("window_id", "source_id")                              # L3 所有窗最小契约
# L3 block-aware 窗额外必备（char-fallback 降级窗豁免，由 check_window_contract 标记）：
_REQUIRED_BLOCK_WINDOW = ("page_start", "page_end", "chapter_title", "chapter_ids",
                          "source_refs", "block_ids")


def _check(name: str, severity: str, status: str, detail: str) -> dict:
    return {"name": name, "severity": severity, "status": status, "detail": detail}


def check_artifact_schema(report: dict, blocks: list, windows: list) -> dict:
    """四层必备字段契约：parse_report 的 L1 字段、block 的 L2 字段、window 的 L3 字段齐全。

    source_type/selected_backend/backend_reason 缺失或为 ""/"unknown" → 视为缺失（strict 不放行）。
    block 字段查键存在（chapter_id 可为空串）。block-aware 窗须有全套 L3 字段；char-fallback 窗
    只需最小契约（降级由 check_window_contract 标记）。任一缺 → high/fail。"""
    problems: list = []
    for k in _REQUIRED_REPORT:
        if report.get(k) in (None, "", "unknown"):
            problems.append(f"parse_report.{k} 缺失/unknown")
    miss_b: dict = {}
    for b in blocks:
        for k in _REQUIRED_BLOCK:
            if k not in b:
                miss_b[k] = miss_b.get(k, 0) + 1
    for k, n in miss_b.items():
        problems.append(f"block 缺字段 {k}（{n} 块）")
    miss_w: dict = {}
    for w in windows:
        required = _REQUIRED_WINDOW + (_REQUIRED_BLOCK_WINDOW if w.get("mode") == "blocks" else ())
        for k in required:
            if k not in w:
                miss_w[k] = miss_w.get(k, 0) + 1
    for k, n in miss_w.items():
        problems.append(f"window 缺字段 {k}（{n} 窗）")
    if problems:
        return _check("artifact_schema", "high", "fail", "; ".join(problems[:20]))
    return _check("artifact_schema", "high", "ok", "四层必备字段齐全")


def check_window_contract(windows: list) -> dict:
    """显式标记 char-fallback 降级窗（mode != 'blocks'，缺 L3 块级字段）。warn，不硬阻断。"""
    degraded = [w.get("window_id", "?") for w in windows if w.get("mode") != "blocks"]
    if degraded:
        return _check("window_contract", "warn", "warn",
                      f"{len(degraded)} 个 char-fallback 降级窗（缺 L3 块字段）：" + ",".join(degraded[:20]))
    return _check("window_contract", "warn", "ok", "全部 block-aware 窗（满足 L3 契约）")


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
    broken: list = []        # asset_path 指向的文件缺失（断链）
    untraceable: list = []   # 视觉块既无可用 asset 也无可追溯内容（修死逻辑：无 asset_path 不再被跳过）
    for b in blocks:
        ap = b.get("asset_path")
        t = b.get("type")
        bid = b.get("block_id", "?")
        if ap:
            if not _staging_has(staging_dir, ap):
                broken.append(f"block {bid} → {ap}（文件缺失）")
        elif t in asset_types:
            # image/chart 的内容就是图，缺图=丢内容；table 有 HTML 文本可兜底，无文本才算丢失。
            if t in ("image", "chart") or not (b.get("text") or "").strip():
                untraceable.append(f"block {bid}（{t}）无 asset 且无可追溯内容")
    for w in windows:
        for ap in w.get("assets", []) or []:
            if not _staging_has(staging_dir, ap):
                broken.append(f"window {w.get('window_id', '?')} → {ap}")
    problems = broken + untraceable
    if problems:
        return _check("asset_traceability", "high", "fail",
                      f"资产不可追溯 {len(problems)} 处：" + "; ".join(problems[:20]))
    return _check("asset_traceability", "high", "ok", "全部视觉块可追溯到 asset 或文本")


def check_dual_audit(reconciliation: dict, report: dict) -> dict:
    """PDF 双审验收（dual-audit 契约，item 10）：reconciliation.json 须存在且兑现
    PyMuPDF（primary）+ MinerU（structural reviewer）双审。

    PyMuPDF 阈值刻意宽、不可作单一真值——本检查把"是否真正双审"从口头约定变成可 CI 化的硬门。
    - 非 PDF（dual_audit_required=False 且 source_type 非 PDF 类）→ info/ok（不适用）。
    - PDF 但无 reconciliation（未跑 source-audit）→ high/fail（验收要求双审证据）。
    - review_status ∈ {degraded_no_review, review_failed} 或 dual_audited=False（PyMuPDF-only，
      未真正双审）→ high/fail（strict 不放行，生产不接受）。
    - cross_checked 但有结构分歧 → warn（可见，不阻断接受）。否则 → ok。"""
    pdf = (report.get("source_type") in source_audit.PDF_TYPES
           or bool(report.get("dual_audit_required")))
    if not pdf:
        return _check("dual_audit", "info", "ok", "非 PDF 源，PyMuPDF+MinerU 双审不适用")
    if not reconciliation:
        return _check("dual_audit", "high", "fail",
                      "PDF 源缺 reconciliation.json（未跑 source-audit）：验收要求 PyMuPDF+MinerU 双审证据")
    status = reconciliation.get("review_status", "")
    dual = bool(reconciliation.get("dual_audited"))
    dis = reconciliation.get("disagreements") or []
    detail = (f"primary={reconciliation.get('primary_backend')} "
              f"review={reconciliation.get('review_backend')} status={status} "
              f"cross_checked={len(reconciliation.get('pages_cross_checked') or [])}页 "
              f"disagreements={len(dis)}")
    if status in ("degraded_no_review", "review_failed") or not dual:
        reason = reconciliation.get("degraded_reason") or status or "not dual-audited"
        return _check("dual_audit", "high", "fail",
                      f"PDF 未真正双审（{status}）：{reason}；PyMuPDF-only 不算生产验收通过。" + detail)
    if dis:
        return _check("dual_audit", "warn", "warn",
                      detail + "：PyMuPDF/MinerU 结构有分歧，建议复核（不阻断接受）")
    return _check("dual_audit", "high", "ok", detail)


def check_evidence_bundle(evidence: dict, decisions: list, blocks: list, windows: list,
                          report: dict) -> dict:
    """证据闭环验收（evidence-assembly 的核心门）：双审分歧是否已闭环进 LLM 实际读取的窗口证据包。

    验收目标不再是"双审跑没跑",而是"下一阶段 LLM 拿到的输入是否完整"。非 PDF / 无双审分歧候选
    → info/ok。否则用 arbitration.check_closure：任一候选未仲裁 / `render` 未物化进窗口 /
    `needs_human` 未决 / `ignore` 缺原因 → high/fail（strict 阻断整本 ingest）。"""
    pdf = (report.get("source_type") in source_audit.PDF_TYPES
           or bool(report.get("dual_audit_required")))
    candidates = (evidence or {}).get("candidates", [])
    if not pdf or not candidates:
        return _check("evidence_bundle", "info", "ok", "无双审分歧候选（不适用或无未闭环分歧）")
    r = arbitration.check_closure(evidence, decisions, blocks, windows)
    if r["closed"]:
        return _check("evidence_bundle", "high", "ok",
                      f"{len(candidates)} 个双审分歧候选已全部闭环进窗口证据包（render 已物化 / ignore 有因）")
    counts: dict = {}
    for kind, _pg in r["problems"]:
        counts[kind] = counts.get(kind, 0) + 1
    pages = sorted({pg for _k, pg in r["problems"]})
    detail = ("双审分歧未闭环进 LLM 读取窗口："
              + ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
              + f"；涉及页 {pages[:20]}（缺仲裁/render未物化/needs_human/ignore缺因 → 阻断整本 ingest）")
    return _check("evidence_bundle", "high", "fail", detail)


def check_risk_coverage(evidence: dict, windows: list, report: dict) -> dict:
    """soft 证据风险（reading_order/heading_structure）是否已记录进窗口 risk_flags（观测，不阻断）。

    与 hard-risk 的 check_evidence_bundle 区分：soft risk 只让 ingest LLM 经最小标签知情，不补图、不要求
    裁决、不阻断 strict。非 PDF / 无 soft risk → info/ok；soft 标签已进某窗 risk_flags → info/ok；
    有 soft 风险页的标签未进任何窗 → warn（提示重跑 windows，不阻断）。"""
    pdf = (report.get("source_type") in source_audit.PDF_TYPES
           or bool(report.get("dual_audit_required")))
    soft_pages = (evidence or {}).get("soft_risk_pages", [])
    if not pdf or not soft_pages:
        return _check("risk_coverage", "info", "ok", "无 soft 证据风险（不适用或无）")
    by_page = (evidence or {}).get("risk_flags_by_page", {}) or {}
    uncovered = []
    for pg in soft_pages:
        soft = {f for f in (by_page.get(str(pg)) or by_page.get(pg) or []) if f in arbitration.SOFT_RISKS}
        if not soft:
            continue
        # 按页判覆盖：只看覆盖本页的窗（page_start≤pg≤page_end），且要求该页**全部** soft flag 都被
        # 这些窗携带——不能用全书并集（那会让无标签页搭别页的便车）、也不能任一命中即算（缺一类也漏）。
        covered: set = set()
        for w in windows or []:
            if int(w.get("page_start", 0)) <= int(pg) <= int(w.get("page_end", 0)):
                covered |= set(w.get("risk_flags") or [])
        if not soft <= covered:
            uncovered.append(pg)
    if uncovered:
        return _check("risk_coverage", "warn", "warn",
                      f"{len(uncovered)} 个 soft 证据风险页的标记未进任何窗口 risk_flags："
                      f"{sorted(uncovered)[:20]}（不阻断；重跑 windows 让 block.risk_flags 并入窗口）")
    return _check("risk_coverage", "info", "ok",
                  f"{len(soft_pages)} 个 soft 证据风险页已记录进窗口 risk_flags")


def check_risk_signals(report: dict, *, low_confidence_pages: list) -> dict:
    """OCR/扫描风险。**硬规则**：扫描件/疑似扫描却没走 OCR（ocr_used=False）→ 扫描内容可能被当
    文本悄悄丢失（最危险）→ high/fail。其余：low_confidence_pages 非空 → warn（提示复核）；否则 ok。"""
    scan = bool(report.get("scan_suspected"))
    ocr = bool(report.get("ocr_used"))
    stype = report.get("source_type", "")
    backend = report.get("selected_backend", "")
    lcp = list(low_confidence_pages or [])
    detail = (f"source_type={stype}, scan_suspected={scan}, ocr_used={ocr}, "
              f"low_confidence_pages={lcp[:20]}")
    if (stype == "scanned_pdf" or scan) and not ocr:
        return _check("risk_signals", "high", "fail",
                      f"scanned/scan_suspected 但 ocr_used=False（backend={backend}）：扫描内容可能未被识别。"
                      + detail)
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


def check_detection_distribution(pages: list, *, ratio_high=None) -> dict:
    """观测：难页(needs_vision)比例异常高 → 疑检测阈值在本文档上过召回（warn，不阻断）。

    把"阈值在某份文档上失准"从静默漂移变成可见信号。pages 空/无 pages.jsonl → info/ok（不适用）。
    阈值缺省取 thresholds.DETECT_RATIO_HIGH（env 可覆盖，纯观测、不折进缓存键）。"""
    ratio_high = thresholds.DETECT_RATIO_HIGH if ratio_high is None else ratio_high
    n = len(pages or [])
    if not n:
        return _check("detection_distribution", "info", "ok", "无 pages.jsonl（不适用）")
    nv = sum(1 for p in pages if p.get("needs_vision"))
    ratio = nv / n
    detail = f"needs_vision {nv}/{n}={ratio:.0%}（高线 {ratio_high:.0%}）"
    if ratio > ratio_high:
        return _check("detection_distribution", "warn", "warn",
                      detail + "：疑检测阈值在本文档上过召回，建议复核或按文档调阈值（见 thresholds）")
    return _check("detection_distribution", "info", "ok", detail)


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
    pages = _read_jsonl(d / "pages.jsonl")          # profile 输出（per-page needs_vision；可缺）
    report = {}
    rp = d / "parse_report.json"
    if rp.exists():
        report = json.loads(rp.read_text(encoding="utf-8"))
    reconciliation = {}                              # source-audit 双审证据（PDF 验收用；可缺）
    rcp = d / "reconciliation.json"
    if rcp.exists():
        reconciliation = json.loads(rcp.read_text(encoding="utf-8"))
    evidence = {}                                    # 逐页证据模型（候选/闭环用；可缺）
    evp = d / "evidence.json"
    if evp.exists():
        evidence = json.loads(evp.read_text(encoding="utf-8"))
    arb_decisions = []                               # agent 仲裁裁决（闭环验收用；可缺）
    adp = d / "arbitration" / "decisions.json"
    if adp.exists():
        arb_decisions = json.loads(adp.read_text(encoding="utf-8")).get("decisions", [])

    # page_count：优先 parse_report；缺则用 blocks 的最大 page（保守，不伪造缺页）。
    page_count = int(report.get("page_count") or 0)
    if page_count <= 0 and blocks:
        page_count = max((int(b.get("page", 0)) for b in blocks), default=0)

    checks = [
        check_artifact_schema(report, blocks, windows),
        check_page_coverage(blocks, page_count=page_count),
        check_window_monotonic(windows),
        check_window_contract(windows),
        check_asset_traceability(d, blocks, windows),
        check_dual_audit(reconciliation, report),
        check_evidence_bundle(evidence, arb_decisions, blocks, windows, report),
        check_risk_coverage(evidence, windows, report),
        check_risk_signals(report, low_confidence_pages=report.get("low_confidence_pages", [])),
        check_orphan_blocks(blocks, windows),
        check_source_ref_integrity(blocks, windows),
        check_detection_distribution(pages),
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
