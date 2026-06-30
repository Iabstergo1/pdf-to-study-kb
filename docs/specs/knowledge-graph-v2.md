# Knowledge Graph v2

日期：2026-06-29（设计）· 2026-06-30（实现后调整：Canvas 移除）
状态：**v2.0 已实现（as-built）**。吸收 `pipeline-workspace/reports/knowledge-graph-v2-design-review-2026-06-29.md` 审核结论。
适用范围：`scripts/pipeline.py` 图谱命令、`scripts/graph_*.py` 派生图谱模块、`scripts/wiki_gate.py` 主题覆盖门禁、`.agents/.claude` ingest 指导、`tests/test_graph_*.py`

> **实现后调整（2026-06-30）：** 应用户要求 **Obsidian Canvas 已移除**——离线 **HTML 力导向图**（点击节点经 `obsidian://` 跳到对应 Obsidian 笔记）为**唯一图谱入口**。`graph_canvas.py` / `canvas_map.py` / `rebuild-canvas` CLI / `test_graph_canvas.py` / `test_canvas_map.py` 均已删除；`topic_membership` 迁入 `graph_model`，发布门禁 A2（概念覆盖）继续生效。本文档已按 as-built 同步，不再描述 Canvas。

## 目标

Knowledge Graph v2.0 把 published wiki 确定性派生成一个**可追溯、可交互、可降级**的学习图谱（零 LLM 阅读层）。它解决的真实痛点：图谱打开后一眼能看出主题/社区分组，概念不会全落到单一 domain 团，且图谱重建不打断发布门禁。

v2.0 必须完成：

- 生成稳定 schema 的 `wiki/graph-data.generated.json`，作为唯一图谱数据契约。
- 用 deterministic Louvain + topic membership 给节点分社区（单一 domain 不塌成一团）。
- 生成自包含 `wiki/knowledge-graph.generated.html`（力导向交互图，点击节点跳 Obsidian），只消费 graph-data，不重扫 Markdown。
- 保持 `graph_model.topic_membership(nodes)` 支撑 `wiki_gate` 的 A2 概念覆盖门禁与图谱社区（同一套归属）。
- 增加 graph lint 与端到端验收，保证 `rebuild-graph` 后发布门禁仍可通过。

## 非目标

- 不引入 LLM 到图谱构建阶段。`graph_model -> graph_analysis -> graph_data -> graph_html` 全程零 LLM。
- 不把 HTML renderer 变成分析器。HTML 只消费 `graph-data.generated.json`，不得重扫 Markdown 或推断关系。
- 不迁移参考项目的 TypeScript monorepo、Sigma、Graphology 或工作台架构（HTML 用零依赖 vanilla JS）。
- v2.0 不要求 LLM 手标 8 类关系；关系标注只是可选增强。
- v2.0 不构建完整 `source_spine`。顶层字段保留为空数组，v2.1 再从 `chapters.json/windows.jsonl/source_refs` 构建。

## 输出文件

| 文件 | 生成者 | 用途 | 是否派生 |
|---|---|---|---|
| `wiki/graph-data.generated.json` | `graph_data.write_graph_data` | 图谱唯一数据契约 | 是 |
| `wiki/knowledge-graph.generated.html` | `graph_html.write_html` | 离线力导向交互图谱（点击节点跳 Obsidian） | 是 |
| `pipeline-workspace/reports/graph-lint-*.md` | `graph_lint.write_report` | 图谱质量报告 | 是 |

派生文件不得由 skill 手写。CLI 可以覆盖它们，但覆盖前必须先完成自检（graph lint）；失败时保留旧产物。

## 单向管线

```text
published wiki pages
  -> graph_model.collect_graph_pages
  -> graph_model.build_graph_model
  -> graph_analysis.analyze_graph
  -> graph_data.to_graph_data
  -> graph_lint.validate_graph_data
  -> graph_html.to_html
```

边界：

- `graph_model` 可以读 Markdown、frontmatter、wikilink 和轻量 graph 注释（图谱里**唯一**读 Markdown 的层）。
- `graph_analysis` 只读 model，不读 Markdown 文件。
- `graph_html` 只读 graph-data 或等价内存对象；不访问文件系统中的 Markdown 页面，不解析 wikilink，不计算社区。

## Graph Data Schema

顶层 schema 固定为：

```json
{
  "version": 2,
  "generated_at": "2026-06-29T00:00:00Z",
  "scope": "v2.0",
  "nodes": [],
  "edges": [],
  "communities": [],
  "learning_paths": [],
  "insights": [],
  "source_spine": [],
  "stats": {}
}
```

