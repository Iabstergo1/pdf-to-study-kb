# PDF to Study KB

把一本 PDF 变成 Obsidian 知识库：每个小节自动生成结构化学习讲义，带阅读地图、自测问题和回原文指引。

> `books/` 是本地书籍工作区，默认不提交。只保留原始 PDF 即可；运行 `init-book` 后会重新生成配置、中间产物和最终知识库。

## 它能做什么

输入一本 PDF，输出一个本地 Obsidian vault：

```
books/my-book/study-kb/
├── Home.md                    # 入口：发布进度、阅读路线
├── Section-Lessons/           # 每小节一篇学习讲义
│   ├── GTW-001-01.md
│   ├── GTW-001-02.md
│   └── ...
├── Learning-Maps/             # 多条阅读路线
│   ├── MOC-全书学习地图.md
│   ├── MOC-入门最短路线.md
│   └── MOC-难点与推导重点路线.md
└── Source-QA/                 # 覆盖率报告、高风险清单
```

每篇讲义包含 12 个固定章节：学习定位、先记住的结论、必须掌握、首遍可略读、核心概念、模型/论证骨架、直觉解释、容易误解的点、个人知识桥接候选、自测问题、何时回原文、原文定位。

**示例讲义片段**（来自博弈论白皮书 1.1.1 节）：

```markdown
## 先记住的结论

1. "散养"不是缺陷，而是高权限游戏模式
2. 自由的代价是绝对责任
3. 研究生涯的真正起点，是你主动接管这份自由的那一刻

## 核心概念

| 概念 | 含义 | 来源标记 |
|------|------|----------|
| 散养 | 导师沟通频率低、研究路径需自主规划的状态 | 原文 |
| 策略空间 | 给定约束下所有可能策略的集合 | 原文引入 |
```

## 适用场景

- 学术论文、教材、技术白皮书等**结构化 PDF**
- 内容以文字为主（扫描件/纯图片 PDF 暂不支持）
- 想在 Obsidian 中按主题、难度、重要性多路线阅读，而非线性翻 PDF

## 前置条件

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 用于 PDF 解析、任务生成、发布流水线 |
| OpenAI-compatible API key | 用于 LangGraph worker 执行 author/review/revise；默认示例使用 DeepSeek，也可替换为其他兼容接口 |
| Claude Code（可选） | 兼容旧的队列执行方式 |
| Obsidian（可选） | 用于阅读生成的知识库 |

## 快速开始：用你自己的 PDF 生成知识库

下面以 `my-book` 为书籍 ID。书籍 ID 会成为目录名，建议只用英文、数字、短横线。

注意：Python CLI 负责初始化、盘点、切片、执行 LangGraph worker 和发布；讲义正文的 author/review/revise 由 OpenAI-compatible 模型 API 执行。

### 1. 安装

