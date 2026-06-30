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


gm = _load("graph_model")


def _page(vault, rel, frontmatter, body):
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


def test_collect_uses_canonical_id_and_label_and_summary(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/game/concepts/博弈.md",
          {"type": "concept", "status": "published", "domain": "game",
           "canonical_id": "concept.game.game", "canonical_name": "博弈"},
          "# 博弈\n\n策略互动。\n")
    pages = gm.collect_graph_pages(v)
    assert len(pages) == 1
    p = pages[0]
    assert p["id"] == "concept.game.game"
    assert p["label"] == "博弈"
    assert p["type"] == "concept"
    assert p["path"] == "domains/game/concepts/博弈.md"
    assert p["summary"] == "策略互动。"


def test_collect_excludes_lesson_and_unpublished(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/d/lessons/l.md", {"type": "lesson", "status": "published", "domain": "d"}, "# L\n")
    _page(v, "topics/t.md", {"type": "topic", "status": "proposed", "domains": ["d"], "title": "T"}, "# T\n")
    _page(v, "overview.md", {"type": "overview", "status": "published", "title": "O"}, "# O\n")
    paths = {p["path"] for p in gm.collect_graph_pages(v)}
    assert paths == {"overview.md"}


def test_graph_comment_confidence_only_defaults_relation_related(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/g/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.a", "canonical_name": "A"},
          '# A\n\n见 [[domains/g/concepts/b.md|B]]。 <!-- graph: confidence=extracted -->\n')
    _page(v, "domains/g/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.b", "canonical_name": "B"}, "# B\n\nbody\n")
    model = gm.build_graph_model(v)
    e = next(e for e in model["edges"] if {e["source"], e["target"]} == {"concept.g.a", "concept.g.b"})
    assert e["confidence"] == "extracted"
    assert e["relation"] == "related"        # relation omitted → related


def test_graph_comment_unknown_relation_and_confidence_degrade(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/g/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.a", "canonical_name": "A"},
          '# A\n\n[[domains/g/concepts/b.md|B]] <!-- graph: relation=causes confidence=weird -->\n')
    _page(v, "domains/g/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.b", "canonical_name": "B"}, "# B\n")
    model = gm.build_graph_model(v)
    e = next(e for e in model["edges"] if {e["source"], e["target"]} == {"concept.g.a", "concept.g.b"})
    assert e["relation"] == "related"        # unknown → related
    assert e["confidence"] == "ambiguous"    # unknown → ambiguous


def test_topic_membership_from_related_and_body(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md",
          {"type": "topic", "status": "published", "domains": ["g"], "title": "T",
           "related_concepts": ["concept.g.b"]},
          "# T\n\n[[domains/g/concepts/a.md|A]]\n")
    _page(v, "domains/g/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.a", "canonical_name": "A"}, "# A\n")
    _page(v, "domains/g/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.b", "canonical_name": "B"}, "# B\n")
    model = gm.build_graph_model(v)
    members = model["membership"]["topics/t.md"]
    assert set(members) == {"concept.g.a", "concept.g.b"}   # body wikilink ∪ related_concepts


def test_same_pair_naked_and_annotated_collapses_to_best(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/g/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.a", "canonical_name": "A"},
          '# A\n\n裸链 [[domains/g/concepts/b.md|B]]\n'
          '依赖 [[domains/g/concepts/b.md|B]] <!-- graph: relation=depends_on confidence=extracted evidence="x" -->\n')
    _page(v, "domains/g/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.b", "canonical_name": "B"}, "# B\n")
    model = gm.build_graph_model(v)
    pair_edges = [e for e in model["edges"]
                  if {e["source"], e["target"]} == {"concept.g.a", "concept.g.b"}]
    assert len(pair_edges) == 1                       # collapsed to one edge
    assert pair_edges[0]["relation"] == "depends_on"  # best relation survives
    assert pair_edges[0]["confidence"] == "extracted"


def test_publish_gate_seam_intact(tmp_path):
    # canvas 移除后 graph_model.topic_membership 仍可用并返回 (membership, unassigned)，
    # wiki_gate.concepts_uncovered_by_topic 仍可运行（A2 门禁不被图谱重构打断）。
    import graph_model
    import wiki_gate
    nodes = {
        "topics/t.md": {"type": "topic", "domain": "g", "canonical_id": "",
                        "related_concepts": [], "links": {"domains/g/concepts/a.md"}},
        "domains/g/concepts/a.md": {"type": "concept", "domain": "g", "canonical_id": "concept.g.a",
                                    "related_concepts": [], "links": set()},
    }
    membership, unassigned = graph_model.topic_membership(nodes)
    assert membership["topics/t.md"] == ["domains/g/concepts/a.md"]
    assert isinstance(unassigned, dict)
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", {"type": "topic", "status": "published", "domains": ["g"], "title": "T"},
          "# T\n\n[[domains/g/concepts/a.md|A]]\n")
    _page(v, "domains/g/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "g",
           "canonical_id": "concept.g.a", "canonical_name": "A"}, "# A\n")
    result = wiki_gate.concepts_uncovered_by_topic(v)
    assert isinstance(result, list)        # 仍可运行、不抛
