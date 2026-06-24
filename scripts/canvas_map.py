"""knowledge-map canvas：从 published 概念图谱确定性生成 JSON Canvas（零 LLM，纯函数 + 一个 IO 写）。

节点 = 概念导航页（type ∈ MAP_TYPES，排除 lesson/source）；边 = 受控 wikilink；
布局 = 领域组 → 主题子组 → 概念网格 + 未分类子区。派生覆盖，不是发布门禁。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage
import thresholds

__all__ = ["MAP_TYPES", "CANVAS_FILE", "collect_map_pages", "build_graph",
           "topic_membership", "layout", "to_canvas", "validate_canvas", "write_canvas"]

CANVAS_FILE = "knowledge-map.generated.canvas"
MAP_TYPES = ("overview", "topic", "concept", "comparison", "synthesis")
_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets"}
_DERIVED = {"index.generated.md", "aliases.md"}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def _h16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def collect_map_pages(vault) -> list[dict]:
    """扫 published 页 → 节点集（type ∈ MAP_TYPES，排除 lesson/source/派生/_meta 等）。"""
    vault = Path(vault)
    pages = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published" or meta.get("type") not in MAP_TYPES:
            continue
        links = {t.strip() for t in _WIKILINK.findall(body)
                 if not t.strip().startswith(("http://", "https://"))}
        pages.append({"page_path": rel, "type": meta.get("type"),
                      "domain": meta.get("domain", "") or "",
                      "title": meta.get("title") or meta.get("canonical_name") or rel,
                      "links": links})
    return pages


def build_graph(pages: list[dict]) -> tuple[dict, list[tuple]]:
    """nodes {page_path: page} + edges [(a,b)] —— 只连节点集内、无向去重、per-node degree 裁剪。"""
    nodes = {p["page_path"]: p for p in pages}
    raw: set[tuple] = set()
    for p in pages:
        src = p["page_path"]
        for tgt in p["links"]:
            for cand in ((tgt,) if tgt.endswith(".md") else (tgt, f"{tgt}.md")):
                if cand in nodes and cand != src:
                    raw.add(tuple(sorted((src, cand))))
                    break
    # Read threshold dynamically to support test monkeypatching
    max_deg = getattr(thresholds, "CANVAS_MAX_DEGREE")
    deg: dict[str, int] = {}
    edges: list[tuple] = []
    for a, b in sorted(raw):
        if deg.get(a, 0) >= max_deg or deg.get(b, 0) >= max_deg:
            continue
        edges.append((a, b))
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return nodes, edges
