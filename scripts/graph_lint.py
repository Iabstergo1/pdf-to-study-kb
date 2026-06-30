"""Knowledge Graph v2.0 — graph lint：对 graph-data（及生成的 HTML）做确定性质量检查。

fail-hard（→ errors，CLI 非零退出 / 不写新产物）：schema 缺字段、edge 指向不存在节点、node path 非
published 页、extracted 边无 evidence 且无 source_refs、HTML 内嵌 JSON 不可解析。
warn-only（→ warnings，不阻断）：孤立 / 过密 / 关系降级 / 学习路径 degraded / _unassigned / 缺
source_refs / 别名多指 / depends_on 环。零 LLM。
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage
import thresholds

REQUIRED_TOP = {"version", "generated_at", "scope", "nodes", "edges", "communities",
                "learning_paths", "insights", "stats"}
_DENSE_DEGREE = thresholds.GRAPH_DENSE_DEGREE
_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets"}
_DERIVED = {"index.generated.md", "aliases.md", "knowledge-map.generated.canvas",
            "graph-data.generated.json", "knowledge-graph.generated.html"}
_DATA_SCRIPT = re.compile(r'<script id="graph-data" type="application/json">\s*(.*?)\s*</script>', re.S)


def _published_paths(vault) -> set:
    vault = Path(vault)
    out: set = set()
    if not vault.exists():
        return out
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, _ = mdpage.read_page(f)
        if meta.get("status") == "published":
            out.add(rel)
    return out


def _has_depends_on_cycle(edges) -> bool:
    adj = defaultdict(list)
    for e in edges:
        if e.get("relation") == "depends_on":
            adj[e["source"]].append(e["target"])
    color = defaultdict(int)  # 0=white 1=grey 2=black

    def dfs(u):
        color[u] = 1
        for v in adj[u]:
            if color[v] == 1 or (color[v] == 0 and dfs(v)):
                return True
        color[u] = 2
        return False

    return any(color[n] == 0 and dfs(n) for n in list(adj))


def validate_graph_data(data: dict, vault=None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    missing = REQUIRED_TOP - set(data)
    if missing:
        errors.append(f"graph-data 缺顶层字段: {sorted(missing)}")

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    node_ids = {n.get("id") for n in nodes}

    nid_list = [n.get("id") for n in nodes]
    if len(nid_list) != len(set(nid_list)):
        errors.append("node id 不唯一")
    eid_list = [e.get("id") for e in edges]
    if len(eid_list) != len(set(eid_list)):
        errors.append("edge id 不唯一")

    for e in edges:
        if e.get("source") not in node_ids:
            errors.append(f"edge {e.get('id')} 指向不存在节点(source={e.get('source')})")
        if e.get("target") not in node_ids:
            errors.append(f"edge {e.get('id')} 指向不存在节点(target={e.get('target')})")
        if e.get("confidence") == "extracted" and not e.get("evidence") and not e.get("source_refs"):
            errors.append(f"extracted 边缺 evidence 且缺 source_refs: {e.get('id')}")

    if vault is not None:
        published = _published_paths(vault)
        for n in nodes:
            path = n.get("path")
            if path and path not in published:
                errors.append(f"node {n.get('id')} path 非 published 页: {path}")

    # ── warn-only ──
    degree: dict = defaultdict(int)
    for e in edges:
        degree[e.get("source")] += 1
        degree[e.get("target")] += 1
    for n in sorted(nodes, key=lambda x: str(x.get("id"))):
        nid, typ = n.get("id"), n.get("type")
        if typ not in ("source", "overview") and not n.get("source_refs"):
            warnings.append(f"非 source/overview 节点缺 source_refs: {nid}")
        if typ != "source" and degree.get(nid, 0) == 0:
            warnings.append(f"孤立节点: {nid}")
        if degree.get(nid, 0) > _DENSE_DEGREE:
            warnings.append(f"过密节点(degree={degree[nid]}): {nid}")
    for e in edges:
        if e.get("downgraded"):
            warnings.append(f"未知关系/置信度被降级: {e.get('id')}")
    for p in data.get("learning_paths", []):
        if p.get("degraded"):
            warnings.append(f"学习路径 degraded: {p.get('id')}")
    n_unassigned = sum(1 for n in nodes if n.get("community_id") == "_unassigned")
    if n_unassigned:
        warnings.append(f"存在未分类(_unassigned)节点: {n_unassigned}")
    alias_map: dict = defaultdict(set)
    for n in nodes:
        for a in (n.get("aliases") or []):
            alias_map[a].add(n.get("id"))
    for a, ids in sorted(alias_map.items()):
        if len(ids) > 1:
            warnings.append(f"别名 '{a}' 指向多个 canonical 节点: {sorted(ids)}")
    if _has_depends_on_cycle(edges):
        warnings.append("depends_on 存在简单环")

    return {"errors": errors, "warnings": warnings}


def validate_html(html: str) -> list[str]:
    errs: list[str] = []
    m = _DATA_SCRIPT.search(html or "")
    if not m:
        errs.append("HTML 缺内嵌 graph-data <script>")
        return errs
    try:
        json.loads(m.group(1).replace("<\\/script>", "</script>"))
    except Exception as e:  # noqa: BLE001 - report any parse failure as fail-hard
        errs.append(f"HTML 内嵌 graph-data JSON 不可解析: {e}")
    return errs


def write_report(result: dict, dest) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# graph-lint report", "",
             f"- errors (fail-hard): {len(result['errors'])}",
             f"- warnings: {len(result['warnings'])}", ""]
    if result["errors"]:
        lines += ["## Fail-hard"] + [f"- {e}" for e in result["errors"]] + [""]
    if result["warnings"]:
        lines += ["## Warnings"] + [f"- {w}" for w in result["warnings"]] + [""]
    dest.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return dest
