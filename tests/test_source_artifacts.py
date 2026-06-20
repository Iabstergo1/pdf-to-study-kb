from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))  # 修正 A：普通 import，避免动态 _load 的 dataclass/双实例隐患
import source_artifacts as sa


def test_artifact_version_present():
    assert sa.ARTIFACT_VERSION  # 非空字符串


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
