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


# ── spec-alignment（详细版 spec：预设色 / _global / 类型权重裁剪 / related_concepts 并集）──

def _raw(vault, rel, frontmatter: dict, body=""):
    """写任意 frontmatter 的 published 页（测细节字段：domains[]/canonical_id/related_concepts[]）。"""
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    p.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")


def test_color_uses_canvas_presets_not_hex(tmp_path):
    # spec 2：color 用 JSON Canvas 预设色 "1"-"6" 常量映射，不读 graph.json 的 RGB int
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    canvas = cm.to_canvas(v)
    by_file = {n["file"]: n for n in canvas["nodes"] if n["type"] == "file"}
    assert by_file["overview.md"]["color"] == "2"            # overview → "2"
    assert by_file["domains/d/concepts/a.md"]["color"] == "5"  # concept → "5"


def test_global_row_and_cross_domain_grouping(tmp_path):
    # spec 3：overview/comparison/synthesis(无 domain)→ _global 独立顶行；多/空 domains[] 的 topic → _cross-domain
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "syntheses/s.md", type="synthesis")                       # 无 domain → _global
    _raw(v, "topics/cd.md", {"type": "topic", "status": "published",
                             "domains": ["d1", "d2"], "title": "跨域主题页"})  # 多 domains → _cross-domain
    _page(v, "domains/d1/concepts/a.md", type="concept", domain="d1")
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    pos, groups = cm.layout(nodes, membership, unassigned)
    assert set(pos) == set(nodes)                                       # 全节点有位置
    labels = [g["label"] for g in groups]
    assert any("全局" in l for l in labels)                             # _global 组存在
    assert any("跨域" in l for l in labels)                             # _cross-domain 组存在
    # _global 顶行在最上方：其 y 不大于任一 domain 组
    gy = [g["y"] for g in groups if "全局" in g["label"]][0]
    other_y = [g["y"] for g in groups if "全局" not in g["label"]]
    assert all(gy <= oy for oy in other_y)


def test_degree_cap_prioritizes_high_weight_target(tmp_path, monkeypatch):
    # spec 4：裁剪按确定性优先级（目标页 type 权重），高权重(overview)边优先保留
    v = tmp_path / "wiki"
    links = ["overview.md"] + [f"domains/d/concepts/c{i}.md" for i in range(20)]
    _page(v, "topics/hub.md", type="topic", domain="d", links=links)
    _page(v, "overview.md", type="overview")
    for i in range(20):
        _page(v, f"domains/d/concepts/c{i}.md", type="concept", domain="d")
    import thresholds; monkeypatch.setattr(thresholds, "CANVAS_MAX_DEGREE", 3)
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    hub_edges = [tuple(sorted(e)) for e in edges if "topics/hub.md" in e]
    assert len(hub_edges) == 3                                          # capped
    assert ("overview.md", "topics/hub.md") in hub_edges               # 高权重边幸存


def test_topic_membership_unions_related_concepts(tmp_path):
    # spec 5：topic.related_concepts[]（canonical_id 格式）作可选补充，解析进 concept 节点取并集
    v = tmp_path / "wiki"
    _raw(v, "domains/d/concepts/b.md",
         {"type": "concept", "status": "published", "domain": "d",
          "canonical_id": "concept.d.b", "title": "B"})
    _raw(v, "topics/t.md",
         {"type": "topic", "status": "published", "domains": ["d"],
          "related_concepts": ["concept.d.b"], "title": "T"})            # 正文无链接，仅 related_concepts
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    assert membership["topics/t.md"] == ["domains/d/concepts/b.md"]      # canonical_id 解析到 page_path
    assert unassigned == {}                                              # b 已被收录，不算未分类


def test_validate_rejects_bad_color_and_missing_id(tmp_path):
    # spec 自检 ⑥ color 合法 + ⑧ 必需字段（id）齐全
    bad = {"nodes": [{"id": "a" * 16, "type": "file", "file": "x.md",
                      "x": 0, "y": 0, "width": 1, "height": 1, "color": "banana"},
                     {"type": "group", "label": "g", "x": 0, "y": 0, "width": 1, "height": 1}],
           "edges": []}
    problems = cm.validate_canvas(bad, valid_files={"x.md"})
    assert any("color" in p for p in problems)                          # "banana" 不是合法 color
    assert any("id" in p for p in problems)                             # group 缺 id


# ── spec-faithfulness 收紧（复核发现的 4 处未钉住分歧）──

def test_node_id_uses_canonical_id_not_path(tmp_path):
    # spec：稳定 id = sha256(canonical_id or page_path)[:16]。概念页改路径不应抖动节点身份。
    import hashlib

    def h16(s):
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    v = tmp_path / "wiki"
    _raw(v, "domains/d/concepts/x.md",
         {"type": "concept", "status": "published", "domain": "d",
          "canonical_id": "concept.d.x", "title": "X"})
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/x.md"])
    canvas = cm.to_canvas(v)
    by_file = {n["file"]: n for n in canvas["nodes"] if n["type"] == "file"}
    # 概念页 id 由 canonical_id 派生，而非 page path
    assert by_file["domains/d/concepts/x.md"]["id"] == h16("concept.d.x")
    assert by_file["domains/d/concepts/x.md"]["id"] != h16("domains/d/concepts/x.md")
    # 无 canonical_id 的 topic 回退到 page path
    assert by_file["topics/t.md"]["id"] == h16("topics/t.md")
    # 边端点引用与节点 id 一致（指向 canonical_id 派生的 concept 节点）
    node_ids = {n["id"] for n in canvas["nodes"]}
    for e in canvas["edges"]:
        assert e["fromNode"] in node_ids and e["toNode"] in node_ids
    assert any(h16("concept.d.x") in (e["fromNode"], e["toNode"]) for e in canvas["edges"])


