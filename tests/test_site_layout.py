import importlib.util
import base64
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


site_layout = _load("site_layout")


def _pages():
    return [
        {"rel": "overview.md", "title": "总览", "domain": "overview.md",
         "type": "overview", "body": ""},
        {"rel": "domains/d/concepts/b.md", "title": "B", "domain": "d",
         "type": "concept", "body": ""},
        {"rel": "domains/d/concepts/a.md", "title": "A", "domain": "d",
         "type": "concept", "body": ""},
        {"rel": "topics/Topic.md", "title": "Topic", "domain": "topics",
         "type": "topic", "body": ""},
    ]


def _fake_katex():
    return {
        "js": "/*katex*/",
        "css": "@font-face{src:url(fonts/X.woff2)}",
        "auto_render_js": "window.renderMathInElement=function(){};",
        "fonts": {"X.woff2": "AA=="},
    }


def test_explorer_tree_groups_domain_type_and_page_order():
    tree = site_layout.build_explorer_tree(_pages())

    assert tree[0]["domain"] == "总览"
    assert tree[0]["types"] == [{
        "type": "overview",
        "pages": [{"path": "overview.md", "title": "总览"}],
    }]
    assert tree[1]["domain"] == "跨域综合"
    assert tree[2]["domain"] == "d"
    assert tree[2]["types"][0]["pages"] == [
        {"path": "domains/d/concepts/a.md", "title": "A"},
        {"path": "domains/d/concepts/b.md", "title": "B"},
    ]
    assert tree[-1]["domain"] == "d"


def test_ordered_paths_and_adjacent_paths_follow_explorer_order():
    ordered = site_layout.ordered_paths(_pages())

    assert ordered == [
        "overview.md",
        "topics/Topic.md",
        "domains/d/concepts/a.md",
        "domains/d/concepts/b.md",
    ]
    assert site_layout.adjacent_paths("overview.md", ordered) == (
        None, "topics/Topic.md")
    assert site_layout.adjacent_paths("domains/d/concepts/a.md", ordered) == (
        "topics/Topic.md", "domains/d/concepts/b.md")
    assert site_layout.adjacent_paths("topics/Topic.md", ordered) == (
        "overview.md", "domains/d/concepts/a.md")


def test_breadcrumb_contains_domain_type_and_page():
    page = _pages()[1]

    breadcrumb = site_layout.breadcrumb(page)

    assert [item["label"] for item in breadcrumb] == ["d", "concept", "B"]
    assert [item["kind"] for item in breadcrumb] == ["domain", "type", "page"]
    assert breadcrumb[0]["target"] == "domain:d"
    assert breadcrumb[1]["target"] == "domain:d|type:concept"
    assert breadcrumb[2]["target"] == page["rel"]


def test_prepare_body_adds_unique_heading_ids_and_toc():
    body, toc = site_layout.prepare_body(
        "<h1>总览</h1><p>正文</p><h2>方法 &amp; 结论</h2><h3><code>A</code></h3>",
        "p0")

    assert '<h1 id="p0-h0">总览</h1>' in body
    assert '<h2 id="p0-h1">方法 &amp; 结论</h2>' in body
    assert '<h3 id="p0-h2"><code>A</code></h3>' in body
    assert toc == [
        {"id": "p0-h0", "text": "总览", "level": 1},
        {"id": "p0-h1", "text": "方法 & 结论", "level": 2},
        {"id": "p0-h2", "text": "A", "level": 3},
    ]


def test_render_html_has_three_columns_typography_theme_and_future_slots(tmp_path):
    pages = _pages()

    def renderer(body, page_set, vault, with_images):
        return "<h1>A</h1><p>正文</p>"

    html = site_layout.render_html(
        pages,
        vault=tmp_path / "wiki",
        with_images=False,
        katex_assets=_fake_katex(),
        render_page_body=renderer,
    )

    assert "grid-template-columns" in html
    assert "minmax(0,1fr)" in html
    assert 'id="explorer"' in html
    assert 'id="toc-panel"' in html
    assert 'data-slot="local-graph"' in html
    assert 'data-slot="source-pages"' in html
    assert "max-width:72ch" in html
    assert "Microsoft YaHei" in html
    assert "study-kb-theme" in html
    assert "matchMedia" in html
    assert ".katex" in html
    assert "renderMath(tocNav" in html


