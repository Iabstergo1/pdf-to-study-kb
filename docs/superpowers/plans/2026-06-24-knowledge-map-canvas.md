# Knowledge-Map Canvas + Writing Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a deterministic, zero-LLM JSON Canvas reading map (`wiki/knowledge-map.generated.canvas`) from the published concept graph, plus callout/embed-width writing enhancements.

**Architecture:** New pure-function module `scripts/canvas_map.py` reads published pages (via `mdpage.read_page`), builds a node/edge model (nodes = `overview/topic/concept/comparison/synthesis`; edges = in-set wikilinks, degree-capped), lays them out deterministically (domain group → topic sub-group → concept grid + "unassigned" subregion), and serializes to JSON Canvas. A `rebuild-canvas` CLI (fail-hard) and a `cmd_lint` finish hook (publish-isolated: canvas failure never rolls back a publish) write it. Writing enhancements add a callout whitelist lint to `wiki_gate.py` and conventions to `write-pages.md`.

**Tech Stack:** Python 3.12, stdlib (`hashlib`/`json`/`re`/`pathlib`), `PyYAML` (via existing `mdpage`), `pytest`. No new dependencies.

## Global Constraints

- **Deterministic, zero-LLM.** `canvas_map.py` and all CLI are pure/IO-only — no model calls, no network. Same input → byte-identical output (stable 16-hex ids, sorted iteration).
- **Canvas is a derived artifact, never a publish gate.** In `cmd_lint`, canvas is generated AFTER promote/registry/aliases/index succeed; canvas failure → warning + keep old canvas + publish still succeeds (no rollback).
- **Dual-tree byte parity.** Any change under `.claude/skills/**` must be mirrored byte-identical in `.agents/skills/**` (modulo the `CLAUDE.md`↔`AGENTS.md` truth pointer). Verified by `tests/test_skill_standard.py::test_t2_dual_agent_parity`.
- **Wikilinks are full vault-relative paths** (`[[domains/x/concepts/y.md]]`), enforced by existing lint — canvas matches node keys against these directly.
- **Run tests as** `$env:PYTHONUTF8=1; python -m pytest tests/ -q`. Baseline before this work: **476 passed**. Interpreter: `D:\miniconda3\envs\study-kb\python.exe`.
- **Windows / newline:** derived files write with `newline="\n"` (match existing `write_index`/`write_registry`).

---

### Task 1: canvas_map — node/edge model + degree cap

**Files:**
- Create: `scripts/canvas_map.py`
- Modify: `scripts/thresholds.py` (add `CANVAS_MAX_DEGREE`)
- Test: `tests/test_canvas_map.py`

**Interfaces:**
- Consumes: `mdpage.read_page(path) -> (meta, body)`; `thresholds.CANVAS_MAX_DEGREE: int`.
- Produces:
  - `MAP_TYPES = ("overview","topic","concept","comparison","synthesis")`
  - `CANVAS_FILE = "knowledge-map.generated.canvas"`
  - `collect_map_pages(vault) -> list[dict]` — each `{page_path, type, domain, title, links: set[str]}`, only published pages whose `type ∈ MAP_TYPES`.
  - `build_graph(pages) -> tuple[dict, list[tuple]]` — `nodes: {page_path: page}`, `edges: list[(a,b)]` (a<b, in-set, deduped, per-node degree ≤ `CANVAS_MAX_DEGREE`).

- [ ] **Step 1: Add threshold**

