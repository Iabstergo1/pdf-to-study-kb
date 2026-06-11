# P9 code review 修复报告（P0/P1/P2）

- 日期：2026-06-11
- 分支：`feat/p9-legacy-cleanup`（未 merge、未 push，遵照指示）
- 对应 findings：`docs/reviews/2026-06-11-p9-code-review.md`
- 方法：TDD——先写 5 个回归测试确认 RED（全部因功能缺失失败），再实现，最后全量 GREEN。

## 修复明细

### P0 — 写入边界路径穿越（`scripts/ingest_guards.py`）

- 新增 `_normalize_rel()`：拒绝绝对路径（`/...`）、盘符（`C:...`）、任何 `..` 段；顺带剔除空段与 `.` 段。
- `in_write_scope()`：先归一化，归一化失败一律 False。`domains/misc/../../outside.md` 不再命中 `domains/misc/**`。
- `can_overwrite()`：纵深防御——同样先归一化，不安全路径直接 `(False, "unsafe path")`，不再依赖"目标不存在=新建放行"被穿越利用。
- 回归测试：`tests/test_ingest_guards.py::test_in_write_scope_rejects_traversal_and_absolute`、`test_can_overwrite_rejects_unsafe_path`。

### P1 — lint 跨 source 发布（`scripts/pipeline.py` cmd_lint + `scripts/wiki_gate.py`）

- 新增 `wiki_gate.belongs_to_source(rel_path, meta, source_id, written)`：归属判定 =
  本 source 各 window 的 `write_set`（权威，覆盖 topic/synthesis/overview 等无归属字段的页）
  ∪ `sources/<id>.md` ∪ frontmatter `source`/`source_id` ∪ `source_refs[].source`。
- `lint --source X` 现在只 lint/promote 归属 X 的 proposed 页；他源 proposed 页打印 `[skip]` 明示留在原地，等其所属 source 自己收尾。input_hash 也只对归属集合计算。
- 契约影响：`/ingest` 协议中 `window-done --writes` 的记账从"建议"升为"必要"——没记写集又没有 frontmatter 归属的页不会被本 source 的 lint 处理（fail-closed）。`tests/test_p6_cli.py::test_lint_fail_blocks_rolls_back_and_queues` 已按新契约给坏页补 `source: note` 归属。
- 回归测试：`tests/test_p6_cli.py::test_lint_scopes_to_own_source_pages`（A/B 两源：B 收尾只发布 B 的页含 write_set 归属的无字段页；A 留 proposed、状态不被推进、index 不收录；之后 A 自己收尾照常发布）。

### P1 — stale lock 无 CLI 落点（`scripts/pipeline.py` + heartbeat 接线）

- `status`：显示 `[lock] vault held by <holder> since <t> (heartbeat <t>)`，stale 时附 `[STALE → pipeline.py unlock]`（补齐 spec §3.3 第 101/120 行既有要求）。
- `next`：锁 stale 时输出清理建议行（含 `pipeline.py unlock`）。
- 新增 `unlock --ttl <秒>`（默认 1800 = `LOCK_TTL_SECONDS`）：只破 heartbeat 超时的 stale 锁；活锁拒绝并 exit 非零。
- `window-start`/`window-done` 现在每次刷新锁 heartbeat——长 ingest 会话不会被误判 stale，崩溃残留则自然超时。
- 回归测试：`tests/test_p4_cli.py::test_stale_lock_visible_and_recoverable`（status 显示锁→活锁不可破→window 记账刷新 heartbeat→做旧后 next 给建议、unlock 成功）。

### P2 — windows 阶段 artifact hash 精度（`scripts/pipeline.py` cmd_windows）

- artifact 与 `complete_stage` 的 output_hash 改记 `windows.jsonl` 本体的 sha256；`should_run_stage` 的 input_hash（source.md hash）保持不变，幂等语义不受影响。
- 回归测试：`tests/test_p1_cli.py::test_windows_artifact_records_windows_jsonl_hash`。

### P1 余项 — 孤儿 proposed 页不得放行发布（复验报告 `2026-06-11-p9-review-fix-verification.md`）

- 症状：`lint --source X` 跳过未归属 proposed 页后仍把 X 标成 published，可出现
  `promoted 0 pages; source published`，source 状态与 vault 内容不一致。
- 修复（`pipeline.py` cmd_lint）：把"非本 source 的 proposed 页"二分——
  - **归属其他已注册 source**（frontmatter 归属 ∪ 该 source 的 window write_set）→ 放行跳过，
    留待其所属 source 收尾（否则多源工作流互相卡死）；
  - **不归属任何 source 的孤儿页** → 作为 `unattributed-proposed` violation 阻断，
    走既有失败路径：不 promote、回滚本 source 快照、写 Review-Queue、source 进 `lint/failed`。
    这才是 "`window-done --writes` 必要" 的 fail-closed 落点。
- 孤儿集合并入 lint 的 input_hash：孤儿出现/消失都会触发重跑，不会被 `[skip] up-to-date` 吞掉。
- 连带根因修复（`templates/overview.md`）：init-vault 的种子 overview 原带 `status: proposed`
  且无归属——每个新 vault 天然自带一个孤儿，会阻断一切 lint。种子是确定性 CLI 脚手架而非
  LLM 提案，改为 `status: published` 落地；/ingest 之后更新它时改回 proposed 并记入
  write_set，照常走门禁。
- 回归测试：`tests/test_p6_cli.py::test_lint_blocks_on_unattributed_proposed`
  （孤儿阻断→不发布任何页→Review-Queue 含 unattributed→lint/failed→补归属后重试通过、
  source published）。

## 验证

| 项 | 命令 | 结果 |
|---|---|---|
| RED | `pytest tests/test_{ingest_guards,p6_cli,p4_cli,p1_cli}.py --basetemp tmp/pytest-red` | 新增 5 个测试全部失败（功能缺失），既有 16 个通过 |
| GREEN（受影响文件） | 同上（实现后） | 21 passed |
| 全量 | `pytest tests -q --basetemp tmp/pytest-full` | **138 passed**（133 旧 + 5 新，0 失败） |
| 空白检查 | `git diff --check` | 通过 |

P1 余项修复后复跑同一组：孤儿阻断回归测试先 RED（完整复现 `promoted 1 pages; source published`
带跳过孤儿），实现后 `tests/test_p6_cli.py` 4 passed，全量 **139 passed**，`git diff --check` 通过。

## 遗留事项

1. **未 commit**：8 个 tracked 文件的修改在工作区（3 源码 + 1 模板 + 4 测试），等人工确认后提交。
   （2026-06-11 复验通过后已随本报告一并提交。）
2. `tmp/pytest-all*`、`pytest-audit`、`pytest-p0*`、`pytest-review` 旧残留目录 ACL 拒绝访问，普通权限删不掉（已尝试清 readonly 属性无效）；需管理员 `takeown /r` + `icacls /reset` 后删除，或重启后再删。在此之前全仓 `pytest` 必须带 `--basetemp` 或限定 `pytest tests`。
3. spec 未给 TTL 数值，`LOCK_TTL_SECONDS=1800` 为本次取值（可 `--ttl` 覆盖）；如需写进 spec 请另行决定。
