---
description: 只读查询知识库并持久化 query-session（不写 vault）
argument-hint: "<question>"
---

# /kb-query "$1" — 只读查询 + 持久化

回答用户关于知识库已有内容的问题。**只读：不写 vault 任何文件**；但必须持久化一份
query-session 供事后 /kb-save 与审计（spec §7.1）。

## 步骤

1. 读 `wiki/index.generated.md`、`wiki/concepts/_registry.yaml`、相关概念/主题/来源页，回答问题。
   答案里引用相关页（wikilink）与来源（source §节）。
2. 生成 run_id（如 `qs-YYYYMMDD-HHMMSS`），把以下文件写到
   `pipeline-workspace/query-sessions/<run_id>/`（这是工作区不是 vault，允许写）：
   - `question.md`（原问题）、`answer.md`（你的回答）
   - `related_pages.json`（涉及的 vault 页路径 list）
   - `candidate_write_set.json`（若回答里产生了值得保存的综合/对比/路线，列出拟写页；否则 `[]`）
   - `evidence_refs.json`（`[{"source": ..., "sections": [...]}]`；没有就 `[]`）
3. 告诉用户 run_id，并提示：若想把结论留进 wiki，运行 `/kb-save <run_id>`（有准入门槛，
   见 `docs/skill-runtime/save-back-policy.md`）。

## 禁止

- 写 `wiki/` 下任何文件（包括 log.md）。
- 把普通解释/翻译/一次性事实当成保存候选。
