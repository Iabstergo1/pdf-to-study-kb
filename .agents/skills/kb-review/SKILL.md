---
name: kb-review
description: 逐条处理 Review-Queue 与 review_proposals 里的待审项（lint 失败清单、跨域提升候选、被覆盖保护拒绝的改动提案），给分析与修复建议，最终采纳/拒绝由用户决定。当用户说“处理复核队列 / 看看待审项 / Review-Queue 里有什么 / 帮我过一遍待办复核”时使用。
---

# kb-review — 复核队列处理

逐条处理 `wiki/Review-Queue/` 与 `review_proposals` 里的待审项。默认只给分析与建议；**最终采纳/拒绝由用户决定**。
执行层是 `scripts/pipeline.py`，本 skill 只编排、提示验收、标失败停点。

## 1. 触发 / 负样本

- **触发**：「处理复核队列」「看看待审项」「Review-Queue 里有什么」「帮我过一遍待办复核」「处理 lint 失败/跨域提升/覆盖提案」。
- **负样本**：新增来源入库（用 `ingest`）；只读查知识（用 `kb-query`）；保存查询结论（用 `kb-save`）；全库语义体检（用 `wiki-lint-semantic`）；用户未确认时不得直接采纳 proposal 或改 human 页。

## 2. 输入

- `wiki/Review-Queue/*.md`：lint 失败清单、`promotion-*.md`、`*-proposal.md`、semantic-lint 报告。
- 机器侧台账：`review_proposals` 表，通过 `python scripts/pipeline.py status` 或相关 CLI 状态查看。
- 相关 vault 页、概念 registry、source 状态。

## 3. 输出

- 每条待审项的分类、风险、建议修复方案与是否需要用户决策。
- 用户确认后，执行对应修复/提升/标记；未确认时不改目标页。
- 处理过的 Review-Queue 条目追加 `> 已处理：<结论>` 或写明仍待用户判断。

## 4. 依赖

- CLI：`status`、`lint`、`promote-concept`、`rebuild-registry`；必要时回到 `ingest` 或目标 source 的 lint 回流。
- 协议：`docs/skill-runtime/schema.md`、`concept-resolution.md`、`save-back-policy.md`。
- human 页保护仍最高优先级：human 页由用户亲自改，skill 不自动覆盖。

## 5. 持久化 artifact

- `wiki/Review-Queue/*.md` 的处理标记。
- 若用户确认修复：对应 proposed 页、registry 派生重建结果、或新的 proposal。
- 机器侧 `review_proposals` 仍作为台账来源，不由本 skill 手写数据库。

## 6. CLI 命令

```text
python scripts/pipeline.py status
python scripts/pipeline.py lint --source <source_id>
python scripts/pipeline.py promote-concept --id <canonical_id>
python scripts/pipeline.py rebuild-registry
```

只在用户确认后运行会改变 vault 结构的命令（如 `promote-concept` / 修复页 / 标记已处理）。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| R1 收集队列 | Review-Queue + status | 待审项列表 | 文件与台账互相对齐 | — | 队列缺失 |
| R2 分类 | 单条待审项 | lint/promotion/coverage/semantic/overwrite 分类 | 分类能映射到处理路径 | 分析草稿 | 类型不明 |
| R3 给建议 | 待审项 + 相关页 | 修复/拒绝/提升建议 | 说明风险、影响页、需要的命令 | 对话输出 | 证据不足 |
| R4 用户确认 | 用户决策 | 执行或拒绝 | 未确认不改目标页 | Review-Queue 标记 | human 页冲突 |
| R5 回流验证 | 修复结果 | lint/rebuild/check 结果 | 对应命令通过或失败入队 | 新 proposal/标记 | 验证失败 |

## 8. 失败停止点

用户未确认采纳；目标页 `managed_by: human`；跨域提升语义不清；promotion 同名异义；lint 修复会越过 write scope；`promote-concept` 或 `rebuild-registry` 失败；处理项缺证据。

## 9. 验收清单

- 每条待审项都有分类、建议和用户决策状态。
- 未经用户确认，不修改目标 vault 页。
- promotion-candidate 已判断“语义复用 vs 同名异义”。
- 已确认提升后运行 `promote-concept` + `rebuild-registry`。
- lint 违规修复后重跑对应 `lint`，失败项仍留在 Review-Queue。
- human 页没有被自动覆盖。
