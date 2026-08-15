import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


site_data = _load("site_data")


def _pages():
    return [
        {"rel": "overview.md", "title": "总览", "domain": "", "type": "overview",
         "body": ""},
        {"rel": "domains/d/concepts/a.md", "title": "A", "domain": "d",
         "type": "concept", "body": "见 [[domains/d/concepts/b.md]]。"},
        {"rel": "domains/d/concepts/b.md", "title": "B", "domain": "d",
         "type": "concept", "body": "见 [[topics/Topic.md]]。"},
        {"rel": "domains/llm/topics/LLM-Topic.md", "title": "LLM Topic",
         "domain": "llm", "type": "topic", "body": ""},
        {"rel": "comparisons/Compare.md", "title": "Compare", "domain": "comparisons",
         "type": "comparison", "body": ""},
        {"rel": "synthesis/Synth.md", "title": "Synth", "domain": "synthesis",
         "type": "synthesis", "body": ""},
        {"rel": "topics/Topic.md", "title": "Topic", "domain": "topics",
         "type": "topic", "body": ""},
    ]


def test_cross_domain_synthesis_types_never_split_across_branches():
    tree = site_data.build_explorer_tree(_pages())
    by_label = {branch["domain"]: branch for branch in tree}

    assert "跨域综合" in by_label
    cross = by_label["跨域综合"]
    assert {group["type"] for group in cross["types"]} == {
        "comparison", "synthesis", "topic"}
    topic_paths = [
        page["path"] for group in cross["types"] if group["type"] == "topic"
        for page in group["pages"]
    ]
    assert topic_paths == [
        "domains/llm/topics/LLM-Topic.md",
        "topics/Topic.md",
    ]

    occurrences = {}
    for branch in tree:
        for group in branch["types"]:
            ptype = group["type"]
            assert ptype not in occurrences, ptype
            occurrences[ptype] = branch["domain"]


def test_breadcrumb_uses_cross_domain_branch_for_synthesis_pages():
    page = next(page for page in _pages() if page["type"] == "comparison")

    breadcrumb = site_data.breadcrumb(page)

    assert [item["label"] for item in breadcrumb] == ["跨域综合", "comparison", "Compare"]
    assert [item["target"] for item in breadcrumb] == [
        "cross-domain",
        "cross-domain|type:comparison",
        "comparisons/Compare.md",
    ]


def test_backlinks_are_sorted_deduplicated_and_resolved():
    pages = _pages()

    backlinks = site_data.build_backlinks(pages)

    assert backlinks["domains/d/concepts/b.md"] == [
        {"path": "domains/d/concepts/a.md", "title": "A"},
    ]
    assert backlinks["topics/Topic.md"] == [
        {"path": "domains/d/concepts/b.md", "title": "B"},
    ]
    assert "comparisons/Compare.md" not in backlinks


def test_sanitize_graph_data_strips_timestamp_and_preserves_counts():
    graph = {
        "version": "2.0",
        "generated_at": "2026-08-12T20:32:33Z",
        "scope": "published",
        "nodes": [
            {"id": "b", "label": "B", "type": "concept", "path": "b.md",
             "community_id": "c", "weight": 1, "summary": "B summary",
             "aliases": [], "domains": ["d"], "source_refs": []},
            {"id": "a", "label": "A", "type": "topic", "path": "a.md",
             "community_id": "c", "weight": 1, "summary": "A summary",
             "aliases": ["甲"], "domains": [], "source_refs": []},
        ],
        "edges": [
            {"id": "e", "source": "a", "target": "b", "relation": "related",
             "weight": 0.5, "evidence": "x", "source_refs": []},
        ],
        "communities": [
            {"id": "c", "label": "C", "node_ids": ["b", "a"],
             "representative_node_ids": ["a"], "weight": 1, "source_refs": []},
        ],
        "learning_paths": [{"id": "p", "node_ids": ["a", "b"]}],
        "insights": [{"id": "i", "text": "drop me"}],
        "stats": {"node_count": 2, "edge_count": 1, "community_count": 1},
    }

    first = site_data.sanitize_graph_data(graph)
    second = site_data.sanitize_graph_data(graph)

    assert "generated_at" not in first
    assert len(first["nodes"]) == 2
    assert len(first["edges"]) == 1
    assert first["nodes"][0]["id"] == "a"
    assert set(first["nodes"][0]) == {
        "aliases", "community_id", "id", "label", "path", "summary", "type", "weight"
    }
    assert first["communities"][0]["representative_node_ids"] == ["a"]
    assert first["learning_paths"] == [{"id": "p", "node_ids": ["a", "b"]}]
    assert first == second
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


def test_quiz_and_proposition_payload_uses_page_rules_results():
    pages = [{
        "rel": "a.md", "title": "A", "domain": "d", "type": "concept",
        "body": (
            "> [!question] 自测\n"
            "> 为什么？\n"
            "> > [!success]- 答案\n"
            "> > 因为。\n\n"
            "**命题（甲）**：结论甲。\n"
        ),
    }]
    render_answer = lambda answer, page_set, vault, with_images: f"<p>{answer}</p>"

    quiz = site_data.build_quiz_items(pages, render_answer)
    propositions = site_data.build_proposition_items(pages)

    assert quiz == [{
        "rel": "a.md",
        "title": "A",
        "stem": "为什么？",
        "answer_html": "<p>因为。</p>",
    }]
    assert propositions == [{
        "rel": "a.md",
        "title": "A",
        "name": "甲",
        "statement": "结论甲。",
    }]
