"""Arbitration — close the cross-parser disagreement loop into the LLM-ready evidence bundle (zero LLM here).

This is the DETERMINISTIC half. PyMuPDF (fast, leaky thresholds) and MinerU (deep structural review) are
both evidence sources, neither authoritative. Where MinerU finds a formula/table/figure on a page PyMuPDF
never flagged for vision (so no source image exists), that page is an *actionable disagreement candidate*:
its content may reach the next-stage LLM flattened/lossy. The candidate is selected purely from signals —
**no page number is ever hard-coded** (real-book pages 39/44/... live only in tests as examples).

Flow (the CLI / agent split):
- CLI: build the per-page evidence model → select candidates → build minimal packets (the agent reads these)
  → after the agent writes structured decisions, materialize them (render/reflag) → gate closure. Never calls an LLM.
- Agent (in the source-preflight/ingest skill flow): auto-reads packets, writes structured decisions only.

These are pure functions; IO (rendering PNGs, writing artifacts) is done by pipeline.py with the plans here.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_audit  # reuse the per-page structural signal helpers + PDF_TYPES
import thresholds     # fragmentation / risk thresholds (env-overridable)

__all__ = ["build_evidence_model", "select_candidates", "build_packets", "materialize_blocks",
           "materialize_pages", "render_pages", "check_closure", "windows_blockers", "assess_risks",
           "asset_rel", "EVIDENCE_FILE", "QUEUE_FILE", "DECISIONS_FILE", "AUDIT_FILE", "ARB_DIR",
           "apply_nonblocking_risk_flags",
           "RENDER", "IGNORE", "NEEDS_HUMAN", "PENDING", "HARD_RISKS", "SOFT_RISKS",
           "FORMULA_TEXT_LOSS", "FORMULA_UNDETECTED", "TABLE_LINEARIZATION", "FIGURE_MISSING_ASSET",
           "READING_ORDER_RISK", "HEADING_STRUCTURE_RISK"]

EVIDENCE_FILE = "evidence.json"
ARB_DIR = "arbitration"
QUEUE_FILE = "arbitration/queue.json"
DECISIONS_FILE = "arbitration/decisions.json"
AUDIT_FILE = "arbitration/audit.jsonl"

PENDING, RENDER, IGNORE, NEEDS_HUMAN = "pending", "render", "ignore", "needs_human"
_STRUCT_KINDS = ("table", "figure", "formula")

# 证据风险类型（②③）：hard = source.md 该页文本不可信且无视觉资产时进 candidate（需仲裁/补图）；
# soft = 只确定性记录 risk_flags 供 ingest LLM 知情，不进 candidate、不阻断 strict。
FORMULA_TEXT_LOSS = "formula_text_loss"          # 两边都检到公式，但 PyMuPDF 文本碎片化
FORMULA_UNDETECTED = "formula_undetected"        # MinerU 检到公式，PyMuPDF 没标（漏检）
TABLE_LINEARIZATION = "table_linearization"      # MinerU 有表，source.md 该页只有线性文本
FIGURE_MISSING_ASSET = "figure_missing_asset"    # MinerU 有图/图表，该页无视觉资产
READING_ORDER_RISK = "reading_order_risk"        # block 流里 page 序倒退（soft）
HEADING_STRUCTURE_RISK = "heading_structure_risk"  # 同一标题跨页断裂（soft）
HARD_RISKS = (FORMULA_TEXT_LOSS, FORMULA_UNDETECTED, TABLE_LINEARIZATION, FIGURE_MISSING_ASSET)
SOFT_RISKS = (READING_ORDER_RISK, HEADING_STRUCTURE_RISK)


def asset_rel(page: int) -> str:
    return f"assets/p{int(page):04d}.png"


def _is_fragmented(text: str) -> bool:
    """PyMuPDF 抽取文本是否碎片化（多短行）——公式被拆成 `MPL\\nw\\n=\\n…` 的确定性信号（纯函数）。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < thresholds.FRAGMENT_MIN_LINES:
        return False
    short = sum(1 for ln in lines if len(ln) <= thresholds.FRAGMENT_SHORTLINE_LEN)
    return short / len(lines) >= thresholds.FRAGMENT_SHORTLINE_RATIO


