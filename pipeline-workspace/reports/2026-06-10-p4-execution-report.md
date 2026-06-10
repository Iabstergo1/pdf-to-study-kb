# P4 命令层 + /ingest + Work Order 执行报告

- 日期：2026-06-10
- 分支：`feat/p4-ingest-command`（基于 `feat/p3-page-templates`，保留本地）
- 计划：`docs/superpowers/plans/2026-06-10-p4-ingest-command.md`
- 验证：`python -m pytest -q --ignore=tmp` → **186 passed**（163 旧 + 23 新，零回归）

## 提交清单

| 提交 | 内容 |
|---|---|
| ff0a7e4 | state_store：window 进度（start/finish/fail/should_run/states）+ work_orders 助手 + latest_run_id |
| ec08e98 | ingest_guards：write_scope glob、覆盖保护三条件、registry hash 守卫 |
| 26dd93c | workorder 生成器（写入边界、registry hash、domain+shared 概念快照、其它页快照） |
| 09e2619 | **修 bug**：write_registry 磁盘字节与返回 hash 不一致（Windows CRLF）——stale 守卫会误报 |
| 9491906 | CLI：workorder（windowed→workorder_ready）+ show-window |
| 24d2b02 | CLI：ingest-start（锁+stale 硬校验）/ingest-done（proposed+释放锁）/window-start/done/fail/resolve-concept/check-write/snapshot-page |
| 596a2d0 | `.claude/commands/ingest.md` 协议（rolling digest、写页纪律、派生禁写）+ skill-runtime 三文档 |

## 执行偏差记录

1. **Windows newline bug（测试抓出的真缺陷）**：`write_registry` 返回 LF 文本的 hash，但 `write_text` 在 Windows 写出 CRLF，`registry_fresh` 对磁盘 hash 校验必然失败。修复：`write_text(..., newline="\n")`。
2. **流程失误**：Task 4 的验证命令用 `;` 连接 git commit，带着 1 个失败测试提交了 26dd93c；缺陷在紧随的 09e2619 修复，最终全绿。教训：验证与提交必须用 `&&` 链接。

## 验收清单（逐项实测）

- [x] work order：write_scope / registry{path,hash,scope} / concept_pages_snapshot（domain+shared 全量、排除他域、含 managed_by）/ other_pages_snapshot / on_failure
- [x] `ingest-start`：取锁 + stale registry 硬中止（篡改实测）；第二个 `/ingest` 被锁拒绝；`ingest-done` 后锁释放、第二个可开工
- [x] window 级续跑：同 hash finished 跳过；failed/换 hash 重做；UPSERT 重启合法
- [x] `check-write`：越界 DENY（index.generated.md）、新页 ALLOW；覆盖三条件各自 DENY（单元测试）
- [x] `resolve-concept`：created→merged，单页不重复；实时扫描保证会话内新鲜、不写派生文件
- [x] `snapshot-page`：manifest 落 `pipeline-workspace/snapshots/<source>/r<run_id>/`
- [x] 两阶段发布：`ingest-done` 后 `(ingested, proposed)`
- [x] `/ingest` 协议文档含 rolling digest（C1 落实）、写页纪律、派生文件禁写；routing 有负例；文档要素由测试断言

## 下一步

P5：综合层一等产物（vault 脚手架 init-vault、overview 模板与 L5 小节、/ingest 综合层职责强化）。