`generated_at` 默认取 UTC 当前时间；测试模式由环境变量 `STUDY_KB_GRAPH_TEST_MODE=1` 固定为 `2026-01-01T00:00:00Z`。

`source_spine` 在 v2.0 必须存在但为空数组；如果非空，graph lint 只校验基本 shape，不承诺完整学习脊柱语义。

### Node

节点字段：

```json
{
  "id": "concept.game-theory.nash-equilibrium",
  "label": "纳什均衡",
  "type": "concept",
  "path": "domains/game-theory/concepts/纳什均衡.md",
  "aliases": ["Nash equilibrium"],
  "summary": "所有参与者都没有单方面偏离动机的策略组合。",
  "source_refs": [{"source": "game-theory", "sections": ["2.4"]}],
  "community_id": "community:concept.game-theory.game",
  "weight": 0.78
}
```

节点类型：

- `overview`
- `topic`
- `concept`
- `comparison`
- `synthesis`
- `source`
- `lesson`

V2.0 默认纳入 `overview/topic/concept/comparison/synthesis/source`。`lesson` 不进入图谱节点（避免章节页压垮概念图）。

节点 ID 规则：

- concept：优先 `canonical_id`。
- source：`source:<source_id>`。
- topic/comparison/synthesis/overview：稳定 vault-relative path。
- 所有 ID 必须全局唯一。

`summary` 取页面正文第一个高信息段落，最大 180 个 CJK 字符或 320 个 ASCII 字符。不得包含 frontmatter、脚注定义、长表格或图片语法。

### Edge

v2.0 边字段：

```json
{
  "id": "edge:2d9d5e8f0df1b5c6",
  "source": "concept.game-theory.information-asymmetry",
  "target": "concept.game-theory.signaling",
  "relation": "depends_on",
  "confidence": "extracted",
  "weight": 0.86,
  "evidence": "§5.4 将类型不可见作为信号发送问题的前提。",
  "source_refs": [{"source": "game-theory", "sections": ["5.4"]}],
  "signals": {
    "co_citation": 0.25,
    "source_overlap": 1.0,
    "type_affinity": 1.0,
    "confidence_score": 1.0,
    "relation_bonus": 1.0
  },
  "inferred_by": "graph-comment",
  "direction": "forward",
  "downgraded": false
}
```

v2.0 允许的 `relation`：

- `depends_on`：A 是理解 B 的前置。
- `contrasts`：A 与 B 构成重要对比。
- `related`：强关系但类型不精确，或由结构信号派生。

允许的 `confidence`：

- `extracted`：页面显式有注释或明确来源证据。
- `inferred`：由 topic membership、comparison 结构、source 页面关键概念或结构信号派生。
- `ambiguous`：链接存在但证据不足。

`direction`：

- `forward`：`depends_on`。
- `both`：`contrasts`。
- `undirected`：`related`。

未知关系类型降级为 `related`、未知置信度降级为 `ambiguous`，并在边上置 `downgraded=true`，graph lint 据此记 warning（“降级记录存在”）。

同一节点对默认只保留一条边。关系择优顺序：`depends_on` > `contrasts` > `related`；置信度择优顺序：`extracted` > `inferred` > `ambiguous`。避免同一对节点同时出现 `depends_on` 和裸链 `related` 两条边。

## 关系写作契约

V2.0 采用“轻标注、重确定性分析”。LLM 不需要为每条边手标关系类型。结构化注释只在关系明确且对图谱有价值时使用。

推荐语法：

```markdown
[[domains/game-theory/concepts/信息不对称.md|信息不对称]] 是 [[domains/game-theory/concepts/信号发送.md|信号发送]] 的前置条件。 <!-- graph: confidence=extracted relation=depends_on evidence="§5.4 信号发送以类型不可见为前提" -->
```

规则：

- `confidence` 建议写；`relation` 可省略。
- `relation` 只能是 `depends_on`、`contrasts`、`related`。
- `evidence` 应是一句话，不超过 120 字。
- 页面 `source_refs` 是证据权威；注释 evidence 只解释这条边。
- 不给弱导航链接加 graph 注释。
- 无注释链接由 graph build 通过结构信号、topic membership 和页面类型确定权重与社区。

无注释时的降级规则：

