from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))  # 修正 A：普通 import，避免动态 _load 的 dataclass/双实例隐患
import source_artifacts as sa


def test_source_block_source_ref():
    assert sa.block_source_ref(43, "b000043") == "p0043#b000043"


def test_write_read_blocks_roundtrip(tmp_path):
    blocks = [
        sa.SourceBlock(block_id="b000001", type="text", text="hello", page=1,
                       char_start=0, char_end=20, risk_flags=["formula"],
                       source_ref="p0001#b000001"),
        sa.SourceBlock(block_id="b000002", type="heading", text="## T", page=1,
                       char_start=20, char_end=24, text_level=2, heading_path="T",
                       source_ref="p0001#b000002"),
    ]
    p = tmp_path / "blocks.jsonl"
    sha = sa.write_blocks(p, blocks)
    assert len(sha) == 64
    got = sa.read_blocks(p)
    assert len(got) == 2
    assert got[0]["block_id"] == "b000001" and got[0]["risk_flags"] == ["formula"]
    assert got[1]["text_level"] == 2 and got[1]["heading_path"] == "T"


def test_artifact_version_bumped():
    # L2 "1"→"2"(chapter_id + source_type/backend_reason)；C1 "2"→"3"(element_id)；
    # dual-audit "3"→"4"(parse_report.dual_audit_required + reconciliation.json 契约)；
    # evidence-assembly "4"→"5"(evidence.json + arbitration 闭环)；
    # evidence-risk "5"→"6"(risk_flags/soft_risk_pages 证据质量层 + needs_human resolve 闭环)
    assert sa.ARTIFACT_VERSION == "6"


def test_source_block_element_id_default_and_roundtrip(tmp_path):
    b = sa.SourceBlock(block_id="b1", type="text", text="x", page=1, char_start=0, char_end=1)
    assert b.element_id == ""                       # 非 table/figure 块默认空
    tb = sa.SourceBlock(block_id="b2", type="table", text="<table/>", page=1,
                        char_start=1, char_end=2, element_id="t0001")
    p = tmp_path / "blocks.jsonl"
    sa.write_blocks(p, [b, tb])
    rt = sa.read_blocks(p)
    assert rt[0]["element_id"] == "" and rt[1]["element_id"] == "t0001"   # 落盘/回读保真


def test_source_block_has_chapter_id_default_empty():
    b = sa.SourceBlock(block_id="b000001", type="text", text="x", page=1,
                       char_start=0, char_end=1)
    assert b.chapter_id == ""


def test_write_read_blocks_preserves_chapter_id(tmp_path):
    blocks = [
        sa.SourceBlock(block_id="b000001", type="text", text="hello", page=1,
                       char_start=0, char_end=5, chapter_id="ch01-intro",
                       source_ref="p0001#b000001"),
    ]
    p = tmp_path / "blocks.jsonl"
    sa.write_blocks(p, blocks)
    got = sa.read_blocks(p)
    assert got[0]["chapter_id"] == "ch01-intro"


def test_routing_advice_defaults():
    ra = sa.RoutingAdvice(recommended_backend="pymupdf",
                          structured_reparse_recommended=False)
    assert ra.advisory_only is True
    assert ra.consumed_by_auto_router is False
    assert ra.reasons == []


def test_build_parse_report_envelope_constants():
    ra = sa.RoutingAdvice(recommended_backend="mineru",
                          structured_reparse_recommended=True,
                          reasons=["scan_suspected"])
    rep = sa.build_parse_report("pymupdf", input_hash="abc",
                                routing_advice=ra, warnings=["w1"],
                                page_count=10, block_count=10,
                                needs_vision_pages=[3], risk_flag_counts={"formula": 2})
    assert rep["selected_backend"] == "pymupdf"
    assert rep["backend_policy"] == "contract_only"
    assert rep["mineru_status"] == "not_checked"
    assert "mineru_available" not in rep            # 禁止写真实探测字段
    assert rep["routing_advice"]["advisory_only"] is True
    assert rep["routing_advice"]["consumed_by_auto_router"] is False
    assert rep["routing_advice"]["reasons"] == ["scan_suspected"]
    assert rep["page_count"] == 10 and rep["risk_flag_counts"] == {"formula": 2}
    assert rep["artifact_version"] == sa.ARTIFACT_VERSION