def assess_risks(primary_pages, primary_blocks, review_blocks) -> dict:
    """逐页确定性证据风险标记（zero-LLM 纯函数）→ {page: [risk_flag,...]}（排序、仅命中页）。

    hard（formula_text_loss / formula_undetected / table_linearization / figure_missing_asset）：source.md
    该页文本不可信。soft（reading_order_risk / heading_structure_risk）：只记录。复用 source_audit 的 presence
    信号 + block 文本/类型/顺序启发式；不重写 source.md、不硬编码任何页码。
    """
    psig = source_audit._primary_page_signals(primary_pages)
    rsig = source_audit._review_page_signals(review_blocks)
    blocks = list(primary_blocks or [])
    text_by_page, type_by_page, asset_pages = {}, {}, set()
    for b in blocks:
        pg = int(b.get("page", 0))
        text_by_page[pg] = text_by_page.get(pg, "") + "\n" + (b.get("text") or "")
        type_by_page.setdefault(pg, set()).add((b.get("type") or "").lower())
        if b.get("asset_path"):
            asset_pages.add(pg)
    flags: dict = {}

    def add(pg, f):
        fs = flags.setdefault(int(pg), [])
        if f not in fs:
            fs.append(f)

    for pg in set(psig) | set(rsig) | set(text_by_page):
        p, r = psig.get(pg, {}), rsig.get(pg, {})
        has_asset = pg in asset_pages
        txt, types = text_by_page.get(pg, ""), type_by_page.get(pg, set())
        if r.get("formula"):
            if p.get("formula"):
                if _is_fragmented(txt):
                    add(pg, FORMULA_TEXT_LOSS)        # 两边都检到，但文本抽碎
            else:
                add(pg, FORMULA_UNDETECTED)           # MinerU 检到、PyMuPDF 漏检
        if r.get("table") and not p.get("table") and "table" not in types and "|" not in txt:
            add(pg, TABLE_LINEARIZATION)              # MinerU 有表、PyMuPDF 没认知 → source.md 线性化
        if r.get("figure") and not has_asset:
            add(pg, FIGURE_MISSING_ASSET)             # 有图但无视觉资产
    # soft：reading_order —— block 流里相邻块 page 倒退。
    for a, b in zip(blocks, blocks[1:]):
        pa, pb = int(a.get("page", 0)), int(b.get("page", 0))
        if pa > pb:
            add(pa, READING_ORDER_RISK)
            add(pb, READING_ORDER_RISK)
    # soft：heading_structure —— 同一非空 heading_path 跨页断裂（隔页重现）。
    hp_last_page: dict = {}
    for b in blocks:
        hp = b.get("heading_path") or ""
        if not hp:
            continue
        pg = int(b.get("page", 0))
        if hp in hp_last_page and pg - hp_last_page[hp] > 1:
            add(pg, HEADING_STRUCTURE_RISK)
        hp_last_page[hp] = pg
    return {pg: sorted(fs) for pg, fs in flags.items()}


