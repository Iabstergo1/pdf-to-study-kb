from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
concept_store = _load("concept_store")
promotion = _load("promotion")


def _registry(vault):
    reg, errors, _ = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    assert not errors
    return reg


def test_find_candidates_same_term_two_domains(tmp_path):
    concept_store.create_concept(tmp_path, domain="econ", name="Utility")
    concept_store.create_concept(tmp_path, domain="cs", name="效用函数", aliases=["Utility"])
    concept_store.create_concept(tmp_path, domain="econ", name="独占概念")          # 单域：不是候选
    concept_store.create_concept(tmp_path, domain="shared", name="期望效用")        # 已 shared：不参与
    cands = promotion.find_candidates(_registry(tmp_path))
    assert len(cands) == 1
    c = cands[0]
    assert c["term"] == "utility" and set(c["domains"]) == {"econ", "cs"}
    # cs 概念名为中文但别名 Utility 是 ASCII → slug 取 utility（P2 canonical_id 规则）
    assert set(c["canonical_ids"]) == {"concept.econ.utility", "concept.cs.utility"}


def test_promote_to_shared_moves_rewrites_and_relinks(tmp_path):
    concept_store.create_concept(tmp_path, domain="econ", name="Utility",
                                 source_ref={"source": "wp", "sections": ["1"]})
    # 另一页链接到它（提升后链接必须跟着改）
    mdpage.write_page(tmp_path / "topics/t.md", {"type": "topic", "status": "published"},
                      "# T\n\n见 [[domains/econ/concepts/utility.md|效用]]。\n")
    new_cid, new_rel = promotion.promote_to_shared(tmp_path, "concept.econ.utility")
    assert new_cid == "concept.shared.utility" and new_rel == "concepts/utility.md"
    assert not (tmp_path / "domains/econ/concepts/utility.md").exists()
    meta, _ = mdpage.read_page(tmp_path / new_rel)
    assert meta["canonical_id"] == new_cid and meta["scope"] == "shared"
    assert meta["domain"] == "shared" and meta["page_path"] == new_rel
    assert meta["source_refs"] == [{"source": "wp", "sections": ["1"]}]  # 内容保留
    topic = (tmp_path / "topics/t.md").read_text(encoding="utf-8")
    assert "[[concepts/utility.md|效用]]" in topic and "domains/econ" not in topic


def test_promote_unknown_or_conflict_aborts(tmp_path):
    with pytest.raises(KeyError):
        promotion.promote_to_shared(tmp_path, "concept.d.nope")
    concept_store.create_concept(tmp_path, domain="econ", name="Utility")
    concept_store.create_concept(tmp_path, domain="shared", name="Utility")  # 目标已存在
    with pytest.raises(FileExistsError):
        promotion.promote_to_shared(tmp_path, "concept.econ.utility")
    assert (tmp_path / "domains/econ/concepts/utility.md").exists()  # 中止不动盘
