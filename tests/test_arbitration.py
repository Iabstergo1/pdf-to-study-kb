"""Arbitration: the deterministic half of the cross-parser → LLM-ready-evidence loop (zero LLM here).

Pure functions over synthetic per-page evidence. The 39/44/50/... real-book pages exist ONLY as a
documented example; this suite binds to NO real page number — it synthesizes "MinerU found a structural
element PyMuPDF missed and never rendered" and proves the mechanism generalizes.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


arb = _load("arbitration")


# ---- synthetic staging signals ----

def _ppage(page, *, needs_vision=False, reasons=None):
    return {"page": page, "needs_vision": needs_vision, "needs_vision_reason": reasons or []}


def _pblock(bid, page, *, asset=None, rf=None, text="x"):
    return {"block_id": bid, "type": "text", "text": text, "page": page,
            "char_start": 0, "char_end": 1, "source_ref": f"p{page:04d}#{bid}",
            "chapter_id": "", "asset_path": asset, "risk_flags": rf or []}


def _rblock(page, typ):
    return {"block_id": f"r{page}", "type": typ, "text": typ, "page": page}


def _model_two_pages():
    # page 1: PyMuPDF flagged formula + already has a route-B asset (agreement, not a candidate)
    # page 2: PyMuPDF did NOT flag, MinerU found a formula, no asset → the actionable candidate
    primary_pages = [_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)]
    primary_blocks = [_pblock("b1", 1, asset="assets/p0001.png", rf=["formula"]),
                      _pblock("b2", 2, text="MPL w = MPK r")]
    review_blocks = [_rblock(1, "equation"), _rblock(2, "equation")]
    return arb.build_evidence_model(primary_pages, primary_blocks, review_blocks)


# ---- build_evidence_model + candidate selection ----

def test_evidence_model_per_page_and_sets():
    m = _model_two_pages()
    assert set(m["pages"]) == {1, 2}
    assert m["pages"][1]["pymupdf"]["needs_vision"] is True
    assert m["pages"][1]["pymupdf"]["has_route_b_asset"] is True
    assert m["pages"][2]["mineru"]["has_formula"] is True
    assert m["pages"][2]["pymupdf"]["needs_vision"] is False
    assert m["initial_needs_vision"] == [1]
    assert 2 in m["reviewer_structural"]
    # final_hard_pages = initial ∪ arbitration-rendered; before arbitration only the initial set
    assert m["final_hard_pages"] == [1]


def test_candidate_is_mineru_found_pymupdf_missed_no_asset():
    m = _model_two_pages()
    assert arb.select_candidates(m) == [2]            # page 2 only
    assert m["pages"][2]["severity"] == "high"
    assert m["pages"][2]["arbitration"] == "pending"


def test_no_candidate_when_already_has_route_b_asset():
    # MinerU finds a formula on a page PyMuPDF flagged AND already rendered → not actionable.
    primary_pages = [_ppage(1, needs_vision=True, reasons=["formula"])]
    primary_blocks = [_pblock("b1", 1, asset="assets/p0001.png", rf=["formula"])]
    m = arb.build_evidence_model(primary_pages, primary_blocks, [_rblock(1, "equation")])
    assert arb.select_candidates(m) == []


def test_no_candidate_when_parsers_agree_nothing():
    primary_pages = [_ppage(1)]
    primary_blocks = [_pblock("b1", 1)]
    m = arb.build_evidence_model(primary_pages, primary_blocks, [_rblock(1, "text")])
    assert arb.select_candidates(m) == []


# ---- packet build ----

def test_build_packets_minimal_evidence():
    m = _model_two_pages()
    packets = arb.build_packets(m, page_text=lambda p: "MPL w = MPK r" if p == 2 else "")
    assert len(packets) == 1
    pk = packets[0]
    assert pk["page"] == 2
    assert "formula_presence" in pk["disagreement_kinds"]
    assert pk["pymupdf_text_excerpt"] == "MPL w = MPK r"
    assert pk["mineru_structural"]["equations"] >= 1
    assert pk["page_image"] == "arbitration/p0002.png"
    assert pk["pymupdf_needs_vision"] is False and pk["has_route_b_asset"] is False


# ---- materialization (pure: plan the block/page mutations) ----

def _decisions(*decs):
    return list(decs)


def test_materialize_blocks_render_sets_asset_and_flags():
    blocks = [_pblock("b1", 1, asset="assets/p0001.png", rf=["formula"]), _pblock("b2", 2)]
    decs = _decisions({"page": 2, "decision": "render", "risk_flags": ["formula"], "reason": "flattened fraction"})
    out = arb.materialize_blocks(blocks, decs)
    b2 = next(b for b in out if b["page"] == 2)
    assert b2["asset_path"] == "assets/p0002.png"
    assert "arbitrated" in b2["risk_flags"] and "formula" in b2["risk_flags"]
    # idempotent
    assert arb.materialize_blocks(out, decs) == out
    # non-render page untouched
    assert next(b for b in out if b["page"] == 1)["asset_path"] == "assets/p0001.png"


def test_materialize_pages_render_sets_needs_vision():
    pages = [_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)]
    decs = _decisions({"page": 2, "decision": "render", "risk_flags": ["formula"], "reason": "r"})
    out = arb.materialize_pages(pages, decs)
    p2 = next(p for p in out if p["page"] == 2)
    assert p2["needs_vision"] is True and any("arbitrated" in r for r in p2["needs_vision_reason"])


def test_render_pages_helper():
    decs = _decisions({"page": 2, "decision": "render", "reason": "r"},
                      {"page": 5, "decision": "ignore", "reason": "decorative"},
                      {"page": 7, "decision": "needs_human", "reason": "ambiguous"})
    assert arb.render_pages(decs) == [2]


# ---- closure gate (the strict "did the disagreement close into the windows" check) ----

def _win(wid, ps, pe, block_ids, assets):
    return {"window_id": wid, "mode": "blocks", "page_start": ps, "page_end": pe,
            "block_ids": block_ids, "assets": assets}


def test_closure_ok_when_render_materialized_and_window_carries_asset():
    m = _model_two_pages()
    blocks = [_pblock("b1", 1, asset="assets/p0001.png"),
              _pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated", "formula"])]
    windows = [_win("w0", 1, 2, ["b1", "b2"], ["assets/p0001.png", "assets/p0002.png"])]
    decs = _decisions({"page": 2, "decision": "render", "reason": "flattened"})
    r = arb.check_closure(m, decs, blocks, windows)
    assert r["closed"] is True and r["problems"] == []


def test_closure_fails_when_candidate_unarbitrated():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2)]
    windows = [_win("w0", 1, 2, ["b2"], [])]
    r = arb.check_closure(m, [], blocks, windows)        # no decision for candidate page 2
    assert r["closed"] is False
    assert any(p[0] == "un_arbitrated" and p[1] == 2 for p in r["problems"])


def test_closure_fails_when_render_not_materialized_in_window():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated"])]
    windows = [_win("w0", 1, 2, ["b2"], [])]              # window does NOT carry the asset
    decs = _decisions({"page": 2, "decision": "render", "reason": "x"})
    r = arb.check_closure(m, decs, blocks, windows)
    assert r["closed"] is False
    assert any(p[0] == "un_materialized" and p[1] == 2 for p in r["problems"])


def test_closure_blocks_on_needs_human():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2)]
    windows = [_win("w0", 1, 2, ["b2"], [])]
    decs = _decisions({"page": 2, "decision": "needs_human", "reason": "ambiguous table/figure"})
    r = arb.check_closure(m, decs, blocks, windows)
    assert r["closed"] is False
    assert any(p[0] == "needs_human" and p[1] == 2 for p in r["problems"])


def test_closure_ok_on_ignore_with_reason():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2)]
    windows = [_win("w0", 1, 2, ["b2"], [])]
    decs = _decisions({"page": 2, "decision": "ignore", "reason": "decorative rule line, no content"})
    r = arb.check_closure(m, decs, blocks, windows)
    assert r["closed"] is True


def test_closure_fails_on_ignore_without_reason():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2)]
    windows = [_win("w0", 1, 2, ["b2"], [])]
    decs = _decisions({"page": 2, "decision": "ignore", "reason": ""})
    r = arb.check_closure(m, decs, blocks, windows)
    assert r["closed"] is False
    assert any(p[0] == "ignore_no_reason" and p[1] == 2 for p in r["problems"])


# ---- pre-windows gate (deterministic fail-closed BEFORE windows are built) ----
# Distinct from check_closure: windows don't exist yet, so a `render` is "ready" once its block carries
# the asset; that the covering window actually lists it is verified post-windows by check_closure.

def test_windows_blockers_flags_pending_candidate():
    m = _model_two_pages()                               # candidate page 2, no decision yet
    assert arb.windows_blockers(m, [], []) == [("un_arbitrated", 2)]


def test_windows_blockers_flags_render_not_yet_materialized():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2)]                           # render decided but block has no asset yet
    decs = _decisions({"page": 2, "decision": "render", "reason": "x"})
    assert arb.windows_blockers(m, decs, blocks) == [("un_materialized", 2)]


def test_windows_blockers_ok_when_render_materialized_into_block():
    m = _model_two_pages()
    blocks = [_pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated"])]
    decs = _decisions({"page": 2, "decision": "render", "reason": "x"})
    assert arb.windows_blockers(m, decs, blocks) == []   # block carries asset → safe to build windows


def test_windows_blockers_blocks_needs_human_and_reasonless_ignore():
    m = _model_two_pages()
    assert arb.windows_blockers(m, _decisions({"page": 2, "decision": "needs_human", "reason": "amb"}), []) \
        == [("needs_human", 2)]
    assert arb.windows_blockers(m, _decisions({"page": 2, "decision": "ignore", "reason": ""}), []) \
        == [("ignore_no_reason", 2)]


def test_windows_blockers_empty_when_no_candidates():
    primary_pages = [_ppage(1)]
    primary_blocks = [_pblock("b1", 1)]
    m = arb.build_evidence_model(primary_pages, primary_blocks, [_rblock(1, "text")])
    assert arb.windows_blockers(m, [], primary_blocks) == []


# ---- end-to-end data loop: a render decision must close into the windows ingest reads ----

def test_end_to_end_render_closes_loop_into_window():
    windowing = _load("windowing")
    # page 1: PyMuPDF flagged + has asset; page 2: MinerU-found formula PyMuPDF missed (the candidate).
    primary_pages = [_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)]
    md1 = "<!-- page 1 -->\n\nintro\n"
    md = md1 + "<!-- page 2 -->\n\nMPL w = MPK r\n"
    primary_blocks = [
        {"block_id": "b1", "type": "text", "text": "intro", "page": 1, "char_start": 0,
         "char_end": len(md1), "heading_path": "", "asset_path": "assets/p0001.png",
         "risk_flags": ["formula"], "chapter_id": "", "source_ref": "p0001#b1"},
        {"block_id": "b2", "type": "text", "text": "MPL w = MPK r", "page": 2, "char_start": len(md1),
         "char_end": len(md), "heading_path": "", "asset_path": None, "risk_flags": [],
         "chapter_id": "", "source_ref": "p0002#b2"},
    ]
    model = arb.build_evidence_model(primary_pages, primary_blocks, [_rblock(1, "equation"), _rblock(2, "equation")])
    assert arb.select_candidates(model) == [2]
    # the agent decides render → deterministic materialization → windows built AFTER carry the asset
    decisions = [{"page": 2, "decision": "render", "risk_flags": ["formula"], "reason": "flattened fraction"}]
    mblocks = arb.materialize_blocks(primary_blocks, decisions)
    ws = windowing.build_windows_from_blocks(mblocks, target_tokens=1000, max_tokens=2000, overlap_tokens=0)
    covering = [w for w in ws if w["page_start"] <= 2 <= w["page_end"]]
    assert covering and any("assets/p0002.png" in (w["assets"] or []) for w in covering)
    assert arb.check_closure(model, decisions, mblocks, ws)["closed"] is True