```powershell
git clone <your-repo-url>
cd pdf-to-study-kb
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填入本地 API 配置：

```powershell
copy .env.example .env
```

`.env` 不会提交到仓库。默认示例使用 DeepSeek；任何 OpenAI-compatible API 都可以按相同字段配置。

### 2. 初始化书籍目录

```powershell
python scripts/pipeline.py init-book --book my-book --pdf "C:\path\to\my.pdf" --title "我的PDF标题"
```

这会在 `books/my-book/` 下创建完整的工作区结构，并把 PDF 复制到 `input/` 目录。同时生成三份配置文件：

| 文件 | 作用 | 是否需要手动改 |
|------|------|----------------|
| `config/book-profile.yaml` | 书籍元信息（标题、领域、语言） | 一般不用改 |
| `config/study-profile.yaml` | 讲义风格（密度、必含章节、阅读路线） | 可按需调整 |
| `config/personal-context.yaml` | 个人知识桥接方向（你的关注领域） | **建议按自己情况填写** |

`personal-context.yaml` 决定了讲义中"与个人知识体系的连接候选"章节的内容质量。不填也能跑，但填了之后桥接内容会更有针对性。

### 3. 盘点 PDF 结构

先预览，确认自动识别的目录结构是否正确：

```powershell
python scripts/pipeline.py inventory --book my-book
```

如果没问题，写入 manifest：

```powershell
python scripts/pipeline.py inventory --book my-book --write
```

如果 PDF 没有可用目录、或自动识别的小节边界不对，手动编辑 `books/my-book/config/section-manifest.yaml`。

也可以先生成候选拆分和边界，再只审核低置信度项：

```powershell
python scripts/pipeline.py plan-sections --book my-book --planner hybrid-llm --force
python scripts/pipeline.py review-sections --book my-book --list
python scripts/pipeline.py review-sections --book my-book
python scripts/pipeline.py apply-section-plan --book my-book --force
```

`plan-sections --planner hybrid-llm` 会先生成确定性候选，再调用 LLM 判断是否 keep、adjust-boundary、merge、split 或 needs_human_review。产物仍是 `section-manifest.candidates.yaml`、`source-boundary-hints.candidates.yaml` 和规划报告；`review-sections` 用于交互式接受或修改边界；`apply-section-plan` 才会写入正式配置。

### 4. 生成原文切片

```powershell
python scripts/pipeline.py extract --book my-book --all
```

`extract` 按 manifest 中的页码范围从 PDF 中提取原文片段（`source-slice.md`）。

如果某本书存在 `config/source-boundary-hints.yaml`，`extract` 会优先按其中的标题边界裁剪。通常先通过 `plan-sections`、`review-sections`、`apply-section-plan` 生成和审核这些边界提示。

### 5. 让 LangGraph worker 执行任务

```powershell
python scripts/pipeline.py run-book --book my-book --executor langgraph-worker --batch-size 3
```

`langgraph-worker` 会逐节执行：

- `load_section`：读取 `source-slice.md` 并做长度、边界风险检查
- `author`：调用默认模型生成 `section-lesson-draft.md`
- `validate`：复用本地结构校验
- `review`：调用 review 模型生成 `review-decision.yaml` 和 `review-report.md`
- `revise`：对 revise 决策自动修订，直到通过或超过重试上限

严格门禁会暂停以下小节并写入 checkpoint：source-slice 过短、低置信度、reject、输出不可解析或超过修订上限。checkpoint 位于 `pipeline-workspace/runs/<run-id>/langgraph-checkpoints/`。

如仍想使用 Claude Code 手动队列，可运行：

```powershell
python scripts/pipeline.py run-book --book my-book --executor claude-code-queue --batch-size 5
```

它会生成：

- `pipeline-workspace/tasks/` 下的 author/review 任务包
- `pipeline-workspace/runs/<run-id>/claude-code-queue.json`
- `pipeline-workspace/runs/<run-id>/batches/batch-*.json`
- `pipeline-workspace/runs/<run-id>/automation-readiness.md`

如果 readiness 报告中出现阻塞小节，先修复 source-slice、manifest 或状态问题，再继续无人值守执行。

### 5B. 让 Claude Code 执行任务（可选兼容路径）

在 Claude Code 中打开项目根目录，发送：

```
请读取 books/my-book/pipeline-workspace/runs/<run-id>/claude-code-queue.json，
按 batch 顺序处理所有小节。
每个小节先执行 author_task（使用 section-lesson-authoring skill），
再执行 review_task（使用 section-lesson-review skill）。
```

Claude Code 会逐个读取任务包，调用对应的 skill 生成讲义草稿并审校。生成的文件在 `pipeline-workspace/staging/` 和 `pipeline-workspace/reviews/` 下。

这一步是最耗时的（每小节需要两次 LLM 调用）。`batch-*.json` 用于分批执行或中断后恢复。

### 6. 审校通过后发布

```powershell
python scripts/pipeline.py mark-reviewed --book my-book --all-accepted
python scripts/pipeline.py publish --book my-book --all-reviewed
python scripts/pipeline.py run-book --book my-book --executor langgraph-worker --resume
```

`mark-reviewed` 只会标记同时满足以下条件的小节：review-decision.yaml 存在、decision 为 accept、无 required_fixes、draft 存在且通过结构校验。

`publish` 将已审校的小节复制到 `study-kb/Section-Lessons/`。`run-book --resume` 会同步最新 manifest 状态，更新可恢复状态、阅读地图和覆盖率报告。若走 Claude Code 队列路径，最后一条命令也可继续使用 `--executor claude-code-queue --resume`。

### 7. 在 Obsidian 中阅读

打开 Obsidian → `Open folder as vault` → 选择 `books/my-book/study-kb/` → 从 `Home.md` 开始。

## Claude Code Skills

Claude Code 队列路径依赖两个自定义 skill 驱动 LLM 生成和审校讲义。skill 文件位于 `.claude/skills/`，Claude Code 会自动加载。LangGraph worker 路径不依赖 Claude Code skill，但复用同一套输出契约。

### section-lesson-authoring

**作用**：为单个小节生成学习讲义草稿。

**输入**：section-manifest 中的小节条目 + PDF 原文片段

**输出**：`pipeline-workspace/staging/<section-id>/section-lesson-draft.md`

**核心设计**：
- 区分三种内容：来源忠实压缩（无标记）、学习解释补充（`[学习补充]` 前缀）、个人桥接候选（放入专门章节）
- 公式风险分级处理：low 正常提取，medium 在"何时回原文"中标注，high 在"必须掌握"中加 `[公式风险]` 标记
- 禁止凭空扩展概念，所有内容必须有原文依据

### section-lesson-review

**作用**：审校讲义草稿的忠实性、可学习性和结构完整性。

**输入**：讲义草稿 + PDF 原文片段 + manifest 条目

**输出**：
- `pipeline-workspace/reviews/<section-id>/review-report.md` — 审校报告
- `pipeline-workspace/reviews/<section-id>/review-decision.yaml` — 决策（accept / revise / reject）

**审校维度**：

| 维度 | 检查内容 |
|------|----------|
| 忠实性 | 核心概念、关键结论、推导骨架是否与原文一致 |
| 可学习性 | 学习定位是否清晰、直觉解释是否易懂、自测问题是否有效 |
| 轻重分级 | importance A/B/C 是否合理，"必须掌握"与"首遍可略读"是否匹配 |
| 回原文条件 | 公式风险标记是否正确，高风险内容是否有回原文提示 |
| 结构完整性 | 12 个必备章节是否齐全，frontmatter 是否完整 |

## CLI 命令参考

```powershell
python scripts/pipeline.py <command> --help
```

| 命令 | 作用 |
|------|------|
| `init-book` | 从 PDF 初始化书籍工作区 |
| `inventory` | 分析 PDF 目录结构，生成 manifest（加 `--write` 写入） |
| `plan-sections` | 基于 manifest/PDF 生成 section 拆分和标题边界候选；`--planner hybrid-llm` 可调用模型语义修正 |
| `review-sections` | 交互式审核中低置信度 section 边界候选 |
| `apply-section-plan` | 将已审核候选应用为正式 manifest 和 boundary hints |
| `extract` | 按 manifest 页码范围生成每个小节的 `source-slice.md` |
| `make-tasks` | 生成 Claude Code author/review 任务包 |
| `validate` | 校验讲义是否满足模板和章节门禁 |
| `mark-reviewed` | 将审校通过的小节标记为 `reviewed` |
| `publish` | 将 `reviewed` 小节发布到 `study-kb/` |
| `run-book` | 全书编排入口；支持 `claude-code-queue` 和 `langgraph-worker` |
| `status` | 查看小节状态分布 |
| `coverage` | 查看章节覆盖率 |

## 项目结构

```text
pdf-to-study-kb/
├── .claude/skills/            # Claude Code 自定义 skills
│   ├── section-lesson-authoring/
│   └── section-lesson-review/
├── scripts/                   # 流水线 CLI 与实现
├── templates/                 # 讲义和审校报告模板
├── schemas/                   # manifest 和讲义 JSON schema
├── tests/                     # 测试
└── books/                     # 本地书籍工作区（不提交）
    └── <book-id>/
        ├── input/             # 原始 PDF
        ├── config/            # book-profile、manifest 等配置
        ├── pipeline-workspace/# 任务包、审校产物等中间状态
        └── study-kb/          # 最终 Obsidian vault 输出
