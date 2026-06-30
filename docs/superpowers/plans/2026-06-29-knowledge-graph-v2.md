# Knowledge Graph v2.0 Implementation Plan

> ⚠️ **已过期 / 已被实现后决策取代（2026-06-30）。** 本计划是**历史执行记录**——已实现完成，且其中按 **Canvas 方案**写的步骤（创建 `graph_canvas.py`、保留/修改 `canvas_map.py`、跑 `rebuild-canvas`、检查 `knowledge-map.generated.canvas` 及相关测试）**在实现后被推翻**：Canvas 整体移除、HTML 力导向图为唯一入口、`topic_membership` 迁入 `graph_model`。**实现真相以 `docs/specs/knowledge-graph-v2.md`（as-built）+ `pipeline-workspace/reports/knowledge-graph-v2-implementation-2026-06-30.md` 为准**，勿再按本计划的 checkbox 执行。

> **For agentic workers — 历史，勿执行：** 本计划已实现完成，且其中的 **Canvas 方案已被推翻**（见上方横幅）。**不要**再按下面的 checkbox 逐步实现；下文仅作历史参考。（原计划曾要求用 superpowers:subagent-driven-development / executing-plans 逐任务实现，现已不适用。）

**Goal:** Build a deterministic Knowledge Graph v2.0 pipeline that emits graph-data, a community-clustered Obsidian Canvas overview, a minimal offline HTML graph, and graph lint while preserving existing publish gates.

**Architecture:** Markdown is read only in `graph_model`; analysis computes deterministic structural signals and Louvain communities; renderers consume graph-data only. `canvas_map.py` remains the compatibility boundary for `topic_membership(nodes)` and `write_canvas(vault)`.

**Tech Stack:** Python 3.12, project `study-kb` environment, existing `scripts/mdpage.py`, JSON Canvas format, self-contained HTML with inline CSS/JavaScript, pytest.

---

## Scope Note

This plan incorporates `pipeline-workspace/reports/knowledge-graph-v2-design-review-2026-06-29.md`. V2.0 deliberately does not implement full `source_spine`, full HTML graph workstation behavior, or eight relation classes. Those move to v2.1 after the Canvas/community/publish-gate path is stable.

Project instructions say not to commit unless the user explicitly asks. The checklist therefore uses review-diff steps instead of commit steps.

## File Structure

- Create `scripts/graph_schema.py`: constants, allowed v2.0 relations/confidences, stable ID helpers.
- Create `scripts/graph_model.py`: collect published pages, parse lightweight graph comments, build topic membership, normalize nodes and raw edges.
- Create `scripts/graph_analysis.py`: compute structural edge signals, deterministic Louvain communities, node weights, lightweight learning paths, insights.
- Create `scripts/graph_data.py`: assemble schema version 2 and write `wiki/graph-data.generated.json`.
- Create `scripts/graph_canvas.py`: render capped community-clustered Canvas from graph-data.
- Create `scripts/graph_html.py`: render minimal offline HTML from graph-data only.
- Create `scripts/graph_lint.py`: fail-hard and warn-only graph quality checks plus report output.
- Modify `scripts/canvas_map.py`: preserve `topic_membership(nodes)`, `to_canvas(vault)`, `write_canvas(vault)`; route Canvas rendering through v2 graph data without breaking callers.
- Modify `scripts/wiki_gate.py`: either keep importing `canvas_map.topic_membership` or move it to `graph_model.topic_membership` and update both callers to the same function.
- Modify `scripts/pipeline.py`: add `rebuild-graph`, `graph-lint`, and keep `rebuild-canvas` using the single-argument `canvas_map.write_canvas(vault)` compatibility entry.
- Modify `.agents/skills/ingest/references/write-pages.md` and `.claude/skills/ingest/references/write-pages.md`: add lightweight graph annotation rules.
- Modify `.agents/skills/ingest/references/synthesis.md` and `.claude/skills/ingest/references/synthesis.md`: add topic/comparison relation guidance.
- Modify `AGENTS.md` and `CLAUDE.md`: point graph truth to `docs/specs/knowledge-graph-v2.md`.
- Create `tests/test_graph_model.py`, `tests/test_graph_analysis.py`, `tests/test_graph_data.py`, `tests/test_graph_canvas.py`, `tests/test_graph_html.py`, `tests/test_graph_lint.py`, `tests/test_graph_v2_e2e.py`.
- Update `tests/test_canvas_map.py`, `tests/test_wiki_gate.py`, `tests/test_conversion_backend_cli.py`, and `tests/test_lint_republish_cli.py`.

