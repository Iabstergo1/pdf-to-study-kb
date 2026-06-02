# PDF to Study KB

把一本 PDF 变成 Obsidian 知识库：语义化切分、人工审批、LangGraph 编排生成、公式/证据验证、完整生态输出。

> `books/` 是本地书籍工作区，默认不提交。只保留原始 PDF 即可；运行 `init-book` 后会重新生成配置、中间产物和最终知识库。

> 实现状态：`game-model-test` 已跑通 LangGraph semantic unit 主流程。详细验证边界、未验证项和 Surya OCR 注意事项见 [执行指导文档](docs/semantic-pdf-to-obsidian-implementation-guide.md)。

## 它能做什么

输入一本 PDF，输出一个本地 Obsidian vault：

```
books/my-book/study-kb/
├── Home.md                    # 入口：发布进度、阅读路线
├── Section-Lessons/           # 每个语义单元一篇学习讲义
│   ├── GTW-001-01.md
│   ├── GTW-001-02.md
│   └── ...
├── Concept-Cards/             # 核心概念独立卡片
├── Glossary/                  # 术语表（定义 + 来源页码）
├── Symbols/                   # 符号表（含义 + 首次出现）
├── Formula-Ledger/            # 公式账本（来源 + 一致性标记）
├── Claims/                    # 核心结论 → 证据对照
├── Questions/                 # 每单元自测问题
├── Review-Queue/              # 待人工复核的高风险笔记
├── Learning-Maps/             # 多条阅读路线
│   ├── MOC-全书学习地图.md
│   ├── MOC-入门最短路线.md
│   └── MOC-难点与推导重点路线.md
├── Source-QA/                 # 覆盖率报告、高风险清单
└── Dashboards/                # 综合看板
```

每篇讲义包含 12 个固定章节：学习定位、先记住的结论、必须掌握、首遍可略读、核心概念、模型/论证骨架、直觉解释、容易误解的点、个人知识桥接候选、自测问题、何时回原文、原文定位。

## 架构概览

两层架构：

- **Book-level Python 编排**：`profile-pdf → plan-units → validate-unit-plan → 人工审批 → run-book`
- **Unit-level LangGraph 图**：每个已审批 unit 单独 invoke，`prepare_context → generate_note → verify_evidence → review_note → [revise] → update_memory → publish_note`

双 SQLite 分工：
- LangGraph checkpointer：unit 图状态恢复（断点续跑）
- 业务 SQLite：观测（model_calls, tokens, cost）、记忆（memory_snapshots）、证据（evidence_ledger）

PDF 提取三档：
- `text`：PyMuPDF 文本抽取（纯文本/低公式页）
- `screenshot_ocr`：PyMuPDF 截图 → surya-ocr 识别（高公式页，支持中文 + LaTeX 公式）
- `hybrid`：混合策略（默认）

模型分工：DeepSeek V4 Flash（规划/审校）+ V4 Pro（讲义生成）+ surya-ocr（高公式页本地 OCR）。

## 适用场景

- 学术论文、教材、技术白皮书等**结构化 PDF**
- 内容以文字为主，可含公式/表格（高公式页自动走 OCR 路径）
- 想在 Obsidian 中按主题、难度、重要性多路线阅读，而非线性翻 PDF

## 前置条件

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 用于 PDF 解析、LangGraph 编排、发布流水线 |
| DeepSeek API key | V4 Flash（规划/审校）+ V4 Pro（讲义生成） |
| surya-ocr（可选） | `requirements.txt` 中已列出 `surya-ocr>=0.20.0`；OCR 还需要 vLLM 或 llama.cpp 推理后端，不安装或后端不可用则高公式页走人工 |
| Obsidian（可选） | 用于阅读生成的知识库 |

## 快速开始

下面以 `my-book` 为书籍 ID。书籍 ID 会成为目录名，建议只用英文、数字、短横线。

### 1. 安装

