"""source-audit：PyMuPDF（primary）× MinerU（structural reviewer）确定性互检层。

reconcile() 是纯函数（合成 blocks/pages）；audit() 编排（注入 mineru_review 以 mock MinerU，
不依赖真实 MinerU 安装 → 套件快且可移植）。覆盖：cross-check 一致/分歧、页数不一致、
born-digital 无审降级、strict fail-closed、scanned(mineru-primary) 双审、markdown N/A、缓存跳过。
"""
import importlib.util
import json
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


sa_audit = _load("source_audit")


# ---- 合成 helpers ----

def _pblock(bid, page):
    return {"block_id": bid, "type": "text", "text": "x", "page": page,
            "char_start": 0, "char_end": 1, "source_ref": f"p{page:04d}#{bid}", "chapter_id": ""}


def _ppage(page, *, needs_vision=False, reasons=None):
    return {"page": page, "needs_vision": needs_vision, "needs_vision_reason": reasons or []}


def _rblock(page, typ):
    return {"block_id": f"r{page}", "type": typ, "text": typ, "page": page}


def _staging(tmp_path, *, blocks, pages):
    d = tmp_path / "staging" / "s"
    d.mkdir(parents=True, exist_ok=True)
    (d / "blocks.jsonl").write_text(
        "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks), encoding="utf-8")
    (d / "pages.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pages), encoding="utf-8")
    return d


# ---- reconcile（纯函数） ----

def test_reconcile_cross_checked_agreement():
    pages = [_ppage(1), _ppage(2, needs_vision=True, reasons=["table"])]
    blocks = [_pblock("b1", 1), _pblock("b2", 2)]
    review = [_rblock(1, "text"), _rblock(2, "table")]   # MinerU 在 p2 也见表 → 一致
    rep = sa_audit.reconcile(pages, blocks, review, source_type="native_pdf",
                             primary_backend="pymupdf", mineru_status="used")
    assert rep["review_status"] == "cross_checked"
    assert rep["dual_audited"] is True and rep["degraded"] is False
    assert rep["production_accepted"] is True
    assert rep["review_backend"] == "mineru"
    assert 2 in rep["pages_cross_checked"]
    assert not any(d["kind"] == "table_presence" and d["page"] == 2 for d in rep["disagreements"])
    assert rep["agreements"] >= 1


def test_reconcile_reports_table_disagreement():
    # PyMuPDF 在 p2 判 table（宽阈值），MinerU 复读没见表 → 记录分歧（互检的价值），但仍算双审过。
    pages = [_ppage(1), _ppage(2, needs_vision=True, reasons=["table"])]
    blocks = [_pblock("b1", 1), _pblock("b2", 2)]
    review = [_rblock(1, "text"), _rblock(2, "text")]
    rep = sa_audit.reconcile(pages, blocks, review, source_type="native_pdf",
                             primary_backend="pymupdf", mineru_status="used")
    assert any(d["kind"] == "table_presence" and d["page"] == 2 for d in rep["disagreements"])
    assert rep["dual_audited"] is True            # 互检发生了，只是有分歧（warn 级，不阻断接受）


def test_reconcile_page_count_mismatch_recorded():
    blocks = [_pblock("b1", 1), _pblock("b2", 2), _pblock("b3", 3)]
    review = [_rblock(1, "text")]                 # 复读只有 1 页 → 页数严重不一致
    rep = sa_audit.reconcile([], blocks, review, source_type="native_pdf",
                             primary_backend="pymupdf", mineru_status="used")
    assert any(d["kind"] == "page_count_mismatch" for d in rep["disagreements"])
    assert rep["page_count_primary"] == 3 and rep["page_count_review"] == 1


def test_reconcile_degraded_when_no_review():
    pages = [_ppage(1)]
    blocks = [_pblock("b1", 1)]
    rep = sa_audit.reconcile(pages, blocks, None, source_type="native_pdf",
                             primary_backend="pymupdf", mineru_status="unavailable")
    assert rep["review_status"] == "degraded_no_review"
    assert rep["dual_audited"] is False and rep["degraded"] is True
    assert rep["production_accepted"] is False
    assert rep["missing_evidence"] == ["mineru_review"]
    assert rep["review_backend"] is None and rep["degraded_reason"]


def test_reconcile_review_failed_marked():
    rep = sa_audit.reconcile([_ppage(1)], [_pblock("b1", 1)], None, source_type="native_pdf",
                             primary_backend="pymupdf", mineru_status="failed")
    assert rep["review_status"] == "review_failed"
    assert rep["degraded"] is True and rep["dual_audited"] is False


def test_reconcile_mineru_primary_scanned_dual_audited():
    # 扫描件：primary=mineru（结构权威），PyMuPDF profile 作页覆盖交叉确认 → 无需第二 reviewer。
    pages = [_ppage(i) for i in (1, 2, 3)]
    blocks = [_pblock("b1", 1), _pblock("b2", 2), _pblock("b3", 3)]
    rep = sa_audit.reconcile(pages, blocks, None, source_type="scanned_pdf",
                             primary_backend="mineru", mineru_status="used")
    assert rep["dual_audited"] is True and rep["degraded"] is False
    assert rep["review_backend"] == "pymupdf" and rep["review_status"] == "cross_checked"
    assert rep["page_count_review"] == 3


def test_reconcile_not_applicable_for_markdown():
    rep = sa_audit.reconcile([_ppage(1)], [_pblock("b1", 1)], None, source_type="markdown",
                             primary_backend="markdown", mineru_status="not_checked")
    assert rep["review_status"] == "not_applicable"
    assert rep["dual_audited"] is True and rep["degraded"] is False


# ---- audit（编排，注入 mineru_review mock） ----

def test_audit_cross_checked_with_mock_reviewer(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1), _pblock("b2", 2)],
                 pages=[_ppage(1), _ppage(2, needs_vision=True, reasons=["table"])])
    review = [_rblock(1, "text"), _rblock(2, "table")]
    rep = sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf",
                         primary_backend="pymupdf", strict=True, input_hash="h",
                         mineru_review=lambda raw, out, ih: review)
    assert rep["review_status"] == "cross_checked" and rep["dual_audited"] is True
    assert (d / "reconciliation.json").exists()
    on_disk = json.loads((d / "reconciliation.json").read_text(encoding="utf-8"))
    assert on_disk["generated_by"] == "source-audit"


