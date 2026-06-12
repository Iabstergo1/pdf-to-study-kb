---
name: kb-save
description: 把某个已有 query-session 的综合/对比/学习路线/自测题候选保存为 status:proposed 写入 wiki（有准入门槛，走两阶段发布）。当用户在一次查询后说“把刚才那个对比/结论存进 wiki / 形成 synthesis / 保存到知识库 / 把这个留成笔记”时使用。一次性事实、普通解释、复述已有页面的内容不保存。
---

# kb-save — 显式保存（query → save 两步闭环的第二步）

作用在已有 query-session 上：先核对保存准入，再把合格候选写成 `status: proposed` 页或 Review-Queue proposal。
执行层是 `scripts/pipeline.py`，本 skill 只编排、提示验收、标失败停点。

## 1. 触发 / 负样本

- **触发**：「把刚才那个对比/结论存进 wiki」「形成 synthesis」「保存到知识库」「把这个留成笔记」「把这个 query-session 写回」。
- **负样本**：一次性事实、普通解释、翻译、无来源证据的推测、只复述已有页面的内容；没有 query-session 的直接写库请求先转 `kb-query` 或要求用户指定 run_id；新外部来源入库用 `ingest`。

## 2. 输入

- `<run_id>`：用户指定的 query-session，或最近一次 `kb-query` 的 run_id。
- 读：`pipeline-workspace/query-sessions/<run_id>/{question.md,answer.md,related_pages.json,candidate_write_set.json,evidence_refs.json}`。
- 读：`docs/skill-runtime/save-back-policy.md`、`docs/skill-runtime/schema.md`、`docs/skill-runtime/concept-resolution.md`、相关 vault 页。

## 3. 输出

- 不满足准入：明确拒绝并说明原因，不写任何页。
- 满足准入：写/更新 `topics/**`、`comparisons/**`、`synthesis/**`、相关 concept 页、`overview.md`、`log.md`；全部 `status: proposed` + `managed_by: pipeline`。
- 覆盖保护 DENY 或 human 页冲突：写 `wiki/Review-Queue/<page>-proposal.md`，不直接改目标页。
- 更新 query-session：补全实际写入与证据，并写 `decision.md`。

## 4. 依赖

- CLI：`resolve-concept`、`check-write`、`snapshot-page`、`check-session --saved`；收尾发布由 `lint` 决定。
- 协议：`save-back-policy.md`（准入门槛）、`schema.md`（页面结构）、`concept-resolution.md`（概念归一）。
- 写库纪律与 `ingest` 一致：概念命中即 merge，绝不新建重复；派生文件不手写。

## 5. 持久化 artifact

- `pipeline-workspace/query-sessions/<run_id>/decision.md`：为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念。
- `candidate_write_set.json`：更新为实际写过或拟议写入的页。
- `evidence_refs.json`：补全实际使用证据。
- vault proposed 页或 `wiki/Review-Queue/*-proposal.md`。

## 6. CLI 命令

```text
python scripts/pipeline.py resolve-concept --mention "<提及>" --domain <domain> [--alias "<别名>"] [--ref-source <source_id> --ref-sections "<sections>"]
python scripts/pipeline.py check-write --source kb-save --path <vault-rel-path>
python scripts/pipeline.py snapshot-page --source kb-save --path <vault-rel-path>
python scripts/pipeline.py check-session --id <run_id> --saved
```

保存后提示用户运行 `python scripts/pipeline.py lint --source kb-save` 或按项目当前回流命令执行收尾门禁；不要绕过两阶段发布。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| S1 读取 session | run_id | session 内容 + related pages | 必需文件齐全、JSON 可解析 | — | session 缺失 |
| S2 准入判断 | session + save-back-policy | 保存/拒绝决定 | 至少满足一项准入且 evidence_refs 非空 | `decision.md` 草稿 | 不满足准入 |
| S3 概念归一 | 候选概念 | canonical_id + concept 页 | 只走 resolve-concept，命中即 merge | 概念 frontmatter | registry corrupt |
| S4 写 proposed | 候选写入页 | proposed 页或 proposal | check-write ALLOW 后才写；覆盖前 snapshot | vault / Review-Queue | check-write DENY |
| S5 session 自检 | 写入结果 | check-session --saved 结果 | 通过 Q1 自检 | session 完整目录 | 自检失败 |
| S6 发布提示 | proposed 页 | lint 回流说明 | 用户知道 promote 由 lint 决定 | 对话摘要 | lint 失败时交 kb-review |

## 8. 失败停止点

query-session 缺失或不完整；`evidence_refs.json` 为空；不满足 `save-back-policy`；`check-write` DENY；目标页 `managed_by: human`；概念 registry 损坏；`check-session --saved` 失败；用户要求覆盖 human 页。

## 9. 验收清单

- 不保存时没有任何 `wiki/` 改动，并给出拒绝理由。
- 保存时所有写出页均为 `status: proposed` + `managed_by: pipeline`。
- 概念写入只经 `resolve-concept`，没有重复 canonical_id。
- 已写 `decision.md`，并更新 `candidate_write_set.json` / `evidence_refs.json`。
- `python scripts/pipeline.py check-session --id <run_id> --saved` 通过。
- 已提示收尾 `lint` 决定 promote；lint 失败交 `kb-review`。