def test_concept_in_two_topics_placed_once_under_primary(tmp_path):
    # spec：每页一个 file node。被两个 topic 收录的 concept 由 sorted-first topic（primary）承载布局，
    # 不被后一个 topic 覆盖坐标；两个 topic 仍各自连边到它。
    v = tmp_path / "wiki"
    _page(v, "topics/t_a.md", type="topic", domain="d", links=["domains/d/concepts/c.md"])
    _page(v, "topics/t_b.md", type="topic", domain="d", links=["domains/d/concepts/c.md"])
    _page(v, "domains/d/concepts/c.md", type="concept", domain="d")
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    membership, _ = cm.topic_membership(nodes)
    # primary = sorted-first topic 独占 member；另一个 topic 不重复收录
    assert membership["topics/t_a.md"] == ["domains/d/concepts/c.md"]
    assert membership["topics/t_b.md"] == []
    pos, groups = cm.layout(nodes, membership, {})
    assert set(pos) == set(nodes)                                       # 每节点恰一坐标
    # concept 坐标落在其 primary topic group 的包围盒内
    g_a = next(g for g in groups if g["label"] == "主题: topics/t_a.md")
    cx, cy, cw, ch = pos["domains/d/concepts/c.md"]
    assert g_a["x"] <= cx and cx + cw <= g_a["x"] + g_a["width"]
    assert g_a["y"] <= cy and cy + ch <= g_a["y"] + g_a["height"]
    # 两个 topic 都连边到 concept（primary 只决定布局，不影响连边）
    inc = {tuple(sorted(e)) for e in edges if "domains/d/concepts/c.md" in e}
    assert ("domains/d/concepts/c.md", "topics/t_a.md") in inc
    assert ("domains/d/concepts/c.md", "topics/t_b.md") in inc


def test_domains_laid_out_horizontally(tmp_path):
    # spec：各 domain 组按名横向铺开（不同 x 带），不是纵向堆叠在同一 x。
    v = tmp_path / "wiki"
    _page(v, "topics/t1.md", type="topic", domain="d1", links=["domains/d1/concepts/a.md"])
    _page(v, "domains/d1/concepts/a.md", type="concept", domain="d1")
    _page(v, "topics/t2.md", type="topic", domain="d2", links=["domains/d2/concepts/b.md"])
    _page(v, "domains/d2/concepts/b.md", type="concept", domain="d2")
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    _, groups = cm.layout(nodes, membership, unassigned)
    d1 = next(g for g in groups if g["label"] == "领域: d1")
    d2 = next(g for g in groups if g["label"] == "领域: d2")
    assert d1["x"] != d2["x"]                                           # 不同列（横向，非堆叠）
    assert abs(d1["y"] - d2["y"]) < max(d1["height"], d2["height"])     # 同一横带，不是上下堆
    lo, hi = sorted((d1, d2), key=lambda g: g["x"])
    assert hi["x"] >= lo["x"] + lo["width"] - 40                        # 横向不重叠（容 20px 对齐）


def test_degree_cap_tiebreak_by_canonical_id(tmp_path, monkeypatch):
    # spec 4：同权重边的 tie-break 用 canonical_id 字典序（非 page path）。
    # 构造 path 序与 canonical_id 序相反的两条同权重边，cap=1 只留一条 → 由 canonical_id 决定幸存者。
    v = tmp_path / "wiki"
    _page(v, "topics/hub.md", type="topic", domain="d",
          links=["domains/d/concepts/m_zzz.md", "domains/d/concepts/m_aaa.md"])
    _raw(v, "domains/d/concepts/m_zzz.md",       # path 大、canonical_id 小（a_aaa）
         {"type": "concept", "status": "published", "domain": "d",
          "canonical_id": "concept.d.a_aaa", "title": "X"})
    _raw(v, "domains/d/concepts/m_aaa.md",       # path 小、canonical_id 大（z_zzz）
         {"type": "concept", "status": "published", "domain": "d",
          "canonical_id": "concept.d.z_zzz", "title": "Y"})
    import thresholds; monkeypatch.setattr(thresholds, "CANVAS_MAX_DEGREE", 1)
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    hub_edges = [tuple(sorted(e)) for e in edges if "topics/hub.md" in e]
    assert len(hub_edges) == 1
    # canonical_id 序：a_aaa < z_zzz → X(m_zzz.md) 幸存；若按 path 序则会是 Y(m_aaa.md)
    assert "domains/d/concepts/m_zzz.md" in hub_edges[0]
