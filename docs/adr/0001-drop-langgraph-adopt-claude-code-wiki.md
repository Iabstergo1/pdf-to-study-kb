# ADR 0001: 舍弃 LangGraph，采用 Claude-Code 维护的 wiki + 确定性 CLI

- 日期：2026-06-09
- 状态：Accepted
- 关联：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`

## 背景

旧管线用 LangGraph 编排每个 unit 的 author→review→revise LLM 循环（DeepSeek），用 checkpointer SQLite 做断点续跑，另有业务 SQLite。输出是按 PDF 章节结构组织的 per-book vault，读起来像原文转写。

目标改为 llm-wiki 模式：LLM 增量构建并维护一个持久、互联、多领域的 Obsidian wiki。生成/审校/合并循环搬进 Claude Code（`/ingest`），它是唯一的 LLM，且**人工触发**（可用的 Claude key 禁止无人值守自动化）。

## 决策

1. **移除 LangGraph**（StateGraph、checkpointer、`langgraph-checkpoint-sqlite` 依赖）。LLM 循环进了 Claude Code 后，CLI 侧只剩预处理、收尾两段确定性直线（无循环/分支/LLM 节点），LangGraph 只剩重量。
2. **单一业务 SQLite + source 级状态机**取代双库；checkpointer 的恢复职责由"CLI 阶段级幂等 + `/ingest` window 级进度"替代。
3. **不做 LLM 语义 unit 规划**（移除 `plan-units` / `validate-unit-plan` / `review-unit-plan`）；长源用确定性 processing windows 读取。
4. **编排 = 确定性 Python CLI + SQLite 状态跟踪**（`pipeline status` / `next`）。

## 后果

- 恢复粒度是阶段级（CLI）+ window 级（`/ingest`），不是 LangGraph 节点级——可接受。
- 唯一 LLM 成本在人工触发的 `/ingest`，规避 key 的自动化限制。
- 设计唯一真值是上面关联的 spec；本 ADR 记录"为什么不用 LangGraph"，供未来 agent 直接看到，无需通读 spec。
