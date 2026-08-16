import importlib.util
import html as html_mod
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


site = _load("site_exporter")
site_render = _load("site_render")
mdpage = _load("mdpage")
wiki_gate = _load("wiki_gate")


def _page(vault, rel, meta, body):
    mdpage.write_page(Path(vault) / rel, meta, body)


def _fake_katex():
    return {"js": "/*katex*/", "css": "@font-face{src:url(fonts/X.woff2)}",
            "auto_render_js": "window.renderMathInElement=function(){};",
            "fonts": {"X.woff2": "AA=="}}


def test_collect_pages_published_only_and_sorted(tmp_path):
    vault = tmp_path / "wiki"
    _page(vault, "domains/d/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "B",
           "aliases": ["B别名"], "source_refs": ["book-a"]}, "正文B。")
    _page(vault, "domains/d/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "A"}, "正文A。")
    _page(vault, "domains/d/concepts/draft.md",
          {"type": "concept", "status": "proposed", "domain": "d", "title": "草稿"}, "未发布。")
    pages = site._vault_pages(vault)
    assert [p["rel"] for p in pages] == [
        "domains/d/concepts/a.md", "domains/d/concepts/b.md"]
    assert all(p["type"] == "concept" for p in pages)
    assert pages[0]["obsidian_uri"] == wiki_gate.obsidian_uri(vault, pages[0]["rel"])
    assert pages[1]["aliases"] == ["B别名"]
    assert pages[1]["source_refs"] == ["book-a"]


def test_render_table_and_inline_math_preserved(tmp_path):
    vault = tmp_path / "wiki"
    html = site_render.render_page_body(
        "|a|b|\n|-|-|\n|1|2|\n\n成本 $c_i$", set(), vault, False)
    assert "<table>" in html
    assert "<td>1</td>" in html
    assert "$c_i$" in html


def test_render_callout_folded_uses_details(tmp_path):
    vault = tmp_path / "wiki"
    body = "> [!question] 自测\n> 题干？\n> > [!success]- 答案\n> > 答。\n"
    html = site_render.render_page_body(body, set(), vault, False)
    assert 'class="callout callout-question"' in html
    assert "<details" in html
    assert "答案" in html
    assert "答。" in html


def test_wikilink_existing_and_missing(tmp_path):
    vault = tmp_path / "wiki"
    page_set = {"domains/d/concepts/a.md"}
    html = site_render.render_page_body(
        "见 [[domains/d/concepts/a.md|甲]] 与 [[missing.md|乙]]",
        page_set, vault, False)
    assert '<a href="#/domains/d/concepts/a.md">甲</a>' in html
    assert "乙" in html


def test_table_wikilink_escaped_pipe_renders_table(tmp_path):
    vault = tmp_path / "wiki"
    page_set = {"domains/d/concepts/a.md"}
    body = ("| 维度 | 甲 |\n"
            "|---|---|\n"
            "| 查找 | [[domains/d/concepts/a.md\\|甲]] |\n")
    html = site_render.render_page_body(body, page_set, vault, False)
    assert "<table>" in html
    assert '<a href="#/domains/d/concepts/a.md">甲</a>' in html
    assert "<td>甲</td>" not in html


def test_math_asterisk_backslash_and_brace_survive_markdown(tmp_path):
    vault = tmp_path / "wiki"
    page_set = set()
    cases = [
        "$p_1^*=p_2^*=c$",
        "$$a&b\\\\c&d$$",
        "$\\left\\{x\\right\\}$",
    ]
    for source in cases:
        html = site_render.render_page_body(source, page_set, vault, False)
        # 还原到 HTML 时会转义 `&`/`<`/`>`；实体解码后必须逐字符回到源公式。
        assert source in html_mod.unescape(html), (source, html)
        assert "*<em>" not in html
        assert "<em>" not in html


def test_math_angle_and_entity_escape_roundtrip(tmp_path):
    vault = tmp_path / "wiki"
    formulas = [
        "$p(x_t\\mid x_{<t})$",
        "$0<T<1$",
        "$$q_i(p_i,p_j)=\\begin{cases}D(p_i) & p_i<p_j\\\\ \\tfrac12 D(p_i) & p_i=p_j\\\\ 0 & p_i>p_j\\end{cases}$$",
    ]
    body = "\n\n".join(formulas)
    html = site_render.render_page_body(body, set(), vault, False)
    assert "<em>" not in html
    for source in formulas:
        # 经 HTML 实体解码后必须逐字符还原源公式；不能有 < > & 被 HTML 解析器吞掉。
        escaped = html_mod.escape(source, quote=False)
        assert escaped in html, (source, html)
        assert html_mod.unescape(escaped) == source


def test_with_images_no_longer_inlines_body_images(tmp_path):
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "x.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    body = "![插图](x.png)"

    inline = site_render.render_page_body(body, set(), vault, True)
    text_only = site_render.render_page_body(body, set(), vault, False)

    assert inline == "<p>插图</p>\n"
    assert text_only == "<p>插图</p>\n"


def test_site_graph_view_keeps_graph_html_degraded_thresholds(tmp_path):
    payload = {
        "nodes": [
            {"id": f"n{i}", "label": f"N{i}", "type": "concept", "path": f"{i}.md",
             "summary": "", "community_id": "c", "weight": 1, "aliases": []}
            for i in range(501)
        ],
        "edges": [],
        "communities": [{"id": "c", "label": "C", "node_ids": ["n0"], "weight": 1}],
    }

    html = site._graph_view_html(tmp_path / "wiki", payload)

    assert "__GRAPH_DEGRADED__ = true" in html
    assert 'id="degraded-banner"' in html


def test_site_graph_view_routes_to_site_and_uses_wiki_obsidian_uri(tmp_path):
    vault = tmp_path / "wiki"
    payload = {
        "nodes": [{"id": "n", "label": "中文节点", "type": "concept", "path": "a.md",
                   "summary": "", "community_id": "c", "weight": 1, "aliases": []}],
        "edges": [],
        "communities": [{"id": "c", "label": "社区", "node_ids": ["n"], "weight": 1}],
    }

    html = site._graph_view_html(vault, payload)

    assert "obsidian://open?path=" not in html
    assert "window.parent.location.hash" in html
    assert wiki_gate.obsidian_uri(vault, "a.md") in html


def test_source_panel_types_are_knowledge_types_not_source_ledgers():
    pages = [
        {"rel": "concept.md", "type": "concept"},
        {"rel": "topic.md", "type": "topic"},
        {"rel": "comparison.md", "type": "comparison"},
        {"rel": "synthesis.md", "type": "synthesis"},
        {"rel": "source.md", "type": "source"},
        {"rel": "overview.md", "type": "overview"},
    ]

    assert site._source_panel_rels(pages) == {
        "concept.md", "topic.md", "comparison.md", "synthesis.md"
    }


def test_build_site_deterministic_and_writes_self_contained(tmp_path):
    vault = tmp_path / "wiki"
    _page(vault, "overview.md",
          {"type": "overview", "status": "published", "domain": "root", "title": "总览"},
          "> [!note] 提示\n> 见 [[domains/d/concepts/a.md|甲]]\n")
    _page(vault, "domains/d/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "甲"},
          "正文 $c_i$。\n")
    assets = _fake_katex()
    one = site.build_site(vault, katex_assets=assets)
    two = site.build_site(vault, katex_assets=assets)
    assert one == two
    assert "data:font/woff2;base64,AA==" in one
    result = site.write_site(vault, workspace=tmp_path, katex_assets=assets)
    assert result.path.read_text(encoding="utf-8") == one
    assert result.page_count == 2


def test_default_write_site_removes_stale_assets_directory(tmp_path):
    vault = tmp_path / "wiki"
    _page(vault, "overview.md",
          {"type": "overview", "status": "published", "domain": "root", "title": "总览"},
          "正文。")
    site.write_site(vault, workspace=tmp_path, katex_assets=_fake_katex())
    assets = tmp_path / "pipeline-workspace" / "exports" / "site" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "stale.png").write_bytes(b"stale")

    site.write_site(vault, workspace=tmp_path, katex_assets=_fake_katex())

    assert not assets.exists()