def build_evidence_model(primary_pages, primary_blocks, review_blocks) -> dict:
    """Per-page unified evidence + the sets the next stage needs (pure).

    candidate(page) ⇔ the page carries a HARD evidence risk (assess_risks) AND has no route-B visual asset —
    a generalization of the old "MinerU found structure PyMuPDF missed". `has_asset` is the real closure
    signal (a needs_vision page normally already rendered → has_asset → closed). Empty `candidates` ⇒ nothing
    to arbitrate (no LLM call). soft-risk pages are recorded (NOT candidates). `final_hard_pages` starts as
    the initial PyMuPDF set; arbitration-rendered pages are added at apply time (materialize_*).
    """
    psig = source_audit._primary_page_signals(primary_pages)
    rsig = source_audit._review_page_signals(review_blocks)
    risk_by_page = assess_risks(primary_pages, primary_blocks, review_blocks)
    reasons_by_page, asset_by_page = {}, {}
    for p in primary_pages or []:
        reasons_by_page[int(p.get("page", 0))] = list(p.get("needs_vision_reason") or [])
    for b in primary_blocks or []:
        if b.get("asset_path"):
            asset_by_page[int(b.get("page", 0))] = True

    all_pages = sorted(set(psig) | set(rsig) | set(asset_by_page) | set(risk_by_page)
                       | {int(b.get("page", 0)) for b in (primary_blocks or [])})
    pages: dict = {}
    for pg in all_pages:
        p = psig.get(pg, {"table": False, "figure": False, "formula": False, "needs_vision": False})
        r = rsig.get(pg, {"table": False, "figure": False, "formula": False})
        has_asset = asset_by_page.get(pg, False)
        page_risks = risk_by_page.get(pg, [])
        hard = [f for f in page_risks if f in HARD_RISKS]
        disagreement = [{"kind": f"{k}_presence", "primary": bool(p.get(k)), "review": bool(r.get(k))}
                        for k in _STRUCT_KINDS if bool(p.get(k)) != bool(r.get(k))]
        is_candidate = bool(hard) and not has_asset
        pages[pg] = {
            "pymupdf": {"needs_vision": bool(p.get("needs_vision")), "reasons": reasons_by_page.get(pg, []),
                        "has_route_b_asset": has_asset, "has_table": bool(p.get("table")),
                        "has_figure": bool(p.get("figure")), "has_formula": bool(p.get("formula"))},
            "mineru": {"has_table": bool(r.get("table")), "has_figure": bool(r.get("figure")),
                       "has_formula": bool(r.get("formula"))},
            "disagreement": disagreement,
            "risk_flags": page_risks,
            "severity": "high" if is_candidate else "low",
            "candidate": is_candidate,
            "arbitration": PENDING if is_candidate else None,
            "resolution": None,
        }
    initial = sorted(pg for pg, v in pages.items() if v["pymupdf"]["needs_vision"])
    reviewer_structural = sorted(pg for pg in pages
                                 if any(pages[pg]["mineru"][f"has_{k}"] for k in _STRUCT_KINDS))
    candidates = sorted(pg for pg, v in pages.items() if v["candidate"])
    soft_risk_pages = sorted(pg for pg, fs in risk_by_page.items()
                             if any(f in SOFT_RISKS for f in fs))
    return {"pages": pages, "initial_needs_vision": initial, "reviewer_structural": reviewer_structural,
            "candidates": candidates, "soft_risk_pages": soft_risk_pages,
            "risk_flags_by_page": {pg: fs for pg, fs in risk_by_page.items()},
            "final_hard_pages": sorted(set(initial))}


def select_candidates(model: dict) -> list:
    return list(model.get("candidates", []))


def build_packets(model: dict, *, page_text, image_dir: str = ARB_DIR) -> list:
    """Minimal evidence packet per candidate (what the agent arbitrates). `page_text(page)->str` injected."""
    packets = []
    for pg in model.get("candidates", []):
        v = model["pages"][pg]
        kinds = [d["kind"] for d in v["disagreement"] if d["review"] and not d["primary"]]
        mineru = {"tables": int(v["mineru"]["has_table"]), "equations": int(v["mineru"]["has_formula"]),
                  "images": int(v["mineru"]["has_figure"])}
        packets.append({
            "page": pg, "disagreement_kinds": kinds, "risk_flags": v.get("risk_flags", []),
            "pymupdf_text_excerpt": (page_text(pg) or "")[:600],
            "mineru_structural": mineru, "page_image": f"{image_dir}/p{pg:04d}.png",
            "pymupdf_needs_vision": v["pymupdf"]["needs_vision"],
            "has_route_b_asset": v["pymupdf"]["has_route_b_asset"],
        })
    return packets


def render_pages(decisions) -> list:
    return [int(d["page"]) for d in (decisions or []) if d.get("decision") == RENDER]


def materialize_blocks(blocks, decisions) -> list:
    """Set asset_path + risk_flags on the block of each `render` page (idempotent, pure)."""
    rp = {int(d["page"]): d for d in (decisions or []) if d.get("decision") == RENDER}
    out = []
    for b in blocks:
        b = dict(b)
        pg = int(b.get("page", 0))
        if pg in rp:
            b["asset_path"] = asset_rel(pg)
            flags = list(b.get("risk_flags") or [])
            for f in ["arbitrated"] + list(rp[pg].get("risk_flags") or []):
                if f not in flags:
                    flags.append(f)
            b["risk_flags"] = flags
        out.append(b)
    return out


def materialize_pages(pages, decisions) -> list:
    """Set needs_vision + an `arbitrated` reason on each `render` page (idempotent, pure)."""
    rp = set(render_pages(decisions))
    out = []
    for p in pages:
        p = dict(p)
        if int(p.get("page", 0)) in rp:
            p["needs_vision"] = True
            reasons = list(p.get("needs_vision_reason") or [])
            if not any("arbitrated" in r for r in reasons):
                reasons.append("arbitrated")
            p["needs_vision_reason"] = reasons
        out.append(p)
    return out