```

## 已知限制与改进方向

### 当前限制

1. **端到端验证仍需完整实跑**：仓库只保留代码、模板和 schema，不内置历史样例输出。不同类型的 PDF 可能遇到未覆盖的问题。
2. **审校门禁还需要实跑校准**：review skill 能检查结构和忠实性，但在高难度内容上的严格度需要通过完整生成结果继续校准。
3. **PDF 切片边界脆弱**：自动切片是整个流水线最脆弱的环节，低置信度边界需要通过 `review-sections` 人工审核后再应用。
4. **不支持扫描件/图片 PDF**：依赖 PyMuPDF 文本提取，纯图片页面无法处理。
5. **模型执行依赖 API 配置**：`langgraph-worker` 会直接调用 OpenAI-compatible API；没有 `.env` 或 API key 时只能使用 Claude Code 队列路径。
6. **无成本预估**：调用 LLM API 生成全书讲义的成本没有提前估算。
7. **并行执行依赖外部调度**：Python 会生成 batch 清单，但多个 Claude Code 会话或 API worker 的并行调度仍需外部执行器。
8. **无生成物版本管理**：讲义被覆盖后无法回溯到上一版本。

### 改进方向

- **扩大验证范围**：用更多不同类型的 PDF 测试流水线，特别是公式密集型和结构复杂的内容。
- **增强切片鲁棒性**：改进 PDF 切片算法，处理跨页段落、表格、公式块的边界问题。
- **门禁调优**：根据高难度小节的审校结果调整 review skill 的评分标准，确保能真正拦截质量问题。
- **执行器集成**：在现有 batch 队列之上接入 Claude Code 或第三方 API worker，实现真正无人值守的 author/review 执行。
- **批次化并行调度**：多个 Claude Code 会话或 API worker 可按 batch 分工处理，失败小节通过 run-state attempt 计数和 `--max-revision-retry` 控制是否重新入队。
- **成本预估**：在 `make-tasks` 阶段根据小节数量和长度估算 API 调用成本。
- **增量更新**：支持修改单个小节后只重新生成该节，而非全书重跑。
