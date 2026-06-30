"""Knowledge Graph v2.0 schema constants + deterministic id helper（零 LLM、零兄弟依赖）。

设计真值见 docs/specs/knowledge-graph-v2.md。v2.0 关系白名单刻意收窄到三类（轻标注、重确定性
分析）；八类细关系与 source_spine 完整脊柱属 v2.1。
"""
from __future__ import annotations

import hashlib

GRAPH_VERSION = 2
GRAPH_SCOPE = "v2.0"

# v2.0 关系白名单（轻标注）：depends_on 前置 / contrasts 对比 / related 强关系但类型不精确。
RELATIONS = {"depends_on", "contrasts", "related"}
CONFIDENCES = {"extracted", "inferred", "ambiguous"}
DIRECTIONS = {"forward", "both", "undirected"}

RELATION_DIRECTION = {
    "depends_on": "forward",
    "contrasts": "both",
    "related": "undirected",
}

# 关系只做小加成（权重以结构信号为主，见 graph_analysis）。
RELATION_BONUS = {
    "depends_on": 1.0,
    "contrasts": 0.8,
    "related": 0.45,
}

CONFIDENCE_SCORE = {
    "extracted": 1.0,
    "inferred": 0.7,
    "ambiguous": 0.35,
}

TYPE_PRIORITY = {
    "overview": 1.0,
    "topic": 0.9,
    "concept": 0.85,
    "synthesis": 0.8,
    "comparison": 0.75,
    "source": 0.65,
    "lesson": 0.45,
}


def stable_id(prefix: str, payload: str) -> str:
    """确定性 id = "<prefix>:<sha256(payload)[:16]>"。同 (prefix,payload) 恒等，跨 prefix 不撞。"""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"
