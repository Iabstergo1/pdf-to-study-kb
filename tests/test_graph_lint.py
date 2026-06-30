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


gl = _load("graph_lint")


def _base():
    return {"version": 2, "generated_at": "2026-01-01T00:00:00Z", "scope": "v2.0",
            "nodes": [], "edges": [], "communities": [], "learning_paths": [],
            "insights": [], "stats": {}}


def _concept(nid, **kw):
    n = {"id": nid, "label": nid, "type": "concept", "path": f"domains/d/concepts/{nid}.md",
         "community_id": "community:t", "weight": 0.5, "source_refs": [{"source": "s"}], "aliases": []}
    n.update(kw)
    return n


def test_missing_top_level_field_is_error():
    data = _base()
    del data["nodes"]
    res = gl.validate_graph_data(data)
    assert any("缺顶层字段" in e for e in res["errors"])


def test_dangling_edge_endpoint_is_error():
    data = _base()
    data["nodes"] = [_concept("a")]
    data["edges"] = [{"id": "e1", "source": "a", "target": "ghost", "relation": "related",
                      "confidence": "ambiguous"}]
    res = gl.validate_graph_data(data)
    assert any("ghost" in e and "不存在节点" in e for e in res["errors"])


def test_extracted_edge_without_evidence_and_refs_is_error():
    data = _base()
    data["nodes"] = [_concept("a"), _concept("b")]
    data["edges"] = [{"id": "e1", "source": "a", "target": "b", "relation": "depends_on",
                      "confidence": "extracted", "evidence": "", "source_refs": []}]
    res = gl.validate_graph_data(data)
    assert any("extracted 边缺 evidence" in e for e in res["errors"])


def test_node_path_unpublished_is_error(tmp_path):
    vault = tmp_path / "wiki"
    (vault / "domains/d/concepts").mkdir(parents=True)
    (vault / "real.md").write_text("---\ntype: overview\nstatus: published\n---\n# r\n", encoding="utf-8")
    data = _base()
    data["nodes"] = [_concept("a", path="ghost.md")]
    res = gl.validate_graph_data(data, vault=vault)
    assert any("ghost.md" in e and "非 published" in e for e in res["errors"])


def test_isolated_non_source_node_is_warning():
    data = _base()
    data["nodes"] = [_concept("a")]
    res = gl.validate_graph_data(data)
    assert res["errors"] == []
    assert any("孤立节点" in w for w in res["warnings"])


def test_unknown_relation_downgrade_is_warning():
    data = _base()
    data["nodes"] = [_concept("a"), _concept("b")]
    data["edges"] = [{"id": "e1", "source": "a", "target": "b", "relation": "related",
                      "confidence": "ambiguous", "evidence": "", "source_refs": [{"source": "s"}],
                      "downgraded": True}]
    res = gl.validate_graph_data(data)
    assert res["errors"] == []
    assert any("降级" in w for w in res["warnings"])


def test_unassigned_node_is_warning():
    data = _base()
    data["nodes"] = [_concept("a", community_id="_unassigned"),
                     _concept("b", community_id="_unassigned")]
    data["edges"] = [{"id": "e1", "source": "a", "target": "b", "relation": "related",
                      "confidence": "ambiguous", "source_refs": [{"source": "s"}]}]
    res = gl.validate_graph_data(data)
    assert any("_unassigned" in w for w in res["warnings"])


def test_validate_html_parse_failure_is_error():
    bad = '<script id="graph-data" type="application/json">{not json]</script>'
    errs = gl.validate_html(bad)
    assert any("不可解析" in e for e in errs)


def test_validate_html_ok_when_parseable():
    good = '<script id="graph-data" type="application/json">{"nodes": []}</script>'
    assert gl.validate_html(good) == []


def test_clean_graph_has_no_errors():
    data = _base()
    data["nodes"] = [_concept("a"), _concept("b")]
    data["edges"] = [{"id": "e1", "source": "a", "target": "b", "relation": "depends_on",
                      "confidence": "extracted", "evidence": "x", "source_refs": [{"source": "s"}]}]
    res = gl.validate_graph_data(data)
    assert res["errors"] == []