- topic 正文或 `related_concepts[]` 指向 concept：写入社区 membership，边可为 `related/inferred`。
- comparison 页面链接两个或多个 concept：concept 对之间可补 `contrasts/inferred`。
- overview 指向 topic：可补 `related/inferred`。
- source 页面“关键概念”链接 concept：可补 `related/inferred` 并记录 source overlap。
- 普通 concept 正文 wikilink：`related/ambiguous`，但权重仍由共引、同源、类型亲和决定。

## Source Refs 与 Source Spine

`source_refs` 是 v2.0 的核心证据信号，必须进入节点和边权重。相比普通 llm-wiki，这能让图谱回答“这条关系来自哪本书/哪节”。

`source_spine` 是 v2.1 能力，不在 v2.0 承诺完整构建。原因：它需要稳定整合 `chapters.json`、`windows.jsonl`、lesson 页面和 source hash；如果半实现，会产生看似存在但不可追溯的空脊柱。

v2.0 规则：

- graph-data 顶层保留 `source_spine: []`。
- `learning_paths` 不依赖 `source_spine`。
- 节点权重不得把 `source_refs` 同时当作 evidence 和 source_spine 重复计分。
- graph lint 对缺 `source_refs` 的非 source/overview 节点发 warning。

v2.1 目标：

- 从 `pipeline-workspace/staging/<source>/chapters.json` 读取章节。
- 从 `windows.jsonl` 读取 window 与 chapter/page 的映射。
- 从 lesson/page frontmatter 和 `source_refs` 回填 chapter -> concept/topic。
- 生成按 source 分组的 `path:<source>:default` 学习路径。

## 权重规则

V2.0 边权重以确定性结构信号为主，关系标注只是加成。

```text
edge.weight =
  0.30 * co_citation
  + 0.25 * source_overlap
  + 0.20 * type_affinity
  + 0.15 * confidence_score
  + 0.10 * relation_bonus
```

信号：

- `co_citation`：两个节点被同一页面共同引用的比例。
- `source_overlap`：两个节点 `source_refs.source` 的交集比例。
- `type_affinity`：topic/concept、concept/concept、comparison/concept 等类型亲和。
- `confidence_score`：`extracted=1.0`、`inferred=0.7`、`ambiguous=0.35`。
- `relation_bonus`：`depends_on=1.0`、`contrasts=0.8`、`related=0.45`。

节点权重：

```text
node.weight =
  0.40 * normalized_degree
  + 0.30 * evidence_score
  + 0.20 * type_priority
  + 0.10 * bridge_score
```

类型优先级：

- overview: 1.00
- topic: 0.90
- concept: 0.85
- synthesis: 0.80
- comparison: 0.75
- source: 0.65
- lesson: 0.45

所有权重四舍五入到 3 位小数。

## 社区规则

V2.0 社区不能退化为 domain 分组。单一 domain 的书也必须能按 topic/共引形成多个社区。

社区构建顺序（实现见 `graph_analysis._assign_communities`）：

1. 收集 topic membership：topic 正文 full-path wikilink 与 `related_concepts[]` 指定的 concept（topic 与其成员为社区骨架）。
2. 基于无向边权运行 deterministic Louvain，处理无主题归属的余量节点。
3. 用 topic 命名社区：优先选社区内权重最高的 topic；没有 topic 时选权重最高节点。
4. 孤立（degree 0、非成员）节点归 `_unassigned`，作为结构债暴露（warn-only）。
5. domain 只作最后 fallback，不得让单域书全部变成一个社区。

Louvain 约束：

- 零依赖，纯 Python。
- 节点遍历按 `id` 排序。
- 增益阈值 `1e-9`。
- 最大 pass 50。
- 输出稳定，不依赖输入顺序。

社区字段：

```json
{
  "id": "community:博弈论基础",
  "label": "博弈论基础",
  "type": "louvain-topic",
  "node_ids": ["topics/博弈论基础.md", "concept.game-theory.game"],
  "source_refs": [{"source": "game-theory", "sections": ["2.1", "3.1"]}],
  "weight": 0.84,
  "representative_node_ids": ["topics/博弈论基础.md", "concept.game-theory.game"]
}
```

`representative_node_ids` 是按 `node.weight` 取的社区前 8 个代表节点，供 HTML 降级模式选点。

## Learning Paths

V2.0 生成轻量学习路径，不声称完整章节脊柱。

规则：

1. 优先从 topic（按权重）顺序读取 topic。
2. 每个 topic 后接该社区的代表 concept。
3. `depends_on` 边决定局部前置顺序。
4. `contrasts` 边作为对比桥，不改变主干顺序。
5. 无法形成至少 3 个节点时，输出 `degraded=true`，但不得失败。

字段：

