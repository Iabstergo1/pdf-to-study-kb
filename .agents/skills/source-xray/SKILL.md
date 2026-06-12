---
name: source-xray
description: 基于已发布的 source/concept/topic 等 wiki 内容生成一份拆书式阅读笔记或 synthesis 候选报告，默认只写 pipeline-workspace/reports/source-xray/，不写 vault。当用户说“给这个已发布来源做 xray / 拆书阅读笔记 / source-xray / 生成学习笔记候选”时使用。不参与预处理、不决定窗口、不决定写页范围、不创建或合并概念页。
---

# source-xray — 已发布来源的阅读笔记报告

基于已发布的 source/concept/topic 等 wiki 内容生成阅读笔记或 synthesis 候选。默认不写 vault；若用户要保存进 wiki，转 `kb-save` 走两阶段发布。

## 1. 触发 / 负样本

- **触发**：「给这个已发布来源做 xray」「拆书阅读笔记」「source-xray」「生成学习笔记候选」「从已发布内容整理一份学习路线」。
- **负样本**：新来源预处理（用 `source-preflight`）；新来源入库（用 `ingest`）；查询已有知识（用 `kb-query`）；保存报告进 wiki（用 `kb-save`）；语义体检（用 `wiki-lint-semantic`）。

## 2. 输入

- `<src>` 或已发布 source 页路径。
- 读：`wiki/sources/<src>.md`、该 source 相关 published lessons/concepts/topics/comparisons/synthesis、`wiki/index.generated.md`。
- 只基于已发布内容；若 source 还未发布，停止并建议先完成 `ingest`/`lint`。

## 3. 输出

- 默认写 `pipeline-workspace/reports/source-xray/<src>.md`。
- 报告可包含：核心问题、共识基线、作者/来源 delta、关键概念地图、学习路线、可转 `kb-save` 的 synthesis 候选。
- 不写 `wiki/`，不创建 `status: proposed` 页，不更新概念页。

## 4. 依赖

- 协议：`docs/skill-runtime/skill-standard.md` 的 source-xray 守卫、`docs/skill-runtime/schema.md` 的页面职责。
- 后续保存：只能转 `kb-save`，并以 query-session / evidence_refs / decision.md 形式进入两阶段发布。
- 不依赖预处理 staging，也不读取未发布 source.md 作为主要依据。

## 5. 持久化 artifact

- `pipeline-workspace/reports/source-xray/<src>.md`
- 可选：若从报告生成保存候选，先落 query-session，再交 `kb-save`；本 skill 不直接写 vault。

## 6. CLI 命令

```text
python scripts/pipeline.py status
```

该命令只用于确认 source 发布状态。没有专用业务 CLI；本 skill 的产物是报告文件，不是 vault 内容页。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| X1 发布校验 | src/index/status | published source 判断 | 只基于已发布内容 | 报告草稿 | source 未发布 |
| X2 收集材料 | source + related pages | 材料清单 | 页面路径真实，含 source refs | 报告草稿 | 相关页缺失 |
| X3 结构提取 | 已发布材料 | 核心问题/基线/delta/概念地图 | 不改变写页范围，不做 unit 规划 | 报告 | 证据不足 |
| X4 候选标注 | 报告内容 | synthesis/learning path 候选 | 候选有 evidence refs | 报告 | 无证据候选剔除 |
| X5 交接 | 报告 | 是否转 kb-save 的建议 | 默认不写 vault | report | 用户要求保存则转 kb-save |

## 8. 失败停止点

source 未发布；只能找到 staging 未发布内容；用户要求预处理、决定 windows、决定写页范围、创建/合并概念页；证据不足；用户要求直接写 vault。

## 9. 验收清单

- 显式遵守：不参与预处理 / 不决定窗口 / 不决定写页范围 / 不建合并概念页 / 只基于已发布内容 / 默认不写 vault。
- 报告写入 `pipeline-workspace/reports/source-xray/<src>.md`。
- 每个候选 synthesis 或学习路线都有 evidence refs。
- 没有修改 `wiki/`。
- 若用户要保存，已转交 `kb-save` 而不是本 skill 直接写库。
