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


ga = _load("graph_analysis")


def _n(nid, typ, label=None, *, domain="", domains=(), refs=(), related=()):
    return {"id": nid, "label": label or nid, "type": typ,
            "path": f"{typ}/{nid}.md", "domain": domain, "domains": list(domains),
            "source_refs": list(refs), "related_concepts": list(related), "aliases": [], "summary": ""}


def _e(s, t, rel="related", conf="ambiguous"):
    return {"id": f"edge:{s}-{t}", "source": s, "target": t, "relation": rel,
            "confidence": conf, "evidence": "", "source_refs": [], "inferred_by": "test"}


def _two_topic_single_domain():
    g = [{"source": "game"}]
    nodes = [
        _n("t1", "topic", "博弈论基础", domains=["game"], related=["c.a", "c.b"]),
        _n("t2", "topic", "经典模型", domains=["game"], related=["c.c", "c.d"]),
        _n("c.a", "concept", "A", domain="game", refs=g),
        _n("c.b", "concept", "B", domain="game", refs=g),
        _n("c.c", "concept", "C", domain="game", refs=g),
        _n("c.d", "concept", "D", domain="game", refs=g),
    ]
    edges = [
        _e("t1", "c.a", "related", "inferred"), _e("t1", "c.b", "related", "inferred"),
        _e("c.a", "c.b", "related", "ambiguous"),
        _e("t2", "c.c", "related", "inferred"), _e("t2", "c.d", "related", "inferred"),
        _e("c.c", "c.d", "contrasts", "inferred"),
    ]
    membership = {"t1": ["c.a", "c.b"], "t2": ["c.c", "c.d"]}
    return {"nodes": nodes, "edges": edges, "membership": membership, "unassigned": {}}


def test_edge_weight_uses_structural_signals_and_relation():
    model = {
        "nodes": [_n("a", "concept", refs=[{"source": "s"}]), _n("b", "concept", refs=[{"source": "s"}]),
                  _n("x", "concept", refs=[{"source": "p"}]), _n("y", "concept", refs=[{"source": "q"}])],
        "edges": [_e("a", "b", "depends_on", "extracted"), _e("x", "y", "related", "ambiguous")],
        "membership": {}, "unassigned": {},
    }
    analyzed = ga.analyze_graph(model)
    strong = next(e for e in analyzed["edges"] if {e["source"], e["target"]} == {"a", "b"})
    weak = next(e for e in analyzed["edges"] if {e["source"], e["target"]} == {"x", "y"})
    assert 0 < strong["weight"] <= 1
    assert strong["weight"] > weak["weight"]          # depends_on+extracted+source-overlap 重于 related+ambiguous
    assert strong["direction"] == "forward"
    assert "signals" in strong and "co_citation" in strong["signals"]


def test_single_domain_two_topics_makes_at_least_two_communities():
    analyzed = ga.analyze_graph(_two_topic_single_domain())
    real = [c for c in analyzed["communities"] if c["id"] != "_unassigned"]
    assert len(real) >= 2
    cids = {n["community_id"] for n in analyzed["nodes"] if n["type"] == "concept"}
    assert len(cids) >= 2                              # 单 domain 不塌成一团


def test_concept_community_not_domain_fallback():
    analyzed = ga.analyze_graph(_two_topic_single_domain())
    by_id = {n["id"]: n for n in analyzed["nodes"]}
    assert by_id["c.a"]["community_id"] == by_id["c.b"]["community_id"]
    assert by_id["c.c"]["community_id"] == by_id["c.d"]["community_id"]
    assert by_id["c.a"]["community_id"] != by_id["c.c"]["community_id"]
    for n in analyzed["nodes"]:
        assert not str(n["community_id"]).startswith("domain:")   # 不按 domain 分组


def test_louvain_is_deterministic_regardless_of_input_order():
    model = _two_topic_single_domain()
    shuffled = {"nodes": list(reversed(model["nodes"])), "edges": list(reversed(model["edges"])),
                "membership": model["membership"], "unassigned": {}}
    a1 = ga.analyze_graph(model)
    a2 = ga.analyze_graph(shuffled)
    m1 = {n["id"]: n["community_id"] for n in a1["nodes"]}
    m2 = {n["id"]: n["community_id"] for n in a2["nodes"]}
    assert m1 == m2


def test_community_labelled_by_topic():
    analyzed = ga.analyze_graph(_two_topic_single_domain())
    labels = {c["label"] for c in analyzed["communities"]}
    assert "博弈论基础" in labels
    assert "经典模型" in labels


def test_learning_path_starts_from_a_topic_when_present():
    analyzed = ga.analyze_graph(_two_topic_single_domain())
    paths = analyzed["learning_paths"]
    assert paths and paths[0]["node_ids"]
    first = paths[0]["node_ids"][0]
    by_id = {n["id"]: n for n in analyzed["nodes"]}
    assert by_id[first]["type"] == "topic"


def test_missing_source_refs_becomes_insight():
    model = {"nodes": [_n("c.x", "concept", refs=[])], "edges": [], "membership": {}, "unassigned": {}}
    analyzed = ga.analyze_graph(model)
    assert any(i["type"] == "missing_source_refs" and i.get("node_id") == "c.x"
               for i in analyzed["insights"])


def test_node_weight_present_and_bounded():
    analyzed = ga.analyze_graph(_two_topic_single_domain())
    for n in analyzed["nodes"]:
        assert 0 <= n["weight"] <= 1