## Task 1: End-To-End Graph Anchor Test

This is the outermost failing test for the graph pipeline only: `rebuild-graph` → graph-data →
community-clustered Canvas → `graph-lint`. It deliberately does **not** drive the finishing `lint
--source` CLI. That command requires a registered source sitting in an `ingested` stage —
`state_store.start_stage` raises `InvalidTransition: unknown source` (and `_allowed_next` rejects
`... -> lint`) on a hand-written vault — so a synthetic published vault cannot satisfy it without
faking the whole state machine, and forcing it green would tempt weakening the publish gate.
Publish-gate compatibility after the `canvas_map` refactor is verified where the state machine
actually exists: the `wiki_gate` seam in Task 3 (unit) and the real publish path in Task 9
(integration).

**Files:**
- Create: `tests/test_graph_v2_e2e.py`
- Modify later: `scripts/pipeline.py`, `scripts/canvas_map.py`, graph modules

- [ ] **Step 1: Write the failing e2e test**

Create `tests/test_graph_v2_e2e.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _page(vault, rel, frontmatter, body):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd), "STUDY_KB_GRAPH_TEST_MODE": "1", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd, capture_output=True, text=True, env=env)


def _fixture(root):
    vault = root / "wiki"
    _page(vault, "overview.md", {"type": "overview", "status": "published", "title": "总览"}, "# 总览\n\n[[topics/博弈论基础.md|博弈论基础]]\n[[topics/经典模型.md|经典模型]]\n")
    _page(vault, "topics/博弈论基础.md", {"type": "topic", "status": "published", "domains": ["game"], "title": "博弈论基础", "related_concepts": ["concept.game.game", "concept.game.player"]}, "# 博弈论基础\n\n[[domains/game/concepts/博弈.md|博弈]] 与 [[domains/game/concepts/参与者.md|参与者]]。\n")
    _page(vault, "topics/经典模型.md", {"type": "topic", "status": "published", "domains": ["game"], "title": "经典模型", "related_concepts": ["concept.game.cournot", "concept.game.bertrand"]}, "# 经典模型\n\n[[domains/game/concepts/古诺模型.md|古诺模型]] 与 [[domains/game/concepts/伯特兰模型.md|伯特兰模型]]。\n")
    _page(vault, "domains/game/concepts/博弈.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.game", "canonical_name": "博弈", "source_refs": ["game:2.1"]}, "# 博弈\n\n策略互动。\n")
    _page(vault, "domains/game/concepts/参与者.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.player", "canonical_name": "参与者", "source_refs": ["game:2.1"]}, "# 参与者\n\n决策主体。[[domains/game/concepts/博弈.md|博弈]]\n")
    _page(vault, "domains/game/concepts/古诺模型.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.cournot", "canonical_name": "古诺模型", "source_refs": ["game:5.1"]}, "# 古诺模型\n\n数量竞争。[[domains/game/concepts/伯特兰模型.md|伯特兰模型]] <!-- graph: confidence=inferred relation=contrasts evidence=\"§5.1 数量竞争与价格竞争对照\" -->\n")
    _page(vault, "domains/game/concepts/伯特兰模型.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.bertrand", "canonical_name": "伯特兰模型", "source_refs": ["game:5.1"]}, "# 伯特兰模型\n\n价格竞争。\n")
    return vault


def test_rebuild_graph_clusters_canvas_single_domain(tmp_path):
    vault = _fixture(tmp_path)
    rebuilt = _run(["rebuild-graph"], tmp_path)
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    data = json.loads((vault / "graph-data.generated.json").read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["scope"] == "v2.0"
    # 单一 domain 的书必须分出多个 topic/共引社区，不能全塌进一个 domain 团
    communities = [c for c in data["communities"] if c["id"] != "_unassigned"]
    assert len(communities) >= 2
    assert len({n["community_id"] for n in data["nodes"] if n["type"] == "concept"}) >= 2
    # 画布必须按社区分簇成 group band（禁止退化成纯权重网格）
    canvas = json.loads((vault / "knowledge-map.generated.canvas").read_text(encoding="utf-8"))
    group_labels = [n.get("label", "") for n in canvas["nodes"] if n["type"] == "group"]
    assert any("博弈论基础" in label for label in group_labels)
    assert any("经典模型" in label for label in group_labels)
    # graph-lint 只校验 graph-data.generated.json，不依赖业务状态机，可在合成 vault 上跑
    linted = _run(["graph-lint"], tmp_path)
    assert linted.returncode == 0, linted.stdout + linted.stderr
```