def test_audit_strict_fail_closed_when_unavailable(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])
    with pytest.raises(sa_audit.DualAuditUnavailable):
        sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf",
                       primary_backend="pymupdf", strict=True, input_hash="h",
                       mineru_review=lambda *a: None)            # MinerU 不可用


def test_audit_strict_fail_closed_when_review_raises(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])

    def boom(*a):
        raise RuntimeError("mineru crashed")
    with pytest.raises(sa_audit.DualAuditUnavailable):
        sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf",
                       primary_backend="pymupdf", strict=True, input_hash="h", mineru_review=boom)


def test_audit_nonstrict_degraded_when_unavailable(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])
    rep = sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf",
                         primary_backend="pymupdf", strict=False, input_hash="h",
                         mineru_review=lambda *a: None)
    assert rep["degraded"] is True and rep["review_status"] == "degraded_no_review"
    assert rep["dual_audited"] is False
    assert (d / "reconciliation.json").exists()


def test_audit_nonstrict_review_failed(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])

    def boom(*a):
        raise RuntimeError("mineru crashed")
    rep = sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf",
                         primary_backend="pymupdf", strict=False, input_hash="h", mineru_review=boom)
    assert rep["review_status"] == "review_failed" and rep["degraded"] is True


def test_audit_cache_skip_on_matching_input_hash(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])
    calls = {"n": 0}

    def rev(*a):
        calls["n"] += 1
        return [_rblock(1, "text")]
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=rev)
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=rev)
    assert calls["n"] == 1                         # 第二次命中 input_hash 缓存 → 不重跑 MinerU


def test_audit_cache_hit_rebuilds_missing_evidence_and_queue(tmp_path):
    # 旧缓存场景：reconciliation.json 命中 input_hash，但 evidence.json/queue.json 缺失 → 必须补齐，
    # 不能出现"有 reconciliation 但没仲裁队列"。补齐须忠实，故 bundle 缺时缓存失效、reviewer 重跑。
    d = _staging(tmp_path, blocks=[_pblock("b1", 1), _pblock("b2", 2)],
                 pages=[_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)])
    review = [_rblock(1, "equation"), _rblock(2, "equation")]
    calls = {"n": 0}

    def rev(*a):
        calls["n"] += 1
        return review
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=rev, render_packets=lambda *a: None)
    assert (d / "evidence.json").exists() and (d / "arbitration" / "queue.json").exists()
    (d / "evidence.json").unlink()
    (d / "arbitration" / "queue.json").unlink()           # 模拟旧缓存：只剩 reconciliation
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=rev, render_packets=lambda *a: None)
    assert (d / "evidence.json").exists() and (d / "arbitration" / "queue.json").exists()  # 补齐
    assert calls["n"] == 2                                 # bundle 缺 → 缓存失效，reviewer 重跑重建