```powershell
git clone <your-repo-url>
cd pdf-to-study-kb
pip install -r requirements.txt
```

`requirements.txt` 默认包含 surya-ocr。若当前环境不准备处理高公式页，可先注释掉该依赖行再安装；未安装或 OCR 推理后端不可用时，高公式页会进入 `Review-Queue/` 人工处理。

OCR 后端 smoke check：

```powershell
python scripts/surya_smoke.py --book game-model-test --page 1 --keep-alive
```

该命令只识别一页 PDF，只有 Surya 返回 `status=ok` 且识别块数大于 0 时才返回 exit code 0。第一次运行可能需要下载或加载模型，CPU/llama.cpp 路径会很慢；后续整书运行会复用 `pipeline-workspace/ocr-cache/` 中已成功识别的页。

复制 `.env.example` 为 `.env`，填入本地 API 配置：

```powershell
copy .env.example .env
```

`.env` 不会提交到仓库。默认使用 DeepSeek V4 Flash（规划/审校）和 V4 Pro（讲义生成）。任何 OpenAI-compatible API 都可以按相同字段配置。surya-ocr 本地运行，无需 API key，也不要求模型支持图片输入；Surya 2 OCR 需要本地 vLLM 或 llama.cpp 推理后端。Windows/CPU 路径优先使用 llama.cpp 的 `llama-server.exe`，也可以通过 `LLAMA_CPP_BINARY` 显式指定。

### 2. 初始化书籍目录

```powershell
python scripts/pipeline.py init-book --book my-book --pdf "C:\path\to\my.pdf" --title "我的PDF标题"
```

这会在 `books/my-book/` 下创建完整的工作区结构，并把 PDF 复制到 `input/` 目录。同时生成配置文件：

| 文件 | 作用 | 是否需要手动改 |
|------|------|----------------|
| `config/book-profile.yaml` | 书籍元信息（标题、领域、语言） | 一般不用改 |
| `config/study-profile.yaml` | 讲义风格（密度、必含章节、阅读路线） | 可按需调整 |
| `config/personal-context.yaml` | 个人知识桥接方向 | **建议按自己情况填写** |

### 3. 分析 PDF 结构

```powershell
python scripts/pipeline.py profile-pdf --book my-book
```

分析 PDF TOC、页码、文本密度、公式/表格/图片风险，输出到 `pipeline-workspace/reports/`。

### 4. 语义单元规划

```powershell
python scripts/pipeline.py plan-units --book my-book --force
```

用 LLM 生成 `config/semantic-unit-plan.candidates.yaml`。每个单元包含标题、类型、页码范围、提取方式、风险标记、依赖关系。

校验覆盖率：

```powershell
python scripts/pipeline.py validate-unit-plan --book my-book
```

检查缺页、越界、未解释重叠。校验失败会阻断后续执行。

### 5. 人工审批语义单元

```powershell
python scripts/pipeline.py review-unit-plan --book my-book
```

人工接受、编辑、合并、拆分、跳过单元。审批结果写入 `config/semantic-unit-plan.yaml`。

- `include: false` 的引言/过渡类单元不生成讲义，但仍计入页码覆盖
- 可以合并连续章节（需说明理由）
- 可以拆分过大的章节

### 6. 运行全书生成

```powershell
python scripts/pipeline.py run-book --book my-book --executor langgraph-worker
```

读取已审批的 `semantic-unit-plan.yaml`，逐个 unit invoke LangGraph 图：

- `prepare_context`：组装 PDF 内容 + rolling memory + 依赖摘要
- `generate_note`：author 模型生成讲义
- `verify_evidence`：自动校验公式/证据覆盖
- `review_note`：reviewer 模型审校（必须输出证据对照表和公式风险清单）
- `revise_note`：对 revise 决策自动修订（最多 3 次）
- `update_memory`：更新 rolling summary、概念/符号/证据索引

