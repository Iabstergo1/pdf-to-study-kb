# 重构分析报告：LangGraph 管线 → llm-wiki 架构迁移现状

- 日期：2026-06-10
- 分支：`feat/p0-state-foundation`（领先 origin/main 约 7 个提交，未 push）
- 验证基线：`python -m pytest -q --ignore=tmp` → **111 passed**（conda env `pythonProject`）
- 结论先行：**重构计划合理，P0 已真实完成并通过验证；P1 只有计划、未执行；P2–P8 未开始；旧管线按计划完整保留。当前没有架构偏离，但 P1 计划里有 1 个会导致测试失败/库污染的路径矛盾（F1），必须在执行前修正。**

---

## 1. 背景与目标架构（核对结果：文档自洽）

目标：从「LangGraph 编排 + 语义 unit 规划 + 逐 unit 生成 + 双 SQLite」迁移到「确定性 Python CLI（零 LLM）+ 单一业务 SQLite 状态机 + 人工触发的 Claude Code `/ingest` 作唯一 LLM」，输出一个多领域、概念为主的 Obsidian wiki，两阶段发布（proposed → 门禁 → published）。

权威链核对：spec（§1–§15）、ADR-0001、domain.md、CLAUDE.md、README、requirements.txt 五者方向一致，无相互矛盾的架构表述。ADR-0001 对「为什么弃 LangGraph」的论证成立：LLM 循环搬进 Claude Code 后，CLI 侧只剩两段确定性直线，编排框架失去独占价值；进度协调需求由状态表 + `status`/`next` 覆盖。

## 2. 计划质量评估

### P0 计划（2026-06-09-p0-state-foundation.md）：高质量，且已按计划执行完

- 与 spec §3.3 逐条对应（状态机、原子阶段 API、失败语义、锁、非 git 快照、vault 级单库）。
- TDD 结构完整（每任务先失败测试、含完整代码、逐任务提交），Self-Review 覆盖了 Codex 前轮 5 点。
- 计划本身的 checkbox 未勾选，但**这不代表未完成**——git 历史与测试证明已全部落地（见 §3）。

### P1 计划（2026-06-09-p1-source-convert.md，未跟踪、未提交、未执行）：方向正确，但有 3 处需修正后才能执行

- 优点：基于真实 P0 API 写（签名逐一核对无误）；默认后端 PyMuPDF（已装）保证当前 env 端到端可跑；重后端按 spec §5 留适配器接口不强装；明确不越界（不写 vault、不做 work order、零 LLM）。
- 问题见 §5 的 F1–F3。

### 分期顺序（spec §15 P0→P8）：合理

确定性底座 → 转换/切窗 → 概念模型 → 模板 → 命令层/ingest → 综合层 → lint 门禁 → 多领域 → query/save-back。每期产出独立可验证，LLM 相关（P4）排在确定性能力之后，符合「先底座后行为」原则。无顺序错误。

## 3. 真实执行进度（以代码 + 测试 + git 为准，不以 checkbox 为准）

| 阶段 | 状态 | 证据 |
|---|---|---|
| 文档权威链同步 | ✅ 完成 | `ea12852`/`35f7bbc`（spec/ADR/CLAUDE/README/requirements 重写，旧文档删除），已在 main |
| P0 状态底座 | ✅ 完成并硬化 | `5ab6420`→`a7f3113` 共 6 个提交；`state_store.py`（7 表 + 原子阶段 API + 状态机 + 双契约守卫）、`locks.py`、`snapshots.py`、`pipeline status/next`；23 个新测试全过 |
| P1 source-convert | 📝 仅计划 | plan 文件未跟踪（`?? docs/superpowers/plans/2026-06-09-p1-source-convert.md`）；`windowing.py`/`source_convert.py`/`source_profile.py` 均不存在 |
| P2–P3 | ⬜ 未开始 | 无 canonical registry 代码、无新模板（`templates/` 仍是旧的 section-lesson 模板） |
| P4 命令层 + /ingest | ⬜ 未开始 | `.claude/commands/` 不存在，`docs/skill-runtime/` 不存在 |
| P5–P8 | ⬜ 未开始 | 无对应代码 |

