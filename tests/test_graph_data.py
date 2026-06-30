import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = _load("graph_schema")


# ── Task 2: schema constants ──

def test_graph_schema_whitelists_are_stable():
    assert schema.GRAPH_VERSION == 2
    assert schema.GRAPH_SCOPE == "v2.0"
    assert schema.RELATIONS == {"depends_on", "contrasts", "related"}
    assert schema.CONFIDENCES == {"extracted", "inferred", "ambiguous"}
    assert schema.DIRECTIONS == {"forward", "both", "undirected"}


def test_relation_direction_and_bonus_tables_match_whitelist():
    assert set(schema.RELATION_DIRECTION) == schema.RELATIONS
    assert set(schema.RELATION_BONUS) == schema.RELATIONS
    assert schema.RELATION_DIRECTION["depends_on"] == "forward"
    assert schema.RELATION_DIRECTION["contrasts"] == "both"
    assert schema.RELATION_DIRECTION["related"] == "undirected"


def test_stable_id_uses_prefix_and_payload():
    assert schema.stable_id("edge", "a->b") == schema.stable_id("edge", "a->b")
    assert schema.stable_id("edge", "a->b").startswith("edge:")
    assert schema.stable_id("node", "a->b") != schema.stable_id("edge", "a->b")


# ── Task 5: graph-data writer ──

graph_data = _load("graph_data")


def test_to_graph_data_shape_and_test_mode(monkeypatch):
    monkeypatch.setenv("STUDY_KB_GRAPH_TEST_MODE", "1")
    analyzed = {"nodes": [], "edges": [], "communities": [], "learning_paths": [],
                "insights": []}
    data = graph_data.to_graph_data(analyzed)
    assert set(data) == {"version", "generated_at", "scope", "nodes", "edges", "communities",
                         "learning_paths", "insights", "stats"}
    assert data["version"] == 2 and data["scope"] == "v2.0"
    assert data["generated_at"] == "2026-01-01T00:00:00Z"
    assert data["stats"] == {"node_count": 0, "edge_count": 0, "community_count": 0}


def test_to_graph_data_sorts_by_id():
    analyzed = {"nodes": [{"id": "b"}, {"id": "a"}], "edges": [{"id": "e2"}, {"id": "e1"}],
                "communities": [{"id": "c2"}, {"id": "c1"}], "learning_paths": [], "insights": []}
    data = graph_data.to_graph_data(analyzed)
    assert [n["id"] for n in data["nodes"]] == ["a", "b"]
    assert [e["id"] for e in data["edges"]] == ["e1", "e2"]
    assert [c["id"] for c in data["communities"]] == ["c1", "c2"]
    assert data["stats"] == {"node_count": 2, "edge_count": 2, "community_count": 2}


def test_write_graph_data_roundtrip(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("STUDY_KB_GRAPH_TEST_MODE", "1")
    vault = tmp_path / "wiki"
    vault.mkdir()
    analyzed = {"nodes": [], "edges": [], "communities": [], "learning_paths": [],
                "insights": []}
    out = graph_data.write_graph_data(vault, analyzed)
    assert out.name == "graph-data.generated.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["scope"] == "v2.0"
