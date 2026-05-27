# PDF to Study KB - Claude Code 项目指令

本项目使用 Claude Code 将长篇 PDF 资料编译为本地 Obsidian 学习知识库。

## 项目概述

- **目标**：PDF -> 结构化 Markdown 学习知识库（Obsidian 兼容）
- **执行模式**：只使用 Claude Code 任务队列；Python 脚本负责确定性流水线
- **公开仓库边界**：不提交原始 PDF、个人配置、中间产物或生成后的知识库

## 核心约束

1. **写入边界**：Worker 只能写自己的 staging 目录，不改全局索引
2. **门禁机制**：未 reviewed 内容不能进入 study-kb
3. **manifest 驱动**：任务状态必须持久化，不依赖会话记忆
4. **来源忠实**：区分原文压缩、学习解释和个人桥接

## Windows 工具选择

Claude Code 的 Bash 工具底层是 Git Bash (MSYS2)，处理含中文的 Windows 路径时会崩溃（`fatal error - add_item`，Exit code 5）。

1. **优先用原生工具**：Glob（文件搜索）、Grep（内容搜索）、Read（读取）、Edit（编辑）——不经过 Bash，无路径问题。
2. **需要执行命令时**：直接调用 `pwsh`（PowerShell 7），不要通过 Git Bash 调用 PowerShell。
3. **禁止**：不要用 Bash 工具执行 `powershell -Command "..."` 或 `Select-String` 等 PowerShell 命令。

## 目录约定

- `books/<book-id>/input/`：原始 PDF
- `books/<book-id>/config/`：配置文件（book-profile, study-profile, manifest）
- `books/<book-id>/pipeline-workspace/`：中间产物（staging, reviews）
- `books/<book-id>/study-kb/`：最终阅读产物

## Claude Code 队列约定

1. 任务包位于 `books/<book-id>/pipeline-workspace/tasks/`。
2. 每个小节先执行 `<section-id>_author.json`，再执行 `<section-id>_review.json`。
3. author 使用 `section-lesson-authoring` skill，只写 `section-lesson-draft.md`。
4. review 使用 `section-lesson-review` skill，只写 `review-report.md` 和 `review-decision.yaml`。
5. 不要直接写入 `study-kb/`；发布必须通过 `pipeline.py publish`。

## Agent 角色

- **orchestrator**：读配置、推进阶段、派发任务
- **pdf-ingest-agent**：解析来源、生成页码与质量报告
- **section-planner**：生成小节任务 manifest
- **section-worker**：为单小节写学习讲义草稿
- **section-reviewer**：审校单小节讲义
- **integrator**：合并已通过门禁的产物

## 流水线阶段

```text
Stage 0 资料登记 → Stage 1 解析 → Stage 2 切分 → Stage 3 并行生成
→ Stage 4 审校 → Stage 5 概念桥接 → Stage 6 索引合并 → Stage 7 校验导出
```

## 报告写入约定

执行报告、修复报告、审阅报告必须写入项目文件（如 `reviews/<section-id>/fix-report-<topic>.md`），不在对话中复制大段输出。对话中只说一句指引用户读本地文件。

## 参考

- [框架设计文档](docs/长篇资料本地学习知识库框架设计.md)
