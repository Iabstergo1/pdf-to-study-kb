---
name: kb-qa
description: 对已发布知识库或保存前候选做 QA/审计/覆盖率检查，产出报告和 Review-Queue proposal。当用户说“做一次知识库 QA / 审计覆盖率 / 抽查证据 / 跑 Q 链 / 检查概念污染”时使用。语义体检、L4、矛盾、Q2 新增价值等触发词归 wiki-lint-semantic，不由本 skill 抢触发。
---

# kb-qa — 发布后 / 保存前 QA 报告

对已发布 vault 或保存前候选做宽口径 QA：覆盖率、证据抽查、公式截图抽查、概念污染、ljg-qa 式 Q 链。只产出报告和 Review-Queue proposal，不直接改写内容页。

## 1. 触发 / 负样本

- **触发**：「做一次知识库 QA」「审计覆盖率」「抽查证据」「跑 Q 链」「检查概念污染」「保存前 QA」。
- **负样本**：「给知识库做语义体检」「检查有没有矛盾」「comparison 是否覆盖关键差异维度」「Q2 新增价值」归 `wiki-lint-semantic`；处理已有 Review-Queue 用 `kb-review`；只读问答用 `kb-query`；新增来源用 `ingest`。

## 2. 输入

- 检查范围：全库、某 domain、某 source、某 query-session、某 proposed 写入候选。
- 读：`wiki/index.generated.md`、`wiki/concepts/_registry.yaml`、相关 source/concept/topic/comparison/synthesis/lesson 页。
- 可读：`pipeline-workspace/query-sessions/<run_id>/`、`wiki/Review-Queue/`、确定性 `lint` 结果。

## 3. 输出

- `pipeline-workspace/reports/kb-qa/<run_id>.md`：QA 报告。
- 对 actionable 问题写 `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md` proposal；不直接改内容页。
- 报告包含范围、Q 链、抽查样本、发现、风险等级、建议后续 skill（通常 `kb-review`）。

## 4. 依赖

- CLI：`python scripts/pipeline.py status`、必要时 `python scripts/pipeline.py lint --source <source_id>`。
- 协议：`docs/skill-runtime/schema.md`、`save-back-policy.md`、`concept-resolution.md`。
- 与 `wiki-lint-semantic` 互斥触发：语义体检类词不在本 skill 处理。

## 5. 持久化 artifact

- `pipeline-workspace/reports/kb-qa/<run_id>.md`
- `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md`（只有存在 actionable 问题时）
- 抽查清单：报告内记录 sampled pages、evidence refs、Q 链结论。

## 6. CLI 命令

```text
python scripts/pipeline.py status
python scripts/pipeline.py lint --source <source_id>
```

本 skill 不 promote、不 rollback、不修改内容页；修复交给 `kb-review` 或对应写库 skill。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| QA1 定范围 | 用户请求 + index | 检查范围与样本 | 不抢 wiki-lint-semantic 触发词 | 报告草稿 | 范围不明 |
| QA2 跑状态背景 | source/status/lint | 确定性背景 | 不重复实现确定性 lint | 报告 | lint 已阻断 |
| QA3 Q 链 | 范围内材料 | 问题→证据→判断→行动 | 每个 Q 有证据路径 | 报告 | 证据不足 |
| QA4 抽查 | 页/公式/证据样本 | 抽查结果 | 样本与结论可追踪 | 报告 | 样本不存在 |
| QA5 投递问题 | actionable findings | Review-Queue proposal | 不直接改内容页 | kb-qa proposal | 重复 proposal |

## 8. 失败停止点

vault/index 缺失；检查范围不明；用户请求实际是语义体检（转 `wiki-lint-semantic`）；确定性 lint 已失败且无可 QA 的稳定范围；证据路径不存在；用户要求直接修内容页（转 `kb-review` 等待确认）。

## 9. 验收清单

- QA 报告已写入 `pipeline-workspace/reports/kb-qa/`。
- Q 链每项都有问题、证据、判断、后续动作。
- actionable 问题已写 Review-Queue proposal。
- 没有直接修改 wiki 内容页。
- 没有处理 `wiki-lint-semantic` 的专属触发词。
