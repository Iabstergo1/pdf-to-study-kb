# 设计：全局确定性知识地图 canvas + 写作增强

日期 2026-06-24 · 分支 `feat/knowledge-map-canvas` · 来源：brainstorming（参考 kepano/obsidian-skills 的 json-canvas / obsidian-markdown 规范，取其意不取其形）。

## Context

项目现有阅读入口是 `overview → topic → concept` 三层 + Obsidian graph view。graph view 易过密、布局不可控、不可重跑。需要第四种阅读方式：一张**确定性生成、随库自动更新、可空间阅读的概念地图**（JSON Canvas）。同时顺带补齐两项写作增强（callout / embed-width）提升笔记质量。

核心原则与项目一脉相承：**确定性、零-LLM、可重跑的派生文件**（和 `index.generated.md` / `aliases.md` / `_registry.yaml` 一个待遇），唯一的 LLM（写库）不参与 canvas 生成。

## Goals / Non-goals

**Goals**
- 从 published 图谱**确定性生成** `wiki/knowledge-map.generated.canvas`（JSON Canvas）：节点 = 概念导航页，边 = 受控 wikilink，布局 = 领域/主题/概念三层。
- 写作增强：callout 学习白名单（进 lint，未知类型 hard fail）+ embed-width 规范。

**Non-goals（这轮不做）**
- 不做 LLM 生成的"精选阅读路线 canvas"（进阶②，后续单独提）。
- 不放 `lesson` / `source` 进 canvas。
- 不做 `cssclasses`（无配套 CSS snippet，避免死字段）。
- canvas **不是发布门禁**，不阻断 publish。
- 派生覆盖，用户手调布局不持久（想固定布局自行复制一份普通 `.canvas`，CLI 永不碰非 `.generated` 文件）。

## 组件（全部确定性、零 LLM）

| 组件 | 职责 | 仿照 |
|---|---|---|
| **`scripts/canvas_map.py`**（新，纯函数为主） | published 页 → 节点/边模型 → 确定性布局 → 序列化 JSON Canvas | `concept_store` / `source_artifacts` 的纯函数风格 |
| **`pipeline.py` `cmd_rebuild_canvas` + `rebuild-canvas` 子命令**（新） | 扫 `wiki/` → 调 `canvas_map` → 写 `knowledge-map.generated.canvas`；**fail-hard**（用户主动跑，失败非零退出） | `cmd_rebuild_registry` |
| **`pipeline.py` `cmd_lint` 收尾钩子**（改） | publish/registry/aliases/index 成功后**再**生成 canvas；**隔离失败**（见下） | 现有收尾派生重建 |
| **`scripts/wiki_gate.py` callout check**（改） | 扫 published 页 `> [!type]`，未知类型 → 走现有 `lint_pages()` 阻断通道 hard fail | 现有 lint 检查 |
| **`write-pages.md`（双树）+ 模板 + `thresholds.py`**（改） | callout/embed-width 写作规范；`CANVAS_MAX_DEGREE` 阈值 | 现有写作协议 |

## 数据流

```
wiki/**/*.md (published, frontmatter type ∈ {overview,topic,concept,comparison,synthesis})
  → 读 frontmatter(type / domain / canonical_id / page_path) + 提取页内 wikilink
  → 节点集（每页一个 file node）
  → 边集（节点集内 wikilink → 去重 → per-node max-degree 裁剪）
  → 确定性布局（domain group → topic 子组 → concept 网格；含"未分类"子区）
  → JSON Canvas（稳定 16-hex id = canonical_id/page_path 哈希）
  → wiki/knowledge-map.generated.canvas（派生，覆盖重建）
```

## 节点 / 边 / 布局

**节点集**：`type ∈ {overview, topic, concept, comparison, synthesis}`（排除 `lesson` / `source`）。
- **file node**：`{id, type:"file", file:<vault 相对路径>, x, y, width, height, color}`；`color` 按页 `type` 映射，**复用 `.obsidian` graph 的 1-6 配色**（concept/topic/comparison/synthesis/overview 各一色）。
- **group node**：每 `domain` 一个大组 + 组内每 `topic` 一个子组 + 每 domain 一个 **"未分类"子组**（容纳无 topic 收录的 concept）。

**边**：节点集内页面已有 wikilink → `{id, fromNode, toNode}`，双向去重；**默认不设 `color`/`side`**（让 Obsidian 自动布线、保持视觉稀疏）。
- **密度控制**：某 node 的 degree > `thresholds.CANVAS_MAX_DEGREE`（默认 12，env 可调）→ 按确定性优先级（如目标页 type 权重 + `canonical_id` 字典序）裁到前 N 条，防 hub 压垮图。