def test_render_html_payload_has_toc_breadcrumbs_and_prev_next(tmp_path):
    pages = _pages()

    def renderer(body, page_set, vault, with_images):
        return "<h2>节一</h2><p>正文</p>"

    html = site_layout.render_html(
        pages,
        vault=tmp_path / "wiki",
        with_images=False,
        katex_assets=_fake_katex(),
        render_page_body=renderer,
    )
    match = re.search(
        r'<script id="pages-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))

    assert payload["pages"][0]["prev"] is None
    assert payload["pages"][0]["next"] == "topics/Topic.md"
    assert payload["pages"][-1]["prev"] == "domains/d/concepts/a.md"
    assert payload["pages"][-1]["next"] is None
    assert payload["pages"][1]["breadcrumb"] == [
        {"label": "跨域综合", "kind": "domain", "target": "cross-domain"},
        {"label": "topic", "kind": "type", "target": "cross-domain|type:topic"},
        {"label": "Topic", "kind": "page", "target": "topics/Topic.md"},
    ]
    assert payload["pages"][2]["breadcrumb"] == [
        {"label": "d", "kind": "domain", "target": "domain:d"},
        {"label": "concept", "kind": "type", "target": "domain:d|type:concept"},
        {"label": "A", "kind": "page", "target": "domains/d/concepts/a.md"},
    ]
    assert payload["pages"][2]["toc"][0]["id"] == "p2-h0"
    assert payload["tree"][0]["domain"] == "总览"


def test_render_html_fake_assets_stay_self_contained(tmp_path):
    pages = _pages()

    def renderer(body, page_set, vault, with_images):
        return "<p>正文</p>"

    html = site_layout.render_html(
        pages,
        vault=tmp_path / "wiki",
        with_images=False,
        katex_assets=_fake_katex(),
        render_page_body=renderer,
    )

    assert 'src="http' not in html
    assert "fetch(" not in html
    assert "@import" not in html
    assert "<link " not in html


def test_render_html_embeds_backlinks_graph_and_collection_views(tmp_path):
    pages = [
        {"rel": "overview.md", "title": "总览", "domain": "", "type": "overview",
         "body": "见 [[domains/d/concepts/a.md]]。"},
        {"rel": "domains/d/concepts/a.md", "title": "A", "domain": "d",
         "type": "concept", "body": "正文。", "obsidian_uri": "obsidian://open?vault=wiki&file=a.md"},
    ]

    def renderer(body, page_set, vault, with_images):
        return "<p>正文</p>"

    graph_payload = {
        "nodes": [{"id": "a", "label": "A", "type": "concept", "path": "domains/d/concepts/a.md",
                   "summary": "A summary", "community_id": "c", "weight": 1, "aliases": []}],
        "edges": [],
        "communities": [{"id": "c", "label": "C", "node_ids": ["a"], "weight": 1}],
    }
    quiz_items = [{"rel": "domains/d/concepts/a.md", "title": "A", "stem": "为什么？",
                   "answer_html": "<p>因为。</p>"}]
    proposition_items = [{"rel": "domains/d/concepts/a.md", "title": "A",
                          "name": "甲", "statement": "结论甲。"}]
    html = site_layout.render_html(
        pages,
        vault=tmp_path / "wiki",
        with_images=False,
        katex_assets=_fake_katex(),
        render_page_body=renderer,
        graph_payload=graph_payload,
        graph_view_html="<!doctype html><html><body>graph</body></html>",
        quiz_items=quiz_items,
        proposition_items=proposition_items,
    )

    assert 'id="graph-view-iframe"' in html
    assert 'data-path="__view:quiz"' in html
    assert 'data-path="__view:propositions"' in html
    assert "obsidian://open?vault=wiki&amp;file=a.md" in html
    assert "A summary" in html
    assert "为什么？" in html
    assert "结论甲。" in html


def test_graph_view_base64_uses_utf8_text_decoder_on_the_browser_side(tmp_path):
    pages = _pages()
    graph_html = "<title>知识图谱</title><body>中文节点</body>"

    html = site_layout.render_html(
        pages,
        vault=tmp_path / "wiki",
        with_images=False,
        katex_assets=_fake_katex(),
        render_page_body=lambda body, page_set, vault, with_images: "<p>正文</p>",
        graph_view_html=graph_html,
    )

    assert "new TextDecoder(\"utf-8\").decode" in html
    assert "Uint8Array.from(atob(encoded)" in html
    encoded = re.search(
        r'<script id="graph-view-data" type="application/octet-stream">(.*?)</script>',
        html,
        re.DOTALL,
    ).group(1).strip()
    assert base64.b64decode(encoded).decode("utf-8") == graph_html