In `scripts/thresholds.py`, after the `FRAGMENT_*` block, add:
```python
# ── knowledge-map canvas（派生阅读层；验收期，不折进缓存键）──
CANVAS_MAX_DEGREE = _int("STUDY_KB_CANVAS_MAX_DEGREE", 12)  # 单节点最大连边数，防 hub 压垮图
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_canvas_map.py`:
```python
import importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


cm = _load("canvas_map")


def _page(vault, rel, *, type, domain="", title="", links=(), status="published"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"see [[{l}]]\n" for l in links)
    meta = f"---\ntype: {type}\nstatus: {status}\ndomain: {domain}\ntitle: {title or rel}\n---\n"
    p.write_text(meta + body, encoding="utf-8")


def test_collect_excludes_lessons_sources_and_unpublished(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/lessons/l1.md", type="lesson", domain="d")         # excluded (type)
    _page(v, "sources/s1.md", type="source")                              # excluded (type)
    _page(v, "topics/t1.md", type="topic", domain="d", status="proposed") # excluded (unpublished)
    paths = {p["page_path"] for p in cm.collect_map_pages(v)}
    assert paths == {"domains/d/concepts/a.md"}


def test_build_graph_edges_dedup_and_in_set(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d",
          links=["domains/d/concepts/a.md", "domains/d/concepts/b.md", "http://x"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d",
          links=["topics/t.md"])               # back-link → dedup with t→a
    _page(v, "domains/d/concepts/b.md", type="concept", domain="d")
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    assert set(nodes) == {"topics/t.md", "domains/d/concepts/a.md", "domains/d/concepts/b.md"}
    assert ("domains/d/concepts/a.md", "topics/t.md") in edges
    assert ("domains/d/concepts/b.md", "topics/t.md") in edges
    assert len(edges) == 2                       # t↔a counted once; http ignored


def test_build_graph_degree_cap(tmp_path, monkeypatch):
    v = tmp_path / "wiki"
    hub_links = [f"domains/d/concepts/c{i}.md" for i in range(20)]
    _page(v, "topics/hub.md", type="topic", domain="d", links=hub_links)
    for i in range(20):
        _page(v, f"domains/d/concepts/c{i}.md", type="concept", domain="d")
    import thresholds; monkeypatch.setattr(thresholds, "CANVAS_MAX_DEGREE", 5)
    nodes, edges = cm.build_graph(cm.collect_map_pages(v))
    hub_deg = sum(1 for a, b in edges if "hub.md" in (a, b))
    assert hub_deg == 5                          # capped
```