**布局（确定性、幂等）**：
- `domain` 按名排序，横向铺开成大 group。
- 组内：`overview` / `comparison` / `synthesis` 置顶行；每个 `topic` 一个子组，其下 **4 列网格**排该 topic 收录的 `concept`；最后一个**"未分类"子组**排无 topic 的 concept（按 `canonical_name` → `page_path` 稳定排序，**故意暴露"哪些概念还没被 topic 收编"的结构债**）。
- 位置 = `f(domain序, topic序, concept序)`，对齐 20px 网格、group padding 30px、节点间隔 ~80px。
- **稳定 id**：每节点 16-hex id = `sha256(canonical_id or page_path)[:16]` → 重建幂等，Obsidian 里节点位置不乱跳。

## 生成时自检（确定性，= kepano json-canvas 的 8 条）

`canvas_map` 序列化前断言：① id 跨 nodes+edges 唯一；② 每条 edge 的 `fromNode`/`toNode` 引用存在节点；③ 每个 file node 的 `file` 指向**存在的 published 页**；④ `type` ∈ 白名单；⑤ side/end 合法；⑥ color 合法；⑦ JSON 可解析、字符串内无字面 `\n`；⑧ 必需字段齐全。canvas 是**派生输出**，靠生成器自检 + 单测保证合法，**不进 lint 发布门禁**。

## 发布隔离（关键收紧）

- `cmd_lint` 收尾顺序：**先**完成 publish（proposed→published）、`_registry.yaml`、`aliases.md`、`index.generated.md`；**这些成功后再**生成 canvas。
- canvas 生成**失败不回滚发布**：打印明确 warning（`[WARN] canvas 重建失败：<原因>；已保留旧 canvas，请手动跑 rebuild-canvas`）、**保留旧 canvas 文件**、发布流程照常算成功。canvas 是派生阅读层，**绝不成为内容发布门禁**。
- 独立 `rebuild-canvas` 命令则 **fail-hard**：用户主动跑、失败就非零退出 + 报错（便于排障）。

## 写作增强（顺带）

- **callout 白名单**（`wiki_gate.py` 常量，设宽）：`note, tip, info, important, warning, question, example, abstract, summary, quote, success, todo`。**不强制必须使用** callout，只**禁止未知类型**。
- **`write-pages.md`（双树）约定**：难点 → `> [!warning]`、自测 → `> [!question]`、例子 → `> [!example]`、关键结论 → `> [!tip]`；难页嵌图用 `![[…png|宽度]]` 控制大小。
- **callout lint**（`wiki_gate.py`）：扫 published 页的 `> [!type]`，`type` 不在白名单 → **hard fail**（复用现有 `lint_pages()` 阻断通道，不新建 warning 语义）。
- **反面参考**：kepano 默认教 basename wikilink `[[Note]]`，**不采纳**——项目硬规则是全 vault 相对路径，更稳。

## 文件清单

- **新**：`scripts/canvas_map.py`、`tests/test_canvas_map.py`、本 spec。
- **改**：`scripts/pipeline.py`（`cmd_rebuild_canvas` + 子命令 + `cmd_lint` 收尾隔离钩子）、`scripts/wiki_gate.py`（callout 白名单 check）、`scripts/thresholds.py`（`CANVAS_MAX_DEGREE`）、`.claude` & `.agents/skills/ingest/references/write-pages.md`（callout/embed-width，双树 parity）、`templates/`（callout 约定）、`tests/`（rebuild-canvas CLI / lint 收尾隔离 / callout lint）、`README.md`（在阅读章节加一句 canvas 阅读层）。

## 测试（TDD）

- `test_canvas_map.py`（纯函数）：合成 vault（几个 domain/topic/concept + wikilink + 一个无 topic 的 concept）→ 节点/边正确、未分类 concept 进"未分类"子区、布局确定性 + id 稳定（同输入同输出）、max-degree 裁剪、8 条自检合法、file 指向存在页。
- `test_*_cli.py`：`rebuild-canvas` CLI 产出合法 canvas 到 vault 根；`cmd_lint` 收尾**先发布后建 canvas**；**canvas 生成失败时发布仍成功 + 保留旧 canvas + warning**（注入失败 mock）；`rebuild-canvas` 失败时 fail-hard。
- callout lint：白名单内 ok、未知类型 fail。
- 双树 parity（`write-pages.md`）。

## 验证

```
$env:PYTHONUTF8=1
python -m pytest tests/ -q          # 全绿（当前基线 476 + 新增）
# 真实 vault（若有 published 内容）：
python scripts/pipeline.py rebuild-canvas
# → Obsidian 打开 wiki/，knowledge-map.generated.canvas 可读、节点点开即跳转对应页
```

## 已知边界

- 用户在 `.generated.canvas` 上的手调布局会被下次重建覆盖（设计如此；要个性化复制一份）。
- 布局是确定性网格，不是"美学最优"——目标是稳定可读、暴露结构，不是排版艺术。
- 进阶②（LLM 精选阅读路线 canvas）不在本轮。
