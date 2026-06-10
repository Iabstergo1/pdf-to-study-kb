# P1 source-convert 执行报告

- 日期：2026-06-10
- 分支：`feat/p1-source-convert`（基于 `feat/p0-state-foundation`，按用户选择保留本地、不合并不 push）
- 计划：`docs/superpowers/plans/2026-06-09-p1-source-convert.md`（已先按 F1/F2/F3 修订并提交 e86b52f）
- 验证：`python -m pytest -q --ignore=tmp` → **129 passed**（111 旧 + 18 新，零回归）

## 提交清单（逐任务 TDD：先失败测试 → 实现 → 通过 → 提交）

| 提交 | 任务 | 内容 |
|---|---|---|
| e86b52f | Task 0 | P1 plan 文档（修订版）入库（在 feat/p0-state-foundation 上） |
| 637638b | Task 2 | `state_store.record_artifact`/`list_artifacts`（同 source+kind+path 覆盖幂等） |
| 5f26bfd | Task 3 | `windowing.py`：标题切分 + 超长 token 滑窗 + overlap，纯函数确定性 |
| 9782177 | Task 4 | `source_profile.py`：公式符号密度、needs_vision 判定、`profile_source` 逐页 |
| 4d6028b | Task 5 | `source_convert.py`：md 直通 + PyMuPDF 文本后端 + 难页渲 PNG，`BackendUnavailable` 适配器协议 |
| 67b5023 | Task 6 | CLI 接线：`_workspace_root()`（`STUDY_KB_ROOT` 可覆盖）+ `add-source`/`profile`/`source-convert`/`windows` 接 P0 状态机 |
| 7fd76ed | Task 7 | `pipeline fail` 维护命令（救回崩溃残留的 running 阶段） |

## 验收清单（Task 8，逐项实测）

- [x] markdown 源端到端 registered → profiled → converted → windowed（CLI 测试 + 手动 smoke，`status` 显示 windowed、`next` 给 `run: workorder`）
- [x] 文本 PDF（fitz 最小件）产出 `source.md`，逐页 profile 含 `needs_vision`
- [x] 难页（空白带图）标 `needs_vision=[1]` 且渲染 `assets/p0001.png`（手动验收脚本实测）
- [x] `windows.jsonl` 确定性（同输入同输出测试）
- [x] 幂等：同输入重跑 `source-convert`/`windows` 输出 `[skip] ... up-to-date`（实测）
- [x] artifacts 表记录 `raw_source` / `pages` / `source_md` / `windows` 四类产物 hash（实测查询）
- [x] `profile` 真实产出 `pages.jsonl`（artifact kind=pages），非空转盖戳
- [x] 崩溃恢复：人为残留 running → `pipeline fail` 标 failed → 该阶段可重跑（端到端测试）
- [x] 测试隔离：CLI 测试与 smoke 全程经 `STUDY_KB_ROOT` 写 tmp，真实仓库 `pipeline-workspace/state` 未被创建（实测 False）；P0 路径相关测试不受影响
- [x] 全量回归 129 passed，工作树干净（仅本地报告目录未跟踪，按约定不提交）

## 当前状态与下一步

P1 完成 = 任意 md/文本 PDF 源可确定性转成 `source.md` + `windows.jsonl` + needs_vision 标记，P0 状态机全程记账、幂等可重跑、崩溃可救。

按 spec §15，下一期为 **P2：canonical 概念模型 + registry + 别名归一 + 概念 merge**。注意事项（来自同日参考对照评估）：P2 必须在 P4 之前完成；写 P4 计划时落实 rolling digest。