- [ ] **Step 2: Run the e2e test and verify it fails**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_v2_e2e.py -q --basetemp=$bt
```

Expected: FAIL because `rebuild-graph` does not exist.

## Task 2: Schema And Constants

**Files:**
- Create: `scripts/graph_schema.py`
- Test: `tests/test_graph_data.py`

- [ ] **Step 1: Write schema tests**

Create `tests/test_graph_data.py` with tests for `GRAPH_VERSION == 2`, `SCOPE == "v2.0"`, relations `{"depends_on", "contrasts", "related"}`, confidences `{"extracted", "inferred", "ambiguous"}`, and stable IDs.

- [ ] **Step 2: Implement `graph_schema.py`**

Create constants:

```python
GRAPH_VERSION = 2
GRAPH_SCOPE = "v2.0"
RELATIONS = {"depends_on", "contrasts", "related"}
CONFIDENCES = {"extracted", "inferred", "ambiguous"}
DIRECTIONS = {"forward", "both", "undirected"}
RELATION_DIRECTION = {"depends_on": "forward", "contrasts": "both", "related": "undirected"}
RELATION_BONUS = {"depends_on": 1.0, "contrasts": 0.8, "related": 0.45}
CONFIDENCE_SCORE = {"extracted": 1.0, "inferred": 0.7, "ambiguous": 0.35}
TYPE_PRIORITY = {"overview": 1.0, "topic": 0.9, "concept": 0.85, "synthesis": 0.8, "comparison": 0.75, "source": 0.65, "lesson": 0.45}
```

Add `stable_id(prefix, payload)`.

- [ ] **Step 3: Run schema tests**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_data.py -q --basetemp=$bt
```

Expected: PASS.

## Task 3: Graph Model And Topic Membership

**Files:**
- Create: `scripts/graph_model.py`
- Modify: `scripts/canvas_map.py`
- Modify: `scripts/wiki_gate.py` only if moving membership out of `canvas_map`
- Test: `tests/test_graph_model.py`
- Test: `tests/test_wiki_gate.py`

- [ ] **Step 1: Write graph model tests**

Tests must cover:

- concept ID uses `canonical_id`.
- graph comment may contain `confidence` only.
- graph comment with unknown `relation` degrades to `related`.
- topic `related_concepts[]` and body wikilinks produce membership.
- same node pair with naked wikilink and annotated relation collapses into one edge with best relation/confidence.
- **publish-gate seam:** after the refactor, `canvas_map.topic_membership(nodes)` is still importable and returns `(membership, unassigned)`, and `wiki_gate.concepts_uncovered_by_topic(vault)` still runs. The A2 gate must not break when Canvas rendering moves into `graph_canvas`.

- [ ] **Step 2: Implement `graph_model.collect_graph_pages(vault)`**

Collect `overview/topic/concept/comparison/synthesis/source` published pages. Store `path`, `id`, `label`, `type`, `aliases`, `summary`, `source_refs`, `domain`, `domains`, `related_concepts`, and private body text for model construction.

- [ ] **Step 3: Implement shared `topic_membership(nodes)`**

Use the current `canvas_map.topic_membership` semantics:

- topic body full-path wikilinks to concept count as membership.
- topic `related_concepts[]` resolves via concept `canonical_id`.
- a concept can appear in multiple edges but only one primary layout membership.
- return `(membership, unassigned)`.

Either keep this function in `canvas_map.py` and import it in `graph_model`, or move it to `graph_model.py` and update both `canvas_map.py` and `wiki_gate.py` to call the same function.

- [ ] **Step 4: Implement edge extraction**

Parse wikilinks and optional comments:

```markdown
<!-- graph: confidence=extracted relation=depends_on evidence="short reason" -->
```

Normalize:

- missing relation -> `related`
- unknown relation -> `related`
- missing confidence -> `ambiguous`
- unknown confidence -> `ambiguous`
- same unordered pair -> keep one best edge by relation/confidence priority

- [ ] **Step 5: Run model and gate tests**

`tests/test_wiki_gate.py` must include the publish-gate seam test above, and the existing A2 tests
(`test_lint_blocks_concepts_uncovered_by_topic`) must stay green through the `canvas_map` refactor.

