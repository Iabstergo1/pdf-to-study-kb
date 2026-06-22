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

__all__ = ["build_evidence_model", "select_candidates", "build_packets", "materialize_blocks",
           "materialize_pages", "render_pages", "check_closure", "windows_blockers", "asset_rel",
           "EVIDENCE_FILE", "QUEUE_FILE", "DECISIONS_FILE", "AUDIT_FILE", "ARB_DIR",
           "RENDER", "IGNORE", "NEEDS_HUMAN", "PENDING"]

EVIDENCE_FILE = "evidence.json"
ARB_DIR = "arbitration"
QUEUE_FILE = "arbitration/queue.json"
DECISIONS_FILE = "arbitration/decisions.json"
AUDIT_FILE = "arbitration/audit.jsonl"

PENDING, RENDER, IGNORE, NEEDS_HUMAN = "pending", "render", "ignore", "needs_human"
_STRUCT_KINDS = ("table", "figure", "formula")


def asset_rel(page: int) -> str:
    return f"assets/p{int(page):04d}.png"


def build_evidence_model(primary_pages, primary_blocks, review_blocks) -> dict:
    """Per-page unified evidence + the three sets the next stage needs (pure).

    candidate(page) ⇔ MinerU found a structural kind PyMuPDF did NOT flag needs_vision AND no route-B asset.
    Empty `candidates` ⇒ nothing to arbitrate (no LLM call). `final_hard_pages` starts as the initial
    PyMuPDF set; arbitration-rendered pages are added at apply time (materialize_*).
    """
    psig = source_audit._primary_page_signals(primary_pages)
    rsig = source_audit._review_page_signals(review_blocks)
    reasons_by_page, asset_by_page = {}, {}
    for p in primary_pages or []:
        reasons_by_page[int(p.get("page", 0))] = list(p.get("needs_vision_reason") or [])
    for b in primary_blocks or []:
        if b.get("asset_path"):
            asset_by_page[int(b.get("page", 0))] = True

    all_pages = sorted(set(psig) | set(rsig) | set(asset_by_page)
                       | {int(b.get("page", 0)) for b in (primary_blocks or [])})
    pages: dict = {}
    for pg in all_pages:
        p = psig.get(pg, {"table": False, "figure": False, "formula": False, "needs_vision": False})
        r = rsig.get(pg, {"table": False, "figure": False, "formula": False})
        has_asset = asset_by_page.get(pg, False)
        disagreement = [{"kind": f"{k}_presence", "primary": bool(p.get(k)), "review": bool(r.get(k))}
                        for k in _STRUCT_KINDS if bool(p.get(k)) != bool(r.get(k))]
        mineru_extra = any(r.get(k) and not p.get(k) for k in _STRUCT_KINDS)
        is_candidate = bool(mineru_extra and not p.get("needs_vision") and not has_asset)
        pages[pg] = {
            "pymupdf": {"needs_vision": bool(p.get("needs_vision")), "reasons": reasons_by_page.get(pg, []),
                        "has_route_b_asset": has_asset, "has_table": bool(p.get("table")),
                        "has_figure": bool(p.get("figure")), "has_formula": bool(p.get("formula"))},
            "mineru": {"has_table": bool(r.get("table")), "has_figure": bool(r.get("figure")),
                       "has_formula": bool(r.get("formula"))},
            "disagreement": disagreement,
            "severity": "high" if is_candidate else "low",
            "candidate": is_candidate,
            "arbitration": PENDING if is_candidate else None,
            "resolution": None,
        }
    initial = sorted(pg for pg, v in pages.items() if v["pymupdf"]["needs_vision"])
    reviewer_structural = sorted(pg for pg in pages
                                 if any(pages[pg]["mineru"][f"has_{k}"] for k in _STRUCT_KINDS))
    candidates = sorted(pg for pg, v in pages.items() if v["candidate"])
    return {"pages": pages, "initial_needs_vision": initial, "reviewer_structural": reviewer_structural,
            "candidates": candidates, "final_hard_pages": sorted(set(initial))}


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
            "page": pg, "disagreement_kinds": kinds,
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