def test_audit_cache_skip_keeps_bundle_intact(tmp_path):
    # 三件套齐全时缓存仍短路（不退化）：第二次命中 reconciliation+evidence+queue → 不重跑 reviewer。
    d = _staging(tmp_path, blocks=[_pblock("b1", 1), _pblock("b2", 2)],
                 pages=[_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)])
    calls = {"n": 0}

    def rev(*a):
        calls["n"] += 1
        return [_rblock(1, "equation"), _rblock(2, "equation")]
    for _ in range(2):
        sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                       input_hash="h", mineru_review=rev, render_packets=lambda *a: None)
    assert calls["n"] == 1                                 # bundle 完整 → 第二次命中缓存
    assert (d / "evidence.json").exists() and (d / "arbitration" / "queue.json").exists()


def test_audit_emits_evidence_and_queue_when_mineru_finds_missed_structure(tmp_path):
    # PyMuPDF missed a structural page MinerU finds → evidence.json + non-empty arbitration queue + packet PNG.
    d = _staging(tmp_path, blocks=[_pblock("b1", 1), _pblock("b2", 2)],
                 pages=[_ppage(1, needs_vision=True, reasons=["formula"]), _ppage(2)])
    review = [_rblock(1, "equation"), _rblock(2, "equation")]   # MinerU finds a formula on p2 too
    rendered = {"pages": []}

    def fake_render(raw, pages, arb_dir):
        rendered["pages"] = [int(p) for p in pages]
        for pg in pages:
            (Path(arb_dir) / f"p{int(pg):04d}.png").write_bytes(b"png")
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=lambda *a: review, render_packets=fake_render)
    ev = json.loads((d / "evidence.json").read_text(encoding="utf-8"))
    assert ev["candidates"] == [2] and ev["initial_needs_vision"] == [1]
    q = json.loads((d / "arbitration" / "queue.json").read_text(encoding="utf-8"))
    assert len(q["packets"]) == 1 and q["packets"][0]["page"] == 2
    assert rendered["pages"] == [2] and (d / "arbitration" / "p0002.png").exists()


def test_audit_writes_soft_risk_flags_into_blocks(tmp_path):
    # soft risk（reading_order：block 流里 page 倒退）被确定性写进 blocks.jsonl，不经任何裁决。
    d = _staging(tmp_path, blocks=[_pblock("b1", 2), _pblock("b2", 1)],
                 pages=[_ppage(1), _ppage(2)])
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=lambda *a: [_rblock(1, "text"), _rblock(2, "text")],
                   render_packets=lambda *a: None)
    blocks = [json.loads(l) for l in (d / "blocks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any("reading_order_risk" in (b.get("risk_flags") or []) for b in blocks)


def test_audit_writes_hard_risk_flag_on_page_with_asset(tmp_path):
    # has_asset=True 的 hard-risk 页（formula_text_loss）不进 candidate，但 hard flag 确定性写进 blocks.jsonl。
    blk = {"block_id": "b1", "type": "text", "text": "MPL\nw\n=\nMPK\nr", "page": 1,
           "char_start": 0, "char_end": 13, "source_ref": "p0001#b1", "chapter_id": "",
           "asset_path": "assets/p0001.png", "risk_flags": []}
    d = _staging(tmp_path, blocks=[blk], pages=[_ppage(1, needs_vision=True, reasons=["formula"])])
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=lambda *a: [_rblock(1, "equation")],
                   render_packets=lambda *a: None)
    ev = json.loads((d / "evidence.json").read_text(encoding="utf-8"))
    assert ev["candidates"] == []                              # 已有 asset → 不进 candidate
    blocks = [json.loads(l) for l in (d / "blocks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert "formula_text_loss" in (blocks[0].get("risk_flags") or [])


def test_audit_empty_queue_when_no_disagreement(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1)], pages=[_ppage(1)])
    sa_audit.audit(d, tmp_path / "x.pdf", source_type="native_pdf", primary_backend="pymupdf",
                   input_hash="h", mineru_review=lambda *a: [_rblock(1, "text")],
                   render_packets=lambda *a: None)
    q = json.loads((d / "arbitration" / "queue.json").read_text(encoding="utf-8"))
    assert q["packets"] == []                          # no structural disagreement → nothing to arbitrate


def test_audit_mineru_primary_no_second_reviewer(tmp_path):
    d = _staging(tmp_path, blocks=[_pblock("b1", 1), _pblock("b2", 2)],
                 pages=[_ppage(1), _ppage(2)])
    called = {"n": 0}

    def rev(*a):
        called["n"] += 1
        return []
    rep = sa_audit.audit(d, tmp_path / "x.pdf", source_type="scanned_pdf",
                         primary_backend="mineru", strict=True, input_hash="h", mineru_review=rev)
    assert called["n"] == 0                         # mineru-primary 不再跑第二个 reviewer
    assert rep["dual_audited"] is True and rep["primary_backend"] == "mineru"
