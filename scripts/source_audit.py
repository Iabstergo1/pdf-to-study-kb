"""source-audit：PyMuPDF（primary 抽取）× MinerU（structural reviewer）确定性互检层（零 LLM）。

为什么存在：PyMuPDF 的 `needs_vision` 阈值刻意宽（高召回），**不可作 PDF 验收的单一真值**。
本层让 MinerU 作独立 structural reviewer，对 PDF 做双审——跑 MinerU 复读同一 PDF（born-digital），
或对 MinerU-primary 源（扫描/低文本）用其结构 + PyMuPDF profile 交叉确认页覆盖——产出可审计的
`reconciliation.json`：哪个后端给了哪类证据、对了哪些页、哪里不一致、是否被接受、是否降级、缺什么。

模式分级（acceptance）：
- **strict / 生产验收**：每个 PDF 都必须有 MinerU structural review；MinerU 不可用 / 复读失败 →
  `DualAuditUnavailable`（fail-closed，不静默回退 PyMuPDF）。
- **non-strict / dev**：MinerU 缺失时 PyMuPDF-only 仍可产出，但显式标 `degraded / not dual-audited`
  （`production_accepted=False`），**不得**满足 strict 验收（由 preflight check_dual_audit 兑现）。

依赖隔离：MinerU 经 `mineru_review` 回调注入（默认走 source_backends.mineru_backend）；测试注入 mock，
套件不依赖真实 MinerU 安装。本模块纯函数 + 一个 audit() 编排，无网络、无 LLM。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_artifacts as sa
import thresholds

__all__ = ["reconcile", "audit", "DualAuditUnavailable", "PDF_TYPES"]

# 需要双审的 PDF 类（docx/pptx 已是 MinerU 结构化；markdown 非 PDF）。
PDF_TYPES = {"native_pdf", "mixed_pdf", "scanned_pdf", "low_text_pdf"}

# primary（PyMuPDF profile）逐页结构信号 ← needs_vision_reason。
_PRIMARY_TABLE = {"table"}
_PRIMARY_FIGURE = {"vector-figure", "caption", "scanned-or-image"}
_PRIMARY_FORMULA = {"formula", "formula-borderline"}
# review（MinerU blocks）逐页结构信号 ← block.type。
_REVIEW_TABLE = {"table"}
_REVIEW_FIGURE = {"image", "chart"}
_REVIEW_FORMULA = {"equation", "formula"}


class DualAuditUnavailable(RuntimeError):
    """strict 验收要求 MinerU 复读，但 MinerU 不可用 / 复读失败。cmd/pipeline 据此 fail-closed。"""


def _max_page(blocks) -> int:
    return max((int(b.get("page", 0)) for b in (blocks or [])), default=0)


def _primary_page_signals(pages) -> dict:
    """PyMuPDF profile（pages.jsonl）逐页 → {page: {table, figure, formula, needs_vision}}。"""
    out: dict = {}
    for p in pages or []:
        page = int(p.get("page", 0))
        reasons = set(p.get("needs_vision_reason") or [])
        out[page] = {
            "table": bool(reasons & _PRIMARY_TABLE),
            "figure": bool(reasons & _PRIMARY_FIGURE),
            "formula": bool(reasons & _PRIMARY_FORMULA),
            "needs_vision": bool(p.get("needs_vision")) or bool(reasons),
        }
    return out


def _review_page_signals(blocks) -> dict:
    """MinerU 复读 blocks 逐页 → {page: {table, figure, formula}}（结构证据是否存在）。"""
    out: dict = {}
    for b in blocks or []:
        page = int(b.get("page", 0))
        t = (b.get("type") or "").lower()
        s = out.setdefault(page, {"table": False, "figure": False, "formula": False})
        if t in _REVIEW_TABLE:
            s["table"] = True
        elif t in _REVIEW_FIGURE:
            s["figure"] = True
        elif t in _REVIEW_FORMULA:
            s["formula"] = True
    return out


def _page_count_mismatch(disagreements, primary_n, review_n, tol):
    if review_n is not None and abs(primary_n - review_n) > tol:
        disagreements.append({"page": 0, "kind": "page_count_mismatch",
                              "primary": primary_n, "review": review_n})


def reconcile(primary_pages, primary_blocks, review_blocks, *, source_type: str,
              primary_backend: str, mineru_status: str, source_id: str = "",
              input_hash: str = "", page_count_tol=None) -> dict:
    """确定性互检 → reconciliation 报告（纯函数）。

    - 非 PDF（markdown/docx/pptx）：双审不适用 → review_status="not_applicable"（dual_audited 视为
      满足，docx/pptx 本就 MinerU 结构化）。
    - primary=mineru（扫描/低文本 PDF）：MinerU 是结构权威，PyMuPDF profile 交叉确认页覆盖 →
      cross_checked / dual_audited（无需第二 reviewer）。
    - primary=pymupdf（born-digital PDF）：
      - review_blocks=None → degraded_no_review（或 review_failed，由 mineru_status 区分）：
        dual_audited=False, degraded=True, production_accepted=False, missing_evidence=[mineru_review]。
      - review_blocks 有 → cross_checked：逐页比对 table/figure/formula 存在性，记录分歧（warn 级，
        不阻断接受），dual_audited=True。
    """
    tol = thresholds.RECONCILE_PAGECOUNT_TOL if page_count_tol is None else page_count_tol
    page_count_primary = _max_page(primary_blocks) or len(primary_pages or [])

    def build(**kw):
        base = dict(source_id=source_id, source_type=source_type, primary_backend=primary_backend,
                    mineru_status=mineru_status, input_hash=input_hash,
                    page_count_primary=page_count_primary)
        base.update(kw)
        return sa.build_reconciliation_report(**base)

    if source_type not in PDF_TYPES:
        return build(review_backend=None, review_status="not_applicable", dual_audited=True,
                     production_accepted=True, degraded=False, page_count_review=None)

    if primary_backend == "mineru":
        # MinerU 是结构权威；PyMuPDF profile 作页覆盖交叉确认（无第二 reviewer）。
        page_count_review = len(primary_pages or []) or None
        disagreements: list = []
        _page_count_mismatch(disagreements, page_count_primary, page_count_review, tol)
        review_pages = sorted({int(p.get("page", 0)) for p in (primary_pages or [])})
        primary_pageset = sorted({int(b.get("page", 0)) for b in (primary_blocks or [])})
        cross = sorted(set(review_pages) & set(primary_pageset))
        return build(review_backend="pymupdf", review_status="cross_checked", dual_audited=True,
                     production_accepted=True, degraded=False, page_count_review=page_count_review,
                     pages_cross_checked=cross, agreements=len(cross), disagreements=disagreements,
                     missing_evidence=[])

    # primary_backend == "pymupdf"（born-digital PDF）
    if review_blocks is None:
        if mineru_status == "failed":
            status, reason = "review_failed", "MinerU structural review failed"
        else:
            status, reason = ("degraded_no_review",
                              "MinerU unavailable: PyMuPDF-only output is NOT dual-audited "
                              "(not production-accepted)")
        return build(review_backend=None, review_status=status, dual_audited=False,
                     production_accepted=False, degraded=True, degraded_reason=reason,
                     page_count_review=None, missing_evidence=["mineru_review"])

    psig = _primary_page_signals(primary_pages)
    rsig = _review_page_signals(review_blocks)
    page_count_review = _max_page(review_blocks)
    disagreements = []
    pages_cross_checked: list = []
    agreements = 0
    for page in sorted(set(psig) & set(rsig)):
        pages_cross_checked.append(page)
        p, r = psig[page], rsig[page]
        page_agree = True
        for kind in ("table", "figure", "formula"):
            if p[kind] != r[kind]:
                disagreements.append({"page": page, "kind": f"{kind}_presence",
                                      "primary": p[kind], "review": r[kind]})
                page_agree = False
        if page_agree:
            agreements += 1
    _page_count_mismatch(disagreements, page_count_primary, page_count_review, tol)
    return build(review_backend="mineru", review_status="cross_checked", dual_audited=True,
                 production_accepted=True, degraded=False, page_count_review=page_count_review,
                 pages_cross_checked=pages_cross_checked, agreements=agreements,
                 disagreements=disagreements, missing_evidence=[])


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _default_mineru_review(raw_path, out_dir, input_hash):
    """默认 reviewer：MinerU 可用则 subprocess 复读同一 PDF → review blocks（dict 列表）；
    不可用 → None（caller 据此降级/strict fail-closed）。运行失败抛 MineruRunFailed（caller 捕获）。"""
    from source_backends import mineru_backend
    if not mineru_backend.mineru_available():
        return None
    res = mineru_backend.convert(raw_path, out_dir=out_dir, input_hash=input_hash)
    return [asdict(b) for b in res.blocks]


def _default_render_packets(raw_path, pages, arb_dir):
    """默认：打开 raw PDF，把分歧候选页渲成 arbitration/p<NNNN>.png（供 agent 仲裁时读图）。"""
    import source_profile
    source_profile.render_pages_png(raw_path, pages, arb_dir, prefix="p")


def _write_evidence_and_queue(staging_dir, raw_path, primary_pages, primary_blocks, review_blocks,
                              *, render_packets=None):
    """落 evidence.json（逐页证据模型）+ arbitration/queue.json（最小分歧证据包），并按需补渲候选页图。

    零 LLM：仅确定性产出；agent 在 source-preflight/ingest skill 流里读 queue 自动仲裁。
    lazy import arbitration 防与本模块循环依赖（arbitration 顶层 import source_audit）。"""
    import arbitration as arb
    sd = Path(staging_dir)
    model = arb.build_evidence_model(primary_pages, primary_blocks, review_blocks)
    (sd / arb.EVIDENCE_FILE).write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = arb.select_candidates(model)
    text_by_page = {int(b.get("page", 0)): (b.get("text") or "") for b in (primary_blocks or [])}
    packets = arb.build_packets(model, page_text=lambda p: text_by_page.get(p, ""))
    (sd / arb.ARB_DIR).mkdir(parents=True, exist_ok=True)
    if candidates:
        (render_packets or _default_render_packets)(raw_path, candidates, sd / arb.ARB_DIR)
    (sd / arb.QUEUE_FILE).write_text(
        json.dumps({"generated_by": "source-audit", "source_id": sd.name, "packets": packets},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return model


def audit(staging_dir, raw_path, *, source_type: str, primary_backend: str, strict: bool = False,
          source_id: str = "", input_hash: str = "", mineru_review=None, render_packets=None) -> dict:
    """编排一次双审 → 写 staging/<src>/reconciliation.json，返回报告。

    缓存：reconciliation.json 已存在且 input_hash 一致 → 直接复用（不重跑 MinerU）。
    strict + (MinerU 不可用 | 复读失败) + born-digital PDF → DualAuditUnavailable（fail-closed）。
    `mineru_review(raw_path, out_dir, input_hash) -> review_blocks | None`：可注入（测试 mock）；
    返回 None=不可用，抛异常=复读失败。
    """
    staging_dir = Path(staging_dir)
    recon_path = staging_dir / "reconciliation.json"
    if recon_path.exists() and input_hash:
        try:
            import arbitration as arb  # lazy：arbitration 顶层 import source_audit（防循环）
            cur = json.loads(recon_path.read_text(encoding="utf-8"))
            # 仅当 reconciliation + evidence + queue 三件套齐全才复用缓存；缺 evidence/queue（旧缓存或
            # 被删）→ 缓存失效、重跑 reviewer 重建，绝不出现"有 reconciliation 但没仲裁队列"的状态。
            bundle_ok = ((staging_dir / arb.EVIDENCE_FILE).exists()
                         and (staging_dir / arb.QUEUE_FILE).exists())
            if cur.get("input_hash") == input_hash and bundle_ok:
                return cur
        except Exception:
            pass

    blocks_path = staging_dir / "blocks.jsonl"
    primary_blocks = sa.read_blocks(blocks_path) if blocks_path.exists() else []
    primary_pages = _read_jsonl(staging_dir / "pages.jsonl")

    review_blocks = None
    mineru_status = "not_checked"
    needs_review = source_type in PDF_TYPES and primary_backend == "pymupdf"
    if needs_review:
        runner = mineru_review or _default_mineru_review
        try:
            review_blocks = runner(raw_path, staging_dir / "audit" / "mineru", input_hash)
        except Exception as e:
            if strict:
                raise DualAuditUnavailable(
                    f"strict acceptance requires a MinerU structural review for PDFs, but the "
                    f"review failed: {e}") from e
            mineru_status = "failed"
            review_blocks = None
        else:
            if review_blocks is None:
                if strict:
                    raise DualAuditUnavailable(
                        "strict acceptance requires a MinerU structural review for PDFs, but "
                        "MinerU is unavailable. Install it (python scripts/install_mineru.py) or "
                        "drop --strict (output will be marked degraded / not dual-audited).")
                mineru_status = "unavailable"
            else:
                mineru_status = "used"
    elif primary_backend == "mineru":
        mineru_status = "used"          # MinerU 已作 primary 抽取（扫描/低文本）

    report = reconcile(primary_pages, primary_blocks, review_blocks, source_type=source_type,
                       primary_backend=primary_backend, mineru_status=mineru_status,
                       source_id=source_id or staging_dir.name, input_hash=input_hash)
    staging_dir.mkdir(parents=True, exist_ok=True)
    sa.write_reconciliation(recon_path, report)
    # 证据归一 + 分歧证据包（驱动 agent 自动仲裁）：与 reconciliation 同批确定性产出。
    _write_evidence_and_queue(staging_dir, raw_path, primary_pages, primary_blocks, review_blocks,
                              render_packets=render_packets)
    return report
