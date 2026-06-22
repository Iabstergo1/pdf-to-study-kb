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
