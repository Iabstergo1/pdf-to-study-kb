# PDF to Study KB - Claude Code 项目指令

把多来源文档（PDF/DOCX/PPTX/MD）编译进一个**不断长大的、多领域、LLM 维护的 Obsidian 学习知识库**（llm-wiki 模式）。

## 权威链（按序）

1. **设计唯一真值**：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`。代码与本 spec 冲突时以 spec 为准（除非更新的 spec/ADR 取代）。
2. **决策**：`docs/adr/`（如 `0001` 舍弃 LangGraph）。
3. **领域语言/术语**：`docs/agents/domain.md`。

> 旧 LangGraph/section/plan-units 管线已在清理期删除（`tests/test_legacy_removed.py` 守卫）。**不要重新引入 LangGraph / 双 SQLite / plan-units / 逐 unit 孤立生成**（见 ADR-0001）。
> 构建期文档（P0–P9 plans / 执行报告 / 审阅报告）已随收尾清理删除，需要时查 git 历史。

## 架构（一句话）

确定性 Python CLI 做预处理 + 后置门禁 + 索引 + 状态跟踪（**零 LLM**）；唯一的 LLM 是**人工触发**的交互式 Claude Code `/ingest`，它读整源、写并合并 wiki、跨页归一概念。

```text
CLI 预处理（零 LLM）：add-source → profile → source-convert → windows → work order
      ↓ 人工触发
Claude Code /ingest（唯一 LLM）：读 source.md/难页图 → 写 status:proposed 页 + 概念归一
      ↓ 人工触发
CLI 收尾（零 LLM）：确定性 lint → 门禁 promote(proposed→published)/失败回滚+Review-Queue → 重建索引
```

## 核心约束

1. **预处理/收尾零 LLM**；唯一 LLM 是人工触发的 `/ingest`（不做无人值守自动化）。
2. **不拆分**：不让 LLM 做语义 unit 规划/审批；长源用确定性 processing windows（TOC/标题/页码/token 滑窗）读取。
3. **概念去重**：所有 concept 创建/更新走单一 `resolve_or_create_concept`，命中 `canonical_id` 则合并、**绝不新建重复页**；`_registry.yaml`/`aliases.md` 由概念页 frontmatter 派生，`/ingest` 不直接写派生文件。
4. **两阶段发布**：`/ingest` 写 `status: proposed`；收尾门禁通过才 promote 到 `published` 并入 index，失败回滚（`pipeline-workspace/snapshots/`，**默认非 git**）+ 进 Review-Queue。
5. **覆盖保护**：写已存在页须在 work-order snapshot 中、`managed_by != human`、hash 一致，否则不覆盖、出 proposal。
6. **单一业务 SQLite** 承载 source 级状态机（见 spec §3.3）。

## Vault 结构（输出）

单一 vault：`wiki/domains/<domain>/{lessons,concepts}`、`concepts/`（仅 shared，含 `_registry.yaml`）、`topics/`、`comparisons/`、`synthesis/`、`sources/`、`assets/`、`overview.md`、`index.generated.md`、`log.md`、`aliases.md`、`Review-Queue/`。**概念/主题为主，lessons 跟随源 TOC 为辅**。详见 spec §4/§8。

## Windows 工具选择

Claude Code 的 Bash 工具底层是 Git Bash (MSYS2)，处理含中文的 Windows 路径会崩溃。

1. **优先用原生工具**：Glob、Grep、Read、Edit —— 不经过 Bash，无路径问题。
2. **需要执行命令时**：直接调用 `pwsh`（PowerShell 7），不要通过 Git Bash 调用 PowerShell。
3. **禁止**：不要用 Bash 工具执行 `powershell -Command "..."` 或 `Select-String` 等 PowerShell 命令。

## 报告写入约定

执行报告、修复报告、审阅报告写入项目文件（如 `pipeline-workspace/reports/` 或 `docs/`），不在对话中复制大段输出；对话中只说一句指引用户读本地文件。