- [ ] **Step 3: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -q`
Expected: FAIL — `No module named 'canvas_map'`.

- [ ] **Step 4: Write minimal implementation**

Create `scripts/canvas_map.py`:
```python
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
    max_deg = thresholds.CANVAS_MAX_DEGREE
    deg: dict[str, int] = {}
    edges: list[tuple] = []
    for a, b in sorted(raw):
        if deg.get(a, 0) >= max_deg or deg.get(b, 0) >= max_deg:
            continue
        edges.append((a, b))
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return nodes, edges
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/canvas_map.py scripts/thresholds.py tests/test_canvas_map.py
git commit -m "feat(canvas): node/edge model + degree cap for knowledge-map"
```

---

### Task 2: canvas_map — topic membership + deterministic layout

**Files:**
- Modify: `scripts/canvas_map.py`
- Test: `tests/test_canvas_map.py`

**Interfaces:**
- Consumes: `build_graph` output `nodes: dict`.
- Produces:
  - `topic_membership(nodes) -> tuple[dict, dict]` — `(membership: {topic_path: [concept_path...]}, unassigned: {domain: [concept_path...]})`. A concept is "claimed" if any topic links to it; unclaimed concepts go to `unassigned[domain]`.
  - `layout(nodes, membership, unassigned) -> tuple[dict, list[dict]]` — `(pos: {page_path: (x,y,w,h)}, groups: [group_node_dict...])`. Deterministic; group node dict = `{id, type:"group", label, x, y, width, height}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_canvas_map.py`:
```python
def test_topic_membership_and_unassigned(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/concepts/orphan.md", type="concept", domain="d")  # no topic links it
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    assert membership["topics/t.md"] == ["domains/d/concepts/a.md"]
    assert unassigned["d"] == ["domains/d/concepts/orphan.md"]


def test_layout_deterministic_and_covers_all_nodes(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    _page(v, "domains/d/concepts/orphan.md", type="concept", domain="d")
    nodes, _ = cm.build_graph(cm.collect_map_pages(v))
    membership, unassigned = cm.topic_membership(nodes)
    pos1, groups1 = cm.layout(nodes, membership, unassigned)
    pos2, groups2 = cm.layout(nodes, membership, unassigned)
    assert pos1 == pos2 and groups1 == groups2                 # deterministic
    assert set(pos1) == set(nodes)                             # every node placed
    labels = [g["label"] for g in groups1]
    assert any("未分类" in l for l in labels)                  # unassigned subregion exists
    assert any(l == "领域: d" for l in labels)                 # domain group exists
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -k "membership or layout" -q`
Expected: FAIL — `module 'canvas_map' has no attribute 'topic_membership'`.

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/canvas_map.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/canvas_map.py tests/test_canvas_map.py
git commit -m "feat(canvas): topic membership + deterministic layout"
```

---

### Task 3: canvas_map — JSON Canvas serialize + validate + write

**Files:**
- Modify: `scripts/canvas_map.py`
- Test: `tests/test_canvas_map.py`

**Interfaces:**
- Consumes: `collect_map_pages`, `build_graph`, `topic_membership`, `layout`.
- Produces:
  - `to_canvas(vault) -> dict` — `{"nodes": [...], "edges": [...]}`. File nodes `{id,type:"file",file,x,y,width,height,color?}` + group nodes; edges `{id,fromNode,toNode}`.
  - `validate_canvas(canvas, valid_files: set[str]) -> list[str]` — the 8 rules; `[]` = valid.
  - `write_canvas(vault) -> Path` — `to_canvas` → `validate_canvas` (raise `ValueError` if invalid) → write `wiki/knowledge-map.generated.canvas`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_canvas_map.py`:
```python
import json


def test_to_canvas_valid_shape_and_self_check(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "overview.md", type="overview")
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d", links=["topics/t.md"])
    canvas = cm.to_canvas(v)
    file_nodes = [n for n in canvas["nodes"] if n["type"] == "file"]
    assert {n["file"] for n in file_nodes} == {"overview.md", "topics/t.md", "domains/d/concepts/a.md"}
    ids = [n["id"] for n in canvas["nodes"]] + [e["id"] for e in canvas["edges"]]
    assert len(ids) == len(set(ids))                        # rule 1: ids unique
    node_ids = {n["id"] for n in canvas["nodes"]}
    for e in canvas["edges"]:                               # rule 2: edges reference existing nodes
        assert e["fromNode"] in node_ids and e["toNode"] in node_ids
    valid = {n["file"] for n in file_nodes}
    assert cm.validate_canvas(canvas, valid) == []          # all 8 rules pass


def test_to_canvas_deterministic(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "topics/t.md", type="topic", domain="d", links=["domains/d/concepts/a.md"])
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    assert cm.to_canvas(v) == cm.to_canvas(v)               # byte-stable


def test_write_canvas_emits_parseable_file(tmp_path):
    v = tmp_path / "wiki"
    _page(v, "domains/d/concepts/a.md", type="concept", domain="d")
    out = cm.write_canvas(v)
    assert out.name == "knowledge-map.generated.canvas"
    data = json.loads(out.read_text(encoding="utf-8"))      # rule 7: parseable JSON
    assert "nodes" in data and "edges" in data


def test_validate_canvas_catches_dangling_file_and_edge(tmp_path):
    canvas = {"nodes": [{"id": "a" * 16, "type": "file", "file": "ghost.md",
                         "x": 0, "y": 0, "width": 10, "height": 10}],
              "edges": [{"id": "b" * 16, "fromNode": "a" * 16, "toNode": "missing"}]}
    problems = cm.validate_canvas(canvas, valid_files=set())
    assert any("ghost.md" in p for p in problems)            # file not in published set
    assert any("missing" in p for p in problems)             # edge → nonexistent node
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -k "canvas" -q`
Expected: FAIL — `module 'canvas_map' has no attribute 'to_canvas'`.

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/canvas_map.py`:
```python
import json as _json

_TYPE_RGB = {"overview": 15054183, "topic": 5214681, "comparison": 10181558,
             "synthesis": 10181558, "concept": 5744499}  # 与 .obsidian graph.json 一致


def _color(ptype: str):
    rgb = _TYPE_RGB.get(ptype)
    return f"#{rgb:06X}" if rgb is not None else None


def to_canvas(vault) -> dict:
    pages = collect_map_pages(vault)
    nodes, edges = build_graph(pages)
    membership, unassigned = topic_membership(nodes)
    pos, groups = layout(nodes, membership, unassigned)
    cnodes: list[dict] = []
    for pp in sorted(nodes):                                # group nodes first (lower z), then files
        pass
    cnodes.extend(groups)                                   # groups render under files (array order = z)
    for pp in sorted(nodes):
        x, y, w, h = pos[pp]
        node = {"id": _h16(pp), "type": "file", "file": pp, "x": x, "y": y,
                "width": w, "height": h}
        col = _color(nodes[pp]["type"])
        if col:
            node["color"] = col
        cnodes.append(node)
    cedges = [{"id": _h16(f"{a}->{b}"), "fromNode": _h16(a), "toNode": _h16(b)}
              for a, b in edges]
    return {"nodes": cnodes, "edges": cedges}


def validate_canvas(canvas: dict, valid_files: set[str]) -> list[str]:
    """kepano json-canvas 的 8 条自检 + file 指向 published 页。返回问题列表（[] = 合法）。"""
    problems: list[str] = []
    ids: list[str] = []
    node_ids: set[str] = set()
    for n in canvas.get("nodes", []):
        ids.append(n.get("id"))
        node_ids.add(n.get("id"))
        if n.get("type") not in ("file", "group", "text", "link"):
            problems.append(f"bad node type: {n.get('type')}")
        if n.get("type") == "file" and n.get("file") not in valid_files:
            problems.append(f"file node points to non-published page: {n.get('file')}")
        for k in ("x", "y", "width", "height"):
            if k not in n:
                problems.append(f"node {n.get('id')} missing {k}")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_canvas_map.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/canvas_map.py tests/test_canvas_map.py
git commit -m "feat(canvas): JSON Canvas serialize + 8-rule self-check + write"
```

---

### Task 4: `rebuild-canvas` CLI (fail-hard)

**Files:**
- Modify: `scripts/pipeline.py` (add `cmd_rebuild_canvas`, subparser, dispatch)
- Test: `tests/test_conversion_backend_cli.py`

**Interfaces:**
- Consumes: `canvas_map.write_canvas(vault)`, `_vault_dir()`.
- Produces: CLI `python scripts/pipeline.py rebuild-canvas` → writes `wiki/knowledge-map.generated.canvas`; **fail-hard** (non-zero exit on any error or no vault).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversion_backend_cli.py`:
```python
def test_rebuild_canvas_writes_canvas(tmp_path):
    import json
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="d", name="A")          # existing helper writes a concept page (proposed)
    # promote it to published so canvas picks it up:
    import importlib.util
    spec = importlib.util.spec_from_file_location("mdpage", ROOT / "scripts" / "mdpage.py")
    mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)
    cpath = next((vault / "domains" / "d" / "concepts").glob("*.md"))
    meta, body = mp.read_page(cpath); meta["status"] = "published"; mp.write_page(cpath, meta, body)
    r = _run(["rebuild-canvas"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = vault / "knowledge-map.generated.canvas"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert any(n.get("type") == "file" for n in data["nodes"])


def test_rebuild_canvas_no_vault_fail_hard(tmp_path):
    r = _run(["rebuild-canvas"], tmp_path)
    assert r.returncode != 0                            # fail-hard when no wiki/


def test_rebuild_canvas_help(tmp_path):
    assert "rebuild-canvas" in _run(["rebuild-canvas", "--help"], tmp_path).stdout or \
           _run(["rebuild-canvas", "--help"], tmp_path).returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_conversion_backend_cli.py -k rebuild_canvas -q`
Expected: FAIL — `invalid choice: 'rebuild-canvas'`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/pipeline.py`, add the command function (place after `cmd_rebuild_registry`):
```python
def cmd_rebuild_canvas(args):
    """从 published 概念图谱确定性重建 wiki/knowledge-map.generated.canvas（零 LLM，fail-hard）。"""
    import canvas_map
    vault = _vault_dir()
    if not vault.exists():
        raise SystemExit("no wiki/ vault yet")
    out = canvas_map.write_canvas(vault)
    print(f"[OK] knowledge map -> {out}")
```
Register the subparser (next to `rebuild-registry`'s `subparsers.add_parser(...)`):
```python
    subparsers.add_parser("rebuild-canvas",
                          help="从 published 概念图谱重建 knowledge-map.generated.canvas（派生阅读层）")
```
Add to the dispatch dict (next to `'rebuild-registry': cmd_rebuild_registry,`):
```python
        'rebuild-canvas': cmd_rebuild_canvas,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_conversion_backend_cli.py -k rebuild_canvas -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline.py tests/test_conversion_backend_cli.py
git commit -m "feat(canvas): rebuild-canvas CLI (fail-hard)"
```

---

### Task 5: `cmd_lint` finish hook + publish isolation

**Files:**
- Modify: `scripts/pipeline.py` (`cmd_lint`, after `wiki_gate.write_index(vault)`)
- Test: `tests/test_lint_republish_cli.py`

**Interfaces:**
- Consumes: `canvas_map.write_canvas(vault)`.
- Produces: on lint pass, after index rebuild, generate canvas inside try/except — failure prints a warning, keeps the old canvas, and **does not fail the publish**.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lint_republish_cli.py` (uses the file's existing `_run` / vault-publish helpers; adapt names to that file's conventions):
```python
def test_lint_finish_builds_canvas(tmp_path):
    # Drive a full ingest→lint that publishes at least one concept + overview, then assert canvas exists.
    # (Reuse this file's existing end-to-end publish helper; pseudo-named _publish_minimal here.)
    vault = _publish_minimal(tmp_path)                  # promotes a concept + overview via lint
    assert (vault / "knowledge-map.generated.canvas").exists()


def test_lint_canvas_failure_does_not_break_publish(tmp_path, monkeypatch):
    import canvas_map
    monkeypatch.setattr(canvas_map, "write_canvas",
                        lambda vault: (_ for _ in ()).throw(RuntimeError("boom")))
    vault = _publish_minimal(tmp_path)
    # publish still succeeded (pages are published, index exists) despite canvas failure:
    assert (vault / "index.generated.md").exists()
    # old canvas preserved if present; absent is fine — key is publish didn't roll back.
```

> If `test_lint_republish_cli.py` has no reusable end-to-end publish helper, add a small one that runs `add-source→…→lint` on a synthetic markdown source that yields one `concept` + one `overview` page; keep it in this test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_lint_republish_cli.py -k canvas -q`
Expected: FAIL — canvas file not generated by lint.

- [ ] **Step 3: Write minimal implementation**

In `scripts/pipeline.py` `cmd_lint`, immediately AFTER `wiki_gate.write_index(vault)` and BEFORE the `log = vault / "log.md"` line, insert:
```python
    # 派生阅读层（不阻断发布）：发布/registry/aliases/index 已成功，再建 canvas；失败只 warn、留旧图。
    try:
        import canvas_map
        canvas_map.write_canvas(vault)
    except Exception as e:
        print(f"[WARN] knowledge-map canvas 重建失败：{e}；已保留旧 canvas，请手动跑 rebuild-canvas")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_lint_republish_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline.py tests/test_lint_republish_cli.py
git commit -m "feat(canvas): build at lint finish, isolated from publish (warn-on-fail)"
```

---

### Task 6: callout whitelist lint

**Files:**
- Modify: `scripts/wiki_gate.py` (`lint_pages` + module constant)
- Test: `tests/test_lint_republish_cli.py` (or wherever `lint_pages` is unit-tested — search for existing `lint_pages(` tests; add beside them)

**Interfaces:**
- Consumes: existing `lint_pages(vault, pages)` violation list shape `{path, rule, detail}`.
- Produces: `CALLOUT_WHITELIST: frozenset`; a new hit `rule="callout-unknown"` for any `> [!type]` whose `type` is not in the whitelist (case-insensitive). Hard fail (goes through the existing blocking list).

- [ ] **Step 1: Write the failing test**

Add to the test file that imports `wiki_gate` directly (create `tests/test_wiki_gate_callout.py` if none exists):
```python
import importlib.util, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(n):
    s = importlib.util.spec_from_file_location(n, ROOT / "scripts" / f"{n}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


wg = _load("wiki_gate")


def _pg(rel, body, type="concept"):
    return {"rel_path": rel, "meta": {"type": type, "status": "proposed"}, "body": body}


def test_callout_whitelist_ok(tmp_path):
    pages = [_pg("c.md", "> [!warning] 易错\n内容\n\n> [!question]\n自测\n")]
    vs = [v for v in wg.lint_pages(tmp_path, pages) if v["rule"] == "callout-unknown"]
    assert vs == []


def test_callout_unknown_type_fails(tmp_path):
    pages = [_pg("c.md", "> [!banana]\n乱编类型\n")]
    vs = [v for v in wg.lint_pages(tmp_path, pages) if v["rule"] == "callout-unknown"]
    assert len(vs) == 1 and "banana" in vs[0]["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_wiki_gate_callout.py -q`
Expected: FAIL — no `callout-unknown` violations produced.

- [ ] **Step 3: Write minimal implementation**

In `scripts/wiki_gate.py`, add near the top-level constants (after `_WIKILINK = ...`):
```python
# callout 学习白名单（设宽；不强制必须用 callout，只禁未知类型，防 LLM 乱编导致 Obsidian 不渲染）
CALLOUT_WHITELIST = frozenset({"note", "tip", "info", "important", "warning", "question",
                               "example", "abstract", "summary", "quote", "success", "todo"})
_CALLOUT = re.compile(r"^>\s*\[!([A-Za-z][\w-]*)\]", re.MULTILINE)
```
In `lint_pages`, inside the `for p in pages:` loop (after the broken-link block, before the loop ends), add:
```python
        # callout 类型白名单（未知类型 → 阻断，复用现有 lint 通道）
        for ct in _CALLOUT.findall(page_rules.strip_code_blocks(body)):
            if ct.lower() not in CALLOUT_WHITELIST:
                hit(rel, "callout-unknown",
                    f"未知 callout 类型 [!{ct}]（白名单：{', '.join(sorted(CALLOUT_WHITELIST))}）")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_wiki_gate_callout.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_gate.py tests/test_wiki_gate_callout.py
git commit -m "feat(lint): callout type whitelist (unknown type hard-fails)"
```

---

### Task 7: write-pages.md (dual tree) + templates + README + full suite

**Files:**
- Modify: `.claude/skills/ingest/references/write-pages.md` and `.agents/skills/ingest/references/write-pages.md` (identical edits)
- Modify: `README.md` (Obsidian reading section)
- Test: `tests/test_skill_standard.py` (parity), full suite

**Interfaces:**
- Consumes: none (docs).
- Produces: callout/embed-width writing conventions; README note about the canvas reading layer.

- [ ] **Step 1: Add writing conventions to write-pages.md (BOTH trees, byte-identical)**

In each `write-pages.md`, add a short subsection (same text in both files):
```markdown
## Callouts & figure width (Obsidian rendering)

- **Callouts** (whitelist — unknown types hard-fail lint): pitfalls → `> [!warning]`, self-test →
  `> [!question]`, worked examples → `> [!example]`, key takeaways → `> [!tip]`. Whitelist:
  `note tip info important warning question example abstract summary quote success todo`. Not required
  to use callouts — just never invent a type outside the whitelist.
- **Figure width**: when embedding a hard-page image, size it with `![[assets/<src>/pNNNN.png|640]]`
  (formula pages narrower, full-page figures wider) so it does not overflow the reading column.
```

- [ ] **Step 2: Verify dual-tree parity holds**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_skill_standard.py::test_t2_dual_agent_parity -q`
Expected: PASS (both trees byte-identical for the new subsection).

- [ ] **Step 3: Add canvas to README reading section**

In `README.md`, in the "👓 在 Obsidian 中阅读" section, add a bullet:
```markdown
4. 打开 **`knowledge-map.generated.canvas`**（vault 根，收尾 `lint` 自动重建、或手动 `rebuild-canvas`）：一张**确定性概念地图**——节点是概念/主题/综合页（点开即跳转），按领域分组、主题分区，"未分类"子区显示还没被 topic 收编的概念。它是派生阅读层，随库更新；想要个性化布局就复制一份普通 `.canvas` 各玩各的。
```

- [ ] **Step 4: Run the full suite**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/ -q`
Expected: PASS — baseline 476 + all new tests (canvas_map, rebuild-canvas CLI, lint hook, callout). Fix any regression before committing.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/ingest/references/write-pages.md .agents/skills/ingest/references/write-pages.md README.md
git commit -m "docs(canvas): callout/embed-width conventions (both trees) + README reading layer"
```

---

## Self-Review

**Spec coverage:**
- canvas module (collect/build/membership/layout/serialize/validate/write) → Tasks 1-3. ✓
- node set = MAP_TYPES, exclude lessons/sources → Task 1 `collect_map_pages` + test. ✓
- edges = in-set wikilinks + max-degree → Task 1. ✓
- layout domain→topic→concept grid + unassigned subregion → Task 2. ✓
- stable 16-hex id, type color from .obsidian → Task 3. ✓
- 8 self-check rules + file points to published → Task 3 `validate_canvas`. ✓
- `rebuild-canvas` fail-hard → Task 4. ✓
- lint finish hook + publish isolation (warn-on-fail) → Task 5. ✓
- callout whitelist hard-fail → Task 6. ✓
- embed-width + callout conventions (dual tree) + README → Task 7. ✓
- Non-goals (no LLM route canvas, no cssclasses, canvas not a gate, no lesson/source) — honored by omission; canvas isolation in Task 5 enforces "not a gate". ✓

**Placeholder scan:** Task 5's `_publish_minimal` is named as a helper to reuse/add — its construction is described (run `add-source→…→lint` on a synthetic md source giving one concept + one overview); the implementer wires it to this test file's existing publish flow. All other steps carry complete code.

**Type consistency:** `write_canvas(vault) -> Path` used identically in Tasks 3/4/5. `validate_canvas(canvas, valid_files)` signature consistent Task 3 ↔ tests. `CANVAS_FILE`/`MAP_TYPES`/`CANVAS_MAX_DEGREE` names consistent across tasks. Node dict keys (`id/type/file/x/y/width/height/color`) and edge keys (`id/fromNode/toNode`) consistent Task 3 ↔ validate ↔ tests.

## Verification

```
$env:PYTHONUTF8=1
python -m pytest tests/ -q            # all green (476 baseline + new)
python scripts/pipeline.py rebuild-canvas   # on a vault with published pages
# → open wiki/ in Obsidian: knowledge-map.generated.canvas readable; nodes jump to pages
```
