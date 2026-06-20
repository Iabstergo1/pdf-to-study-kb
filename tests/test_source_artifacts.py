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
