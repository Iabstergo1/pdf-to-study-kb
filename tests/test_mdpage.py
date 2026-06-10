from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mdpage", ROOT / "scripts" / "mdpage.py")
mdpage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdpage)


def test_read_page_splits_frontmatter_and_body(tmp_path):
    p = tmp_path / "c.md"
    p.write_text("---\ntype: concept\ncanonical_name: 信号博弈\n---\n# 信号博弈\n\nbody\n", encoding="utf-8")
    meta, body = mdpage.read_page(p)
    assert meta["type"] == "concept" and meta["canonical_name"] == "信号博弈"
    assert body.startswith("# 信号博弈")


def test_read_page_no_frontmatter(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("just text\n", encoding="utf-8")
    meta, body = mdpage.read_page(p)
    assert meta == {} and body == "just text\n"


def test_write_then_read_roundtrip_deterministic(tmp_path):
    p = tmp_path / "x.md"
    meta = {"type": "concept", "aliases": ["Signaling Game"], "canonical_name": "信号博弈"}
    mdpage.write_page(p, meta, "BODY\n")
    m2, b2 = mdpage.read_page(p)
    assert m2 == meta and b2 == "BODY\n"
    first = p.read_text(encoding="utf-8")
    mdpage.write_page(p, m2, b2)  # 再写一遍字节不变（确定性）
    assert p.read_text(encoding="utf-8") == first
