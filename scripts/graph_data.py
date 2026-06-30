"""Knowledge Graph v2.0 — graph-data 写入器：组装 schema v2.0 并写 graph-data.generated.json。

这是唯一的中间数据契约（spec §2）：HTML renderer 只消费它，不得重扫 Markdown。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_schema as gs

GRAPH_DATA_FILE = "graph-data.generated.json"


def _generated_at() -> str:
    if os.environ.get("STUDY_KB_GRAPH_TEST_MODE") == "1":
        return "2026-01-01T00:00:00Z"
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_graph_data(analyzed: dict) -> dict:
    nodes = sorted(analyzed.get("nodes", []), key=lambda n: n["id"])
    edges = sorted(analyzed.get("edges", []), key=lambda e: e["id"])
    communities = sorted(analyzed.get("communities", []), key=lambda c: c["id"])
    return {
        "version": gs.GRAPH_VERSION,
        "generated_at": _generated_at(),
        "scope": gs.GRAPH_SCOPE,
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "learning_paths": analyzed.get("learning_paths", []),
        "insights": analyzed.get("insights", []),
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "community_count": len(communities),
        },
    }


def write_graph_data(vault, analyzed: dict) -> Path:
    vault = Path(vault)
    data = to_graph_data(analyzed)
    out = vault / GRAPH_DATA_FILE
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    return out
