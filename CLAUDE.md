# PDF to Study KB - Claude Code 项目指令

本项目将长篇 PDF 资料编译为本地 Obsidian 学习知识库。

实现状态：本文描述目标架构。当前代码仍处于从旧 `section` 流程迁移到 semantic unit 流程的阶段，具体实施步骤见 `docs/semantic-pdf-to-obsidian-implementation-guide.md`。

## 项目概述

- **目标**：PDF → 语义化结构知识库（Obsidian 兼容）
- **执行模式**：LangGraph 编排 + SQLite 记忆/观测；Python CLI 负责 book-level 编排，LangGraph StateGraph 负责 unit-level 生成循环
- **公开仓库边界**：不提交原始 PDF、个人配置、SQLite 状态库、中间产物或生成后的知识库

## 核心约束

1. **写入边界**：unit 图只能写自己的 staging 目录和业务 SQLite，不改全局索引
2. **门禁机制**：未通过 evidence 校验和 review 的内容不能进入 study-kb
3. **语义规划驱动**：切分由 LLM 语义规划 + 人工审批，不依赖 Python 正则切片
4. **来源忠实**：区分原文压缩、学习解释和个人桥接；每个核心结论必须有 evidence_id
5. **公式保真**：不允许模型凭空补全公式；高公式页用 PyMuPDF 检测风险，缺失公式标记 `[公式缺失]` 并进 Review-Queue

## 架构分层

### Book-level 编排（Python CLI）

```text
profile-pdf → plan-units → validate-unit-plan → [人工审批] → run-book
```

- `profile-pdf`：分析 PDF TOC、页码、文本密度、公式/表格/图片风险
- `plan-units`：LLM 生成 `semantic-unit-plan.candidates.yaml`
- `validate-unit-plan`：校验页码覆盖、重叠、越界、schema
- `review-unit-plan`：人工接受/编辑/合并/拆分/跳过 unit
- `run-book`：读取已审批 plan，逐个 unit invoke LangGraph 图

### Unit-level LangGraph 图（每个 unit 单独 invoke）

```text
prepare_context → generate_note → verify_evidence → review_note
    → [revise_note 循环，最多 3 次] → update_memory → publish_note
```

- `prepare_context`：组装当前 unit PDF 抽取 + rolling summary + 依赖 unit 摘要 + 符号/术语表
- `generate_note`：author 生成讲义草稿
- `verify_evidence`：自动校验公式/证据覆盖
- `review_note`：reviewer 审校，必须输出证据对照表和公式风险清单
- `revise_note`：条件循环，最多 3 次
- `update_memory`：更新 running summary、概念索引、符号表、证据账本；超限触发 compaction
- `publish_note`：写入 staging，准备发布

### Run 结束后聚合（Python）

- `build_obsidian_indexes`：重建 Home、MOC、Coverage、Risk、Dashboard、Review-Queue

## 双 SQLite 分工

| 数据库 | 路径 | 用途 |
|--------|------|------|
| LangGraph checkpointer | `pipeline-workspace/checkpoints/langgraph.sqlite` | unit 图状态恢复（断点续跑） |
| 业务数据库 | `pipeline-workspace/state/study-kb.sqlite` | runs, units, model_calls, tokens, cost, errors, memory_snapshots, evidence_ledger |

## 模型分工

| 任务 | 默认模型 | 说明 |
|------|----------|------|
| 语义规划 (`plan-units`) | DeepSeek V4 Flash | Flash 够用且便宜 |
| 讲义生成 (`generate_note`) | DeepSeek V4 Pro | 复杂生成任务用 Pro |
| 审校 (`review_note`) | DeepSeek V4 Flash | 审校对推理要求较低 |
| 公式风险检测 | PyMuPDF（本地） | 符号/空白变量启发式检测 |
| 高公式页 OCR | surya-ocr（本地，可选依赖） | 支持中文 + LaTeX 公式；Surya 2 需要 vllm 或 llama.cpp 后端，未安装或后端不可用时走人工处理 |

注意：`deepseek-chat` / `deepseek-reasoner` 将于 2026/07/24 停用，需切换到 `deepseek-v4-flash` / `deepseek-v4-pro`。

## PDF 提取策略

第一版默认使用本地 PyMuPDF + surya-ocr。surya-ocr 是可选依赖；未安装或推理后端不可用时，高公式页进入 Review-Queue 人工处理，不要求模型支持图片输入。

| extraction_method | 适用场景 | 实现 |
|-------------------|----------|------|
| `text` | 纯文本页、低公式页 | PyMuPDF `get_text("dict")` |
| `screenshot_ocr` | 高公式页、空白变量页 | PyMuPDF 截图 → surya-ocr 识别（LaTeX 公式输出） |
| `hybrid` | 混合页 | 普通段落用文本，公式/表格区域用 surya-ocr |