P0 验收清单逐项复核（对照 P0 plan Task 7）：7 表存在且 `init_db` 幂等 ✓；`register_source → (registered, done)`、next 建议 `run: profile` ✓；原子 API 同事务、非法迁移拒绝、`ingested→proposed`、`lint→published`、lint 失败可回 `ingest_waiting` ✓；幂等跳过 ✓；锁三态 ✓；快照含「父目录被删仍恢复」 ✓；`status`/`next` vault 级单库 smoke ✓。另含 Codex 两个契约缺口的修复（`a7f3113`）：running 中拒绝重复 start；无 running run 时 complete/fail 抛 `InvalidTransition` 且不改 sources 表——均有对应测试。

## 4. 已实现 vs 计划 vs 旧遗留（scripts/ 全量分类）

- **新架构（已实现，4 个文件）**：`state_store.py`、`locks.py`、`snapshots.py`、`pipeline.py` 的 `status`/`next` + `_vault_state_db()`。
- **仅计划（P1 起）**：`windowing.py`、`source_convert.py`、`source_profile.py`、`record_artifact`/`list_artifacts`、CLI 四子命令。
- **旧管线遗留（15 个脚本 + 13 个旧测试 + 周边目录，按计划 P4 删）**：`langgraph_worker.py`、`unit_plan.py`、`unit_context.py`、`run_book.py`、`business_db.py`（旧双库一侧）、`llm_provider.py`、`pdf_profile.py`、`ocr_surya.py`/`surya_smoke.py`、`evidence_verifier.py`、`review_gate.py`、`obsidian_indexes.py`、`memory_store.py`、`cost_guard.py`、`web_ops.py`/`serve.py`；以及 `webapp/`、`schemas/`、`tools/`、`books/game-theory-whitepaper/`（旧 per-book 输出）、`templates/` 旧模板。`requirements.txt` 的 `langgraph*`/`surya-ocr` 已标注 DEPRECATED/过渡期。
- **重要确认**：迁移开始后没有任何提交往旧路径加新功能；新旧命令面共存于 `pipeline.py` 是 spec/README 明示的过渡期设计，不算偏离。

## 5. 发现的问题（按严重度）

### F1（高，阻塞 P1 执行）：P1 计划的状态库路径语义自相矛盾

P0 的 `_vault_state_db()` 是 **repo 根锚定**（`Path(__file__).resolve().parents[1]/...`，见 `scripts/pipeline.py:193`），与 cwd 无关。但 P1 plan Task 6：

- 测试以 `cwd=tmp_path` 跑 CLI，并断言产物在 `tmp_path/pipeline-workspace/staging/...`；
- 注释却写「cwd 下 pipeline-workspace/state/study-kb.sqlite，沿用 P0 `_vault_state_db()` 思路」；
- 计划里 `_staging_dir()` 用相对路径（cwd 锚定），而 db 用 `_vault_state_db()`（repo 根锚定）。

按原文执行的后果：`add-source` 把测试数据写进**真实仓库**的 `pipeline-workspace/state/study-kb.sqlite`（污染 + 测试间互相干扰），staging 却落在 tmp cwd，`status` 断言可能假阳/假阴；并且一旦真实库存在且有行，P0 的 `test_status_smoke_runs`（期望 "no state" 或空输出）会连带失败。**修法（选一，建议前者）**：① 给 `_vault_state_db()` 加环境变量覆盖（如 `STUDY_KB_STATE_DB`），测试注入 tmp 路径；② 改为 cwd 锚定并同步修 P0 的路径断言测试。修订属于 plan 文档级，改动很小，但必须先做。

### F2（中）：CLI 进程崩溃可把 source 永久卡死在 running，且无维护出口

P0 契约硬化后 `running → start_stage` 一律拒绝（正确），但若 CLI 在 `start_stage` 与 `complete/fail` 之间崩溃（进程被杀、断电），DB 里留下 running run：`fail_stage` 在库层可以救（running run 还在），可是**没有任何 CLI 命令暴露它**；`next_actions` 对该态只会显示 `resume/in-progress: <stage>`，而 CLI 阶段并无 resume 概念，用户没有自助恢复路径。`source_stage_runs` 也不记 pid/heartbeat，无法自动判死。建议 P1 一并加一个 `pipeline fail --source <id> --stage <stage> --error <msg>`（或 `reset`）维护命令 + 对应测试；spec §3.3 可补一句「CLI 阶段崩溃恢复 = 人工 fail 后重跑」。

