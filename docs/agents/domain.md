# Domain Docs

本仓库从多种来源文档（PDF / DOCX / PPTX / Markdown）编译一个**多领域、LLM 维护的 Obsidian 学习 wiki**（llm-wiki 模式）。

## 权威链（按序阅读）

1. **设计唯一真值**：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`。代码与该 spec 冲突时以 spec 为准（除非更新的 spec/ADR 取代）。
2. **决策记录**：`docs/adr/`（如 `0001` 舍弃 LangGraph）。
3. **构建计划/进度**：`docs/superpowers/plans/`（P0–P7）。
4. **Agent 指令**：根 `CLAUDE.md`。
5. **人读概览**：`README.md`。

## 领域术语（现行架构）

- `source`：一个被 ingest 的文档（PDF/DOCX/PPTX/MD）。取代旧的 `section` / `semantic unit`。
- `source-convert`：确定性阶段，把任意来源转成干净 `source.md` + 抽取的 `assets/` + 难页 PNG。
- `processing window`：长源的确定性读取切片（按 TOC/标题/页码/token 预算），仅为喂模型；输出里不可见，不是知识结构单位。
- `work order`：每个 source 的契约（`pipeline-workspace/staging/<source>/workorder.yaml`），定义写入边界、registry 快照、页面快照、失败处理。
- `/ingest`：唯一的 LLM 步骤——一个交互式 Claude Code slash 命令，读一个 source 并织进 wiki。**人工触发，非自动化**。
- `canonical concept`：去重后的概念，命名空间化 `canonical_id`（`concept.<domain>.<slug>`）；真值在概念页 frontmatter；`concepts/_registry.yaml` 与 `aliases.md` 为派生。
- `two-stage publish`：`/ingest` 写 `status: proposed` 页；确定性收尾门禁把通过的页 promote 成 `published`（并入 `index.generated.md`），失败回滚 + 落 `Review-Queue/` proposal。
- `business SQLite`（`pipeline-workspace/state/study-kb.sqlite`）：单库，含状态机表 `sources / source_stage_runs / artifacts / work_orders / source_locks / review_proposals / ingest_progress`。
- `managed_by: pipeline`：frontmatter 标记，允许 pipeline 覆盖某生成页（已升级为 snapshot+hash 覆盖守卫）。
- `Review-Queue`：未过收尾门禁内容的暂存区。

## 工作规则

1. spec 是权威；**不要重新引入 LangGraph、`plan-units`、双 SQLite、逐 unit 孤立生成**——这些是被刻意移除的（见 `docs/adr/0001`）。
2. 预处理与收尾是确定性 Python CLI（零 LLM）；唯一 LLM 是人工触发的 `/ingest`。
3. 按 `docs/superpowers/plans/` 逐期构建；P0（状态底座 + 文档同步）是硬前置。
4. 先确定性底座、后 LLM 行为；实现同时加聚焦测试。
5. 未过收尾门禁的内容不 promote，转 `Review-Queue/`。
6. 覆盖已存在页前须：在 work-order snapshot 中、`managed_by != human`、磁盘 hash 一致；否则不覆盖、出 proposal。
7. 旧管线代码（`langgraph_worker.py`、`plan-units` 等）在其删除期（P4）前仍在仓库；**不要在旧路径上加新功能**。
