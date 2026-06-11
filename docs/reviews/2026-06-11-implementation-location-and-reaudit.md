# 实现定位 + feat/p9 分支复审报告

- 日期：2026-06-11
- 背景：前次审计（`2026-06-11-refactor-completion-audit.md`）在 `main` 工作区上得出"P0-P7 实现缺失"的结论。本次任务：先定位实现，再在找到的分支上做同样的审计，不 merge。

## 一、定位结果：实现没丢，在本地分支栈上

### 搜索范围与方法

| 范围 | 方法 | 结果 |
|---|---|---|
| 所有本地/远端 refs | `git log --all --name-only -- "*state_store*" "*source_convert*" ...` | **命中**，见下 |
| stash | `git stash list` | 空 |
| 其他 worktree | `git worktree list` | 只有 `D:/pdf-to-study-kb` 一个 |
| untracked 文件 | `git status --short` | 仅 `docs/reviews/`（审计报告本身） |

### 分支栈

新架构实现完整地存在于本地分支 `feat/p0-state-foundation` … `feat/p9-legacy-cleanup`（每个 phase 一个分支，逐级堆叠），从未 merge 进 `main`，也从未 push 到 origin。

- 终点分支：`feat/p9-legacy-cleanup`（ffc7c95）
- 拓扑：`git merge-base main feat/p9-legacy-cleanup` = `main` 的 tip（35f7bbc），即 **main 是 p9 的直接祖先，p9 领先 46 个提交，可 fast-forward，无分叉**。
- `main`（本地，领先 origin/main 2 个提交）只含文档重写 + 旧文档删除，`scripts/pipeline.py` 仍是旧版——这就是前次审计在 main 上得出"未完成"的原因。**前次审计的"实现缺失"结论是工作区位置错误造成的假阴性，不是实现真的缺失。**

### feat/p9-legacy-cleanup 上的内容清单

- 源码（`scripts/`）：`state_store.py`、`source_convert.py`、`source_profile.py`、`windowing.py`、`workorder.py`、`concept_store.py`、`promotion.py`、`wiki_gate.py`、`snapshots.py`、`locks.py`、`ingest_guards.py`、`mdpage.py`、`page_rules.py`、`query_session.py`、新版 `pipeline.py`
- 测试（`tests/`）：28 个测试文件，含 `test_p1_cli.py`…`test_p8_cli.py`、`test_legacy_removed.py` 守卫
- 计划文档：`docs/superpowers/plans/` 下存在**真正的 P0–P9 计划**（2026-06-09 ~ 06-10，每 phase 一份），不只是 documentation-authority-chain plan
- 执行报告：`pipeline-workspace/reports/` 下有 P1–P9 各期执行报告 + 最终完成报告
- skills：分支上的 `.claude/` 含 `/ingest`、`/kb-query`、`/kb-save`、`/kb-review`、`/wiki-lint-semantic`

## 二、feat/p9 分支复审（与前次同口径）

已 checkout 到 `feat/p9-legacy-cleanup` 执行：

| 检查项 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `python -m pytest tests -q` | **133 passed**（25s，0 失败） |
| CLI 命令面 | `pipeline.py --help` | 新架构 23 个子命令（add-source/profile/source-convert/windows/workorder/show-window/ingest-start/done/resolve-concept/check-write/snapshot-page/lint/promotion-candidates/promote-concept/check-session/fail 等），**无 plan-units/run-book** |
| `pipeline.py status` | 实测 | 正常运行，输出 `no state db yet`（合法空态，不再是 invalid choice） |
| 旧依赖 | grep `langgraph|surya` requirements*.txt | 无匹配 |
| 旧文件 | glob `scripts/{graph,units,run_book,plan_units}*.py` | 不存在 |
| 目标文件存在性 | ls-tree + 工作区 | 全部在 tracked tree 中 |

注意事项：

1. 仓库根的 `tmp/pytest-*` 遗留目录有 WinError 5 权限问题，会让无参 `pytest` 在 collection 阶段崩溃；用 `pytest tests` 规避。建议后续清理这些目录（可能含只读 SQLite 残留）。
2. pytest-asyncio 有一条 deprecation warning（`asyncio_default_fixture_loop_scope` 未设置），无碍。
3. 测试通过验证的是分支自身一致性；133 个测试是否覆盖 spec 全部验收口径，未在本次复核（前次审计对 main 的质疑里"测试通过≠落地"同样适用于任何分支，但本分支的测试就是为新架构写的 P0–P9 测试，证据强度完全不同）。

## 三、结论与建议

1. **不需要从 P0 重写**。实现 + 测试 + 计划 + 执行报告完整存在于 `feat/p9-legacy-cleanup`。
2. 当前工作区已停在 `feat/p9-legacy-cleanup`，未 merge（遵照指示）。
3. 后续决策（任选其一，需人工确认）：
   - 在 p9 分支上做一轮正式 code review（如 `/code-review`），通过后 fast-forward merge 到 `main`；
   - 或先把 `feat/p9-legacy-cleanup` push 到 origin 开 PR 走评审流程。
4. merge 前建议顺手处理：清理 `tmp/pytest-*` 遗留目录；决定本地 `main` 领先 origin/main 的 2 个文档提交与分支栈一起如何推送（p9 已包含它们，推 p9 即可带上）。
