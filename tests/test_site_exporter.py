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
mdpage = _load("mdpage")


def _page(vault, rel, meta, body):
    mdpage.write_page(Path(vault) / rel, meta, body)


def _fake_katex():
    return {"js": "/*katex*/", "css": "@font-face{src:url(fonts/X.woff2)}",
            "auto_render_js": "window.renderMathInElement=function(){};",
            "fonts": {"X.woff2": "AA=="}}


def test_collect_pages_published_only_and_sorted(tmp_path):
    vault = tmp_path / "wiki"
    _page(vault, "domains/d/concepts/b.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "B"}, "正文B。")
    _page(vault, "domains/d/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "A"}, "正文A。")
    _page(vault, "domains/d/concepts/draft.md",
          {"type": "concept", "status": "proposed", "domain": "d", "title": "草稿"}, "未发布。")
    pages = site._vault_pages(vault)
    assert [p["rel"] for p in pages] == [
        "domains/d/concepts/a.md", "domains/d/concepts/b.md"]
    assert all(p["type"] == "concept" for p in pages)


def test_render_table_and_inline_math_preserved(tmp_path):
    vault = tmp_path / "wiki"
    html = site.render_page_body("|a|b|\n|-|-|\n|1|2|\n\n成本 $c_i$", set(), vault, False)
    assert "<table>" in html
    assert "<td>1</td>" in html
    assert "$c_i$" in html


def test_render_callout_folded_uses_details(tmp_path):
    vault = tmp_path / "wiki"
    body = "> [!question] 自测\n> 题干？\n> > [!success]- 答案\n> > 答。\n"
    html = site.render_page_body(body, set(), vault, False)
    assert 'class="callout callout-question"' in html
    assert "<details" in html
    assert "答案" in html
    assert "答。" in html


def test_wikilink_existing_and_missing(tmp_path):
    vault = tmp_path / "wiki"
    page_set = {"domains/d/concepts/a.md"}
    html = site.render_page_body("见 [[domains/d/concepts/a.md|甲]] 与 [[missing.md|乙]]",
                                 page_set, vault, False)
    assert '<a href="#/domains/d/concepts/a.md">甲</a>' in html
    assert "乙" in html


def test_table_wikilink_escaped_pipe_renders_table(tmp_path):
    vault = tmp_path / "wiki"
    page_set = {"domains/d/concepts/a.md"}
    body = ("| 维度 | 甲 |\n"
            "|---|---|\n"
            "| 查找 | [[domains/d/concepts/a.md\\|甲]] |\n")
    html = site.render_page_body(body, page_set, vault, False)
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
        html = site.render_page_body(source, page_set, vault, False)
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
    html = site.render_page_body(body, set(), vault, False)
    assert "<em>" not in html
    for source in formulas:
        # 经 HTML 实体解码后必须逐字符还原源公式；不能有 < > & 被 HTML 解析器吞掉。
        escaped = html_mod.escape(source, quote=False)
        assert escaped in html, (source, html)
        assert html_mod.unescape(escaped) == source


def test_build_site_deterministic_and_writes_self_contained(tmp_path):
    vault = tmp_path / "wiki"
    _page(vault, "overview.md",
          {"type": "overview", "status": "published", "domain": "root", "title": "总览"},
          "> [!note] 提示\n> 见 [[domains/d/concepts/a.md|甲]]\n")
    _page(vault, "domains/d/concepts/a.md",
          {"type": "concept", "status": "published", "domain": "d", "title": "甲"},
          "正文 $c_i$。\n")
    assets = _fake_katex()
    one = site.build_site(vault, workspace=tmp_path, katex_assets=assets)
    two = site.build_site(vault, workspace=tmp_path, katex_assets=assets)
    assert one == two
    assert "data:font/woff2;base64,AA==" in one
    result = site.write_site(vault, workspace=tmp_path, katex_assets=assets)
    assert result.path.read_text(encoding="utf-8") == one
    assert result.page_count == 2
