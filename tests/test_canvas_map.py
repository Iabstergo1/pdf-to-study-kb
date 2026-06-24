import importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


cm = _load("canvas_map")


def _page(vault, rel, *, type, domain="", title="", links=(), status="published"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"see [[{l}]]\n" for l in links)
    meta = f"---\ntype: {type}\nstatus: {status}\ndomain: {domain}\ntitle: {title or rel}\n---\n"
    p.write_text(meta + body, encoding="utf-8")


def test_collect_excludes_lessons_sources_and_unpublished(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/lessons/l1.md", type="lesson", domain="d")         # excluded (type)
    _page(v, "sources/s1.md", type="source")                              # excluded (type)
    _page(v, "topics/t1.md", type="topic", domain="d", status="proposed") # excluded (unpublished)
    paths = {p["page_path"] for p in cm.collect_map_pages(v)}
    assert paths == {"domains/d/concepts/a.md"}


def test_build_graph_edges_dedup_and_in_set(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d",
          links=["domains/d/concepts/a.md", "domains/d/concepts/b.md", "http://x"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d",
          links=["topics/t.md"])               # back-link → dedup with t→a
    _page(v, "domains/d/concepts/b.md", type="concept", domain="d")
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    assert set(nodes) == {"topics/t.md", "domains/d/concepts/a.md", "domains/d/concepts/b.md"}
    assert ("domains/d/concepts/a.md", "topics/t.md") in edges
    assert ("domains/d/concepts/b.md", "topics/t.md") in edges
    assert len(edges) == 2                       # t↔a counted once; http ignored


def test_build_graph_degree_cap(tmp_path, monkeypatch):
    v = tmp_path / "wiki"
    hub_links = [f"domains/d/concepts/c{i}.md" for i in range(20)]
    _page(v, "topics/hub.md", type="topic", domain="d", links=hub_links)
    for i in range(20):
        _page(v, f"domains/d/concepts/c{i}.md", type="concept", domain="d")
    import thresholds; monkeypatch.setattr(thresholds, "CANVAS_MAX_DEGREE", 5)
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    hub_deg = sum(1 for a, b in edges if "hub.md" in a or "hub.md" in b)
    assert hub_deg == 5                          # capped


def test_topic_membership_and_unassigned(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/concepts/orphan.md", type="concept", domain="d")  # no topic links it
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    assert membership["topics/t.md"] == ["domains/d/concepts/a.md"]
    assert unassigned["d"] == ["domains/d/concepts/orphan.md"]


def test_layout_deterministic_and_covers_all_nodes(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/concepts/orphan.md", type="concept", domain="d")
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    pos1, groups1 = cm.layout(nodes, membership, unassigned)
    pos2, groups2 = cm.layout(nodes, membership, unassigned)
    assert pos1 == pos2 and groups1 == groups2                 # deterministic
    assert set(pos1) == set(nodes)                             # every node placed
    labels = [g["label"] for g in groups1]
    assert any("未分类" in l for l in labels)                  # unassigned subregion exists
    assert any(l == "领域: d" for l in labels)                 # domain group exists


import json


def test_to_canvas_valid_shape_and_self_check(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d", links=["topics/t.md"])
    canvas = cm.to_canvas(v)
    file_nodes = [n for n in canvas["nodes"] if n["type"] == "file"]
    assert {n["file"] for n in file_nodes} == {"overview.md", "topics/t.md", "domains/d/concepts/a.md"}
    ids = [n["id"] for n in canvas["nodes"]] + [e["id"] for e in canvas["edges"]]
    assert len(ids) == len(set(ids))                        # rule 1: ids unique
    node_ids = {n["id"] for n in canvas["nodes"]}
    for e in canvas["edges"]:                               # rule 2: edges reference existing nodes
        assert e["fromNode"] in node_ids and e["toNode"] in node_ids
    valid = {n["file"] for n in file_nodes}
    assert cm.validate_canvas(canvas, valid) == []          # all 8 rules pass


def test_to_canvas_deterministic(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    assert cm.to_canvas(v) == cm.to_canvas(v)               # byte-stable


def test_write_canvas_emits_parseable_file(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    out = cm.write_canvas(v)
    assert out.name == "knowledge-map.generated.canvas"
    data = json.loads(out.read_text(encoding="utf-8"))      # rule 7: parseable JSON
    assert "nodes" in data and "edges" in data


def test_validate_canvas_catches_dangling_file_and_edge(tmp_path):
    canvas = {"nodes": [{"id": "a" * 16, "type": "file", "file": "ghost.md",
                         "x": 0, "y": 0, "width": 10, "height": 10}],
              "edges": [{"id": "b" * 16, "fromNode": "a" * 16, "toNode": "missing"}]}
    problems = cm.validate_canvas(canvas, valid_files=set())
    assert any("ghost.md" in p for p in problems)            # file not in published set
    assert any("missing" in p for p in problems)             # edge → nonexistent node
