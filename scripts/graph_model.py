"""Knowledge Graph v2.0 — model 层：读 published 页 → 节点 + 轻量边 + topic membership（零 LLM）。

这是图谱管线里**唯一**允许读 Markdown 的层（spec §单向管线边界）。关系语义只取页面已有的轻量
`<!-- graph: ... -->` 注释，缺失时确定性降级；`topic_membership` 由本模块提供，发布门禁 A2
（wiki_gate.concepts_uncovered_by_topic）与图谱社区共用同一套归属（不另起第二套）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_schema as gs
import mdpage

GRAPH_TYPES = ("overview", "topic", "concept", "comparison", "synthesis", "source")
_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets"}
_DERIVED = {"index.generated.md", "aliases.md", "knowledge-map.generated.canvas",
            "graph-data.generated.json", "knowledge-graph.generated.html"}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_GRAPH_COMMENT = re.compile(r"<!--\s*graph:\s*(.*?)\s*-->")
_FIELD = re.compile(r'(\w+)=("[^"]*"|\S+)')

# 同一无序对多条 raw 边时的择优：关系强 > 弱、置信高 > 低。
_REL_RANK = {"depends_on": 3, "contrasts": 2, "related": 1}
_CONF_RANK = {"extracted": 3, "inferred": 2, "ambiguous": 1}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _page_id(rel: str, meta: dict) -> str:
    """spec 节点 ID 规则：concept 优先 canonical_id；source → source:<id>；其余 → vault 相对路径。"""
    if meta.get("type") == "concept" and meta.get("canonical_id"):
        return str(meta["canonical_id"])
    if meta.get("type") == "source" and meta.get("source_id"):
        return "source:" + str(meta["source_id"])
    return rel


def _label(rel: str, meta: dict) -> str:
    return str(meta.get("title") or meta.get("canonical_name") or Path(rel).stem)


def _wikilinks(text: str) -> set[str]:
    # 剥掉表格内转义写法 [[path\|alias]] 的目标尾部反斜杠（Obsidian 标准转义，勿判丢边）
    return {t.strip().rstrip("\\") for t in _WIKILINK.findall(text)
            if not t.strip().startswith(("http://", "https://"))}


def _summary(body: str, limit_cjk: int = 180, limit_ascii: int = 320) -> str:
    """第一段高信息正文：跳过标题/表格/图片嵌入/脚注/分隔线/引用/注释；CJK 多→180 字，纯 ASCII→320。"""
    para: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if para:
                break
            continue
        if line.startswith(("#", "|", "![[", "![", ">", "[^", "---", "<!--")):
            continue
        para.append(line)
    text = " ".join(para)
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    limit = limit_cjk if cjk * 2 >= len(text) else limit_ascii
    return text[:limit]


def collect_graph_pages(vault) -> list[dict]:
    vault = Path(vault)
    pages: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published" or meta.get("type") not in GRAPH_TYPES:
            continue
        pages.append({
            "id": _page_id(rel, meta),
            "label": _label(rel, meta),
            "type": meta.get("type"),
            "path": rel,
            "aliases": [str(a) for a in _as_list(meta.get("aliases"))],
            "summary": _summary(body),
            "source_refs": _as_list(meta.get("source_refs")),
            "domain": meta.get("domain", "") or "",
            "domains": [str(d) for d in _as_list(meta.get("domains"))],
            "related_concepts": [str(c) for c in _as_list(meta.get("related_concepts"))],
            "canonical_id": meta.get("canonical_id", "") or "",
            "_links": _wikilinks(body),
            "_body": body,
        })
    return pages


def _parse_graph_comment(text: str) -> dict:
    fields: dict[str, str] = {}
    for key, value in _FIELD.findall(text):
        fields[key] = value.strip('"')
    relation = fields.get("relation", "")
    confidence = fields.get("confidence", "")
    downgraded = (bool(relation) and relation not in gs.RELATIONS) or \
                 (bool(confidence) and confidence not in gs.CONFIDENCES)
    return {
        "relation": relation if relation in gs.RELATIONS else "related",
        "confidence": confidence if confidence in gs.CONFIDENCES else "ambiguous",
        "evidence": fields.get("evidence", ""),
        "downgraded": downgraded,
    }


def _resolve(target: str, path_to_id: dict) -> str | None:
    for cand in ((target,) if target.endswith(".md") else (target, f"{target}.md")):
        if cand in path_to_id:
            return path_to_id[cand]
    return None


def _better(new: dict, old: dict) -> bool:
    return ((_REL_RANK[new["relation"]], _CONF_RANK[new["confidence"]])
            > (_REL_RANK[old["relation"]], _CONF_RANK[old["confidence"]]))


def topic_membership(nodes: dict) -> tuple[dict, dict]:
    """topic 收录 concept = 正文 full-path wikilink ∪ frontmatter.related_concepts[]（canonical_id 解析）。
    primary assignment：一个 concept 只归属 sorted-first 的 topic。**发布门禁 A2
    （wiki_gate.concepts_uncovered_by_topic）与图谱社区共用此唯一实现**（原在 canvas_map，canvas 移除后迁此）。
    返回 (membership {topic_path: [concept_path...]}, unassigned {concept.domain: [concept_path...]})。"""
    concepts = {pp for pp, p in nodes.items() if p["type"] == "concept"}
    cid_index = {p["canonical_id"]: pp for pp, p in nodes.items()
                 if p["type"] == "concept" and p.get("canonical_id")}
    membership: dict[str, list] = {}
    claimed: set = set()
    for tp in sorted(pp for pp, p in nodes.items() if p["type"] == "topic"):
        members: list[str] = []
        for tgt in sorted(nodes[tp]["links"]):
            for cand in ((tgt,) if tgt.endswith(".md") else (tgt, f"{tgt}.md")):
                if cand in concepts and cand not in claimed and cand not in members:
                    members.append(cand)
                    break
        for rc in sorted(str(x) for x in (nodes[tp].get("related_concepts") or [])):
            cp = cid_index.get(rc)
            if cp and cp not in claimed and cp not in members:
                members.append(cp)
        claimed |= set(members)
        membership[tp] = members
    unassigned: dict[str, list] = {}
    for cp in sorted(concepts - claimed):
        unassigned.setdefault(nodes[cp]["domain"], []).append(cp)
    return membership, unassigned


def topic_membership_ids(pages: list[dict]) -> tuple[dict, dict]:
    """复用本模块 topic_membership（唯一实现），把结果从 page_path 翻译成节点 id。
    返回 (membership {topic_id: [concept_id...]}, unassigned {domain: [concept_id...]})。"""
    path_to_id = {p["path"]: p["id"] for p in pages}
    mnodes = {p["path"]: {"type": p["type"], "canonical_id": p["canonical_id"],
                          "domain": p["domain"], "related_concepts": p["related_concepts"],
                          "links": p["_links"]} for p in pages}
    m_paths, un_paths = topic_membership(mnodes)
    membership = {path_to_id[tp]: [path_to_id[cp] for cp in members if cp in path_to_id]
                  for tp, members in m_paths.items()}
    unassigned = {dom: [path_to_id[cp] for cp in cps if cp in path_to_id]
                  for dom, cps in un_paths.items()}
    return membership, unassigned


def build_graph_model(vault) -> dict:
    pages = collect_graph_pages(vault)
    path_to_id = {p["path"]: p["id"] for p in pages}
    id_to_path = {p["id"]: p["path"] for p in pages}
    by_id = {p["id"]: p for p in pages}
    membership, unassigned = topic_membership_ids(pages)

    pair: dict[frozenset, dict] = {}

    def _consider(src_id, tgt_id, meta, inferred_by, src_refs):
        if src_id == tgt_id or src_id is None or tgt_id is None:
            return
        key = frozenset((src_id, tgt_id))
        cand = {"source": src_id, "target": tgt_id, "relation": meta["relation"],
                "confidence": meta["confidence"], "evidence": meta.get("evidence", ""),
                "inferred_by": inferred_by, "source_refs": src_refs,
                "downgraded": meta.get("downgraded", False)}
        old = pair.get(key)
        if old is None or _better(cand, old):
            pair[key] = cand

    # (a) 正文 wikilink（带可选 graph 注释）。
    for p in pages:
        for line in p["_body"].splitlines():
            targets = [t for t in (_resolve(x, path_to_id) for x in _wikilinks(line))
                       if t and t != p["id"]]
            if not targets:
                continue
            match = _GRAPH_COMMENT.search(line)
            if match:
                meta = _parse_graph_comment(match.group(1))
                inferred_by = "graph-comment"
            else:
                meta = {"relation": "related", "confidence": "ambiguous", "evidence": ""}
                inferred_by = "wikilink"
            for t in targets:
                _consider(p["id"], t, meta, inferred_by, p["source_refs"])

    # (b) topic membership 派生边（related/inferred）：覆盖只在 related_concepts[] 里、正文无链接的成员。
    for tid, members in membership.items():
        for cid in members:
            if frozenset((tid, cid)) not in pair:
                _consider(tid, cid, {"relation": "related", "confidence": "inferred", "evidence": ""},
                          "topic-membership", by_id[tid]["source_refs"] if tid in by_id else [])

    edges = []
    for edge in pair.values():
        a, b = sorted((edge["source"], edge["target"]))
        edge["id"] = gs.stable_id("edge", f"{a}\t{b}\t{edge['relation']}")
        edge["source_path"] = id_to_path.get(edge["source"], "")
        edge["target_path"] = id_to_path.get(edge["target"], "")
        edges.append(edge)
    edges.sort(key=lambda e: e["id"])

    nodes = [{k: v for k, v in p.items() if not k.startswith("_")} for p in pages]
    return {"nodes": nodes, "edges": edges, "membership": membership, "unassigned": unassigned}
