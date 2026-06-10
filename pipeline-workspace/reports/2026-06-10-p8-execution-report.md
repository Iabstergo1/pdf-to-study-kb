# P8 Query/Save-back 闭环执行报告

- 日期：2026-06-10
- 分支：`feat/p8-query-saveback`（基于 `feat/p7-cross-domain-promotion`，保留本地）
- 计划：`docs/superpowers/plans/2026-06-10-p8-query-saveback.md`
- 验证：`python -m pytest -q --ignore=tmp` → **217 passed**（207 旧 + 10 新，零回归）

## 提交清单

| 提交 | 内容 |
|---|---|
| 5211f5d | `query_session.py`：session 目录契约 + Q1 确定性检查（query/saved 两级） |
| 8035b6b | CLI `check-session --id [--saved]`（Q1 硬门禁，问题即 exit 1） |
| a3ad400 | 4 个显式命令：`/kb-query`（只读+持久化）、`/kb-save`（准入门槛+写入纪律+decision+Q1 自检）、`/kb-review`（复核队列）、`/wiki-lint-semantic`（L4/矛盾/Q2 只出 proposal）+ `save-back-policy.md`；全部要素由文档测试锁定 |

## 验收（spec §14 命令路由条目，实测/文档锁定）

- [x] 副作用命令均显式 slash command（已被 Claude Code 识别注册）
- [x] `/kb-query` 只读、持久化 query-session 到 `pipeline-workspace/query-sessions/<run_id>/`、不写 vault
- [x] `/kb-save` 只有命中准入门槛才写 proposed，必须留 `decision.md`，Q1 由 `check-session --saved` 硬检查
- [x] session 不进 artifacts 表（文件系统-only，spec §3.4）
- [x] `/kb-review`、`/wiki-lint-semantic` 只产出建议/proposal，不直接改写页

## 里程碑

**spec §15 的 P0–P8 全部实现完毕。** 剩余收尾：旧管线下线清理期（spec §12 删除清单：LangGraph/unit 旧代码、langgraph*/surya 依赖、旧 Web 前端；同步 README/CLAUDE/domain 文档到最终状态）。