### F3（中）：P1 计划的 `profile` 阶段是空转盖戳

`cmd_profile` 只 `start_stage` + 立即 `complete_stage`，真实逐页 profile（`pages.jsonl`、needs_vision）全部发生在 `source-convert` 内。这与 spec 的阶段语义（profiled = 逐页风险/难页判定完成）名实不符：状态机里多一个无产物阶段，`status` 显示 profiled 但磁盘上什么都没有。建议二选一：把逐页 profile 真正挪进 `cmd_profile`（产出 `staging/<source>/pages.jsonl`，convert 复用），或在 plan/spec 明确记录「P1 期 profile 与 convert 合并计算」的取舍。

### F4（低）：分期编号文档不一致

CLAUDE.md、README、domain.md 写「P0–P7」，spec §15 实际是 **P0–P8**（P8 = query/save-back 闭环）。三处各改一字即可。

### F5（低，已知遗留）：`pipeline.py` 顶层 `import yaml`

使 `status`/`next` 不再 stdlib-only（P0 plan 声明零第三方依赖）。把 `import yaml` 下沉进旧命令函数即可；可随 P1 顺手做或留到 P4 清旧。

### F6（低）：「旧代码 P4 删除」只写在 requirements.txt/domain.md，spec §15 的 P4 条目本身没提

建议 spec §15 P4 补一句「同期删除旧管线（langgraph_worker/unit_plan/run_book/webapp 等）与 langgraph*/surya 依赖」，避免 P4 执行时漏删或歧义。

### F7（低，环境）：`tmp/` 遗留 pytest basetemp 目录（Windows 权限锁）

导致裸跑 `pytest` 收集时 PermissionError，目前靠 `--ignore=tmp` 绕过。建议删掉 `tmp/pytest-*` 或在 pytest 配置加 `norecursedirs = tmp`，消除每次手工绕过。

### 测试缺口（小）

- 锁的真实并发竞争（两进程同时 `acquire`）未测——机制靠 PRIMARY KEY 冲突，风险低，可接受。
- F2 描述的「崩溃留下 running run → fail_stage 救回 → 重跑」恢复链路无测试，建议随 F2 的维护命令补。
- 其余 P0 行为测试覆盖充分（13+3+4+3=23 个新测试）。

## 6. 风险评估

- **总体风险低**。最大的顺序风险是「不修 F1 直接执行 P1」——会当场把测试和真实状态库搅在一起。
- 合规约束（Claude key 禁自动化）在 spec/ADR/CLAUDE.md 三处反复钉死，P4 设计为显式 slash command，无漂移迹象。
- 分支管理健康：7 个提交粒度清晰、逐任务对应；未 push 符合用户「全部完成后再 push」约束。唯一例外是 P1 plan 文件还游离在工作树外（未跟踪），建议尽快提交以免丢失。

## 7. 建议的下一步（按序）

1. **修订 P1 plan**（文档级小改）：F1 路径语义统一（建议 env 覆盖方案）、F2 加 `pipeline fail`/`reset` 维护命令任务、F3 决定 profile 阶段的真实产出；顺手修 F4 三处「P0–P7」。
2. **把 P1 plan 作为 docs commit 提交**到当前分支（它现在是工作树里唯一未跟踪文件）。
3. **执行 P1**（superpowers:executing-plans，Inline）：纯文本 PDF/Markdown 端到端 registered → windowed。
4. 顺手项：清 `tmp/pytest-*`（F7）、`import yaml` 下沉（F5）、spec §15 P4 补删除清单（F6）。
5. **不要开始 P4/P8**；P2（canonical 概念模型）等 P1 验收后再排。

---

*验证命令：`D:\miniconda3\envs\pythonProject\python.exe -m pytest -q --ignore=tmp` → 111 passed in 15.35s（2026-06-10）。*
