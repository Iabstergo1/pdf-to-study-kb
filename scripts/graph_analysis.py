"""Knowledge Graph v2.0 — analysis 层：结构信号边权 + 确定性社区 + 学习路径 + insights（零 LLM）。

只读 model（不读 Markdown，spec §单向管线边界）。边权以确定性结构信号为主、关系标注只做加成；
社区以 topic membership 为骨架（spec 约束：单一 domain 不得塌成一团），零依赖 Louvain 处理无主题
余量并提供桥接/结构信号。输出确定性，与输入顺序无关。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_schema as gs

# 类型亲和（无序对查表；缺省 0.5）。
_AFFINITY = {
    ("concept", "concept"): 1.0,
    ("concept", "topic"): 1.0,
    ("comparison", "concept"): 0.9,
    ("concept", "synthesis"): 0.8,
    ("topic", "topic"): 0.8,
    ("overview", "topic"): 0.7,
    ("concept", "source"): 0.6,
    ("source", "source"): 0.3,
}


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _ref_source(ref):
    if isinstance(ref, dict):
        return ref.get("source") or ""
    if isinstance(ref, str):
        return ref.split(":", 1)[0]
    return ""


def _node_sources(node) -> set:
    return {_ref_source(r) for r in (node.get("source_refs") or [])} - {"", None}


def _source_overlap(na, nb) -> float:
    if not na or not nb:
        return 0.0
    sa, sb = _node_sources(na), _node_sources(nb)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def _co_citation(a, b, inlinks) -> float:
    ia, ib = inlinks.get(a, set()), inlinks.get(b, set())
    if not ia and not ib:
        return 0.0
    return len(ia & ib) / max(len(ia), len(ib), 1)


def _type_affinity(ta, tb) -> float:
    if not ta or not tb:
        return 0.5
    return _AFFINITY.get(tuple(sorted((ta, tb))), 0.5)


# ── 零依赖确定性 Louvain（移植自参考项目 graph-analysis.js，纯结构、可重复）──

def _local_move(graph):
    nodes = sorted(graph["nodes"].keys())
    communities = {n: n for n in nodes}
    totals = {n: graph["degrees"].get(n, 0.0) for n in nodes}
    if graph["m2"] == 0:
        return communities, False
    moved = False
    changed = True
    passes = 0
    while changed and passes < 50:
        passes += 1
        changed = False
        for n in nodes:
            degree = graph["degrees"].get(n, 0.0)
            current = communities[n]
            neigh = {}
            for m, w in graph["nodes"][n].items():
                c = communities[m]
                neigh[c] = neigh.get(c, 0.0) + w
            totals[current] = totals.get(current, 0.0) - degree
            neigh.setdefault(current, 0.0)
            best_c, best_gain = current, 0.0
            for c in sorted(neigh.keys()):
                gain = neigh[c] - (totals.get(c, 0.0) * degree) / graph["m2"]
                if gain > best_gain + 1e-9:
                    best_gain, best_c = gain, c
            communities[n] = best_c
            totals[best_c] = totals.get(best_c, 0.0) + degree
            if best_c != current:
                changed = True
                moved = True
    return communities, moved


def _aggregate(graph, communities):
    comm_ids = sorted(set(communities.values()))
    agg = {c: {} for c in comm_ids}
    members = {c: [] for c in comm_ids}
    for n, c in communities.items():
        members[c].extend(graph["members"].get(n, [n]))
    for n, neighbors in graph["nodes"].items():
        sc = communities[n]
        for m, w in neighbors.items():
            if n > m:
                continue
            tc = communities[m]
            agg[sc][tc] = agg[sc].get(tc, 0.0) + w
            if sc != tc:
                agg[tc][sc] = agg[tc].get(sc, 0.0) + w
    degrees = {}
    for c, neighbors in agg.items():
        degrees[c] = sum((w * 2 if m == c else w) for m, w in neighbors.items())
    return {"nodes": agg, "degrees": degrees, "members": members, "m2": sum(degrees.values())}


def _run_louvain(node_ids, pair_weight):
    adjacency = {n: {} for n in node_ids}
    degrees = {n: 0.0 for n in node_ids}
    for (a, b), w in pair_weight.items():
        if a not in adjacency or b not in adjacency or a == b:
            continue
        adjacency[a][b] = adjacency[a].get(b, 0.0) + w
        adjacency[b][a] = adjacency[b].get(a, 0.0) + w
        degrees[a] += w
        degrees[b] += w
    graph = {"nodes": adjacency, "degrees": degrees,
             "members": {n: [n] for n in node_ids}, "m2": sum(degrees.values())}
    best_members = graph["members"]
    while True:
        communities, changed = _local_move(graph)
        nxt = _aggregate(graph, communities)
        best_members = nxt["members"]
        if not changed or len(nxt["nodes"]) == len(graph["nodes"]):
            break
        graph = nxt
    final = {}
    for cid, members in best_members.items():
        for n in members:
            final[n] = cid
    for n in node_ids:
        final.setdefault(n, n)
    return final


def _assign_communities(nodes, edges, membership, base_weight, degree):
    node_ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}
    pair_weight = {}
    adj = defaultdict(list)
    for e in edges:
        a, b = e["source"], e["target"]
        pair_weight[tuple(sorted((a, b)))] = e["weight"]
        adj[a].append((b, e["weight"]))
        adj[b].append((a, e["weight"]))
    louvain = _run_louvain(node_ids, pair_weight)

    # topic membership 为社区骨架：topic 自成社区，成员归入其 topic 社区（primary）。
    member_of = {}
    for tid, members in membership.items():
        for cid in members:
            member_of.setdefault(cid, tid)
    final = {}
    for n in nodes:
        nid = n["id"]
        if n["type"] == "topic":
            final[nid] = "community:" + nid
        elif nid in member_of and member_of[nid] in by_id:
            final[nid] = "community:" + member_of[nid]

    # 余量节点：附到最强已归属邻居的社区（迭代到稳定）。
    for _ in range(len(node_ids) + 1):
        changed = False
        for nid in node_ids:
            if nid in final or degree.get(nid, 0) == 0:
                continue
            for nb, _w in sorted(adj[nid], key=lambda x: (-x[1], str(x[0]))):
                if final.get(nb, "_unassigned") != "_unassigned" and nb in final:
                    final[nid] = final[nb]
                    changed = True
                    break
        if not changed:
            break

    # 仍无主题可附但有边的连通块：用 Louvain 分组，按最高权重节点命名。
    lgroups = defaultdict(list)
    for nid in node_ids:
        lgroups[louvain[nid]].append(nid)
    for members in lgroups.values():
        unseeded = [m for m in members if m not in final]
        if not unseeded:
            continue
        anchor = min(unseeded, key=lambda m: (-base_weight.get(m, 0.0), m))
        cid = "community:" + anchor
        for m in unseeded:
            final[m] = cid

    for nid in node_ids:
        final.setdefault(nid, "_unassigned")
    return final


def _aggregate_refs(nodes):
    by_src = {}
    for n in nodes:
        for r in (n.get("source_refs") or []):
            src = _ref_source(r)
            if not src:
                continue
            secs = by_src.setdefault(src, set())
            if isinstance(r, dict):
                secs.update(str(s) for s in (r.get("sections") or []))
            else:
                parts = str(r).split(":", 1)
                if len(parts) > 1 and parts[1]:
                    secs.add(parts[1])
    return [{"source": s, "sections": sorted(secs)} for s, secs in sorted(by_src.items())]


def _build_communities(nodes, final, node_weight):
    by_id = {n["id"]: n for n in nodes}
    groups = defaultdict(list)
    for nid, cid in final.items():
        groups[cid].append(nid)
    comms = []
    for cid in sorted(groups):
        members = sorted(groups[cid], key=lambda m: (-node_weight.get(m, 0.0), m))
        topics = [m for m in members if by_id[m]["type"] == "topic"]
        if cid == "_unassigned":
            label = "未分类"
        elif topics:
            label = by_id[topics[0]]["label"]
        else:
            label = by_id[members[0]]["label"] if members else cid
        comms.append({
            "id": cid, "label": label,
            "type": "fallback" if cid == "_unassigned" else "louvain-topic",
            "node_ids": members,
            "source_refs": _aggregate_refs([by_id[m] for m in members]),
            "weight": round(sum(node_weight.get(m, 0.0) for m in members) / max(len(members), 1), 3),
            "representative_node_ids": members[:8],
        })
    return comms


def _learning_paths(nodes, edges, node_weight):
    by_id = {n["id"]: n for n in nodes}
    topics = sorted((n["id"] for n in nodes if n["type"] == "topic"),
                    key=lambda i: (-node_weight.get(i, 0.0), i))
    members_by_comm = defaultdict(list)
    for n in nodes:
        if n["type"] == "concept":
            members_by_comm[n["community_id"]].append(n["id"])
    order = []
    for tid in topics:
        order.append(tid)
        for cid in sorted(members_by_comm.get(by_id[tid]["community_id"], []),
                          key=lambda i: (-node_weight.get(i, 0.0), i)):
            if cid not in order:
                order.append(cid)
    if not order:
        order = [n["id"] for n in sorted(nodes, key=lambda x: (-node_weight.get(x["id"], 0.0), x["id"]))[:8]]
    order_set = set(order)
    edge_ids = [e["id"] for e in edges
                if e["relation"] == "depends_on" and e["source"] in order_set and e["target"] in order_set]
    return [{
        "id": "path:default", "label": "默认学习路径", "source": None,
        "node_ids": order, "edge_ids": sorted(edge_ids),
        "rationale": "按 topic 与社区代表概念顺序生成，depends_on 决定局部前置。",
        "degraded": len(order) < 3,
    }]


def _insights(nodes, edges, degree):
    inc = defaultdict(list)
    for e in edges:
        inc[e["source"]].append(e)
        inc[e["target"]].append(e)
    out = []
    for n in sorted(nodes, key=lambda x: x["id"]):
        nid, typ = n["id"], n["type"]
        if typ not in ("source", "overview") and not (n.get("source_refs")):
            out.append({"type": "missing_source_refs", "node_id": nid, "severity": "warn"})
        if typ != "source" and degree.get(nid, 0) == 0:
            out.append({"type": "isolated_node", "node_id": nid, "severity": "warn"})
        if n.get("_bridge"):
            out.append({"type": "bridge_node", "node_id": nid,
                        "communities": n.get("_comms", []), "severity": "info"})
        es = inc.get(nid, [])
        if len(es) >= 5:
            weak = sum(1 for e in es if e["confidence"] == "ambiguous" or e["relation"] == "related")
            if weak / len(es) >= 0.6:
                out.append({"type": "weak_high_degree_node", "node_id": nid,
                            "degree": len(es), "severity": "warn"})
    return out


def analyze_graph(model: dict) -> dict:
    nodes_in = model.get("nodes", [])
    by_id = {n["id"]: n for n in nodes_in}
    inlinks = defaultdict(set)
    for e in model.get("edges", []):
        inlinks[e["target"]].add(e["source"])

    edges = []
    degree = defaultdict(int)
    for e in model.get("edges", []):
        rel = e.get("relation") if e.get("relation") in gs.RELATIONS else "related"
        conf = e.get("confidence") if e.get("confidence") in gs.CONFIDENCES else "ambiguous"
        s, t = e["source"], e["target"]
        co = _co_citation(s, t, inlinks)
        so = _source_overlap(by_id.get(s), by_id.get(t))
        ta = _type_affinity((by_id.get(s) or {}).get("type"), (by_id.get(t) or {}).get("type"))
        cs, rb = gs.CONFIDENCE_SCORE[conf], gs.RELATION_BONUS[rel]
        weight = _round(0.30 * co + 0.25 * so + 0.20 * ta + 0.15 * cs + 0.10 * rb)
        edges.append({**e, "relation": rel, "confidence": conf,
                      "direction": gs.RELATION_DIRECTION[rel], "weight": weight,
                      "signals": {"co_citation": _round(co), "source_overlap": _round(so),
                                  "type_affinity": _round(ta), "confidence_score": cs,
                                  "relation_bonus": rb}})
        degree[s] += 1
        degree[t] += 1
    max_deg = max(degree.values(), default=0)

    base = {}
    for n in nodes_in:
        nd = (degree.get(n["id"], 0) / max_deg) if max_deg else 0.0
        ev = min(len(n.get("source_refs") or []), 3) / 3
        tp = gs.TYPE_PRIORITY.get(n.get("type"), 0.4)
        base[n["id"]] = 0.40 * nd + 0.30 * ev + 0.20 * tp

    final = _assign_communities(nodes_in, edges, model.get("membership", {}), base, degree)

    adj = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    out_nodes = []
    for n in nodes_in:
        nid = n["id"]
        cid = final.get(nid, "_unassigned")
        comms = {final.get(nb, "_unassigned") for nb in adj[nid]} - {cid, "_unassigned"}
        bridge = 1.0 if len(comms) >= 2 else 0.0
        nn = dict(n)
        nn["community_id"] = cid
        nn["weight"] = _round(base[nid] + 0.10 * bridge)
        nn["_bridge"] = bridge
        nn["_comms"] = sorted(comms)
        out_nodes.append(nn)
    node_weight = {n["id"]: n["weight"] for n in out_nodes}

    communities = _build_communities(out_nodes, final, node_weight)
    learning = _learning_paths(out_nodes, edges, node_weight)
    insights = _insights(out_nodes, edges, degree)
    for n in out_nodes:
        n.pop("_bridge", None)
        n.pop("_comms", None)
    return {"nodes": out_nodes, "edges": edges, "communities": communities,
            "learning_paths": learning, "insights": insights}
