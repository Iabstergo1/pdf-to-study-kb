---
description: 处理 Review-Queue 与 review_proposals 中的待审项（人工决策辅助）
---

# /kb-review — 复核队列处理

帮用户逐条处理待审项。**你只给分析与建议，最终采纳/拒绝由用户决定。**

## 待审项来源

1. `wiki/Review-Queue/*.md`：lint 失败清单（`<source>-lint-*.md`）、跨域提升候选
   （`promotion-*.md`）、被覆盖保护拒绝的改动提案（`*-proposal.md`）。
2. 机器侧台账：`review_proposals` 表（`python scripts/pipeline.py status` 看 source 状态；
   表内容含 kind：L1/L2/.../promotion-candidate 等）。

## 逐条处理建议

- lint 违规：给出修复方案 → 用户确认后修复 → 重新 `/ingest` 或直接改页后跑 `lint`（回流）。
- promotion-candidate：判断"语义复用 vs 同名异义"；确认提升则
  `python scripts/pipeline.py promote-concept --id <canonical_id>`，随后 `rebuild-registry`；
  同名异义则保留各自页并在两页 frontmatter `aliases` 里**不要**互相添加。
- 覆盖提案：对比提案与现页，建议合并方式；human 页永远由用户亲自改。
- 处理完一条，把对应 Review-Queue 文件中该条标记为已处理（追加 `> 已处理：<结论>`）。