```json
{
  "id": "path:default",
  "label": "默认学习路径",
  "source": null,
  "node_ids": ["topics/博弈论基础.md", "concept.game-theory.game"],
  "edge_ids": ["edge:2d9d5e8f0df1b5c6"],
  "rationale": "按 topic 与社区代表概念顺序生成，depends_on 决定局部前置。",
  "degraded": false
}
```

## Insights

V2.0 insight 类型：

- `isolated_node`：入度 + 出度为 0 的非 source 节点。
- `missing_source_refs`：非 overview/source 节点没有 `source_refs`。
- `weak_high_degree_node`：高连接但多数边为 `ambiguous/related`。
- `bridge_node`：连接两个以上社区的节点。
- `unresolved_relation`：未知关系/置信度降级（边 `downgraded=true`）产生的记录。

insight 不阻断 publish；graph lint 决定哪些问题 fail-hard。

## HTML 力导向渲染（`graph_html.py`）

HTML 是**唯一图谱入口**：自包含、零依赖、无构建链、无 CDN、不 fetch、不读 Markdown。内嵌 graph-data JSON（对 `</` 安全转义，避免提前闭合 `</script>`），用 vanilla JS 跑**力导向布局**渲染 SVG。

必须具备：

- 力导向布局：**确定性初始位置 + 冷却收敛（alpha 衰减）+ 速度上限**——保证每次打开布局一致、不发散（否则须手动重置）。社区（主题）着色分簇。
- 交互：拖拽节点、滚轮缩放、拖空白处平移、悬停高亮邻居并暗化其余。
- **点击节点 → 详情面板**（label/type/社区/相邻边数/summary/source_refs/path）+ 「在 Obsidian 中打开」按钮；**双击节点直接跳转**。跳转用 `obsidian://open?path=<vault 绝对路径>/<page.path>`（vault 绝对路径在生成时内嵌；需 `wiki/` 已在 Obsidian 打开过）。
- 搜索（label/alias/path）、社区过滤、学习路径高亮、重置视图、边类型图例。

降级：

- 默认渲染最多 500 节点、1200 边。
- 超限仍生成 HTML，但进入降级模式：默认只显示社区代表节点 + 学习路径节点，搜索仍可查全量。

不得具备：

- 从 Markdown 文件重新解析链接。
- 重新计算社区、权重或 insight。
- 访问外部 CDN / 发送 vault 内容到网络（`obsidian://` 与 SVG 命名空间不算网络资源）。

实现坑（维护者注意）：HTML 模板用 `str.replace(token, ...)` 注入 payload/vault 根，**占位符不得与 JS 标识符同名**（曾因占位符 `__VAULT_ROOT__` 与属性名同名导致整脚本崩、白屏）——用 `__VAULT_ROOT_JSON__` 等独立 token。

## 兼容与门禁

`wiki_gate.concepts_uncovered_by_topic`（A2 概念覆盖门禁）与图谱社区共用**同一套 topic 归属逻辑**。该逻辑（`topic_membership(nodes) -> (membership, unassigned)`）**实现在 `graph_model`**（canvas 移除后从 `canvas_map` 迁入），`wiki_gate` 调 `graph_model.topic_membership`，`graph_analysis` 经 `graph_model.topic_membership_ids` 复用——不另起第二套归属。

发布门禁 A2 与图谱解耦：图谱构建失败（publish-isolated）只 warning，绝不打断发布；但 A2“概念没被 topic 收编就阻断发布”始终独立生效（fail-closed），保证 published 库里的概念都挂在 topic 下、图谱不会出现一个大 `_unassigned` 团。

## Graph Lint

`python scripts/pipeline.py graph-lint` 应检查：

- graph-data 顶层字段完整。
- node id 唯一、edge id 唯一。
- edge source/target 存在。
- node path 存在且对应 published 页。
- 非 source/overview 节点缺 `source_refs`（warn）。
- 高置信 `extracted` 边无 evidence 且无 source_refs（fail-hard）。
- 孤立非 source 节点（warn）。
- 重复 alias 指向多个 canonical node（warn）。
- `depends_on` 简单循环（warn）。
- 单节点 degree 超过 `thresholds.GRAPH_DENSE_DEGREE`（warn）。
- `downgraded` 边的降级记录（warn）。
- `_unassigned` 节点存在（warn）。
- （`validate_html`）HTML 内嵌 graph-data JSON 可解析。

Fail-hard（errors，CLI 非零退出 / 不写新产物）：