Run:

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_model.py tests/test_wiki_gate.py -q --basetemp=$bt
```

Expected: PASS.

## Task 4: Deterministic Analysis And Louvain Communities

**Files:**
- Create: `scripts/graph_analysis.py`
- Test: `tests/test_graph_analysis.py`

- [ ] **Step 1: Write analysis tests**

Tests must cover:

- edge weight uses co-citation, source overlap, type affinity, confidence, and relation bonus.
- deterministic Louvain returns the same communities independent of input order.
- a single-domain fixture with two topics produces at least two communities.
- concept nodes inherit community labels from topic membership or Louvain, not from domain fallback.
- learning path uses overview topic order when present.

- [ ] **Step 2: Port Louvain**

Implement zero-dependency Python equivalents of:

- `build_undirected_graph`
- `run_local_move`
- `aggregate_graph`
- `run_louvain`

Determinism rules:

- node IDs sorted before each pass.
- candidate communities sorted.
- gain threshold `1e-9`.
- max 50 passes.

- [ ] **Step 3: Implement structural signals**

Compute:

- `co_citation`: shared incoming references ratio.
- `source_overlap`: overlap of `source_refs.source`, or 0 if absent.
- `type_affinity`: deterministic pair score for topic/concept, concept/concept, comparison/concept, source/concept.
- `relation_bonus`: from `graph_schema.RELATION_BONUS`.

- [ ] **Step 4: Implement communities and learning path**

Use Louvain assignments, then label communities with the highest-weight topic inside each community. If no topic exists, use highest-weight node label. Learning path starts from overview topic navigation, then representative nodes in each community.

- [ ] **Step 5: Run analysis tests**

Run:

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_analysis.py -q --basetemp=$bt
```

Expected: PASS.

## Task 5: Graph Data Writer

**Files:**
- Create: `scripts/graph_data.py`
- Modify: `tests/test_graph_data.py`

- [ ] **Step 1: Add graph-data assembly tests**

Assert:

- top-level fields exactly include `version`, `generated_at`, `scope`, `nodes`, `edges`, `communities`, `learning_paths`, `insights`, `source_spine`, `stats`.
- test mode timestamp is fixed.
- `source_spine` is `[]` in v2.0.
- stats count nodes, edges, communities.

- [ ] **Step 2: Implement writer**

Create `to_graph_data(analyzed)` and `write_graph_data(vault, analyzed)`. Sort nodes by `id`, edges by `id`, communities by `id`. Write UTF-8 JSON with `ensure_ascii=False`, indent 2, trailing newline.

- [ ] **Step 3: Run graph data tests**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_data.py -q --basetemp=$bt
```

Expected: PASS.

## Task 6: Community-Clustered Canvas Renderer

**Files:**
- Create: `scripts/graph_canvas.py`
- Modify: `scripts/canvas_map.py`
- Test: `tests/test_graph_canvas.py`
- Test: `tests/test_canvas_map.py`

- [ ] **Step 1: Write Canvas clustering tests**

Tests must assert:

- Canvas has group nodes named after communities.
- Nodes in the same community are inside that group bounding box.
- Different community groups do not overlap.
- File nodes <= 60 and edges <= 90.
- Each node has at most 4 Canvas edges.
- A text node mentions `knowledge-graph.generated.html`.

- [ ] **Step 2: Implement `graph_canvas.to_canvas(data)`**

Layout rules:

- global row for overview/source.
- one band per community.
- community anchor/topic in first column.
- representative nodes in grid columns.
- `_unassigned` gets its own band.
- preserve JSON Canvas validity and stable IDs.

- [ ] **Step 3: Implement compatibility in `canvas_map.py`**

Preserve these public functions:

```python
def topic_membership(nodes) -> tuple[dict, dict]: ...
def to_canvas(vault) -> dict: ...
def write_canvas(vault) -> Path: ...
```

`write_canvas(vault)` must internally build model, analyze it, assemble graph-data in memory, call `graph_canvas.write_canvas(vault, data)`, and return the Canvas path.

- [ ] **Step 4: Run Canvas and compatibility tests**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_canvas.py tests/test_canvas_map.py -q --basetemp=$bt
```

Expected: PASS.

## Task 7: Minimal HTML Renderer

**Files:**
- Create: `scripts/graph_html.py`
- Test: `tests/test_graph_html.py`