surya-ocr 特点：
- 支持中文（82.5%）和英文（92.3%），90+ 语言
- 公式以 `<math>...</math>` 标签返回，KaTeX 兼容 LaTeX
- CPU / Apple Silicon 需要 llama.cpp `llama-server` 后端；NVIDIA GPU 路径使用 vllm
- 个人/研究使用免费

Fallback 策略：
- surya-ocr 未安装或 vllm/llama.cpp 后端不可用：高公式页标记 `formula_risk=high`，进 Review-Queue 人工处理
- 已安装但 OCR 失败：重试一次，仍失败则阻断进 Review-Queue/
- 文本/OCR 冲突：OCR 优先，标记 conflict 让 review 人工确认

## Rolling Memory

每个 unit 生成后更新，作为下一个 unit 的上下文：

```yaml
running_book_summary: string          # 纯文本，compaction 目标
concept_index: {term: {definition, first_unit, units}}
symbol_index: {symbol: {meaning, first_unit, units}}
evidence_ledger: [{evidence_id, claim, unit_id, page, source_heading, evidence_type}]
recent_accepted: [unit_summary]       # 固定保留最近 2 个
```

超过 `memory_compact_char_limit=20000` 时只 compact `running_book_summary`，不压缩结构化索引。

## 成本控制

- per-unit 上限：`max_unit_input_tokens`、`max_unit_output_tokens`
- per-book 上限：`max_book_tokens`、`max_book_cost`
- 超 unit 上限：该 unit 暂停进 Review-Queue/
- 超 book 上限：整本暂停，等人工确认

## Windows 工具选择

Claude Code 的 Bash 工具底层是 Git Bash (MSYS2)，处理含中文的 Windows 路径时会崩溃。

1. **优先用原生工具**：Glob、Grep、Read、Edit —— 不经过 Bash，无路径问题。
2. **需要执行命令时**：直接调用 `pwsh`（PowerShell 7），不要通过 Git Bash 调用 PowerShell。
3. **禁止**：不要用 Bash 工具执行 `powershell -Command "..."` 或 `Select-String` 等 PowerShell 命令。

## 目录约定

```text
books/<book-id>/
├── input/                         # 原始 PDF
├── config/                        # book-profile, study-profile, semantic-unit-plan
├── pipeline-workspace/
│   ├── staging/<unit-id>/         # unit 生成产物
│   ├── reviews/<unit-id>/         # 审校产物
│   ├── runs/<run-id>/             # 运行状态
│   ├── checkpoints/               # LangGraph checkpointer SQLite
│   ├── state/                     # 业务 SQLite
│   └── reports/                   # 规划报告、校验报告
└── study-kb/                      # 最终 Obsidian vault
    ├── Home.md
    ├── Section-Lessons/
    ├── Concept-Cards/
    ├── Glossary/
    ├── Symbols/
    ├── Formula-Ledger/
    ├── Claims/
    ├── Questions/
    ├── Review-Queue/
    ├── Learning-Maps/
    ├── Source-QA/
    └── Dashboards/
```

## Obsidian 产物

| 产物 | 说明 |
|------|------|
| Section-Lessons | 每个 unit 一篇学习讲义 |
| Concept-Cards | 核心概念独立卡片 |
| Glossary | 术语表，带定义和来源页码 |
| Symbols | 符号表，带含义和首次出现 unit |
| Formula-Ledger | 公式账本，带来源页码和一致性标记 |
| Claims | 核心结论 → 证据对照 |
| Questions | 每 unit 自测问题 |
| Review-Queue | 待人工复核的高风险笔记 |
| Learning-Maps | 全书地图、最短路线、难点路线 |
| Source-QA | 覆盖报告、高风险清单 |
| Dashboards | 综合看板（进度、风险、质量分） |

所有生成笔记 frontmatter 包含 Dataview 友好字段：`type`, `unit_id`, `chapter`, `difficulty`, `formula_risk`, `status`, `concepts`, `symbols`, `depends_on`, `source_pdf`, `source_pages`, `risk_flags`, `managed_by: pipeline`。

## 报告写入约定

执行报告、修复报告、审阅报告必须写入项目文件（如 `pipeline-workspace/reports/`），不在对话中复制大段输出。对话中只说一句指引用户读本地文件。

## 旧代码

旧的 `extract`、`source-slice`、Claude Code 队列相关代码已归档到 `scripts/legacy/`。主流程使用新的语义单元规划 + LangGraph 路径。

## Agent skills

### Issue tracker

Work is tracked in GitHub Issues for `Iabstergo1/pdf-to-study-kb`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default triage label vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain layout. Migration context is anchored by `docs/semantic-pdf-to-obsidian-implementation-guide.md`; future domain terms should live in `CONTEXT.md` and ADRs in `docs/adr/`. See `docs/agents/domain.md`.
