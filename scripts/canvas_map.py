"""knowledge-map canvas：从 published 概念图谱确定性生成 JSON Canvas（零 LLM，纯函数 + 一个 IO 写）。

节点 = 概念导航页（type ∈ MAP_TYPES，排除 lesson/source）；边 = 受控 wikilink；
布局 = _global 顶行 → 领域组（含 _cross-domain）→ 主题子组 → 概念网格 + 未分类子区。
派生覆盖，不是发布门禁。设计真值见 docs/superpowers/specs/2026-06-24-knowledge-map-canvas-design.md。
"""
from __future__ import annotations

import hashlib
import json as _json
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

# spec 2：JSON Canvas 预设色 "1"-"6" 常量映射（硬编码，不读 .obsidian/graph.json 的 RGB int）。
_TYPE_COLOR = {"concept": "5", "topic": "6", "comparison": "3",
               "synthesis": "4", "overview": "2"}
_VALID_COLORS = {"1", "2", "3", "4", "5", "6"}

# spec 4：degree 裁剪的"目标页 type 权重"（越小越优先保留）。nav 页（overview/topic）连边优先于
# concept-concept，hub 超额时先保住通往导航中枢的边。
_EDGE_TYPE_WEIGHT = {"overview": 0, "topic": 1, "comparison": 2, "synthesis": 2, "concept": 3}

_GLOBAL = "_global"
_CROSS = "_cross-domain"


def _h16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _node_id(page: dict) -> str:
    """spec：稳定 16-hex id = sha256(canonical_id or page_path)[:16]。概念页改路径不抖动身份。"""
    return _h16(page.get("canonical_id") or page["page_path"])


def _color(ptype: str):
    return _TYPE_COLOR.get(ptype)


def _node_domain(page: dict) -> str:
    """spec 3 domain 归属：concept→frontmatter.domain；topic→单 domains[] 取唯一值，多/空→_cross-domain；
    overview/comparison/synthesis（视为无 domain）→ _global。topic 缺 domains[] 时回退到 domain 单值。"""
    t = page["type"]
    if t == "concept":
        return page.get("domain") or ""
    if t == "topic":
        doms = page.get("domains") or ([page["domain"]] if page.get("domain") else [])
        return doms[0] if len(doms) == 1 else _CROSS
    return _GLOBAL  # overview / comparison / synthesis


def collect_map_pages(vault) -> list[dict]:
    """扫 published 页 → 节点集（type ∈ MAP_TYPES，排除 lesson/source/派生/_meta 等）。
    捕获 domain（concept）/ domains[]（topic）/ canonical_id（concept）/ related_concepts[]（topic）。"""
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
                      "domains": meta.get("domains") or [],
                      "canonical_id": meta.get("canonical_id", "") or "",
                      "related_concepts": meta.get("related_concepts") or [],
                      "title": meta.get("title") or meta.get("canonical_name") or rel,
                      "links": links})
    return pages


def _edge_key(edge: tuple, nodes: dict) -> tuple:
    """spec 4 全局优先级：按两端点中更高优先（更小权重）的目标 type 排序，再按 canonical_id 字典序
    （无 canonical_id 的页回退到 page_path）。"""
    a, b = edge
    w = min(_EDGE_TYPE_WEIGHT.get(nodes[a]["type"], 9),
            _EDGE_TYPE_WEIGHT.get(nodes[b]["type"], 9))
    ka = nodes[a].get("canonical_id") or a
    kb = nodes[b].get("canonical_id") or b
    return (w, tuple(sorted((ka, kb))))


