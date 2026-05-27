# Claude Code 工作流指南

本指南说明如何用 Claude Code 任务队列把一篇 PDF 转成本地 Obsidian 学习知识库。

## 1. 环境准备

```powershell
git clone <repo-url>
cd pdf-to-study-kb
python -m pip install -r requirements.txt
```

在本机安装并登录 Claude Code。模型账号、订阅、token 和本机路径由本机 Claude Code 环境管理，不写入项目文件。

## 2. 先用 Python 建立任务队列

```powershell
python scripts/pipeline.py init-book --book my-book --pdf "C:\path\to\my.pdf" --title "我的 PDF 标题"
python scripts/pipeline.py inventory --book my-book
python scripts/pipeline.py inventory --book my-book --write
python scripts/pipeline.py extract --book my-book --all
python scripts/pipeline.py make-tasks --book my-book --all-registered
```

也可以用全书入口刷新队列和 Obsidian 索引：

```powershell
python scripts/pipeline.py run-book --book my-book --executor claude-code-queue
```

任务包位置：

```text
books/my-book/pipeline-workspace/tasks/
```

## 3. 在 Claude Code 中执行任务

在项目根目录打开 Claude Code，发送：

```text
请读取 books/my-book/pipeline-workspace/tasks/ 下的任务包，按 source_order 顺序处理所有小节。
每个小节先执行 *_author.json，使用 section-lesson-authoring skill 生成 section-lesson-draft.md。
再执行 *_review.json，使用 section-lesson-review skill 生成 review-report.md 和 review-decision.yaml。
只写任务包指定的输出路径，不要直接写 study-kb。
```

Claude Code 应写入：

```text
books/my-book/pipeline-workspace/staging/<section-id>/section-lesson-draft.md
books/my-book/pipeline-workspace/reviews/<section-id>/review-report.md
books/my-book/pipeline-workspace/reviews/<section-id>/review-decision.yaml
```

## 4. 收口发布

```powershell
python scripts/pipeline.py mark-reviewed --book my-book --all-accepted
python scripts/pipeline.py publish --book my-book --all-reviewed
python scripts/pipeline.py run-book --book my-book --executor claude-code-queue
```

`mark-reviewed` 会检查 review 决策、required fixes 和 draft 结构。`publish` 只发布已经 `reviewed` 的小节。

## 5. 阅读

在 Obsidian 中选择 `Open folder as vault`，打开：

```text
books/my-book/study-kb/
```

从 `Home.md` 开始阅读。

## 6. 高风险小节

`formula_risk: high` 或 `formula_risk: medium` 的小节会进入风险清单。发布后仍建议回原文核对公式、图表和推导边界。
