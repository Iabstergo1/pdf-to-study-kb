---
name: kb-query
description: 只读查询已有学习知识库回答问题，并持久化一份 query-session（不写 vault 任何文件）。当用户问“知识库里关于 X 怎么说 / 查我的 wiki / 我之前学过的 Y 是什么 / 知识库里有没有讲过 Z”时使用。只读不写库；想把结论留进 wiki 要后续显式走 kb-save。
---

# kb-query — 只读查询 + query-session 持久化

回答用户关于知识库已有内容的问题。**只读：不写 `wiki/` 任何文件**；但必须持久化一份 query-session，供后续 `kb-save` 与审计使用。
执行层是 `scripts/pipeline.py`，本 skill 只编排、提示验收、约束中间产物。

## 1. 触发 / 负样本

- **触发**：「知识库里关于 X 怎么说」「查我的 wiki」「我之前学过的 Y 是什么」「知识库里有没有讲过 Z」。
- **负样本**：新外部来源入库（用 `ingest`）；把查询结论写回 wiki（用 `kb-save`）；处理 Review-Queue（用 `kb-review`）；语义体检（用 `wiki-lint-semantic`）；普通总结/翻译/解释外部文本（普通回答，不建 query-session，除非用户明确查 wiki）。

## 2. 输入

- 用户问题；可选的领域、概念名、来源名、页路径或时间范围。
- 读：`wiki/index.generated.md`、`wiki/concepts/_registry.yaml`、相关 concept/topic/comparison/synthesis/source/lesson 页。
- 若答案可能值得保存，读 `docs/skill-runtime/save-back-policy.md` 来判断是否生成保存候选。

## 3. 输出

- 对话中的回答：引用相关 vault 页（wikilink）与来源定位（source §节/页）。
- `pipeline-workspace/query-sessions/<run_id>/` 下的 query-session 文件。
- 不写 `wiki/`、不改 `log.md`、不 promote、不创建 proposed 页。

## 4. 依赖

- 协议：`docs/skill-runtime/save-back-policy.md`（保存候选准入）、`docs/skill-runtime/schema.md`（页类型理解）。
- CLI：`scripts/pipeline.py check-session --id <run_id>` 用于 query-session 自检。
- 后续保存只能交给 `kb-save`，本 skill 不内联保存逻辑。

## 5. 持久化 artifact

在 `pipeline-workspace/query-sessions/<run_id>/` 写入：

- `question.md`：原问题。
- `answer.md`：本次回答。
- `related_pages.json`：涉及的 vault 页路径 list。
- `candidate_write_set.json`：若产生值得保存的综合/对比/路线候选，列出拟写页；否则写 `[]`。
- `evidence_refs.json`：`[{"source": "...", "sections": ["..."]}]`；没有可用来源证据则写 `[]`。

## 6. CLI 命令

```text
python scripts/pipeline.py check-session --id <run_id>
```

`check-session` 必须在写完 query-session 后运行；失败则修 session 文件，不把失败 session 当可保存基础。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| Q1 定位材料 | 用户问题 + index/registry | 相关页列表 | 页路径存在，优先 published 内容 | `related_pages.json` 草稿 | vault/index 缺失 |
| Q2 回答 | 相关页正文 | 带 wikilink/source 定位的回答 | 不引入无证据断言；区分已有结论与推断 | `answer.md` 草稿 | 证据不足则明说 |
| Q3 保存候选判断 | 回答 + save-back-policy | candidate_write_set/evidence_refs | 普通解释/一次性事实候选为 `[]` | JSON 文件 | 候选无证据则置空 |
| Q4 session 自检 | session 目录 | check-session 结果 | `check-session` 通过 | query-session 完整目录 | 自检失败 |

## 8. 失败停止点

`wiki/index.generated.md` 或 registry 缺失；相关页不存在；query-session 写入失败；`check-session` 失败；用户在查询中途要求写库（停止当前 skill，转 `kb-save`）；没有来源证据时不得生成保存候选。

## 9. 验收清单

- `wiki/` 无任何改动。
- query-session 五类文件齐全，JSON 可解析。
- `related_pages.json` 中路径真实存在。
- `candidate_write_set.json` 对普通解释/翻译/一次性事实为 `[]`。
- `python scripts/pipeline.py check-session --id <run_id>` 通过。
