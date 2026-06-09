# 📚 PDF → Study KB

把多来源文档（PDF / DOCX / PPTX / Markdown）编译进一个**不断长大的、多领域的本地 Obsidian 学习知识库**——按概念/主题导航，而不是线性翻原文。采用 [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 模式：LLM 增量维护一个持久、互联的 wiki。

> **状态**：本仓库正从旧的 LangGraph/section 管线迁移到新架构。**设计唯一真值**是 [`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`](docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md)；构建进度见 [`docs/superpowers/plans/`](docs/superpowers/plans/)；关键决策见 [`docs/adr/`](docs/adr/)。旧管线代码仍在过渡期保留。

## 架构

确定性 Python CLI 做预处理 + 后置门禁 + 索引 + 状态跟踪（**零 LLM**）；唯一的 LLM 是**人工触发**的交互式 Claude Code `/ingest`，它读整源、写并合并 wiki、跨页归一概念。

```text
CLI 预处理（零 LLM）        add-source → profile → source-convert → windows → work order
        ↓ 人工触发
Claude Code /ingest（唯一 LLM）  读 source.md / 难页图 → 写 status:proposed 页 + 概念归一
        ↓ 人工触发
CLI 收尾（零 LLM）          确定性 lint → 门禁 promote / 失败回滚+Review-Queue → 重建索引
```

设计要点：
- **不拆分**：不让 LLM 做语义切分；长源用确定性 processing windows（TOC/页码/token 滑窗）读取。
- **概念去重**：canonical concept + 别名归一，同一概念一页、跨来源累积。
- **两阶段发布**：未过门禁的内容不进正式 index，进 `Review-Queue/`。
- **非文字内容**：分层后端（pymupdf4llm / marker / docling），难页交 Claude 多模态读图、公式 KaTeX、源页截图可核对。

## Vault 结构（输出）

```text
wiki/
  domains/<domain>/{lessons, concepts}   # 讲义（跟随源 TOC）+ 领域私有概念
  concepts/        # 仅 shared（跨域提升后），含 _registry.yaml（派生）
  topics/ comparisons/ synthesis/        # 综合层（一等产物）
  sources/  assets/  Review-Queue/
  overview.md      # living synthesis，入口
  index.generated.md  log.md  aliases.md # 派生
```

## 现状与运行

- 新架构命令面（`add-source` / `profile` / `source-convert` / `windows` / `workorder` / `lint` / `promote` / `status` / `next` + `/ingest`）随 `docs/superpowers/plans/` 逐期落地，详见 spec §3、§9。
- 旧 CLI（`scripts/pipeline.py` 的 `init-book` / `plan-units` / `run-book` 等）及本地 Web 前端（`scripts/serve.py`）在迁移期仍可运行，但**不代表目标架构**，将按计划替换。
- 依赖见 `requirements.txt`（`langgraph*` 为过渡期保留，将在旧代码删除时一并移除）。

## 在 Obsidian 中阅读

Obsidian → `Open folder as vault` → 选 `wiki/` → 从 `overview.md` 开始。所有生成笔记 frontmatter 为 Dataview 友好（`type`/`canonical_id`/`domain`/`status`/`source_refs`…）。

## 文档导航

| 文档 | 用途 |
|------|------|
| `docs/superpowers/specs/2026-06-08-…design.md` | 设计唯一真值 |
| `docs/adr/` | 架构决策记录 |
| `docs/superpowers/plans/` | 分期实现计划（P0–P7） |
| `docs/agents/domain.md` | 领域术语 |
| `CLAUDE.md` | Agent 指令 |
