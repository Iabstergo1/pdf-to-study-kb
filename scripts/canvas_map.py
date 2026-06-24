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


# ── layout constants（确定性网格；像素值不进测试断言，只测确定性/结构）──
_NODE_W, _NODE_H = 260, 90
_COL = 4
_GX, _GY = 40, 40
_PAD = 40
_BAND_GAP = 160


def topic_membership(nodes: dict) -> tuple[dict, dict]:
    concepts = {pp for pp, p in nodes.items() if p["type"] == "concept"}
    membership: dict[str, list] = {}
    claimed: set = set()
    for tp in sorted(pp for pp, p in nodes.items() if p["type"] == "topic"):
        members = []
        for tgt in sorted(nodes[tp]["links"]):
            for cand in ((tgt,) if tgt.endswith(".md") else (tgt, f"{tgt}.md")):
                if cand in concepts:
                    members.append(cand); claimed.add(cand); break
        membership[tp] = members
    unassigned: dict[str, list] = {}
    for cp in sorted(concepts - claimed):
        unassigned.setdefault(nodes[cp]["domain"], []).append(cp)
    return membership, unassigned


def _align(v: float) -> int:
    return int(round(v / 20.0)) * 20


def _group(key: str, label: str, members: list, pos: dict, pad: int = _PAD) -> dict:
    x0 = min(pos[m][0] for m in members) - pad
    y0 = min(pos[m][1] for m in members) - pad
    xe = max(pos[m][0] + pos[m][2] for m in members) + pad
    ye = max(pos[m][1] + pos[m][3] for m in members) + pad
    return {"id": _h16("group:" + key), "type": "group", "label": label,
            "x": _align(x0), "y": _align(y0),
            "width": _align(xe - x0), "height": _align(ye - y0)}


def layout(nodes: dict, membership: dict, unassigned: dict) -> tuple[dict, list[dict]]:
    pos: dict[str, tuple] = {}
    groups: list[dict] = []
    y = 0
    # overview（全局置顶横排）
    x = _PAD
    for pp in sorted(pp for pp, p in nodes.items() if p["type"] == "overview"):
        pos[pp] = (x, y, _NODE_W, _NODE_H); x += _NODE_W + _GX
    if x > _PAD:
        y += _NODE_H + _BAND_GAP
    domains = sorted({p["domain"] for p in nodes.values()
                      if p["type"] in ("concept", "topic", "comparison", "synthesis")})
    for dom in domains:
        dom_members: list[str] = []
        # 顶行：comparison + synthesis
        x = _PAD
        for pp in sorted(pp for pp, p in nodes.items()
                         if p["domain"] == dom and p["type"] in ("comparison", "synthesis")):
            pos[pp] = (x, y, _NODE_W, _NODE_H); x += _NODE_W + _GX; dom_members.append(pp)
        if x > _PAD:
            y += _NODE_H + _GY
        # 各 topic 行（topic 锚 + concept 网格）
        for tp in sorted(pp for pp, p in nodes.items()
                         if p["domain"] == dom and p["type"] == "topic"):
            ty = y
            pos[tp] = (_PAD, ty, _NODE_W, _NODE_H); dom_members.append(tp)
            members = membership.get(tp, [])
            cx0 = _PAD + _NODE_W + _GX
            for i, cp in enumerate(members):
                pos[cp] = (cx0 + (i % _COL) * (_NODE_W + _GX),
                           ty + (i // _COL) * (_NODE_H + _GY), _NODE_W, _NODE_H)
                dom_members.append(cp)
            groups.append(_group("topic:" + tp, f"主题: {nodes[tp]['title']}", [tp] + members, pos))
            rows = max(1, (len(members) + _COL - 1) // _COL)
            y = ty + rows * (_NODE_H + _GY)
        # 未分类
        un = unassigned.get(dom, [])
        if un:
            uy = y
            for i, cp in enumerate(un):
                pos[cp] = (_PAD + (i % _COL) * (_NODE_W + _GX),
                           uy + (i // _COL) * (_NODE_H + _GY), _NODE_W, _NODE_H)
                dom_members.append(cp)
            groups.append(_group("unassigned:" + dom, "未分类（待 topic 收编）", un, pos))
            y = uy + ((len(un) + _COL - 1) // _COL) * (_NODE_H + _GY)
        if dom_members:
            groups.append(_group("domain:" + dom, f"领域: {dom}", dom_members, pos, pad=_PAD * 2))
        y += _BAND_GAP
    return pos, groups
