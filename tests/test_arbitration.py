"""Arbitration: the deterministic half of the cross-parser → LLM-ready-evidence loop (zero LLM here).

Pure functions over synthetic per-page evidence. The 39/44/50/... real-book pages exist ONLY as a
documented example; this suite binds to NO real page number — it synthesizes "MinerU found a structural
element PyMuPDF missed and never rendered" and proves the mechanism generalizes.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

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


# ---- candidate selection + evidence-risk layer（合成 (pages, blocks, review) → 候选页 [+ 该页 flag]）----
# 决策矩阵：select_candidates 结果 + 可选 assess_risks flag（原风险层测试同时断言两者，合并进矩阵）。
_CANDIDATE_CASES = [
    # cid, pages, blocks, review, expected_candidates, expected_flag(page,name)|None
    ("mineru_found_pymupdf_missed_no_asset",
     [_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)],
     [_pblock("b1", 1, asset="assets/p0001.png", rf=["formula"]),
      _pblock("b2", 2, text="MPL w = MPK r")],
     [_rblock(1, "equation"), _rblock(2, "equation")], [2], None),
    # MinerU 在 PyMuPDF 已 flag 且已渲染的页上检到公式 → 已闭环，不可执行
    ("already_has_route_b_asset_not_candidate",
     [_ppage(1, needs_vision=True, reasons=["formula"])],
     [_pblock("b1", 1, asset="assets/p0001.png", rf=["formula"])], [_rblock(1, "equation")], [], None),
    ("parsers_agree_nothing_not_candidate",
     [_ppage(1)], [_pblock("b1", 1)], [_rblock(1, "text")], [], None),
    # 两边都检到公式但 PyMuPDF 文本碎片化（多短行）→ formula_text_loss(hard) → candidate
    ("formula_text_loss_fragmented",
     [_ppage(1, needs_vision=True, reasons=["formula"])],
     [_pblock("b1", 1, text="MPL\nw\n=\nMPK\nr")], [_rblock(1, "equation")],
     [1], (1, "formula_text_loss")),
    # MinerU 检到表但 source.md 该页只有线性文本 → table_linearization → candidate
    ("table_linearization",
     [_ppage(1)], [_pblock("b1", 1, text="row one row two row three flattened text")],
     [_rblock(1, "table")], [1], (1, "table_linearization")),
    ("figure_missing_asset",
     [_ppage(1)], [_pblock("b1", 1, text="see the figure below")], [_rblock(1, "image")],
     [1], (1, "figure_missing_asset")),
    # 同样图风险但该页已有视觉资产 → 已闭环 → 不进 candidate
    ("hard_risk_with_asset_closed_not_candidate",
     [_ppage(1, needs_vision=True, reasons=["vector-figure"])],
     [_pblock("b1", 1, asset="assets/p0001.png", text="fig")], [_rblock(1, "image")], [], None),
    # blocks 流 page 倒退 → reading_order_risk(soft)：记录但不进 candidate（flag 断言见下方专测）
    ("soft_reading_order_not_candidate",
     [_ppage(1), _ppage(2)],
     [_pblock("b1", 2, text="later page appears first in stream"),
      _pblock("b2", 1, text="earlier page appears second")],
     [_rblock(1, "text"), _rblock(2, "text")], [], None),
]


@pytest.mark.parametrize("cid,pages,blocks,review,expected_candidates,expected_flag", _CANDIDATE_CASES,
                         ids=[c[0] for c in _CANDIDATE_CASES])
def test_select_candidates_and_risk(cid, pages, blocks, review, expected_candidates, expected_flag):
    m = arb.build_evidence_model(pages, blocks, review)
    assert arb.select_candidates(m) == expected_candidates
    if expected_flag is not None:
        page, name = expected_flag
        assert name in arb.assess_risks(pages, blocks, review).get(page, [])


def test_candidate_page_severity_high_and_pending():
    # 候选页 model 内部契约（迁自 test_candidate_is_mineru_found）：severity=high + arbitration=pending。
    m = _model_two_pages()
    assert arb.select_candidates(m) == [2]
    assert m["pages"][2]["severity"] == "high"
    assert m["pages"][2]["arbitration"] == "pending"


def test_soft_reading_order_risk_recorded_not_candidate():
    # blocks 流里 page 倒退 → reading_order_risk（soft）：记录但不进 candidate、不阻断（迁自原测试）。
    pages = [_ppage(1), _ppage(2)]
    blocks = [_pblock("b1", 2, text="later page appears first in stream"),
              _pblock("b2", 1, text="earlier page appears second")]
    review = [_rblock(1, "text"), _rblock(2, "text")]
    flags = arb.assess_risks(pages, blocks, review)
    assert any("reading_order_risk" in v for v in flags.values())
    m = arb.build_evidence_model(pages, blocks, review)
    assert arb.select_candidates(m) == []                        # soft 不进 candidate
    assert m["soft_risk_pages"]                                  # 但被记录


def test_evidence_model_exposes_risk_flags_per_page():
    pages = [_ppage(1)]
    blocks = [_pblock("b1", 1, text="row a row b row c flattened")]
    review = [_rblock(1, "table")]
    m = arb.build_evidence_model(pages, blocks, review)
    assert "table_linearization" in m["pages"][1]["risk_flags"]
    assert m["risk_flags_by_page"].get(1) == m["pages"][1]["risk_flags"]


def test_windows_blockers_covers_generalized_risk_candidate():
    # 泛化候选（table_linearization）未仲裁 → windows_blockers 阻断（闭环走 candidates，自动覆盖新风险）。
    pages = [_ppage(1)]
    blocks = [_pblock("b1", 1, text="row one row two flattened")]
    review = [_rblock(1, "table")]
    m = arb.build_evidence_model(pages, blocks, review)
    assert ("un_arbitrated", 1) in arb.windows_blockers(m, [], blocks)


def test_nonblocking_writes_hard_flag_on_page_with_asset():
    # has_asset=True 的 hard-risk 页（formula_text_loss）不进 candidate，但 hard flag 仍确定性写进 block。
    pages = [_ppage(1, needs_vision=True, reasons=["formula"])]
    blocks = [_pblock("b1", 1, asset="assets/p0001.png", text="MPL\nw\n=\nMPK\nr")]
    review = [_rblock(1, "equation")]
    m = arb.build_evidence_model(pages, blocks, review)
    assert "formula_text_loss" in m["pages"][1]["risk_flags"]
    assert arb.select_candidates(m) == []                     # 已有图 → 已闭环 → 不进 candidate
    nb = arb.apply_nonblocking_risk_flags(blocks, m)
    assert "formula_text_loss" in nb[0]["risk_flags"]         # 但 hard flag 仍写进 block（LLM 知文本不可信）


def test_nonblocking_skips_hard_flag_on_candidate_page():
    # hard ∧ !has_asset 的候选页：flag 不在 nonblocking 写（由 arbitration render 物化，避免绕过仲裁）。
    pages = [_ppage(1)]
    blocks = [_pblock("b1", 1, text="row one row two flattened")]
    review = [_rblock(1, "table")]
    m = arb.build_evidence_model(pages, blocks, review)
    assert 1 in arb.select_candidates(m)                      # !has_asset → candidate
    nb = arb.apply_nonblocking_risk_flags(blocks, m)
    assert "table_linearization" not in (nb[0].get("risk_flags") or [])


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


# check_closure 决策矩阵：(blocks, windows, decisions) → (closed, problem)。
# problem=() 表示断言 problems==[]（严格空）；problem=(kind,page) 断言该问题在列表中；None 不校验 problems。
_CLOSURE_CASES = [
    ("ok_render_materialized_window_carries_asset",
     [_pblock("b1", 1, asset="assets/p0001.png"),
      _pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated", "formula"])],
     [_win("w0", 1, 2, ["b1", "b2"], ["assets/p0001.png", "assets/p0002.png"])],
     _decisions({"page": 2, "decision": "render", "reason": "flattened"}), True, ()),
    ("fails_candidate_unarbitrated",
     [_pblock("b2", 2)], [_win("w0", 1, 2, ["b2"], [])], [], False, ("un_arbitrated", 2)),
    # window does NOT carry the rendered asset → un_materialized
    ("fails_render_not_materialized_in_window",
     [_pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated"])], [_win("w0", 1, 2, ["b2"], [])],
     _decisions({"page": 2, "decision": "render", "reason": "x"}), False, ("un_materialized", 2)),
    ("blocks_on_needs_human",
     [_pblock("b2", 2)], [_win("w0", 1, 2, ["b2"], [])],
     _decisions({"page": 2, "decision": "needs_human", "reason": "ambiguous table/figure"}),
     False, ("needs_human", 2)),
    ("ok_on_ignore_with_reason",
     [_pblock("b2", 2)], [_win("w0", 1, 2, ["b2"], [])],
     _decisions({"page": 2, "decision": "ignore", "reason": "decorative rule line, no content"}),
     True, None),
    ("fails_on_ignore_without_reason",
     [_pblock("b2", 2)], [_win("w0", 1, 2, ["b2"], [])],
     _decisions({"page": 2, "decision": "ignore", "reason": ""}), False, ("ignore_no_reason", 2)),
]


@pytest.mark.parametrize("cid,blocks,windows,decisions,closed,problem", _CLOSURE_CASES,
                         ids=[c[0] for c in _CLOSURE_CASES])
def test_check_closure(cid, blocks, windows, decisions, closed, problem):
    m = _model_two_pages()
    r = arb.check_closure(m, decisions, blocks, windows)
    assert r["closed"] is closed
    if problem == ():
        assert r["problems"] == []
    elif problem is not None:
        assert any(p[0] == problem[0] and p[1] == problem[1] for p in r["problems"])


# ---- pre-windows gate (deterministic fail-closed BEFORE windows are built) ----
# Distinct from check_closure: windows don't exist yet, so a `render` is "ready" once its block carries
# the asset; that the covering window actually lists it is verified post-windows by check_closure.

# windows_blockers 前置门决策矩阵：(model, decisions, blocks) → 精确 blocker 列表。
_WB_CASES = [
    # candidate page 2, 尚无裁决
    ("pending_candidate", _model_two_pages(), [], [], [("un_arbitrated", 2)]),
    # render 已决但 block 还没 asset
    ("render_not_yet_materialized", _model_two_pages(),
     _decisions({"page": 2, "decision": "render", "reason": "x"}), [_pblock("b2", 2)],
     [("un_materialized", 2)]),
    # block 带 asset → 可安全构窗
    ("render_materialized_into_block", _model_two_pages(),
     _decisions({"page": 2, "decision": "render", "reason": "x"}),
     [_pblock("b2", 2, asset="assets/p0002.png", rf=["arbitrated"])], []),
    ("needs_human", _model_two_pages(),
     _decisions({"page": 2, "decision": "needs_human", "reason": "amb"}), [], [("needs_human", 2)]),
    ("reasonless_ignore", _model_two_pages(),
     _decisions({"page": 2, "decision": "ignore", "reason": ""}), [], [("ignore_no_reason", 2)]),
    ("empty_when_no_candidates",
     arb.build_evidence_model([_ppage(1)], [_pblock("b1", 1)], [_rblock(1, "text")]),
     [], [_pblock("b1", 1)], []),
]


@pytest.mark.parametrize("cid,model,decisions,blocks,expected", _WB_CASES,
                         ids=[c[0] for c in _WB_CASES])
def test_windows_blockers_pre_gate(cid, model, decisions, blocks, expected):
    assert arb.windows_blockers(model, decisions, blocks) == expected


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