- [ ] **Step 1: Write HTML renderer tests**

Tests must assert:

- embedded JSON safely escapes `</script>`.
- parsed embedded graph-data equals the input.
- HTML contains no `fetch(`, `XMLHttpRequest`, CDN URL, or Markdown file reads.
- search input exists.
- community select exists.
- node detail panel exists.
- learning path control exists.
- graph-size degraded mode appears when input exceeds 500 nodes or 1200 edges.

- [ ] **Step 2: Implement `to_html(data)` and `write_html(vault, data)`**

Use self-contained HTML with inline CSS/JS. JavaScript reads only `document.getElementById("graph-data").textContent`, renders SVG, and supports search, community filtering, node detail, relation legend, learning path highlight, and reset zoom.

- [ ] **Step 3: Run HTML tests**

Run:

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_html.py -q --basetemp=$bt
```

Expected: PASS.

## Task 8: Graph Lint

**Files:**
- Create: `scripts/graph_lint.py`
- Test: `tests/test_graph_lint.py`

- [ ] **Step 1: Write graph lint tests**

Tests must cover:

- missing top-level field is error.
- dangling edge endpoint is error.
- node path missing or unpublished is error.
- `extracted` edge without evidence and without source_refs is error.
- isolated non-source node is warning.
- unknown relation downgrade appears as warning.
- Canvas over cap is error when validating generated Canvas.
- HTML embedded graph-data parse failure is error.

- [ ] **Step 2: Implement `validate_graph_data`**

Return:

```python
{"errors": ["..."], "warnings": ["..."]}
```

Add `validate_canvas(canvas)` and `validate_html(html)` helpers for CLI use.

- [ ] **Step 3: Run lint tests**

Run:

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_lint.py -q --basetemp=$bt
```

Expected: PASS.

## Task 9: Pipeline CLI Integration

**Files:**
- Modify: `scripts/pipeline.py`
- Modify: `tests/test_conversion_backend_cli.py`
- Modify: `tests/test_lint_republish_cli.py`

- [ ] **Step 1: Add CLI tests**

Add tests for:

```powershell
python scripts/pipeline.py rebuild-graph
python scripts/pipeline.py graph-lint
python scripts/pipeline.py rebuild-canvas
```

Assertions:

- `graph-data.generated.json` exists.
- `knowledge-map.generated.canvas` exists.
- `knowledge-graph.generated.html` exists.
- `graph-lint` exits 0 for the fixture.
- `rebuild-canvas` still exits 0 and uses the single-argument compatibility path.
- **publish-path compat (in `tests/test_lint_republish_cli.py`, reusing its existing source fixture that drives a source to `ingested`):** the real publish flow still reaches `lint passed`, and the publish-isolated hook writes `graph-data.generated.json` + a clustered `knowledge-map.generated.canvas` through the single-arg `canvas_map.write_canvas(vault)`. This is the end-to-end proof that the gate survives the refactor; the synthetic Task 1 graph test does not exercise the state machine.

- [ ] **Step 2: Implement `cmd_rebuild_graph`**

Flow:

1. Locate vault with `_vault_dir()`.
2. Call `graph_model.build_graph_model(vault)`.
3. Call `graph_analysis.analyze_graph(model)`.
4. Call `graph_data.to_graph_data(analyzed)`.
5. Call `graph_lint.validate_graph_data(data, vault=vault)`.
6. If errors exist, exit nonzero before writing new outputs.
7. Write graph-data, Canvas, and HTML.
8. Print output paths and counts.

- [ ] **Step 3: Implement `cmd_graph_lint`**

Read `wiki/graph-data.generated.json`, validate it, print errors/warnings, write a report under `pipeline-workspace/reports/`, and exit nonzero only for fail-hard errors.

- [ ] **Step 4: Keep publish isolated**

After existing publish success, call rebuild graph in a `try/except` block. On exception, print warning and retain old outputs. `tests/test_lint_republish_cli.py` must assert that a graph-build failure here does **not** change the lint exit code (publish already succeeded) and that a healthy build leaves `graph-data.generated.json` + a clustered Canvas in place.

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_conversion_backend_cli.py tests/test_lint_republish_cli.py -q --basetemp=$bt
```

Expected: PASS.

## Task 10: Skill And Project Documentation

**Files:**
- Modify: `.agents/skills/ingest/references/write-pages.md`
- Modify: `.claude/skills/ingest/references/write-pages.md`
- Modify: `.agents/skills/ingest/references/synthesis.md`
- Modify: `.claude/skills/ingest/references/synthesis.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add lightweight relation writing rules**

