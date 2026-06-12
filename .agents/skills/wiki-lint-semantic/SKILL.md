---
name: wiki-lint-semantic
description: 对知识库做需要语义判断的体检——comparison 是否真正覆盖关键差异维度（L4）、跨页结论是否矛盾、近期 kb-save 产物是否真新增价值（Q2）——只产出 Review-Queue proposal，不直接改写任何 wiki 页。当用户说“给知识库做个语义体检 / 检查有没有矛盾 / 看看对比页写全了没”时使用。确定性 lint（L1/L2/L3/L5/L6/断链/重复）由 scripts/pipeline.py lint 负责，不在本 skill。
---

# wiki-lint-semantic — 语义 lint（收尾 CLI 不做的那一半）

确定性 lint（L1/L2/L3/L5/L6/断链/重复）由 `python scripts/pipeline.py lint` 负责；本 skill 只做需要语义判断的部分。
**只产出 Review-Queue proposal，不直接改写任何 wiki 内容页**。执行层是 `scripts/pipeline.py`，本 skill 只编排。

## 1. 触发 / 负样本

- **触发**：「给知识库做个语义体检」「检查有没有矛盾」「看看对比页写全了没」「检查 kb-save 产物有没有新增价值」。
- **负样本**：断链/缺小节/裸证据 ID/孤儿页等确定性 lint（用 `python scripts/pipeline.py lint`）；只读问答（用 `kb-query`）；处理已有 Review-Queue（用 `kb-review`）；覆盖率、Q 链、公式/证据抽查等宽 QA/审计请求归后续 `kb-qa`，不抢触发词。

## 2. 输入

- `wiki/comparisons/**`、`wiki/topics/**`、`wiki/synthesis/**`、`wiki/overview.md`、相关 concept/lesson/source 页。
- 近期 `kb-save` query-session（尤其 `decision.md`、`candidate_write_set.json`、`evidence_refs.json`）。
- 既有 `wiki/Review-Queue/`，避免重复提交同一 proposal。

## 3. 输出

- `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`。
- 每条 proposal 包含：页面路径、问题类型（L4/矛盾/Q2）、证据、建议修复方向、是否需要用户决策。
- 不直接修改 comparison/topic/synthesis/concept/lesson/source 等内容页。

## 4. 依赖

- CLI：可先运行或参考 `python scripts/pipeline.py lint --source <source_id>` 的确定性结果，但不重复实现 L1/L2/L3/L5/L6/断链/重复规则。
- 协议：`docs/skill-runtime/schema.md`（页面职责与必需小节）、`save-back-policy.md`（Q2 保存价值判断）。
- 后续处理交 `kb-review`：本 skill 只投递 proposal。

## 5. 持久化 artifact

- `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`：语义 lint proposal。
- 对话摘要：本次检查范围、发现数量、建议用户用 `kb-review` 处理。

## 6. CLI 命令

```text
python scripts/pipeline.py lint --source <source_id>
python scripts/pipeline.py status
```

这些命令只用于确认确定性门禁/状态背景；语义判断结果写成 Review-Queue proposal，不直接 promote 或改内容页。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| L1 定范围 | 用户请求 + vault index | 检查页集合 | 聚焦 comparison/矛盾/Q2，不泛化成 QA | 检查清单 | vault 缺失 |
| L2 comparison L4 | comparisons 页 | 关键维度缺口 | 覆盖假设/适用条件/结果/成本等维度 | proposal 草稿 | 页面证据不足 |
| L3 矛盾检查 | 相关 concept/topic/lesson | 冲突论断对 | 同一概念/模型下论断可定位 | proposal 草稿 | 无法定位来源 |
| L4 Q2 检查 | kb-save 产物 + evidence | 新增价值判断 | 区分新增综合与复述已有页 | proposal 草稿 | evidence 缺失 |
| L5 写 proposal | proposal 草稿 | semantic-lint 文件 | 每条含路径/问题/证据/修复方向 | Review-Queue | 重复 proposal |

## 8. 失败停止点

vault 或 index 不存在；确定性 lint 已失败且阻断语义判断范围；相关页面缺证据无法判断；发现问题已经有未处理 proposal；用户要求直接改内容页（转 `kb-review` 并等待确认）。

## 9. 验收清单

- 没有直接修改 wiki 内容页。
- `semantic-lint-<YYYY-MM-DD>.md` 已写入 Review-Queue。
- 每条 proposal 有页面路径、问题描述、证据、建议修复方向。
- L4/Q2/矛盾判断不重复确定性 lint 职责。
- 已提示用户用 `kb-review` 处理 proposal。