def build_graph(pages: list[dict]) -> tuple[dict, list[tuple]]:
    """nodes {page_path: page} + edges [(a,b)] —— 只连节点集内、无向去重折叠、按确定性优先级全局排序后
    贪心 per-node degree 裁剪（spec：输出与输入顺序无关）。"""
    nodes = {p["page_path"]: p for p in pages}
    raw: set[tuple] = set()
    for p in pages:
        src = p["page_path"]
        for tgt in p["links"]:
            for cand in ((tgt,) if tgt.endswith(".md") else (tgt, f"{tgt}.md")):
                if cand in nodes and cand != src:
                    raw.add(tuple(sorted((src, cand))))     # 双向折叠：min→max
                    break
    max_deg = getattr(thresholds, "CANVAS_MAX_DEGREE")
    deg: dict[str, int] = {}
    edges: list[tuple] = []
    for a, b in sorted(raw, key=lambda e: _edge_key(e, nodes)):
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
    """topic 收录 concept = 正文 full-path wikilink ∪ frontmatter.related_concepts[]（canonical_id 解析）。
    **primary assignment**：一个 concept 可被多个 topic 链接，但布局上只归属 sorted-first 的 topic
    （每页一个 file node，spec），后续 topic 不重复收录、只各自连边。
    返回 (membership {topic_path: [concept_path...]}, unassigned {concept.domain: [concept_path...]})。"""
    concepts = {pp for pp, p in nodes.items() if p["type"] == "concept"}
    cid_index = {p["canonical_id"]: pp for pp, p in nodes.items()
                 if p["type"] == "concept" and p.get("canonical_id")}
    membership: dict[str, list] = {}
    claimed: set = set()
    for tp in sorted(pp for pp, p in nodes.items() if p["type"] == "topic"):
        members: list[str] = []
        # 正文 wikilink → concept（只识别 full vault 相对路径；裸名宽容补 .md）
        for tgt in sorted(nodes[tp]["links"]):
            for cand in ((tgt,) if tgt.endswith(".md") else (tgt, f"{tgt}.md")):
                if cand in concepts and cand not in claimed and cand not in members:
                    members.append(cand); break
        # related_concepts[]（canonical_id）作可选补充，解析到 concept page_path，取并集
        for rc in sorted(str(x) for x in (nodes[tp].get("related_concepts") or [])):
            cp = cid_index.get(rc)
            if cp and cp not in claimed and cp not in members:
                members.append(cp)
        claimed |= set(members)                              # primary：先到先得，后续 topic 不再收录
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
    """确定性、幂等布局：_global 独立顶行 → 领域组（含 _cross-domain，按名排序）横向铺开。
    组内每 topic 一子组（topic 锚 + 4 列 concept 网格）+ 一个未分类子组。"""
    pos: dict[str, tuple] = {}
    groups: list[dict] = []
    y = 0
    # 第一行：_global（overview + 无 domain 的 comparison / synthesis），横排
    glob = sorted(pp for pp, p in nodes.items() if _node_domain(p) == _GLOBAL)
    if glob:
        x = _PAD
        for pp in glob:
            pos[pp] = (x, y, _NODE_W, _NODE_H); x += _NODE_W + _GX
        groups.append(_group("global", "全局（总览 / 跨域综合）", glob, pos, pad=_PAD * 2))
        y += _NODE_H + _BAND_GAP
    # 第二行起：所有领域组（含 _cross-domain）按名排序，**横向铺开**（各占独立 x 带，组内向下流）
    band_y0 = y
    dom_x = _PAD
    domains = sorted({_node_domain(p) for p in nodes.values()} - {_GLOBAL})
    for dom in domains:
        dom_members: list[str] = []
        y = band_y0
        dom_max_x = dom_x + _NODE_W                          # 至少 topic 列宽
        # 各 topic 行（topic 锚 + 4 列 concept 网格），在本 domain 的 x 带内向下排
        for tp in sorted(pp for pp, p in nodes.items()
                         if p["type"] == "topic" and _node_domain(p) == dom):
            ty = y
            pos[tp] = (dom_x, ty, _NODE_W, _NODE_H); dom_members.append(tp)
            members = membership.get(tp, [])
            cx0 = dom_x + _NODE_W + _GX
            for i, cp in enumerate(members):
                cx = cx0 + (i % _COL) * (_NODE_W + _GX)
                pos[cp] = (cx, ty + (i // _COL) * (_NODE_H + _GY), _NODE_W, _NODE_H)
                dom_members.append(cp); dom_max_x = max(dom_max_x, cx + _NODE_W)
            groups.append(_group("topic:" + tp, f"主题: {nodes[tp]['title']}", [tp] + members, pos))
            rows = max(1, (len(members) + _COL - 1) // _COL)
            y = ty + rows * (_NODE_H + _GY)
        # 未分类子组（无 topic 收录的 concept，故意暴露结构债）
        un = unassigned.get(dom, [])
        if un:
            uy = y
            for i, cp in enumerate(un):
                cx = dom_x + (i % _COL) * (_NODE_W + _GX)
                pos[cp] = (cx, uy + (i // _COL) * (_NODE_H + _GY), _NODE_W, _NODE_H)
                dom_members.append(cp); dom_max_x = max(dom_max_x, cx + _NODE_W)
            groups.append(_group("unassigned:" + dom, "未分类（待 topic 收编）", un, pos))
            y = uy + ((len(un) + _COL - 1) // _COL) * (_NODE_H + _GY)
        if dom_members:
            label = "跨域主题" if dom == _CROSS else f"领域: {dom}"
            groups.append(_group("domain:" + dom, label, dom_members, pos, pad=_PAD * 2))
        dom_x = dom_max_x + _BAND_GAP                        # 下一 domain 排到右侧
    return pos, groups


def to_canvas(vault) -> dict:
    pages = collect_map_pages(vault)
    nodes, edges = build_graph(pages)
    membership, unassigned = topic_membership(nodes)
    pos, groups = layout(nodes, membership, unassigned)
    cnodes: list[dict] = list(groups)                       # groups render under files (array order = z)
    for pp in sorted(nodes):
        x, y, w, h = pos[pp]
        node = {"id": _node_id(nodes[pp]), "type": "file", "file": pp, "x": x, "y": y,
                "width": w, "height": h}
        col = _color(nodes[pp]["type"])
        if col:
            node["color"] = col
        cnodes.append(node)
    # spec 6：edge id = sha256(from_id + "->" + to_id)[:16]（节点 id，非 path）
    cedges = []
    for a, b in edges:
        fid, tid = _node_id(nodes[a]), _node_id(nodes[b])
        cedges.append({"id": _h16(f"{fid}->{tid}"), "fromNode": fid, "toNode": tid})
    return {"nodes": cnodes, "edges": cedges}


def validate_canvas(canvas: dict, valid_files: set[str]) -> list[str]:
    """kepano json-canvas 自检 + file 指向 published 页。返回问题列表（[] = 合法）。
    ① id 唯一 ② edge 引用存在节点 ③ file 指向 published ④ type 白名单 ⑥ color 合法
    ⑦ JSON 可解析 ⑧ 必需字段（id/x/y/width/height）齐全。"""
    problems: list[str] = []
    ids: list[str] = []
    node_ids: set[str] = set()
    for n in canvas.get("nodes", []):
        nid = n.get("id")
        if nid is None:
            problems.append(f"node missing id: {n.get('type')} {n.get('label') or n.get('file')}")
        ids.append(nid)
        node_ids.add(nid)
        if n.get("type") not in ("file", "group", "text", "link"):
            problems.append(f"bad node type: {n.get('type')}")
        if n.get("type") == "file" and n.get("file") not in valid_files:
            problems.append(f"file node points to non-published page: {n.get('file')}")
        if "color" in n and not (n["color"] in _VALID_COLORS
                                 or (isinstance(n["color"], str) and n["color"].startswith("#"))):
            problems.append(f"bad color (not preset 1-6 or #hex): {n.get('color')}")
        for k in ("x", "y", "width", "height"):
            if k not in n:
                problems.append(f"node {nid} missing {k}")
    for e in canvas.get("edges", []):
        ids.append(e.get("id"))
        if e.get("fromNode") not in node_ids:
            problems.append(f"edge fromNode missing node: {e.get('fromNode')}")
        if e.get("toNode") not in node_ids:
            problems.append(f"edge toNode missing node: {e.get('toNode')}")
    if len(ids) != len(set(ids)):
        problems.append("duplicate id across nodes/edges")
    try:
        _json.dumps(canvas)                                 # parseable, no unencodable
    except (TypeError, ValueError) as e:
        problems.append(f"not JSON-serializable: {e}")
    return problems


def write_canvas(vault) -> Path:
    vault = Path(vault)
    canvas = to_canvas(vault)
    valid = {n["file"] for n in canvas["nodes"] if n["type"] == "file"}
    problems = validate_canvas(canvas, valid)
    if problems:
        raise ValueError("canvas self-check failed: " + "; ".join(problems[:10]))
    out = vault / CANVAS_FILE
    out.write_text(_json.dumps(canvas, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    return out