Add:

```markdown
- Graph comments are optional edge-strength hints, not mandatory markup.
- Prefer `<!-- graph: confidence=extracted evidence="<short source-backed reason>" -->`.
- Add `relation=depends_on` or `relation=contrasts` only when the relation is explicit and source-backed.
- Allowed relation in v2.0: `depends_on`, `contrasts`, `related`.
- Allowed confidence: `extracted`, `inferred`, `ambiguous`.
- Do not annotate weak navigational links.
```

- [ ] **Step 2: Add synthesis guidance**

Add:

```markdown
Topic pages should link member concepts normally; graph build derives membership.
Comparison pages may mark compared concepts with `relation=contrasts` when the page explicitly explains the contrast.
Synthesis pages should avoid broad graph comments unless the relation is source-backed.
```

- [ ] **Step 3: Point AGENTS and CLAUDE to the spec**

Add:

```markdown
Knowledge Graph v2.0 design truth lives in `docs/specs/knowledge-graph-v2.md`; graph build remains zero-LLM, Canvas is only the capped community overview, and `canvas_map` compatibility is part of the publish contract.
```

- [ ] **Step 4: Re-read docs**

Run:

```powershell
Select-String -LiteralPath '.agents\skills\ingest\references\write-pages.md','.claude\skills\ingest\references\write-pages.md','AGENTS.md','CLAUDE.md' -Pattern 'graph: confidence|knowledge-graph-v2|canvas_map'
```

Expected: each file has the new guidance or spec pointer.

## Task 11: Verification On Current Vault

**Files:**
- Runtime outputs under `wiki/`
- Reports under `pipeline-workspace/reports/`

- [ ] **Step 1: Run focused graph tests**

Run:

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_model.py tests/test_graph_analysis.py tests/test_graph_data.py tests/test_graph_canvas.py tests/test_graph_html.py tests/test_graph_lint.py tests/test_graph_v2_e2e.py -q --basetemp=$bt
```

Expected: PASS.

- [ ] **Step 2: Run fast suite**

Run:

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests -q -m "not slow and not realbook" --basetemp=$bt
```

Expected: PASS.

- [ ] **Step 3: Build graph on current game-theory vault**

Run:

```powershell
$env:PYTHONUTF8=1
python scripts/pipeline.py rebuild-graph
python scripts/pipeline.py graph-lint
python scripts/pipeline.py lint --source game-theory
```

Expected:

- `wiki/graph-data.generated.json` exists with `"version": 2` and `"scope": "v2.0"`.
- `wiki/knowledge-map.generated.canvas` exists.
- `wiki/knowledge-graph.generated.html` exists.
- Canvas has multiple community/topic groups for game-theory.
- Concepts do not all share one `domain:game-theory` community.
- `graph-lint` prints no fail-hard errors.
- `lint --source game-theory` does not fail because of `canvas_map` compatibility.

- [ ] **Step 4: Review diff**

Run:

```powershell
git diff --stat
git diff -- docs/specs/knowledge-graph-v2.md docs/superpowers/plans/2026-06-29-knowledge-graph-v2.md
```

Expected: diff only contains intended v2.0 design, plan, implementation, docs, tests, and generated graph outputs if the project decides to keep generated outputs.

## Self-Review

- Spec coverage: the plan covers the Claude review blockers: Canvas clustering, `canvas_map` compatibility, `wiki_gate.py`, Louvain communities, source spine deferral, lightweight relation annotations, graph lint, and e2e verification.
- Gate-compat verification is split by where the state machine exists: the Task 1 graph e2e asserts rebuild-graph / clustering / graph-lint on a synthetic published vault (no state machine); the `canvas_map`↔`wiki_gate` seam is a unit test in Task 3; the real publish path is integration-tested in Task 9. The finishing `lint --source` CLI is never driven on a hand-written vault, because it requires a registered, `ingested`-stage source.
- Placeholder scan: this plan contains no red-flag placeholder language.
- Type consistency: schema field names match `docs/specs/knowledge-graph-v2.md`: `version`, `generated_at`, `scope`, `nodes`, `edges`, `communities`, `learning_paths`, `insights`, `source_spine`, `stats`, node `source_refs`, edge `source/target/relation/confidence/weight/evidence/signals/inferred_by/direction`.
