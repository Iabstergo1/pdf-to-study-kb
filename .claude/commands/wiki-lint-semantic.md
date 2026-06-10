---
description: 语义体检（L4/矛盾/Q2）——只产出 proposal，不直接改写任何页
---

# /wiki-lint-semantic — 语义 lint（收尾 CLI 不做的那一半）

确定性 lint（L1/L2/L3/L5/L6/断链/重复）由 `pipeline lint` 负责；本命令做需要语义判断的部分，
**只产出 proposal，不直接改写任何 wiki 页**（spec §11）。

## 检查项

- **L4**：每个 `comparisons/` 页是否真正覆盖了关键差异维度（假设/适用条件/结果/成本），
  还是只有表面罗列。
- **矛盾**：跨页结论是否互相冲突（同一概念在不同 lesson/topic 里的论断不一致）。
- **Q2**：近期 `/kb-save` 产物是否真的新增学习价值，还是复述已有页面。

## 输出

把发现写成 `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`：每条含
页面路径、问题描述、建议修复方向。用户经 `/kb-review` 处理。
