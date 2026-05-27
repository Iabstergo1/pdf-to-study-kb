# PDF to Study KB

本项目把一篇 PDF 通过固定流水线转换成本地 Obsidian 风格学习知识库。公开仓库只保留 **Claude Code 任务队列模式**：Python 脚本负责 PDF 盘点、切片、任务包、校验、发布和 Obsidian 索引；Claude Code 负责按任务包生成和审校每个小节的学习讲义。

仓库中的“博弈论白皮书”只是本地验证用示例，不作为公开数据提交。公开仓库默认只提交代码、文档、模板、schema、Claude Code skills 和测试；原始 PDF、个人配置、任务中间产物、LLM 输出和生成后的知识库都留在本地。

## 快速开始

### 1. 克隆项目

```powershell
git clone <your-repo-url>
cd pdf-to-study-kb
```

### 2. 准备 Python

建议使用 Python 3.11 或更高版本。不要在仓库中写入个人电脑上的 Python 绝对路径。

```powershell
python -m pip install -r requirements.txt
```

如需在本机记录命令别名，可复制 `.env.example` 到 `.env`，但 `.env` 默认不会提交。

```powershell
Copy-Item .env.example .env
```

### 3. 准备 Claude Code

在本机安装并登录 Claude Code，使用你自己的订阅账号或 Claude Code 支持的付费模型配置。密钥、账号 token 和本机路径都不要写进本仓库。

确认项目内的 skills 存在：

```text
.claude/skills/section-lesson-authoring/SKILL.md
.claude/skills/section-lesson-review/SKILL.md
```

## 用自己的 PDF 生成知识库

下面以 `my-book` 为书籍 ID。书籍 ID 会成为 `books/my-book/` 目录名，建议只用英文、数字、短横线或下划线。

### 1. 初始化书籍目录

```powershell
python scripts/pipeline.py init-book --book my-book --pdf "C:\path\to\my.pdf" --title "我的 PDF 标题"
```

这一步会复制 PDF 到本地工作区：

```text
books/my-book/input/
books/my-book/config/
books/my-book/pipeline-workspace/
books/my-book/study-kb/
```

### 2. 盘点 PDF 并生成 manifest

先只读预览：

```powershell
python scripts/pipeline.py inventory --book my-book
```

确认目录结构可用后写入 manifest：

```powershell
python scripts/pipeline.py inventory --book my-book --write
```

如果 PDF 没有可用目录，或自动识别的小节边界不正确，需要手动编辑：

```text
books/my-book/config/section-manifest.yaml
```

manifest 至少要包含每个小节的 `id`、`title`、`source_order`、`source_locator.pages` 和 `status`。

### 3. 批量抽取 source-slice

```powershell
python scripts/pipeline.py extract --book my-book --all
```

每个小节会生成：

```text
books/my-book/pipeline-workspace/staging/<section-id>/source-slice.md
```

### 4. 生成 Claude Code 任务队列

```powershell
python scripts/pipeline.py make-tasks --book my-book --all-registered
```

或使用全书编排入口生成队列和 Obsidian 索引：

```powershell
python scripts/pipeline.py run-book --book my-book --executor claude-code-queue
```

任务包位于：

```text
books/my-book/pipeline-workspace/tasks/
```

每个小节有两个任务包：

```text
<section-id>_author.json
<section-id>_review.json
```

### 5. 让 Claude Code 批量执行任务

在 Claude Code 中打开项目根目录，发送类似指令：

```text
请读取 books/my-book/pipeline-workspace/tasks/ 下的任务包，按 source_order 顺序处理所有小节。
每个小节先执行 *_author.json，使用 section-lesson-authoring skill 生成 section-lesson-draft.md。
再执行 *_review.json，使用 section-lesson-review skill 生成 review-report.md 和 review-decision.yaml。
只写任务包指定的输出路径，不要直接写 study-kb。
```

Claude Code 生成的文件应位于：

```text
books/my-book/pipeline-workspace/staging/<section-id>/section-lesson-draft.md
books/my-book/pipeline-workspace/reviews/<section-id>/review-report.md
books/my-book/pipeline-workspace/reviews/<section-id>/review-decision.yaml
```

### 6. 标记已审校小节

```powershell
python scripts/pipeline.py mark-reviewed --book my-book --all-accepted
```

该命令只会把同时满足以下条件的小节标记为 `reviewed`：

- `review-decision.yaml` 存在
- `decision: accept`
- `required_fixes` 为空
- `section-lesson-draft.md` 存在
- draft 通过结构校验

### 7. 发布到 Obsidian 知识库