def test_write_parse_report_roundtrip(tmp_path):
    import json
    ra = sa.RoutingAdvice(recommended_backend="markdown",
                          structured_reparse_recommended=False)
    rep = sa.build_parse_report("markdown", input_hash="h",
                                routing_advice=ra, section_count=3,
                                heading_count=2, block_count=3)
    p = tmp_path / "parse_report.json"
    sha = sa.write_parse_report(p, rep)
    assert len(sha) == 64
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["selected_backend"] == "markdown" and loaded["section_count"] == 3


def test_build_parse_report_forces_advisory_constants():
    # 即使调用方误传 advisory_only=False / consumed_by_auto_router=True，也被强制回安全值。
    ra = sa.RoutingAdvice(recommended_backend="mineru", structured_reparse_recommended=True,
                          advisory_only=False, consumed_by_auto_router=True)
    rep = sa.build_parse_report("pymupdf", input_hash="h", routing_advice=ra)
    assert rep["routing_advice"]["advisory_only"] is True
    assert rep["routing_advice"]["consumed_by_auto_router"] is False


def test_build_parse_report_allows_explicit_router_consumption():
    # Spec 2 auto router 实际消费时，可显式置 consumed_by_auto_router=True（advisory_only 仍恒 True）。
    ra = sa.RoutingAdvice(recommended_backend="mineru", structured_reparse_recommended=True)
    rep = sa.build_parse_report("mineru", input_hash="h", routing_advice=ra,
                                consumed_by_auto_router=True)
    assert rep["routing_advice"]["advisory_only"] is True
    assert rep["routing_advice"]["consumed_by_auto_router"] is True


# --- dual-audit 契约：parse_report.dual_audit_required + reconciliation.json ---

def _ra():
    return sa.RoutingAdvice(recommended_backend="pymupdf", structured_reparse_recommended=False)


def test_build_parse_report_dual_audit_required_default_false():
    # 非 PDF（md/docx/pptx）默认不要求双审 → dual_audit_required=False。
    rep = sa.build_parse_report("pymupdf", input_hash="h", routing_advice=_ra())
    assert rep["dual_audit_required"] is False


def test_build_parse_report_dual_audit_required_true_for_pdf():
    rep = sa.build_parse_report("pymupdf", input_hash="h", routing_advice=_ra(),
                                dual_audit_required=True)
    assert rep["dual_audit_required"] is True


def test_build_reconciliation_report_required_fields():
    rep = sa.build_reconciliation_report(
        source_id="book", source_type="native_pdf", primary_backend="pymupdf",
        review_backend="mineru", review_status="cross_checked", dual_audited=True,
        production_accepted=True, degraded=False, mineru_status="used", input_hash="h",
        page_count_primary=10, page_count_review=10, pages_cross_checked=[1, 2, 3],
        agreements=3, disagreements=[], missing_evidence=[])
    assert rep["generated_by"] == "source-audit"
    # 必备字段齐全且非 vague（不得有 None/"unknown" 占位需要校验的字段）
    for k in ("source_type", "primary_backend", "review_backend", "review_status",
              "dual_audited", "production_accepted", "degraded", "mineru_status",
              "page_count_primary"):
        assert k in rep, f"reconciliation 缺字段 {k}"
    assert rep["dual_audited"] is True and rep["production_accepted"] is True
    assert rep["review_backend"] == "mineru" and rep["page_count_review"] == 10
    assert rep["pages_cross_checked"] == [1, 2, 3] and rep["agreements"] == 3


def test_build_reconciliation_report_degraded_no_review():
    rep = sa.build_reconciliation_report(
        source_id="book", source_type="native_pdf", primary_backend="pymupdf",
        review_backend=None, review_status="degraded_no_review", dual_audited=False,
        production_accepted=False, degraded=True, degraded_reason="mineru unavailable",
        mineru_status="unavailable", input_hash="h", page_count_primary=10,
        page_count_review=None, missing_evidence=["mineru_review"])
    assert rep["degraded"] is True and rep["dual_audited"] is False
    assert rep["review_backend"] is None and rep["missing_evidence"] == ["mineru_review"]
    assert rep["degraded_reason"]                       # 降级必须有可读原因，不留空


def test_write_reconciliation_roundtrip(tmp_path):
    import json
    rep = sa.build_reconciliation_report(
        source_id="b", source_type="native_pdf", primary_backend="pymupdf",
        review_backend="mineru", review_status="cross_checked", dual_audited=True,
        production_accepted=True, degraded=False, mineru_status="used", input_hash="h",
        page_count_primary=2, page_count_review=2)
    p = tmp_path / "reconciliation.json"
    sha = sa.write_reconciliation(p, rep)
    assert len(sha) == 64
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["generated_by"] == "source-audit" and loaded["dual_audited"] is True