高风险 unit（formula_loss_risk、screenshot_ocr_failed、evidence_missing）会进入 `Review-Queue/`，不得自动发布。

全书完成后自动调用 `build_obsidian_indexes` 重建所有索引和看板。

### 7. 在 Obsidian 中阅读

打开 Obsidian → `Open folder as vault` → 选择 `books/my-book/study-kb/` → 从 `Home.md` 开始。

## CLI 命令参考

```powershell
python scripts/pipeline.py <command> --help
```

| 命令 | 作用 |
|------|------|
| `init-book` | 从 PDF 初始化书籍工作区 |
| `profile-pdf` | 分析 PDF 结构（TOC、页码、风险） |
| `plan-units` | LLM 语义单元规划 |
| `validate-unit-plan` | 校验规划覆盖率和 schema |
| `review-unit-plan` | 人工审批语义单元 |
| `run-book` | 全书编排（LangGraph worker） |
| `publish` | 将审校通过的单元发布到 study-kb/ |
| `status` | 查看单元状态分布 |
| `coverage` | 查看章节覆盖率 |

## 项目结构

```text
pdf-to-study-kb/
├── .claude/skills/            # Claude Code 自定义 skills
│   ├── section-lesson-authoring/
│   └── section-lesson-review/
├── scripts/                   # 流水线 CLI 与实现
│   ├── pipeline.py            # 主 CLI 入口
│   ├── run_book.py            # Book-level 编排
│   ├── langgraph_worker.py    # Unit-level LangGraph 图
│   ├── llm_provider.py        # OpenAI-compatible LLM provider
│   ├── obsidian_output.py     # Obsidian 索引生成
│   ├── validate_section_lesson.py  # 结构校验
│   └── legacy/                # 旧 extract/queue 代码（归档）
├── templates/                 # 讲义和审校报告模板
├── schemas/                   # 语义单元规划和讲义 JSON schema
├── tests/                     # 测试
└── books/                     # 本地书籍工作区（不提交）
    └── <book-id>/
        ├── input/             # 原始 PDF
        ├── config/            # book-profile、semantic-unit-plan 等
        ├── pipeline-workspace/
        │   ├── staging/       # unit 生成产物
        │   ├── reviews/       # 审校产物
        │   ├── runs/          # 运行状态
        │   ├── checkpoints/   # LangGraph checkpointer SQLite
        │   ├── state/         # 业务 SQLite
        │   └── reports/       # 规划/校验报告
        └── study-kb/          # 最终 Obsidian vault
```

## Dataview 兼容

所有生成笔记的 frontmatter 包含统一字段，可直接用 Dataview 查询：

```dataview
TABLE difficulty, formula_risk, status
FROM "Section-Lessons"
WHERE status = "published"
SORT chapter
```

```dataview
LIST
FROM "Section-Lessons"
WHERE contains(concepts, "贝叶斯更新")
```

## 已知限制

1. **surya-ocr 未安装或推理后端不可用时**：高公式页标记 `formula_risk=high` 进 Review-Queue，需人工补充公式后才能发布
2. **DeepSeek 不支持图片输入**：高公式页 OCR 由 surya-ocr 本地完成，不依赖 DeepSeek 的图片能力
3. **surya-ocr 中文准确率约 82.5%**：公式识别以 LaTeX 输出，可能需要 review 阶段人工校验
4. **端到端验证仍需完整实跑**：不同类型的 PDF 可能遇到未覆盖的问题
5. **不支持纯扫描件 PDF**：surya-ocr 对老旧扫描件准确率较低（约 41.8%）
6. **成本预估是估算值**：per-unit/per-book token 上限基于估算，实际成本可能有偏差
7. **DeepSeek 旧模型名称即将停用**：`deepseek-chat`/`deepseek-reasoner` 于 2026/07/24 停用，需使用 `deepseek-v4-flash`/`deepseek-v4-pro`