```powershell
python scripts/pipeline.py publish --book my-book --all-reviewed
python scripts/pipeline.py run-book --book my-book --executor claude-code-queue
```

发布后的学习讲义在：

```text
books/my-book/study-kb/Section-Lessons/
```

`run-book` 会更新：

```text
books/my-book/study-kb/Home.md
books/my-book/study-kb/Learning-Maps/
books/my-book/study-kb/Source-QA/
```

## Obsidian 阅读方式

1. 打开 Obsidian。
2. 选择 `Open folder as vault`。
3. 选择 `books/my-book/study-kb/`。
4. 从 `Home.md` 开始阅读。

主要目录：

- `Home.md`：知识库入口和发布进度。
- `Learning-Maps/`：全书学习地图、入门路线、难点与推导路线。
- `Section-Lessons/`：已经发布的小节讲义。
- `Source-QA/`：小节覆盖报告和高风险内容清单。

## 常用命令

```powershell
python scripts/pipeline.py --help
python scripts/pipeline.py <command> --help
```

| 命令 | 作用 |
| --- | --- |
| `init-book` | 从一个 PDF 初始化本地书籍工作区。 |
| `inventory` | 分析 PDF 目录结构，生成盘点报告；加 `--write` 后写入 manifest。 |
| `extract` | 根据 manifest 页码范围生成每个小节的 `source-slice.md`。 |
| `make-tasks` | 为 Claude Code 生成 author/review 任务包。 |
| `validate` | 校验小节讲义是否满足模板、frontmatter 和章节门禁。 |
| `mark-reviewed` | 将 review 通过且 draft 校验通过的小节标记为 `reviewed`。 |
| `publish` | 将 `reviewed` 小节发布到 `study-kb/Section-Lessons/`。 |
| `coverage` | 汇总章节覆盖率和发布进度。 |
| `status` | 查看总小节数、已发布数量和状态分布。 |
| `run-book` | 全书级编排入口；当前只支持 `--executor claude-code-queue`。 |

## 目录含义

```text
pdf-to-study-kb/
├── README.md                 # 项目使用说明
├── CLAUDE.md                 # Claude Code 项目协作规则
├── .claude/                  # Claude Code skills 和项目设置
├── docs/                     # 阶段计划、设计文档、工作流说明
├── schemas/                  # manifest 和小节讲义 JSON schema
├── templates/                # 小节讲义和 review 报告模板
├── scripts/                  # 流水线 CLI 与实现代码
├── tests/                    # pytest 与手动 smoke 测试
├── requirements.txt          # Python 依赖
└── books/                    # 本地书籍工作区；公开仓库只保留 .gitkeep
```

单本书籍目录：

```text
books/<book-id>/
├── input/                    # 原始 PDF，本地资产，不提交
├── config/                   # book-profile、study-profile、section-manifest 等配置
├── pipeline-workspace/       # source-slice、任务包、review、运行状态等中间产物，不提交
└── study-kb/                 # Obsidian vault 输出，不提交
```

## 本地配置

公开仓库不需要模型接口配置。Claude Code 的登录、订阅和模型设置由你的本机 Claude Code 环境负责。

`.env.example` 只提供本地命令占位，不含密钥：

```text
PDF_TO_STUDY_KB_PYTHON=python
CLAUDE_CODE_COMMAND=claude
```

本机可创建 `.env` 记录自己的值；不要提交 `.env`。

## 公开仓库提交范围

建议提交：

- `.claude/`
- `CLAUDE.md`
- `README.md`
- `docs/`
- `schemas/`
- `scripts/`
- `templates/`
- `tests/`
- `requirements.txt`
- `.gitignore`
- `.env.example`
- `books/.gitkeep`

默认不提交：

- `.env`、`.env.*`
- `books/*/input/*.pdf`
- `books/*/config/personal-context.yaml`
- `books/*/pipeline-workspace/`
- `books/*/study-kb/`
- Python 缓存、pytest 缓存、临时目录

## 本地验证

公开仓库不包含示例 PDF，所以真实书籍 smoke 在示例不存在时会自动跳过。

```powershell
python -m pytest tests/test_manual.py -q
python tests/test_manual.py
python scripts/pipeline.py --help
python scripts/pipeline.py run-book --help
```

## 发布到 GitHub

首次提交前检查没有本机路径、密钥和 PDF：

```powershell
git status --short
git diff --cached --stat
git commit -m "Initial Claude Code PDF to Study KB pipeline"
```

创建公开 GitHub 仓库后再添加远程并推送：

```powershell
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```
