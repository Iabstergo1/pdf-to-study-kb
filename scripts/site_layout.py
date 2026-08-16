"""Offline-site layout and interaction layer for the P4 reading UI.

This module owns the page shell, Explorer model, breadcrumbs, in-page TOC,
previous/next ordering, theme state, and responsive drawer behaviour. It does
not parse Markdown or callouts: content rendering is injected as
``render_page_body``, so ``page_rules.parse_callouts`` and the exporter's
protect -> process -> restore math pipeline remain the only content parsers.

The only post-render transformation here is adding deterministic heading IDs to
the already-rendered HTML and reading those same heading tags for TOC labels.
That annotation pass is deliberately layout-only; it never re-parses Markdown.

Graph-view HTML is UTF-8 encoded in Python before base64. Browser-side restore
must therefore decode ``atob``'s latin-1 binary string back to UTF-8 bytes with
``TextDecoder``; assigning the binary string directly corrupts every non-ASCII
character. This restore step owns the target encoding contract.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from html import escape as _html_escape
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_data
import site_media


_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_HEADING_START_RE = re.compile(r"<h([1-6])(?=\s|>)", re.IGNORECASE)
_KATEX_FONT_URL = re.compile(r"url\(fonts/([^)]+\.woff2)\)")


class _HeadingText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def value(self) -> str:
        return "".join(self.parts).strip()


def _heading_text(html: str) -> str:
    parser = _HeadingText()
    parser.feed(html)
    parser.close()
    return parser.value()


def _domain_label(page: dict) -> str:
    return site_data.classify_page(page)["label"]


def build_explorer_tree(pages: list[dict]) -> list[dict]:
    """Group pages as domain -> type -> page, with the overview branch first."""
    return site_data.build_explorer_tree(pages)


def ordered_paths(pages: list[dict]) -> list[str]:
    """Flatten the Explorer tree into the navigation order used by A7."""
    return site_data.ordered_paths(pages)


def adjacent_paths(path: str, ordered: list[str]) -> tuple[str | None, str | None]:
    return site_data.adjacent_paths(path, ordered)


def breadcrumb(page: dict) -> list[dict]:
    """Return domain / type / page entries with deterministic tree targets."""
    return site_data.breadcrumb(page)


def prepare_body(body_html: str, prefix: str) -> tuple[str, list[dict]]:
    """Add per-page-unique heading IDs and return the TOC model.

    IDs are ``<prefix>-h<position>``. The TOC text is extracted from the rendered
    heading with an HTML parser; KaTeX runs afterwards and does not change the
    heading tag, so its scroll targets remain stable.
    """
    toc: list[dict] = []

    def add_id(match: re.Match) -> str:
        original = match.group(0)
        open_tag, rest = original.split(">", 1)
        open_tag += ">"
        level = int(match.group(1))
        heading_id = f"{prefix}-h{len(toc)}"
        annotated_open = _HEADING_START_RE.sub(
            lambda inner: f'{inner.group(0)} id="{heading_id}"',
            open_tag,
            count=1,
        )
        toc.append({
            "id": heading_id,
            "text": _heading_text(original),
            "level": level,
        })
        return annotated_open + rest

    annotated = _HEADING_RE.sub(add_id, body_html)
    return annotated, toc


def _inline_katex_css(assets: dict) -> str:
    def repl(match: re.Match) -> str:
        b64 = assets.get("fonts", {}).get(match.group(1))
        return f"url(data:font/woff2;base64,{b64})" if b64 is not None else match.group(0)

    return _KATEX_FONT_URL.sub(repl, assets["css"])


_THEME_JS = r"""
(function(){
  var saved=null;
  try{saved=localStorage.getItem("study-kb-theme");}catch(e){}
  var dark=saved==="dark"||(!saved&&window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme",dark?"dark":"light");
})();
"""


_BASE_CSS = r"""
:root{
 --bg:#f7f8fa;--panel:#fff;--panel-muted:#f2f4f7;--line:#dde2e8;--line-strong:#cbd2da;
 --text:#1f2733;--muted:#66717e;--accent:#2f6fed;--accent-soft:#eaf1ff;
 --accent-soft-strong:#dbe7ff;--code:#f1f3f6;--code-text:#26303c;--warn:#8a5b00;
 --callout-bg:#fff;--callout-title:#fff;--scrim:rgba(17,24,39,.28);
}
[data-theme="dark"]{
 --bg:#12161c;--panel:#191f27;--panel-muted:#212a34;--line:#303a46;--line-strong:#414d5b;
 --text:#e7ebf0;--muted:#9ca8b5;--accent:#7da5ff;--accent-soft:#22304a;
 --accent-soft-strong:#2d4263;--code:#111720;--code-text:#e1e8f0;--warn:#e5b45d;
 --callout-bg:#1b222b;--callout-title:#202933;--scrim:rgba(0,0,0,.56);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",
"Hiragino Sans GB","Noto Sans CJK SC",system-ui,sans-serif;line-height:1.75;
overflow-x:hidden;text-rendering:optimizeLegibility}
button,input{font:inherit}
button{color:inherit}
#app{display:grid;grid-template-columns:minmax(248px,300px) minmax(0,1fr) minmax(220px,260px);
min-height:100vh}
#explorer,#toc-panel{position:sticky;top:0;height:100vh;overflow:auto;background:var(--panel);
scrollbar-width:thin}
#explorer{border-right:1px solid var(--line);padding:20px 14px}
#toc-panel{border-left:1px solid var(--line);padding:20px 14px}
.panel-title{font-weight:700;font-size:14px;letter-spacing:0;margin:0 0 12px;
display:flex;align-items:center;justify-content:space-between;gap:8px}
.panel-title .toolbar-btn{height:30px;min-width:38px;padding:0 7px;font-size:12px}
.view-switcher{display:flex;gap:6px;margin:0 0 12px}
.view-switcher-btn{flex:1;min-width:0;height:30px;border:1px solid var(--line);
background:var(--panel);border-radius:7px;font-size:12px;cursor:pointer}
.view-switcher-btn:hover{background:var(--accent-soft)}
#search{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;
font-size:14px;background:var(--panel);color:var(--text);margin-bottom:12px}
#search:focus{outline:2px solid var(--accent);outline-offset:1px}
.filter-row{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:12px}
.filter-row select{min-width:0;width:100%;height:30px;padding:0 6px;border:1px solid var(--line);
border-radius:7px;background:var(--panel);color:var(--text);font-size:12px}
.search-results{max-height:52vh;overflow:auto;margin:0 0 12px;border-top:1px solid var(--line);
padding-top:8px}
.search-result{display:block;width:100%;text-align:left;border:0;background:none;
border-radius:7px;padding:8px;cursor:pointer;color:var(--text)}
.search-result:hover,.search-result.active{background:var(--accent-soft)}
.search-result-title{font-weight:650;font-size:13.5px;line-height:1.4;overflow-wrap:anywhere}
.search-result-meta{color:var(--muted);font-size:11px;margin:3px 0;overflow-wrap:anywhere}
.search-result-snippet{color:var(--muted);font-size:12.5px;line-height:1.5;
overflow-wrap:anywhere;max-height:60px;overflow:hidden}
.search-result mark{background:var(--accent-soft-strong);color:var(--text);padding:0 1px;
border-radius:2px}
.searching #nav{display:none}
#nav{min-width:0}
.tree-domain,.tree-type,.nav-item{display:flex;width:100%;text-align:left;background:none;
border:0;padding:7px 8px;border-radius:7px;color:var(--text);cursor:pointer;gap:7px;
align-items:flex-start;min-width:0}
.tree-domain,.tree-type{font-weight:600;color:var(--muted)}
.tree-domain{font-size:13px}
.tree-type{font-size:12px;padding-left:22px}
.tree-twisty{flex:none;width:12px;color:var(--muted);font-size:11px;line-height:1.5}
.tree-domain.open .tree-twisty,.tree-type.open .tree-twisty{transform:rotate(90deg)}
.tree-children{display:none;margin:0 0 4px;padding:0}
.tree-children.open{display:block}
.tree-domain .tree-children,.tree-type .tree-children{margin-left:8px}
.nav-item{font-size:13.5px;padding-left:36px;line-height:1.45;overflow-wrap:anywhere}
.nav-item:hover,.tree-domain:hover,.tree-type:hover{background:var(--accent-soft)}
.nav-item.active{background:var(--accent);color:#fff}
.nav-item .badge{margin-left:auto;flex:none;padding:1px 6px;border-radius:99px;
font-size:10px;background:var(--panel-muted);color:var(--muted)}
.nav-item.active .badge{background:rgba(255,255,255,.2);color:#fff}
#local-graph-slot,#backlinks{margin-top:22px;border-top:1px solid var(--line);padding-top:16px}
.source-pages-panel{margin-top:34px;border-top:1px solid var(--line);padding-top:18px}
.source-pages-note{font-size:12.5px;line-height:1.6;color:var(--muted);margin:6px 0 12px}
.source-pages-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));
gap:10px}
.source-page-thumb{display:block;border:1px solid var(--line);border-radius:8px;
overflow:hidden;background:var(--panel-muted);min-height:84px}
.source-page-thumb img{width:100%;height:112px;object-fit:contain;display:block;
background:var(--panel-muted)}
#local-graph{height:210px;position:relative;overflow:hidden;border:1px solid var(--line);
border-radius:8px;background:var(--panel-muted)}
#local-graph svg{width:100%;height:100%;display:block}
.backlink-item{display:block;width:100%;text-align:left;border:0;background:none;
color:var(--text);padding:6px 8px;border-radius:6px;font-size:12.5px;line-height:1.45;
cursor:pointer;overflow-wrap:anywhere}
.backlink-item:hover{background:var(--accent-soft)}
.backlink-path{display:block;color:var(--muted);font-size:11px;margin-top:2px;
overflow-wrap:anywhere}
.obsidian-link{color:var(--accent);white-space:nowrap}
#graph-view.modal{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;
background:var(--bg);padding:0}
#graph-view[hidden]{display:none}
.modal-bar{display:flex;align-items:center;justify-content:space-between;height:52px;
padding:0 14px;border-bottom:1px solid var(--line);background:var(--panel)}
.modal-bar .toolbar-btn{height:32px}
#graph-view-iframe{flex:1;width:100%;border:0;background:#fff}
.preview-popover{position:fixed;z-index:60;width:min(360px,calc(100vw - 24px));
background:var(--panel);border:1px solid var(--line-strong);border-radius:8px;
box-shadow:0 12px 30px rgba(0,0,0,.18);padding:12px 14px;pointer-events:auto}
.preview-popover[hidden]{display:none}
.preview-title{font-weight:700;font-size:14px;line-height:1.4;overflow-wrap:anywhere}
.preview-meta{color:var(--muted);font-size:11.5px;margin:4px 0 8px}
.preview-summary{font-size:13px;line-height:1.6;color:var(--text);max-height:180px;
overflow:auto;overflow-wrap:anywhere}
.collection-view .page-body{max-width:none}
.quiz-entry,.proposition-entry{margin:0 0 14px;border:1px solid var(--line);
border-radius:8px;background:var(--panel)}
.quiz-entry{padding:0}
.quiz-entry>summary{cursor:pointer;font-weight:700;padding:10px 12px;
background:var(--panel-muted);border-radius:8px;font-size:15px;overflow-wrap:anywhere}
.quiz-answer,.proposition-entry{padding:10px 12px}
.proposition-entry h2{font-size:14px;margin:0 0 5px}
.proposition-entry p{margin:0;color:var(--text)}
.source-link{display:inline-block;margin-top:8px;font-size:12.5px;color:var(--accent);
text-decoration:none}
#main{min-width:0;padding:34px clamp(18px,4vw,64px) 88px}
.page{max-width:72ch;margin:0 auto;padding:0 0 48px}
.page[hidden]{display:none}
.breadcrumbs{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:0 0 12px;
color:var(--muted);font-size:12.5px;line-height:1.4}
.crumb{background:none;border:0;padding:1px 2px;color:var(--muted);cursor:pointer;
border-radius:4px;font-size:12.5px;max-width:100%;overflow-wrap:anywhere}
.crumb:hover{color:var(--accent);background:var(--accent-soft)}
.crumb[aria-current="page"]{color:var(--text);font-weight:600}
.crumb-sep{color:var(--line-strong)}
.page-head h1{margin:0 0 8px;font-size:clamp(26px,4vw,38px);line-height:1.25;
letter-spacing:0;overflow-wrap:anywhere}
.page-meta{color:var(--muted);font-size:12.5px;margin-bottom:24px}
.page-body{font-size:16px;line-height:1.8}
.page-body>*{max-width:100%}
.page-body h1,.page-body h2,.page-body h3,.page-body h4,.page-body h5,.page-body h6{
margin:1.65em 0 .55em;line-height:1.35;letter-spacing:0;overflow-wrap:anywhere}
.page-body h1{font-size:1.75em;border-bottom:1px solid var(--line);padding-bottom:.25em}
.page-body h2{font-size:1.45em;border-bottom:1px solid var(--line);padding-bottom:.22em}
.page-body h3{font-size:1.2em}
.page-body h4{font-size:1.05em}
.page-body h5,.page-body h6{font-size:1em;color:var(--muted)}
.page-body p{margin:.8em 0;overflow-wrap:anywhere}
.page-body a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:2px}
.page-body a:hover{text-decoration-thickness:2px}
.page-body ul,.page-body ol{padding-left:1.65em;margin:.8em 0}
.page-body li{margin:.35em 0}
.page-body pre{background:var(--code);color:var(--code-text);padding:14px 16px;
border:1px solid var(--line);border-radius:8px;overflow:auto;max-width:100%;
font-family:"Cascadia Mono","JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",monospace;
font-size:.88em;line-height:1.6}
.page-body pre code{white-space:pre;overflow-wrap:normal}
.page-body code{background:var(--code);color:var(--code-text);padding:2px 5px;
border-radius:5px;font-size:.88em;font-family:"Cascadia Mono","JetBrains Mono",
"SFMono-Regular",Consolas,"Liberation Mono",monospace;overflow-wrap:anywhere}
.page-body pre code{background:none;padding:0;overflow-wrap:normal}
.page-body table{border-collapse:collapse;width:max-content;max-width:100%;margin:16px 0;
font-size:13.5px;line-height:1.55;display:block;overflow-x:auto;white-space:nowrap}
.page-body th,.page-body td{border:1px solid var(--line);padding:8px 10px;text-align:left;
white-space:normal;min-width:7ch}
.page-body th{background:var(--panel-muted);font-weight:650}
.page-body blockquote{margin:14px 0;padding:5px 0 5px 15px;border-left:3px solid var(--line-strong);
color:var(--muted);background:var(--panel-muted);border-radius:0 8px 8px 0}
.page-body img{max-width:100%;height:auto;border-radius:8px}
.katex{color:var(--text);font-size:1.04em}
.katex .mord,.katex .mbin,.katex .mrel,.katex .mopen,.katex .mclose,.katex .mpunct,
.katex .minner,.katex .mathnormal,.katex .text,.katex .mtext,.katex-html{color:inherit}
.katex-display{margin:1.2em 0;overflow-x:auto;overflow-y:hidden;padding:.2em 0;max-width:100%}
.callout{border:1px solid var(--line);border-left:5px solid var(--accent);border-radius:8px;
margin:16px 0;background:var(--callout-bg);overflow:visible}
.callout-title{font-weight:700;padding:9px 13px;border-bottom:1px solid var(--line);
font-size:14px;background:var(--callout-title);border-radius:7px 0 0}
.callout-body{padding:11px 14px;overflow-wrap:anywhere}
details.callout>.callout-body{padding-top:10px}
details.callout>summary{cursor:pointer;padding:9px 13px;font-weight:700;font-size:14px;
list-style:none;background:var(--callout-title);border-radius:7px 0 0}
details.callout>summary::before{content:"▸ ";color:var(--accent)}
details.callout[open]>summary::before{content:"▾ "}
.callout-question{border-left-color:#2f6fed;background:var(--accent-soft)}
.callout-success{border-left-color:#2e9e5b}
.callout-warning{border-left-color:#d97706}
.callout-important{border-left-color:#dc2626}
.callout-example,.callout-abstract,.callout-tip{border-left-color:#7c3aed}
.callout-summary,.callout-info,.callout-note{border-left-color:#0e7490}
.callout-quote,.callout-todo{border-left-color:#64748b}
.page-nav{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:42px 0 0;
border-top:1px solid var(--line);padding-top:20px}
.page-nav-btn{min-width:0;border:1px solid var(--line);background:var(--panel);
border-radius:8px;padding:10px 12px;text-align:left;cursor:pointer}
.page-nav-btn:hover{border-color:var(--accent);background:var(--accent-soft)}
.page-nav-btn:disabled{opacity:.38;cursor:not-allowed}
.page-nav-next{text-align:right}
.page-nav-kicker{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}
.page-nav-title{display:block;font-size:13px;line-height:1.45;overflow-wrap:anywhere}
.toc-list{margin:0;padding:0;list-style:none;min-width:0}
.toc-item{display:block;width:100%;text-align:left;border:0;background:none;color:var(--muted);
padding:5px 8px;border-radius:6px;font-size:12.5px;line-height:1.45;cursor:pointer;
overflow-wrap:anywhere;border-left:2px solid transparent}
.toc-item:hover{background:var(--accent-soft);color:var(--text)}
.toc-item.active{color:var(--accent);background:var(--accent-soft);border-left-color:var(--accent)}
.empty-panel{color:var(--muted);font-size:12.5px;line-height:1.6}
[data-slot]{min-height:0}
#local-graph-slot{margin-top:24px;border-top:1px solid var(--line);padding-top:16px}
.topbar{display:none;position:fixed;left:0;right:0;top:0;z-index:30;height:52px;
background:var(--panel);border-bottom:1px solid var(--line);align-items:center;gap:8px;
padding:0 10px}
.toolbar-btn{height:36px;min-width:42px;padding:0 9px;border:1px solid var(--line);
background:var(--panel);border-radius:8px;font-size:13px;cursor:pointer}
.topbar-spacer{flex:1}
.topbar-title{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
#theme-glyph{display:inline-block;min-width:20px;text-align:center}
.backdrop{display:none}
@media(max-width:900px){
 #app{display:block}
 #explorer,#toc-panel{position:fixed;top:0;bottom:0;z-index:40;width:min(86vw,320px);
 transform:translateX(-110%);transition:transform .18s ease;height:auto}
 #toc-panel{right:0;left:auto;transform:translateX(110%)}
 body.explorer-open #explorer,body.toc-open #toc-panel{transform:none}
 .backdrop{display:block;position:fixed;inset:0;z-index:35;background:var(--scrim);
 opacity:0;pointer-events:none;transition:opacity .18s ease}
 body.explorer-open .backdrop,body.toc-open .backdrop{opacity:1;pointer-events:auto}
 .topbar{display:flex}
 #theme-toggle-desktop{display:none}
 #main{padding:70px 16px 64px}
 .page{max-width:100%}
 .page-body pre{overflow-x:auto}
 .page-body pre code{white-space:pre}
}
@media(max-width:480px){
 .page-head h1{font-size:25px}
 .page-body{font-size:15.5px}
 .page-nav{grid-template-columns:1fr}
 .page-nav-next{text-align:left}
 .toolbar-btn{padding:0 8px;font-size:12.5px}
}
"""


_APP_JS = r"""
"use strict";
(function(){
  var payload = JSON.parse(document.getElementById("pages-data").textContent);
  var pages = payload.pages;
  var tree = payload.tree;
  var graph = payload.graph || {nodes:[],edges:[],communities:[]};
  var quiz = payload.quiz || [];
  var propositions = payload.propositions || [];
  var nav = document.getElementById("nav");
  var tocNav = document.getElementById("toc");
  var backlinksList = document.getElementById("backlinks-list");
  var localGraph = document.getElementById("local-graph");
  var search = document.getElementById("search");
  var searchResults = document.getElementById("search-results");
  var domainFilter = document.getElementById("domain-filter");
  var typeFilter = document.getElementById("type-filter");
  var sourceFilter = document.getElementById("source-filter");
  var main = document.getElementById("main");
  var preview = document.getElementById("preview-popover");
  var byPath = {};
  var byTitle = {};
  var nodeByPath = {};
  var searchDocs = [];
  var lastPath = "";
  var progressTimer = null;
  var lastScrollSave = 0;
  var articles = Array.prototype.slice.call(main.querySelectorAll("article.page"));
  articles.forEach(function(a){ byPath[a.getAttribute("data-path")] = a; });
  pages.forEach(function(p){ byPath[p.path] = byPath[p.path] || null; byTitle[p.path] = p.title; });
  (graph.nodes||[]).forEach(function(n){ nodeByPath[n.path] = n; });

  function esc(s){ var d=document.createElement("span"); d.textContent=s; return d.innerHTML; }
  function currentPath(){
    var h = decodeURIComponent(location.hash||"");
    if(h.indexOf("#/")===0) return h.slice(2);
    return "";
  }
  function defaultPath(){
    if(byPath["overview.md"]) return "overview.md";
    return pages.length ? pages[0].path : "";
  }
  function normalize(value){ return String(value||"").toLowerCase(); }
  function activeFilters(){
    return {
      domain: normalize(domainFilter.value),
      type: normalize(typeFilter.value),
      source: normalize(sourceFilter.value)
    };
  }
  function passesFilters(page){
    var filters = activeFilters();
    if(filters.domain && normalize(page.navigation_domain)!==filters.domain) return false;
    if(filters.type && normalize(page.type)!==filters.type) return false;
    if(filters.source && !(page.source_refs||[]).some(function(s){
      return normalize(s)===filters.source;
    })) return false;
    return true;
  }
  function matchesFilter(p, q){
    if(!passesFilters(p)) return false;
    var hay = (p.title+" "+(p.aliases||[]).join(" ")+" "+p.domain+" "+
      p.navigation_domain_label+" "+p.type+" "+p.path+" "+
      (p.source_refs||[]).join(" ")).toLowerCase();
    return hay.indexOf(q)>=0;
  }
  function buildSearchDocs(){
    searchDocs = pages.filter(function(p){
      return p.path.indexOf("__view:")!==0 && byPath[p.path];
    }).map(function(p){
      var body = byPath[p.path].querySelector(".page-body");
      return {
        page: p,
        title: normalize(p.title),
        aliases: (p.aliases||[]).map(normalize),
        bodyText: body ? body.textContent : "",
        body: normalize(body ? body.textContent : ""),
        hay: normalize(p.title+" "+(p.aliases||[]).join(" ")+" "+
          p.domain+" "+p.navigation_domain_label+" "+p.type+" "+p.path+" "+
          (p.source_refs||[]).join(" "))
      };
    });
  }
  function countMatches(hay, q){
    var count=0, index=hay.indexOf(q);
    while(index>=0){ count++; index=hay.indexOf(q,index+q.length); }
    return count;
  }
  function scoreDoc(doc, q){
    var title = countMatches(doc.title, q)*1000;
    var aliases = doc.aliases.reduce(function(sum,a){ return sum+countMatches(a,q)*500; },0);
    var body = countMatches(doc.body, q)*1;
    var hay = countMatches(doc.hay, q)*0.25;
    return title+aliases+body+hay;
  }
  function highlighted(snippet, q){
    var out="", lower=snippet.toLowerCase(), index=lower.indexOf(q), cursor=0;
    while(index>=0){
      out += esc(snippet.slice(cursor,index));
      out += "<mark>"+esc(snippet.slice(index,index+q.length))+"</mark>";
      cursor=index+q.length;
      index=lower.indexOf(q,cursor);
    }
    out += esc(snippet.slice(cursor));
    return out;
  }
  function snippetFor(doc, q){
    var body = doc.bodyText || "";
    var match = doc.body.indexOf(q);
    var start = match>=0 ? Math.max(0,match-50) : 0;
    var raw = body.slice(start,start+150);
    if(match<0 && (doc.title.indexOf(q)>=0 || doc.aliases.some(function(a){return a.indexOf(q)>=0;}))) {
      raw = body.slice(0,150);
    }
    return highlighted(raw.trim(), q);
  }
  function renderSearchResults(){
    var q = normalize(search.value.trim());
    document.getElementById("explorer").classList.toggle("searching", Boolean(q));
    if(!q){
      searchResults.hidden = true;
      searchResults.innerHTML="";
      renderExplorer("");
      return;
    }
    var ranked = searchDocs.filter(function(doc){ return passesFilters(doc.page); })
      .map(function(doc){ return {doc:doc, score:scoreDoc(doc,q)}; })
      .filter(function(item){ return item.score>0; })
      .sort(function(a,b){ return b.score-a.score || (a.doc.page.path<b.doc.page.path?-1:1); });
    if(!ranked.length){
      searchResults.innerHTML='<div class="empty-panel">没有匹配页面</div>';
      searchResults.hidden=false;
      return;
    }
    searchResults.innerHTML = ranked.map(function(item,index){
      var p = item.doc.page;
      return '<button class="search-result" data-index="'+index+'" data-path="'+esc(p.path)+'">'
        + '<span class="search-result-title">'+esc(p.title)+'</span>'
        + '<span class="search-result-meta">'+esc(p.navigation_domain_label)+' · '+esc(p.type)+' · '
        + esc((p.source_refs||[]).join(" / "))+'</span>'
        + '<span class="search-result-snippet">'+snippetFor(item.doc,q)+'</span></button>';
    }).join("");
    searchResults.hidden=false;
    Array.prototype.forEach.call(searchResults.querySelectorAll(".search-result"), function(btn){
      btn.addEventListener("click", function(){ location.hash="#/"+btn.getAttribute("data-path"); });
    });
  }
  function openKeys(path){
    var page = pages.find(function(p){ return p.path===path; }) || pages[0];
    if(!page) return {};
    var out = {};
    out[page.navigation_domain] = 1;
    out[page.navigation_domain+"|type:"+page.type] = 1;
    return out;
  }
  function renderExplorer(filter){
    var path = currentPath() || lastPath || defaultPath();
    var q = (filter||"").trim().toLowerCase();
    var filterActive = Boolean(domainFilter.value || typeFilter.value || sourceFilter.value);
    var open = (q || filterActive) ? null : openKeys(path);
    var html = "";
    tree.forEach(function(domain){
      var domainHtml = "";
      domain.types.forEach(function(typeGroup){
        var pageRows = "";
        typeGroup.pages.forEach(function(page){
          var full = pages.find(function(p){ return p.path===page.path; }) || {
            path: page.path, title: byTitle[page.path],
            domain: domain.domain_key, type: typeGroup.type
          };
          var matched = matchesFilter(full, q);
          if((q || filterActive) && !matched) return;
          pageRows += '<button class="nav-item" data-path="'+esc(page.path)+'"'
            + (page.path===path ? ' aria-current="page"' : "")+'>'
            + esc(page.title)+'<span class="badge">'+esc(typeGroup.type)+'</span></button>';
        });
        if(!pageRows) return;
        var typeKey = domain.domain_key+"|type:"+typeGroup.type;
        var typeOpen = !q && open && open[typeKey];
        domainHtml += '<button class="tree-type'+(typeOpen?' open':'')+'" data-tree-target="'+esc(typeKey)+'">'
          + '<span class="tree-twisty">▸</span><span>'+esc(typeGroup.type)+'</span></button>'
          + '<div class="tree-children'+(typeOpen?' open':'')+'">'+pageRows+'</div>';
      });
      if(!domainHtml) return;
      var domainKey = domain.domain_key;
      var domainOpen = !q && open && open[domainKey];
      html += '<div class="nav-group"><button class="tree-domain'+(domainOpen?' open':'')+'" data-tree-target="'+esc(domainKey)+'">'
        + '<span class="tree-twisty">▸</span><span>'+esc(domain.domain)+'</span></button>'
        + '<div class="tree-children'+(domainOpen?' open':'')+'">'+domainHtml+'</div></div>';
    });
    nav.innerHTML = html || '<div class="empty-panel">没有匹配页面</div>';
    Array.prototype.forEach.call(nav.querySelectorAll(".tree-domain,.tree-type"), function(btn){
      btn.addEventListener("click", function(){
        var children = btn.nextElementSibling;
        if(children && children.classList.contains("tree-children")){
          btn.classList.toggle("open");
          children.classList.toggle("open");
        }
      });
    });
    Array.prototype.forEach.call(nav.querySelectorAll(".nav-item"), function(btn){
      btn.addEventListener("click", function(){ location.hash = "#/"+btn.getAttribute("data-path"); });
    });
  }
  function focusTree(target){
    var btn = nav.querySelector('[data-tree-target="'+CSS.escape(target)+'"]');
    if(!btn) return;
    var node = btn;
    while(node){
      var children = node.nextElementSibling;
      if(children && children.classList.contains("tree-children")){
        node.classList.add("open");
        children.classList.add("open");
      }
      node = node.parentElement && node.parentElement.parentElement
        ? node.parentElement.parentElement.querySelector(":scope > .tree-domain, :scope > .tree-type")
        : null;
    }
    btn.scrollIntoView({block:"nearest"});
    document.getElementById("explorer").scrollTop = Math.max(0, btn.offsetTop-12);
    if(window.innerWidth<=900) openDrawer("explorer");
  }
  function renderMath(root){
    if(!window.renderMathInElement) return;
    renderMathInElement(root, {
      delimiters:[{left:"$$",right:"$$",display:true},{left:"$",right:"$",display:false}],
      throwOnError:false,
      ignoredTags:["script","noscript","style","textarea","pre","code","option"]
    });
  }
  function renderToc(path){
    var page = pages.find(function(p){ return p.path===path; });
    if(!page || !page.toc.length){
      tocNav.innerHTML = '<div class="empty-panel">本页没有小节标题</div>';
      return;
    }
    tocNav.innerHTML = '<ul class="toc-list">'+page.toc.map(function(item){
      return '<li><button class="toc-item" data-heading-id="'+esc(item.id)+'"'
        + ' style="padding-left:'+Math.max(8,item.level*12-4)+'px">'+esc(item.text)+'</button></li>';
    }).join("")+'</ul>';
    Array.prototype.forEach.call(tocNav.querySelectorAll(".toc-item"), function(btn){
      btn.addEventListener("click", function(){
        var target = document.getElementById(btn.getAttribute("data-heading-id"));
        if(!target) return;
        var top = target.getBoundingClientRect().top + window.scrollY - 82;
        window.scrollTo({top:Math.max(0,top),behavior:"smooth"});
      });
    });
    renderMath(tocNav);
    updateActiveToc();
  }
  function updateActiveToc(){
    var page = pages.find(function(p){ return p.path===(currentPath()||defaultPath()); });
    var active = "";
    if(page){
      page.toc.forEach(function(item){
        var heading = document.getElementById(item.id);
        if(heading && heading.getBoundingClientRect().top<=112) active = item.id;
      });
    }
    Array.prototype.forEach.call(tocNav.querySelectorAll(".toc-item"), function(btn){
      btn.classList.toggle("active", btn.getAttribute("data-heading-id")===active);
    });
  }
  function renderBacklinks(path){
    var page = pages.find(function(p){ return p.path===path; });
    var links = page && page.backlinks ? page.backlinks : [];
    if(!links.length){
      backlinksList.innerHTML = '<div class="empty-panel">暂无反向链接</div>';
      return;
    }
    backlinksList.innerHTML = links.map(function(item){
      return '<button class="backlink-item" data-path="'+esc(item.path)+'">'
        + esc(item.title)+'<span class="backlink-path">'+esc(item.path)+'</span></button>';
    }).join("");
    Array.prototype.forEach.call(backlinksList.querySelectorAll(".backlink-item"), function(btn){
      btn.addEventListener("click", function(){ location.hash = "#/"+btn.getAttribute("data-path"); });
    });
  }
  function localNeighbors(path){
    var node = nodeByPath[path];
    if(!node) return {node:null, nodes:[], edges:[]};
    var ids = {};
    ids[node.id] = node;
    var edges = [];
    (graph.edges||[]).forEach(function(edge){
      if(edge.source !== node.id && edge.target !== node.id) return;
      edges.push(edge);
      var other = edge.source === node.id ? edge.target : edge.source;
      (graph.nodes||[]).forEach(function(candidate){
        if(candidate.id === other) ids[other] = candidate;
      });
    });
    return {node:node, nodes:Object.keys(ids).map(function(id){return ids[id];}), edges:edges};
  }
  function renderLocalGraph(path){
    var local = localNeighbors(path);
    if(!local.node){
      localGraph.innerHTML = '<div class="empty-panel">本页不在图谱中</div>';
      return;
    }
    var nodes = [local.node].concat(local.nodes.filter(function(n){return n.id!==local.node.id;})
      .sort(function(a,b){return (a.label||a.id)<(b.label||b.id)?-1:1;}));
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns,"svg");
    var width = localGraph.clientWidth || 220, height = localGraph.clientHeight || 210;
    var cx = width/2, cy = height/2;
    function point(i,count){
      if(count===1) return {x:cx,y:cy};
      var angle = -Math.PI/2 + i*2*Math.PI/count;
      var radius = Math.min(width,height)*0.32;
      return {x:cx+Math.cos(angle)*radius,y:cy+Math.sin(angle)*radius};
    }
    var points = nodes.map(function(node,i){return point(i,nodes.length);});
    var idIndex = {};
    nodes.forEach(function(node,i){idIndex[node.id]=i;});
    local.edges.forEach(function(edge){
      var line = document.createElementNS(ns,"line");
      line.setAttribute("x1",points[idIndex[edge.source]].x);
      line.setAttribute("y1",points[idIndex[edge.source]].y);
      line.setAttribute("x2",points[idIndex[edge.target]].x);
      line.setAttribute("y2",points[idIndex[edge.target]].y);
      line.style.stroke = "var(--line-strong)";
      line.setAttribute("stroke-width","1");
      svg.appendChild(line);
    });
    nodes.forEach(function(node,i){
      var g = document.createElementNS(ns,"g");
      g.setAttribute("class","local-node");
      g.setAttribute("data-node-id",node.id);
      g.setAttribute("data-path",node.path||"");
      g.style.cursor="pointer";
      g.addEventListener("click",function(){ location.hash = "#/"+node.path; });
      var circle = document.createElementNS(ns,"circle");
      circle.setAttribute("cx",points[i].x);
      circle.setAttribute("cy",points[i].y);
      circle.setAttribute("r",node.id===local.node.id?9:6);
      circle.style.fill = node.id===local.node.id ? "var(--accent)" : "var(--accent-soft-strong)";
      circle.style.stroke = "var(--line)";
      var label = document.createElementNS(ns,"text");
      label.setAttribute("x",points[i].x+12);
      label.setAttribute("y",points[i].y+4);
      label.setAttribute("font-size","10");
      label.style.fill = "var(--text)";
      label.textContent = (node.label||node.id).slice(0,18);
      g.appendChild(circle);g.appendChild(label);svg.appendChild(g);
    });
    localGraph.innerHTML=""; localGraph.appendChild(svg);
  }
  function renderCollectionView(path){
    var body = path === "__view:quiz" ? document.getElementById("quiz-view-body")
      : document.getElementById("propositions-view-body");
    if(path === "__view:quiz"){
      body.innerHTML = quiz.map(function(item){
        return '<details class="quiz-entry"><summary>'+esc(item.stem)+'</summary>'
          + '<div class="quiz-answer">'+item.answer_html+'</div>'
          + '<a class="source-link" href="#/'+encodeURI(item.rel)+'">回到 '+esc(item.title)+'</a>'
          + '</details>';
      }).join("");
    }else{
      body.innerHTML = propositions.map(function(item){
        return '<div class="proposition-entry"><h2>'+esc(item.name)+'</h2>'
          + '<p>'+esc(item.statement)+'</p>'
          + '<a class="source-link" href="#/'+encodeURI(item.rel)+'">回到 '+esc(item.title)+'</a>'
          + '</div>';
      }).join("");
    }
    renderMath(body);
  }
  function attachPreviews(){
    Array.prototype.forEach.call(
      main.querySelectorAll('.page:not([hidden]) .page-body a[href^="#/"]'),
      function(anchor){
        anchor.addEventListener("mouseenter", function(){ showPreview(anchor); });
        anchor.addEventListener("focus", function(){ showPreview(anchor); });
        anchor.addEventListener("mouseleave", function(ev){
          if(!ev.relatedTarget || ev.relatedTarget !== preview) hidePreview();
        });
      }
    );
  }
  function showPreview(anchor){
    var path = decodeURIComponent(anchor.getAttribute("href").slice(2));
    var page = pages.find(function(p){return p.path===path;});
    var node = nodeByPath[path];
    var summary = node && node.summary ? node.summary : "";
    if(!page || !summary) return;
    preview.innerHTML = '<div class="preview-title">'+esc(page.title)+'</div>'
      + '<div class="preview-meta">'+esc(page.type)+'</div>'
      + '<div class="preview-summary">'+esc(summary)+'</div>';
    preview.hidden = false;
    var rect = anchor.getBoundingClientRect();
    var left = Math.min(window.innerWidth-preview.offsetWidth-10, Math.max(10,rect.left));
    var top = rect.bottom+8;
    if(top+preview.offsetHeight>window.innerHeight) top = Math.max(10,rect.top-preview.offsetHeight-8);
    preview.style.left = left+"px"; preview.style.top = top+"px";
  }
  function hidePreview(){ preview.hidden = true; }
  function loadProgress(){
    try{
      var raw = localStorage.getItem("study-kb-reading-progress");
      var data = raw ? JSON.parse(raw) : null;
      if(data && typeof data.path==="string" && typeof data.scrollY==="number") return data;
    }catch(e){}
    return null;
  }
  function saveProgress(path, scrollY){
    if(!path) return;
    try{
      localStorage.setItem("study-kb-reading-progress",
        JSON.stringify({path:path, scrollY:Math.max(0,scrollY||0)}));
    }catch(e){}
  }
  function throttledProgressSave(){
    var path = currentPath() || lastPath || defaultPath();
    var now = Date.now();
    if(now-lastScrollSave<250) return;
    lastScrollSave = now;
    saveProgress(path, window.scrollY);
  }
  function buildFilterOptions(){
    var filters = payload.filters || {domains:[],types:[],sources:[]};
    [["domain-filter",filters.domains.map(function(item){return item.value;})],
     ["type-filter",filters.types],
     ["source-filter",filters.sources]]
      .forEach(function(group){
        var select = document.getElementById(group[0]);
        group[1].forEach(function(value){
          var option = document.createElement("option");
          option.value = value;
          option.textContent = value || "未分类";
          select.appendChild(option);
        });
      });
    filters.domains.forEach(function(item){
      var select = document.getElementById("domain-filter");
      var option = Array.prototype.find.call(select.options, function(opt){return opt.value===item.value;});
      if(option) option.textContent = item.label;
    });
  }
  function moveSearchResult(delta){
    var results = Array.prototype.slice.call(searchResults.querySelectorAll(".search-result"));
    if(!results.length) return;
    var current = results.findIndex(function(item){return item.classList.contains("active");});
    var next = Math.min(results.length-1, Math.max(0, current+delta));
    if(current<0 && delta<0) next = results.length-1;
    if(current<0 && delta>0) next = 0;
    results.forEach(function(item){item.classList.remove("active");});
    results[next].classList.add("active");
    results[next].scrollIntoView({block:"nearest"});
  }
  function chooseSearchResult(){
    var active = searchResults.querySelector(".search-result.active");
    if(active) location.hash = "#/"+active.getAttribute("data-path");
  }
  function route(){
    var requestedPath = currentPath();
    var saved = requestedPath ? null : loadProgress();
    var path = requestedPath || (saved && byPath[saved.path] ? saved.path : defaultPath());
    var el = byPath[path];
    if(!el && path) path = defaultPath(), el = byPath[path];
    lastPath = path;
    articles.forEach(function(a){ a.hidden = (a!==el); });
    renderSearchResults();
    renderToc(path);
    if(path.indexOf("__view:")===0){
      renderCollectionView(path);
      renderBacklinks("");
      renderLocalGraph("");
    }else{
      renderBacklinks(path);
      renderLocalGraph(path);
    }
    hidePreview();
    attachPreviews();
    if(el) document.title = el.getAttribute("data-title") + " · 学习知识库";
    main.scrollTop = 0;
    window.scrollTo(0, saved && saved.path===path ? saved.scrollY : 0);
    closeDrawers();
  }
  function openDrawer(which){
    document.body.classList.remove("explorer-open","toc-open");
    document.body.classList.add(which+"-open");
  }
  function closeDrawers(){ document.body.classList.remove("explorer-open","toc-open"); }

  search.addEventListener("input", function(){
    renderSearchResults();
  });
  [domainFilter,typeFilter,sourceFilter].forEach(function(select){
    select.addEventListener("change", function(){
      renderSearchResults();
    });
  });
  document.getElementById("explorer-toggle").addEventListener("click", function(){
    openDrawer("explorer");
  });
  document.getElementById("toc-toggle").addEventListener("click", function(){
    openDrawer("toc");
  });
  document.getElementById("backdrop").addEventListener("click", closeDrawers);
  document.getElementById("graph-open").addEventListener("click", function(){
    var modal = document.getElementById("graph-view");
    var frame = document.getElementById("graph-view-iframe");
    if(!frame.getAttribute("srcdoc")){
      var encoded = document.getElementById("graph-view-data").textContent.trim();
      var bytes = Uint8Array.from(atob(encoded), function(ch){ return ch.charCodeAt(0); });
      frame.setAttribute("srcdoc", new TextDecoder("utf-8").decode(bytes));
    }
    modal.hidden = false;
  });
  document.getElementById("graph-close").addEventListener("click", function(){
    document.getElementById("graph-view").hidden = true;
  });
  document.getElementById("quiz-open").addEventListener("click", function(){
    location.hash = "#/__view:quiz";
  });
  document.getElementById("propositions-open").addEventListener("click", function(){
    location.hash = "#/__view:propositions";
  });
  document.addEventListener("click", function(ev){
    var pageButton = ev.target.closest ? ev.target.closest(".page-nav-btn[data-path]") : null;
    if(pageButton){
      location.hash = "#/"+pageButton.getAttribute("data-path");
      return;
    }
    var crumb = ev.target.closest ? ev.target.closest(".breadcrumbs .crumb[data-target]") : null;
    if(!crumb) return;
    var target = crumb.getAttribute("data-target");
    if(target.indexOf("domain:")===0){
      focusTree(target);
    }else{
      location.hash = "#/"+target;
    }
  });

  function setTheme(theme, save){
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelectorAll(".theme-glyph").forEach(function(glyph){
      glyph.textContent = theme==="dark" ? "深" : "浅";
    });
    document.querySelectorAll(".theme-toggle").forEach(function(button){
      button.setAttribute("aria-label",
        theme==="dark" ? "切换到浅色模式" : "切换到深色模式");
    });
    if(save){
      try{ localStorage.setItem("study-kb-theme", theme); }catch(e){}
    }
  }
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var savedTheme = null;
  try{ savedTheme = localStorage.getItem("study-kb-theme"); }catch(e){}
  setTheme(savedTheme || (media && media.matches ? "dark" : "light"), false);
  document.querySelectorAll(".theme-toggle").forEach(function(button){
    button.addEventListener("click", function(){
      var next = document.documentElement.getAttribute("data-theme")==="dark" ? "light" : "dark";
      setTheme(next, true);
    });
  });
  if(media && media.addEventListener){
    media.addEventListener("change", function(ev){
      var saved = null;
      try{ saved = localStorage.getItem("study-kb-theme"); }catch(e){}
      if(!saved) setTheme(ev.matches ? "dark" : "light", false);
    });
  }

  search.addEventListener("keydown", function(ev){
    if(ev.key==="ArrowDown"){ ev.preventDefault(); moveSearchResult(1); }
    else if(ev.key==="ArrowUp"){ ev.preventDefault(); moveSearchResult(-1); }
    else if(ev.key==="Enter"){ ev.preventDefault(); chooseSearchResult(); }
    else if(ev.key==="Escape"){ search.value=""; renderSearchResults(); search.blur(); }
  });
  window.addEventListener("keydown", function(ev){
    var tag = (ev.target && ev.target.tagName ? ev.target.tagName : "").toLowerCase();
    if(tag==="input" || tag==="textarea" || tag==="select") return;
    if(ev.key==="/" || (ev.ctrlKey && (ev.key==="k" || ev.key==="K"))){
      ev.preventDefault();
      search.focus();
      search.select();
    }
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("scroll", updateActiveToc, {passive:true});
  window.addEventListener("scroll", throttledProgressSave, {passive:true});
  window.addEventListener("pagehide", function(){
    saveProgress(currentPath() || lastPath || defaultPath(), window.scrollY);
  });
  window.addEventListener("beforeunload", function(){
    saveProgress(currentPath() || lastPath || defaultPath(), window.scrollY);
  });
  window.addEventListener("resize", function(){
    if(window.innerWidth>900) closeDrawers();
  });
  preview.addEventListener("mouseleave", hidePreview);
  document.addEventListener("click", hidePreview);
  buildFilterOptions();
  buildSearchDocs();
  renderSearchResults();
  route();
  renderMath(main);
})();
"""


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>学习知识库（离线）</title>
<script>__THEME_JS__</script>
<style>__BASE_CSS__</style>
<style>__KATEX_CSS__</style>
</head>
<body>
<header class="topbar">
 <button id="explorer-toggle" class="toolbar-btn" type="button" aria-label="打开页面目录">目录</button>
 <span class="topbar-title">学习知识库</span>
 <span class="topbar-spacer"></span>
 <button id="toc-toggle" class="toolbar-btn" type="button" aria-label="打开本页目录">本页</button>
 <button id="theme-toggle" class="toolbar-btn theme-toggle" type="button" aria-label="切换深浅色模式">
  <span class="theme-glyph">浅</span>
 </button>
</header>
<div id="app">
 <aside id="explorer" aria-label="页面目录">
  <div class="panel-title"><span>学习知识库</span>
   <button id="theme-toggle-desktop" class="toolbar-btn theme-toggle" type="button"
    aria-label="切换深浅色模式"><span class="theme-glyph">浅</span></button>
  </div>
  <input id="search" type="search" placeholder="搜索标题 / 域 / 类型">
  <div class="filter-row">
   <select id="domain-filter" aria-label="按域筛选"><option value="">全部域</option></select>
   <select id="type-filter" aria-label="按类型筛选"><option value="">全部类型</option></select>
   <select id="source-filter" aria-label="按来源筛选"><option value="">全部来源</option></select>
  </div>
  <div id="search-results" class="search-results" hidden></div>
  <div class="view-switcher" aria-label="站点视图">
   <button id="graph-open" class="view-switcher-btn" type="button">图谱</button>
   <button id="quiz-open" class="view-switcher-btn" type="button">自测题</button>
   <button id="propositions-open" class="view-switcher-btn" type="button">命题</button>
  </div>
  <nav id="nav" aria-label="分类树"></nav>
 </aside>
 <main id="main">__PAGES_HTML__</main>
 <aside id="toc-panel" aria-label="本页目录">
  <div class="panel-title"><span>本页目录</span></div>
  <nav id="toc" aria-label="本页小节"></nav>
  <section id="local-graph-slot" data-slot="local-graph">
   <div class="panel-title"><span>关联图谱</span></div>
   <div id="local-graph" aria-label="本页局部图谱"></div>
  </section>
  <section id="backlinks">
   <div class="panel-title"><span>反向链接</span></div>
   <div id="backlinks-list"></div>
  </section>
 </aside>
</div>
<div id="backdrop" class="backdrop"></div>
<div id="graph-view" class="modal" hidden>
 <header class="modal-bar">
  <span>知识图谱</span>
  <button id="graph-close" class="toolbar-btn" type="button">关闭</button>
 </header>
 <iframe id="graph-view-iframe" title="知识图谱"></iframe>
</div>
<div id="preview-popover" class="preview-popover" hidden></div>
<script id="graph-view-data" type="application/octet-stream">__GRAPH_VIEW_B64__</script>
<script id="pages-data" type="application/json">__PAGES_INDEX__</script>
<script>__KATEX_JS__</script>
<script>__AUTORENDER_JS__</script>
<script>__APP_JS__</script>
</body>
</html>
"""


def _render_breadcrumb(items: list[dict]) -> str:
    parts: list[str] = []
    for index, item in enumerate(items):
        target = _html_escape(item["target"], quote=True)
        label = _html_escape(item["label"])
        current = ' aria-current="page"' if item["kind"] == "page" else ""
        parts.append(
            f'<button class="crumb" type="button" data-target="{target}"{current}>'
            f'{label}</button>'
        )
        if index < len(items) - 1:
            parts.append('<span class="crumb-sep">/</span>')
    return '<nav class="breadcrumbs" aria-label="面包屑">' + "".join(parts) + "</nav>"


def _render_page_nav(previous: dict | None, following: dict | None) -> str:
    buttons: list[str] = []
    for entry, cls, kicker in (
        (previous, "page-nav-prev", "上一页"),
        (following, "page-nav-next", "下一页"),
    ):
        if entry is None:
            buttons.append(
                f'<button class="page-nav-btn {cls}" type="button" disabled>'
                f'<span class="page-nav-kicker">{kicker}</span>'
                '<span class="page-nav-title">—</span></button>'
            )
        else:
            path = _html_escape(entry["path"], quote=True)
            title = _html_escape(entry["title"])
            buttons.append(
                f'<button class="page-nav-btn {cls}" type="button" data-path="{path}">'
                f'<span class="page-nav-kicker">{kicker}</span>'
                f'<span class="page-nav-title">{title}</span></button>'
            )
    return '<nav class="page-nav" aria-label="上一页和下一页">' + "".join(buttons) + "</nav>"


def render_html(
    pages: list[dict],
    *,
    vault: str | Path,
    with_images: bool,
    katex_assets: dict,
    render_page_body,
    graph_payload: dict | None = None,
    graph_view_html: str = "",
    quiz_items: list[dict] | None = None,
    proposition_items: list[dict] | None = None,
    source_media: dict[str, list[dict]] | None = None,
) -> str:
    """Build the complete single-file reading interface from rendered pages."""
    vault_path = Path(vault)
    page_set = {page["rel"] for page in pages}
    ordered = ordered_paths(pages)
    path_to_page = {page["rel"]: page for page in pages}
    backlinks = site_data.build_backlinks(pages, page_set)
    graph_payload = graph_payload or {"nodes": [], "edges": [], "communities": []}
    quiz_items = quiz_items or []
    proposition_items = proposition_items or []
    articles: list[str] = []
    payload_pages: list[dict] = []

    for index, rel in enumerate(ordered):
        page = path_to_page[rel]
        body_html = render_page_body(page["body"], page_set, vault_path, with_images)
        body_html, toc = prepare_body(body_html, f"p{index}")
        previous, following = adjacent_paths(page["rel"], ordered)
        previous_entry = (
            {"path": previous, "title": path_to_page[previous]["title"]}
            if previous is not None else None
        )
        following_entry = (
            {"path": following, "title": path_to_page[following]["title"]}
            if following is not None else None
        )
        crumbs = breadcrumb(page)
        branch = site_data.classify_page(page)
        rel_esc = _html_escape(page["rel"], quote=True)
        title_esc = _html_escape(page["title"])
        domain_esc = _html_escape(page["domain"], quote=True)
        type_esc = _html_escape(page["type"], quote=True)
        obsidian_esc = _html_escape(page.get("obsidian_uri") or "", quote=True)
        obsidian_html = (
            f' · <a class="obsidian-link" href="{obsidian_esc}">在 Obsidian 中打开</a>'
            if obsidian_esc else ""
        )
        articles.append(
            f'<article class="page" data-path="{rel_esc}" data-title="{title_esc}" '
            f'data-domain="{domain_esc}" data-type="{type_esc}">'
            f'<header class="page-head">{_render_breadcrumb(crumbs)}'
            f'<h1>{title_esc}</h1>'
            f'<div class="page-meta">{_html_escape(branch["label"])} · '
            f'{_html_escape(page["type"])}{obsidian_html}</div>'
            f'</header><div class="page-body">{body_html}</div>'
            f'{_render_page_nav(previous_entry, following_entry)}'
            f'<div data-slot="source-pages" aria-hidden="true">'
            f'{site_media.render_source_panel((source_media or {}).get(page["rel"], []))}'
            f'</div></article>'
        )
        payload_pages.append({
            "path": page["rel"],
            "title": page["title"],
            "domain": page["domain"],
            "type": page["type"],
            "navigation_domain": branch["key"],
            "navigation_domain_label": branch["label"],
            "toc": toc,
            "breadcrumb": crumbs,
            "prev": previous,
            "next": following,
            "backlinks": backlinks.get(page["rel"], []),
            "obsidian_uri": page.get("obsidian_uri") or "",
            "aliases": page.get("aliases") or [],
            "source_refs": page.get("source_refs") or [],
        })

    articles.extend([
        ('<article class="page collection-view" data-path="__view:quiz" '
         'data-title="自测题库" data-domain="" data-type="view" hidden>'
         '<header class="page-head"><nav class="breadcrumbs" aria-label="面包屑">'
         '<span class="crumb" aria-current="page">自测题库</span></nav>'
         '<h1>自测题库</h1><div class="page-meta">聚合视图</div></header>'
         '<div class="page-body" id="quiz-view-body"></div></article>'),
        ('<article class="page collection-view" data-path="__view:propositions" '
         'data-title="命题总表" data-domain="" data-type="view" hidden>'
         '<header class="page-head"><nav class="breadcrumbs" aria-label="面包屑">'
         '<span class="crumb" aria-current="page">命题总表</span></nav>'
         '<h1>命题总表</h1><div class="page-meta">聚合视图</div></header>'
         '<div class="page-body" id="propositions-view-body"></div></article>'),
    ])

    payload = {
        "pages": payload_pages,
        "tree": build_explorer_tree(pages),
        "filters": site_data.build_filter_options(pages),
        "graph": graph_payload,
        "quiz": quiz_items,
        "propositions": proposition_items,
    }
    html = _HTML_TEMPLATE
    html = html.replace("__THEME_JS__", _THEME_JS)
    html = html.replace("__BASE_CSS__", _BASE_CSS)
    html = html.replace("__KATEX_CSS__", _inline_katex_css(katex_assets))
    html = html.replace("__KATEX_JS__", katex_assets["js"])
    html = html.replace("__AUTORENDER_JS__", katex_assets["auto_render_js"])
    html = html.replace("__APP_JS__", _APP_JS)
    html = html.replace("__PAGES_HTML__", "\n".join(articles))
    graph_view_b64 = base64.b64encode(
        graph_view_html.encode("utf-8")
    ).decode("ascii") if graph_view_html else ""
    html = html.replace("__GRAPH_VIEW_B64__", graph_view_b64)
    html = html.replace(
        "__PAGES_INDEX__",
        json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
    )
    return html