- schema 缺字段。
- edge 指向不存在节点。
- node path 指向不存在 published 页。
- 高置信 `extracted` 边无 evidence 且无 source_refs。
- HTML 内嵌 JSON 不可解析。

Warn-only（warnings，不阻断）：孤立 / 过密 / 关系降级 / 学习路径 degraded / `_unassigned` / 缺 `source_refs` / 别名多指 / `depends_on` 环。

## CLI 命令

```powershell
python scripts/pipeline.py rebuild-graph   # 重建 graph-data + HTML（手动 fail-hard：errors → 退出 2）
python scripts/pipeline.py graph-lint      # 校验 graph-data(+HTML)：fail-hard 非零退出、warn-only 不阻断
```

publish 收尾（`cmd_lint` 钩子，**publish-isolated**）：

- publish 成功后调用 `_rebuild_graph_artifacts`（建模 → 分析 → graph-data → lint → 写 graph-data + HTML）。
- 图谱失败只 warning，保留旧 `graph-data/html`，**不改 lint 退出码**。
- 手动运行 `rebuild-graph` 时 fail-hard，便于调试。

## 测试清单

图谱测试文件：

- `tests/test_graph_model.py`（含发布门禁 seam：`graph_model.topic_membership` + `wiki_gate.concepts_uncovered_by_topic`）
- `tests/test_graph_analysis.py`
- `tests/test_graph_data.py`（含 schema 常量）
- `tests/test_graph_html.py`
- `tests/test_graph_lint.py`
- `tests/test_graph_v2_e2e.py`（锚点：合成 published vault → rebuild-graph → graph-data + HTML + graph-lint）

相关更新：`tests/test_wiki_gate.py`、`tests/test_conversion_backend_cli.py`、`tests/test_lint_republish_cli.py`。

必须覆盖：

- 轻量 graph 注释解析；未知关系/置信度降级（`downgraded`）。
- 无注释 wikilink 由结构信号赋权。
- topic membership 仍可供 `wiki_gate.py` 使用（A2 门禁不被打断）。
- Louvain 输出稳定、与输入顺序无关。
- 单 domain 书不会全部落进同一个 domain community（≥2 社区、概念分布 ≥2 社区）。
- HTML 只消费 graph-data、不读 Markdown；内嵌 JSON 安全转义；含 obsidian 跳转；>500 节点降级。
- graph lint fail-hard 与 warn-only。
- 端到端 `rebuild-graph` 后 graph-lint 通过；真实状态机发布路径（`test_lint_republish_cli.py`）lint 仍 passed、钩子写出 graph-data + HTML、图谱失败不改 lint 退出码。

测试命令：

```powershell
$env:PYTHONUTF8=1; $env:STUDY_KB_GRAPH_TEST_MODE=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests/test_graph_model.py tests/test_graph_analysis.py tests/test_graph_data.py tests/test_graph_html.py tests/test_graph_lint.py tests/test_graph_v2_e2e.py -q --basetemp=$bt
python -m pytest tests -q -m "not slow and not realbook" --basetemp=$bt
```

## 指导文档同步

需要同步更新：`.agents/.claude` 两树 `ingest/references/write-pages.md` 与 `synthesis.md`、`AGENTS.md`、`CLAUDE.md`。

写作指导要点：

- graph 注释是可选增强，不是每条边的必填项。
- `confidence` 优先于 `relation`；模型不确定时只写普通 wikilink。
- v2.0 关系类型只有 `depends_on`、`contrasts`、`related`。
- evidence 句长和证据要求；`source_refs` 是图谱证据权威，不得为空泛造边。
- 图谱入口是 `knowledge-graph.generated.html`（力导向图，点击节点跳 Obsidian）。

## 验收标准

在当前 `game-theory` vault 上运行：

```powershell
$env:PYTHONUTF8=1
python scripts/pipeline.py rebuild-graph
python scripts/pipeline.py graph-lint
python scripts/pipeline.py lint --source game-theory
```

验收结果：

- `wiki/graph-data.generated.json` 存在，schema version 为 2、scope 为 v2.0。
- 多个 community/topic 社区；game-theory 概念不全落进单一 domain 团（实测：50 节点 → 6 社区、0 未分类）。
- `wiki/knowledge-graph.generated.html` 存在、内嵌 JSON 可解析、含 `obsidian://` 跳转。
- 不再生成 `knowledge-map.generated.canvas`。
- graph lint fail-hard 为 0。
- 发布门禁 `lint` 不因图谱改动崩溃（A2 概念覆盖门禁照常生效）。
- 测试快速层通过。
