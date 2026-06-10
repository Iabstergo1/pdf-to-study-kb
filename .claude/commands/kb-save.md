---
description: 把一个 query-session 的候选提升为 proposed 写入 wiki（有准入门槛）
argument-hint: <session_run_id>
---

# /kb-save $1 — 显式保存（两步闭环的第二步）

作用在已有 query-session 上：先读 `pipeline-workspace/query-sessions/$1/` 全部文件，
按 `docs/skill-runtime/save-back-policy.md` 判断**准入门槛**——不满足就明确拒绝并说明原因，
不写任何页。

## 满足门槛时的写入纪律（与 /ingest 完全相同）

1. 写入范围仅限：`topics/**`、`comparisons/**`、`synthesis/**`、相关 concept 页、
   `overview.md`、`log.md`；全部 `status: proposed` + `managed_by: pipeline`。
2. 概念只走 `python scripts/pipeline.py resolve-concept ...`（命中合并绝不新建）；
   写已存在页前 `python scripts/pipeline.py check-write --source kb-save --path <rel>`
   （没有 work order 时按 DENY 处理：改走 Review-Queue proposal）+ `snapshot-page`。
3. 更新 session 目录：补全 `candidate_write_set.json`（实际写过的页）、`evidence_refs.json`，
   写 `decision.md`（为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念）。
4. 自检：`python scripts/pipeline.py check-session --id $1 --saved` 必须通过（Q1）。
5. 提示用户运行收尾 `lint` 决定 promote（语义新增价值判断 Q2 属 /wiki-lint-semantic）。
