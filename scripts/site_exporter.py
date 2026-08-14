"""Static site exporter — one self-contained offline HTML for the published wiki.

P3 deliverable. This is a manual distribution action, not a vault derived layer:
it is not in any ``_DERIVED`` set, not in ``derived_violations``, not wired into the
publish hook, and not in ``retract-source``'s derived rebuild tuple. Rendering is
deterministic (sorted traversal, no timestamps) so two runs are byte-identical.

Callout structure comes exclusively from :func:`page_rules.parse_callouts` (the one
and only callout parser); this module only converts the parsed node tree to HTML.

Math is protected before Markdown rendering and restored afterwards. Restoring must
HTML-escape ``<`` / ``>`` / ``&`` (while preserving existing entities), because the
restored TeX is parsed as HTML before KaTeX reads it — an unescaped ``<`` would be
swallowed as a tag start. This is the same class as P1-R4: target-format escaping at
restore time must not be forgotten.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from html import escape as _html_escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mdpage
import page_rules
import wiki_gate

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - exercised only when the dependency is missing
    MarkdownIt = None


_QUOTE_PREFIX = re.compile(r"^ {0,3}((?:> ?)+)")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_KATEX_FONT_URL = re.compile(r"url\(fonts/([^)]+\.woff2)\)")
_MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MATH_INLINE = re.compile(r"(?<!\$)\$(?!\s)(.+?)(?<!\$)\$(?!\d)", re.DOTALL)
_HTML_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

_MD = None
if MarkdownIt is not None:
    _MD = MarkdownIt("commonmark")
    _MD.enable("table")


def _vault_pages(vault: Path) -> list[dict]:
    """Published non-derived pages, sorted by vault-relative path."""
    out: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in wiki_gate._DERIVED or rel.split("/")[0] in wiki_gate._EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published":
            continue
        title = str(meta.get("title") or meta.get("canonical_name") or Path(rel).stem)
        domain = str(meta.get("domain") or rel.split("/")[0])
        ptype = str(meta.get("type") or "")
        out.append({"rel": rel, "title": title, "domain": domain,
                    "type": ptype, "body": body})
    return out


def _resolve_target(target: str, page_set: set[str]) -> str | None:
    target = target.split("#", 1)[0].replace("\\", "/")
    if target in page_set:
        return target
    if not target.endswith(".md") and target + ".md" in page_set:
        return target + ".md"
    if target.endswith(".md") and target[:-3] in page_set:
        return target[:-3]
    return None


def _replace_wikilinks(text: str, page_set: set[str]) -> str:
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        # Obsidian 表格里把 wikilink 的 `|` 分隔符写成 `\|`；先按该转义形式拆分，
        # 否则会把 target 与 alias 误拼成同一个字符串，还可能在表格单元格里留一根裸 `|`。
        parts = inner.split("\\|", 1) if "\\|" in inner else re.split(
            r"(?<!\\)\|", inner, maxsplit=1)
        target = parts[0].strip().replace("\\", "/")
        alias = parts[1].strip() if len(parts) > 1 else ""
        resolved = _resolve_target(target, page_set)
        if resolved is None:
            return alias or Path(target).stem
        if not alias:
            alias = Path(resolved).stem
        return f"[{alias}](#/{resolved})"
    return _WIKILINK.sub(repl, text)


def _image_data_uri(vault: Path, src: str) -> str | None:
    if src.startswith(("http://", "https://", "data:")):
        return None
    candidate = (vault / src).resolve()
    if not candidate.is_file():
        return None
    ext = candidate.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(
        ext.lstrip("."), "application/octet-stream")
    b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _replace_images(text: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    def repl(match: re.Match) -> str:
        alt = match.group(1) or "图"
        if not with_images:
            return alt
        uri = _image_data_uri(vault, match.group(2))
        if uri is None:
            return alt
        return f'<img alt="{_html_escape(alt, quote=True)}" src="{uri}">'
    return _IMAGE.sub(repl, text)


def _protect_math(text: str) -> tuple[str, list[str]]:
    """先把 `$$..$$` 与 `$..$` 换成占位符，markdown 渲染后再原样还原。

    markdown-it 的 inline 规则会把公式内的 ``*`` / ``\\`` / ``\\{`` 当强调或转义处理；
    这是 P3-1 的根因。数学内容必须对 markdown 完全不透明（与 ``anki_inline_html`` 的
    “先保护 → 再处理 → 后还原”同一条纪律）。
    """
    protected: list[str] = []

    def protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"@@KATEX{len(protected) - 1}@@"

    out = _MATH_BLOCK.sub(protect, text)
    out = _MATH_INLINE.sub(protect, out)
    return out, protected


def _restore_math(html: str, protected: list[str]) -> str:
    for i, raw in enumerate(protected):
        html = html.replace(f"@@KATEX{i}@@", _escape_math_html(raw))
    return html


def _escape_math_html(raw: str) -> str:
    """把还原回 HTML 的数学文本做目标格式要求的转义。

    数学是在 markdown-it 之后才还原的，因此不能直接塞原始 `<` / `>` / `&`：HTML 解析器会
    把 `<` 当标签起始，一路吞到下一个 `>`。这与 P1-R4 的 Anki 数学绕过转义是同一类缺陷。
    已存在的实体（如源里的 `&amp;`）必须保留，不能双重转义。
    """
    protected: list[str] = []

    def keep(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00ENT{len(protected) - 1}\x00"

    out = _HTML_ENTITY.sub(keep, raw)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for i, token in enumerate(protected):
        out = out.replace(f"\x00ENT{i}\x00", token)
    return out


def _render_markdown(text: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    if _MD is None:
        raise RuntimeError("markdown-it-py is required for export-site")
    text = _replace_wikilinks(text, page_set)
    text = _replace_images(text, page_set, vault, with_images)
    text, protected = _protect_math(text)
    html = _MD.render(text)
    return _restore_math(html, protected)


def _quote_depth(line: str) -> int:
    match = _QUOTE_PREFIX.match(line)
    return match.group(1).count(">") if match else 0


def _strip_quote(line: str) -> str:
    match = _QUOTE_PREFIX.match(line)
    return line[match.end():] if match else line


def _compute_spans(lines: list[str], nodes: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    """Return (line -> node index, node index -> exclusive end line index)."""
    by_line = {node["line"] - 1: i for i, node in enumerate(nodes)}
    spans: dict[int, int] = {}
    for i, node in enumerate(nodes):
        start = node["line"] - 1
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if _quote_depth(lines[idx]) == 0:
                end = idx
                break
            other = by_line.get(idx)
            if other is not None and other != i and nodes[other]["depth"] <= node["depth"]:
                end = idx
                break
        spans[i] = end
    return by_line, spans


def _render_callout(index: int, lines: list[str], nodes: list[dict],
                    by_line: dict[int, int], spans: dict[int, int],
                    ctx: tuple[set[str], Path, bool]) -> str:
    node = nodes[index]
    start = node["line"] - 1
    end = spans[index]
    page_set, vault, with_images = ctx
    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            parts.append(_render_markdown("\n".join(buffer), page_set, vault, with_images))
            buffer.clear()

    idx = start + 1
    while idx < end:
        child = by_line.get(idx)
        if child is not None and child in node["children"]:
            flush()
            parts.append(_render_callout(child, lines, nodes, by_line, spans, ctx))
            idx = spans[child] - 1
        else:
            buffer.append(_strip_quote(lines[idx]))
        idx += 1
    flush()
    inner = "\n".join(parts)
    ctype = node["type"]
    if node["folded"]:
        summary = node["title"] or ctype
        return (f'<details class="callout callout-{ctype}" data-callout="{ctype}">'
                f'<summary>{_html_escape(summary)}</summary>'
                f'<div class="callout-body">{inner}</div></details>')
    label = node["title"] or ctype
    return (f'<div class="callout callout-{ctype}" data-callout="{ctype}">'
            f'<div class="callout-title">{_html_escape(label)}</div>'
            f'<div class="callout-body">{inner}</div></div>')


def render_page_body(body: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    nodes, _errors = page_rules.parse_callouts(body)
    if not nodes:
        return _render_markdown(body, page_set, vault, with_images)
    lines = body.splitlines()
    by_line, spans = _compute_spans(lines, nodes)
    ctx = (page_set, vault, with_images)
    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            parts.append(_render_markdown("\n".join(buffer), page_set, vault, with_images))
            buffer.clear()

    idx = 0
    while idx < len(lines):
        node_idx = by_line.get(idx)
        if node_idx is not None:
            flush()
            parts.append(_render_callout(node_idx, lines, nodes, by_line, spans, ctx))
            idx = spans[node_idx] - 1
        else:
            buffer.append(lines[idx])
        idx += 1
    flush()
    return "\n".join(parts)


def load_katex(vendor_dir: str | Path | None = None) -> dict:
    """Read KaTeX vendor assets (js/css/auto-render/fonts base64)."""
    vd = Path(vendor_dir) if vendor_dir else Path(__file__).resolve().parent / "vendor" / "katex"
    js = (vd / "katex.min.js").read_text(encoding="utf-8")
    css = (vd / "katex.min.css").read_text(encoding="utf-8")
    auto_render_js = (vd / "auto-render.min.js").read_text(encoding="utf-8")
    fonts: dict[str, str] = {}
    fonts_dir = vd / "fonts"
    if fonts_dir.is_dir():
        for f in sorted(fonts_dir.glob("*.woff2")):
            fonts[f.name] = base64.b64encode(f.read_bytes()).decode("ascii")
    return {"js": js, "css": css, "auto_render_js": auto_render_js, "fonts": fonts}


def _inline_katex_css(assets: dict) -> str:
    def repl(match: re.Match) -> str:
        b64 = assets.get("fonts", {}).get(match.group(1))
        return f"url(data:font/woff2;base64,{b64})" if b64 is not None else match.group(0)
    return _KATEX_FONT_URL.sub(repl, assets["css"])


_BASE_CSS = r"""
:root{--bg:#f7f8fa;--panel:#fff;--line:#e3e6ea;--text:#1f2733;--muted:#6b7480;
--accent:#2f6fed;--accent-soft:#eaf1ff;--code:#f2f4f7;--warn:#8a5b00}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.65}
#app{display:flex;min-height:100vh}
#sidebar{width:320px;flex:none;background:var(--panel);border-right:1px solid var(--line);
position:sticky;top:0;height:100vh;overflow:auto;padding:16px}
.brand{font-weight:700;font-size:17px;margin:0 0 10px}
#search{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;
font-size:14px;background:#fff;margin-bottom:12px}
.nav-group{margin:0 0 14px}
.nav-domain{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;
letter-spacing:.04em;margin:12px 0 6px}
.nav-item{display:block;width:100%;text-align:left;background:none;border:0;padding:7px 9px;
border-radius:7px;color:var(--text);font-size:14px;cursor:pointer;word-break:break-word}
.nav-item:hover{background:var(--accent-soft)}
.nav-item.active{background:var(--accent);color:#fff}
.badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:99px;
font-size:11px;background:#eef1f5;color:var(--muted)}
#main{flex:1;min-width:0;padding:34px clamp(18px,5vw,76px) 90px}
.page{max-width:860px}
.page[hidden]{display:none}
.page-head h1{margin:0 0 6px;font-size:clamp(26px,4vw,38px);line-height:1.2}
.page-meta{color:var(--muted);font-size:13px;margin-bottom:22px}
.page-body h1,.page-body h2,.page-body h3{margin:1.4em 0 .5em;line-height:1.3}
.page-body p{margin:.7em 0}
.page-body pre{background:var(--code);padding:13px 15px;border-radius:8px;overflow:auto}
.page-body code{background:var(--code);padding:2px 5px;border-radius:5px;font-size:.92em}
.page-body pre code{background:none;padding:0}
.page-body table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px;display:block;overflow-x:auto}
.page-body th,.page-body td{border:1px solid var(--line);padding:8px 10px;text-align:left}
.page-body blockquote{margin:12px 0;padding:4px 0 4px 14px;border-left:3px solid var(--line);color:#3d4652}
.callout{border:1px solid var(--line);border-left:5px solid var(--accent);border-radius:8px;
margin:14px 0;background:#fff}
.callout-title{font-weight:700;padding:9px 13px;border-bottom:1px solid var(--line);font-size:14px}
.callout-body{padding:11px 14px}
details.callout>.callout-body{padding-top:10px}
details.callout>summary{cursor:pointer;padding:9px 13px;font-weight:700;font-size:14px;list-style:none}
details.callout>summary::before{content:"▸ ";color:var(--accent)}
details.callout[open]>summary::before{content:"▾ "}
.callout-question{border-left-color:#2f6fed}
.callout-success{border-left-color:#2e9e5b}
.callout-warning{border-left-color:#d97706}
.callout-important{border-left-color:#dc2626}
.callout-example,.callout-abstract,.callout-tip{border-left-color:#7c3aed}
.callout-summary,.callout-info,.callout-note{border-left-color:#0e7490}
.callout-quote,.callout-todo{border-left-color:#64748b}
.menu-btn{display:none;position:fixed;left:14px;top:14px;z-index:10;border:1px solid var(--line);
background:#fff;border-radius:8px;padding:7px 10px;font-size:14px}
@media(max-width:760px){
#sidebar{position:fixed;left:0;top:0;z-index:9;transform:translateX(-100%);transition:transform .18s ease}
#sidebar.open{transform:none}
#main{padding:64px 16px 60px}
.menu-btn{display:block}
}
"""


_APP_JS = r"""
"use strict";
(function(){
  var pages = JSON.parse(document.getElementById("pages-data").textContent);
  var nav = document.getElementById("nav");
  var search = document.getElementById("search");
  var main = document.getElementById("main");
  var byPath = {};
  var articles = Array.prototype.slice.call(main.querySelectorAll("article.page"));
  articles.forEach(function(a){ byPath[a.getAttribute("data-path")] = a; });

  function esc(s){ var d=document.createElement("span"); d.textContent=s; return d.innerHTML; }
  function domainOrder(domains){
    var sorted = domains.slice().sort(function(a,b){return a<b?-1:1;});
    if(sorted[0]==="overview"){ sorted.splice(0,1); }
    return sorted;
  }
  function buildNav(filter){
    var groups = {};
    pages.forEach(function(p){
      var hay = (p.title+" "+p.domain+" "+p.path).toLowerCase();
      if(filter && hay.indexOf(filter)<0) return;
      (groups[p.domain]=groups[p.domain]||[]).push(p);
    });
    var html="";
    domainOrder(Object.keys(groups)).forEach(function(domain){
      html += '<div class="nav-group"><div class="nav-domain">'+esc(domain)+'</div>';
      groups[domain].forEach(function(p){
        html += '<button class="nav-item" data-path="'+esc(p.path)+'">'+esc(p.title)
             + '<span class="badge">'+esc(p.type)+'</span></button>';
      });
      html += '</div>';
    });
    nav.innerHTML = html;
    Array.prototype.forEach.call(nav.querySelectorAll(".nav-item"), function(btn){
      btn.addEventListener("click", function(){ location.hash = "#/"+btn.getAttribute("data-path"); });
    });
  }
  function currentPath(){
    var h = decodeURIComponent(location.hash||"");
    if(h.indexOf("#/")===0) return h.slice(2);
    return "";
  }
  function defaultPath(){
    if(byPath["overview.md"]) return "overview.md";
    return pages.length ? pages[0].path : "";
  }
  function route(){
    var path = currentPath() || defaultPath();
    var el = byPath[path];
    if(!el && path) path = defaultPath(), el = byPath[path];
    articles.forEach(function(a){ a.hidden = (a!==el); });
    Array.prototype.forEach.call(nav.querySelectorAll(".nav-item"), function(btn){
      btn.classList.toggle("active", btn.getAttribute("data-path")===path);
    });
    if(el) document.title = el.getAttribute("data-title") + " · 学习知识库";
    main.scrollTop = 0; window.scrollTo(0,0);
  }
  search.addEventListener("input", function(){
    var q = (search.value||"").trim().toLowerCase();
    buildNav(q);
    if(!q) route();
  });
  document.getElementById("menu-btn").addEventListener("click", function(){
    document.getElementById("sidebar").classList.toggle("open");
  });
  window.addEventListener("hashchange", route);
  buildNav("");
  route();
  if(window.renderMathInElement){
    renderMathInElement(main, {
      delimiters:[{left:"$$",right:"$$",display:true},{left:"$",right:"$",display:false}],
      throwOnError:false,
      ignoredTags:["script","noscript","style","textarea","pre","code","option"]
    });
  }
})();
"""


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>学习知识库（离线）</title>
<style>__BASE_CSS__</style>
<style>__KATEX_CSS__</style>
</head>
<body>
<button id="menu-btn" class="menu-btn" type="button">目录</button>
<div id="app">
 <aside id="sidebar">
  <div class="brand">学习知识库</div>
  <input id="search" type="search" placeholder="搜索标题 / 正文">
  <nav id="nav"></nav>
 </aside>
 <main id="main">__PAGES_HTML__</main>
</div>
<script id="pages-data" type="application/json">__PAGES_INDEX__</script>
<script>__KATEX_JS__</script>
<script>__AUTORENDER_JS__</script>
<script>__APP_JS__</script>
</body>
</html>
"""


class SiteResult:
    def __init__(self, path: Path, page_count: int):
        self.path = path
        self.page_count = page_count


def _render_html(pages: list[dict], vault: Path, with_images: bool,
                 katex_assets: dict) -> str:
    page_set = {p["rel"] for p in pages}
    articles: list[str] = []
    index_rows: list[dict] = []
    for page in pages:
        body_html = render_page_body(page["body"], page_set, vault, with_images)
        rel_esc = _html_escape(page["rel"], quote=True)
        title_esc = _html_escape(page["title"])
        domain_esc = _html_escape(page["domain"], quote=True)
        type_esc = _html_escape(page["type"], quote=True)
        articles.append(
            f'<article class="page" data-path="{rel_esc}" data-title="{title_esc}" '
            f'data-domain="{domain_esc}" data-type="{type_esc}">'
            f'<header class="page-head"><h1>{title_esc}</h1>'
            f'<div class="page-meta">{_html_escape(page["domain"])} · {_html_escape(page["type"])}</div>'
            f'</header><div class="page-body">{body_html}</div></article>'
        )
        index_rows.append({"path": page["rel"], "title": page["title"],
                           "domain": page["domain"], "type": page["type"]})
    html = _HTML_TEMPLATE
    html = html.replace("__BASE_CSS__", _BASE_CSS)
    html = html.replace("__KATEX_CSS__", _inline_katex_css(katex_assets))
    html = html.replace("__KATEX_JS__", katex_assets["js"])
    html = html.replace("__AUTORENDER_JS__", katex_assets["auto_render_js"])
    html = html.replace("__APP_JS__", _APP_JS)
    html = html.replace("__PAGES_HTML__", "\n".join(articles))
    html = html.replace("__PAGES_INDEX__",
                        json.dumps(index_rows, ensure_ascii=False).replace("</", "<\\/"))
    return html


def build_site(vault: str | Path, workspace: str | Path | None = None, *,
               with_images: bool = False, vendor_dir: str | Path | None = None,
               katex_assets: dict | None = None) -> str:
    """Build the self-contained site HTML (pure: reads only, no timestamps)."""
    vault = Path(vault)
    pages = _vault_pages(vault)
    if katex_assets is None:
        katex_assets = load_katex(vendor_dir)
    return _render_html(pages, vault, with_images, katex_assets)


def write_site(vault: str | Path, workspace: str | Path | None = None, *,
               with_images: bool = False, vendor_dir: str | Path | None = None,
               katex_assets: dict | None = None) -> SiteResult:
    vault = Path(vault)
    workspace = Path(workspace) if workspace is not None else vault.parent
    pages = _vault_pages(vault)
    if katex_assets is None:
        katex_assets = load_katex(vendor_dir)
    html = _render_html(pages, vault, with_images, katex_assets)
    out = workspace / "pipeline-workspace" / "exports" / "site" / "study-kb.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return SiteResult(path=out, page_count=len(pages))