def windows_blockers(model: dict, decisions, blocks) -> list:
    """Pre-windows fail-closed gate (pure): may we build windows yet? Returns problem tuples; [] ⇒ safe.

    Distinct from `check_closure`: windows do NOT exist yet, so a `render` is "ready" once its block
    carries the asset (materialized by `arbitration-apply`). That the covering window actually lists the
    asset is verified post-windows by `check_closure` / `check_evidence_bundle`. A non-empty result means
    a candidate is un-arbitrated, a `render` is not yet materialized, a `needs_human` is pending, or an
    `ignore` lacks a reason — any of which must block windows so the next LLM never reads an un-closed page.
    """
    by_page = {int(d["page"]): d for d in (decisions or [])}
    asset_pages = {int(b.get("page", 0)) for b in (blocks or []) if b.get("asset_path")}
    problems = []
    for pg in model.get("candidates", []):
        d = by_page.get(pg)
        if not d:
            problems.append(("un_arbitrated", pg))
            continue
        dec = d.get("decision")
        if dec == RENDER:
            if pg not in asset_pages:
                problems.append(("un_materialized", pg))
        elif dec == IGNORE:
            if not (d.get("reason") or "").strip():
                problems.append(("ignore_no_reason", pg))
        elif dec == NEEDS_HUMAN:
            problems.append(("needs_human", pg))
        else:
            problems.append(("unknown_decision", pg))
    return problems


def apply_nonblocking_risk_flags(blocks, model) -> list:
    """把 evidence model 的**非阻断** risk_flags 确定性合并进 block.risk_flags（幂等、纯函数、zero-LLM）。

    非阻断 = soft risk（reading_order / heading_structure，任何页）∪ hard risk 但该页已有视觉资产
    （has_asset → 已闭环、不进 candidate）。让 ingest LLM 经 window 的最小标签知道"这页 source.md 文本
    不可信、需看图"，但不阻断（证据已闭环或本就不要求补图）。candidate（hard ∧ !has_asset）的 flag 由
    arbitration `render` 物化时写，不在这里（避免绕过仲裁）。不改 source.md、不物化 MinerU 结构块。
    `_attach_block_meta` 会把 block.risk_flags 自动并入 window.risk_flags。
    """
    by_page = model.get("risk_flags_by_page", {}) or {}
    pages = model.get("pages", {}) or {}
    out = []
    for b in blocks:
        b = dict(b)
        pg = int(b.get("page", 0))
        page_flags = by_page.get(pg) or by_page.get(str(pg)) or []
        pinfo = pages.get(pg) or pages.get(str(pg)) or {}
        has_asset = bool((pinfo.get("pymupdf") or {}).get("has_route_b_asset")) or bool(b.get("asset_path"))
        nonblocking = [f for f in page_flags
                       if f in SOFT_RISKS or (f in HARD_RISKS and has_asset)]
        if nonblocking:
            flags = list(b.get("risk_flags") or [])
            for f in nonblocking:
                if f not in flags:
                    flags.append(f)
            b["risk_flags"] = flags
        out.append(b)
    return out


def check_closure(model: dict, decisions, blocks, windows) -> dict:
    """Has every actionable disagreement been closed into the windows the next LLM reads? (pure)

    Per candidate page: must have a decision; `render` must be materialized (block.asset_path set AND a
    covering window carries it); `needs_human` blocks; `ignore` needs a reason. Returns {closed, problems}.
    """
    by_page = {int(d["page"]): d for d in (decisions or [])}
    blk_by_page: dict = {}
    for b in blocks or []:
        blk_by_page.setdefault(int(b.get("page", 0)), []).append(b)
    problems = []
    for pg in model.get("candidates", []):
        d = by_page.get(pg)
        if not d:
            problems.append(("un_arbitrated", pg))
            continue
        dec = d.get("decision")
        if dec == RENDER:
            blks = blk_by_page.get(pg, [])
            has_asset = any(b.get("asset_path") for b in blks)
            covering = [w for w in (windows or [])
                        if int(w.get("page_start", 0)) <= pg <= int(w.get("page_end", 0))]
            wanted = {asset_rel(pg)} | {b.get("asset_path") for b in blks if b.get("asset_path")}
            carried = any(set(w.get("assets") or []) & wanted for w in covering)
            if not (has_asset and covering and carried):
                problems.append(("un_materialized", pg))
        elif dec == IGNORE:
            if not (d.get("reason") or "").strip():
                problems.append(("ignore_no_reason", pg))
        elif dec == NEEDS_HUMAN:
            problems.append(("needs_human", pg))
        else:
            problems.append(("unknown_decision", pg))
    return {"closed": not problems, "problems": problems}
