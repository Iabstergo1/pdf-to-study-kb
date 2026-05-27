# 阶段 3B：PDF 初始化盘点与批量切片计划

## 目标

补齐 PDF → study-kb MVP 前半段自动化：从一本 PDF 自动建立 book 目录、生成结构清单、批量生成 source-slice，为后续 Claude Code author/review 任务包提供输入。

**不做**：不生成新讲义内容、不发布新小节、不运行 git init。

## 当前状态

- Stage 3A 已通过 Codex 复审
- `scripts/pipeline.py` 已支持：status / validate / coverage / publish / make-tasks
- 博弈论白皮书：total_sections=82, published=3, registered=79
- **缺口**：init-book / inventory / extract 三个命令

## 新增命令设计

### 1. init-book

**用途**：从零初始化一本书的完整目录结构和最小配置文件。

**输入**：
```
python scripts/pipeline.py init-book --book <book-id> --pdf <pdf-path> --title <title> [--force]
```

**参数**：
- `--book`：book 目录名（如 `博弈论白皮书`）
- `--pdf`：原始 PDF 文件路径
- `--title`：书籍标题
- `--force`：覆盖已有 book（默认拒绝覆盖）

**输出目录结构**：
```
books/<book-id>/
├── input/                          # 原始 PDF 存放
│   └── <pdf-filename>
├── config/
│   ├── book-profile.yaml           # 最小 book 元信息
│   ├── study-profile.yaml          # 最小学习配置
│   └── personal-context.yaml       # 最小个人桥接配置
├── pipeline-workspace/
│   ├── reports/                    # 分析报告
│   ├── staging/                    # 中间产物
│   ├── reviews/                    # 审校产物
│   └── tasks/                      # 任务包
└── study-kb/
    ├── Section-Lessons/            # 最终讲义
    ├── Learning-Maps/              # 学习地图
    └── Source-QA/                  # 质量报告
```

**生成的配置文件内容**（最小模板，用户后续手动补充）：
- `book-profile.yaml`：book_id, title, source_type: pdf, language: zh
- `study-profile.yaml`：lesson_style.density: medium, importance_levels: [A, B, C]
- `personal-context.yaml`：bridge_policy 默认值

**安全约束**：
- 不覆盖已有 book 目录，除非 `--force`
- PDF 路径必须存在
- book-id 用作目录名，需合法

### 2. inventory

**用途**：分析 PDF 结构，生成结构报告；若 PDF 有内置 TOC 则生成初始 section-manifest.yaml。

**输入**：
```
python scripts/pipeline.py inventory --book <book-id> [--write] [--force]
```

**参数**：
- `--book`：book 目录名
- `--write`：显式写入 section-manifest.yaml（默认只做 dry-run 生成报告）
- `--force`：覆盖已有 manifest（默认不覆盖）

**输出**：
```
pipeline-workspace/reports/
├── pdf-structure-raw.json          # pymupdf 原始分析数据
└── pdf-structure-report.md         # 可读结构报告
config/
└── section-manifest.yaml           # 仅在 --write 且 PDF 有 TOC 时生成
```

**逻辑**：
1. 调用 `analyze-pdf.py` 的 `analyze_pdf()` 函数获取 PDF 结构
2. 生成 `pdf-structure-raw.json` 和 `pdf-structure-report.md`
3. 若 PDF 有内置 TOC（`toc_source != 'NO_TOC'`）：
   - 解析 TOC 生成 section 列表，每个 section 带 `part` 字段（从 level 1 TOC 推导）
   - 若 `--write`：写入 `section-manifest.yaml`（`coverage` 通过 `part` 字段或注释块分组）
   - 否则：打印预览，标记 `needs_manual_toc: false`
4. 若 PDF 无内置 TOC：
   - 报告中标记 `needs_manual_toc: true`
   - 不生成 manifest

**对已有 manifest 的保护**：
- 默认 dry-run，不写 manifest
- `--write` 时若 manifest 已存在且无 `--force`，拒绝覆盖
- 当前博弈论白皮书已有完整 manifest，inventory 只更新报告不改 manifest

### 3. extract

**用途**：按 section-manifest.yaml 批量生成 source-slice.md。

**输入**：
```
python scripts/pipeline.py extract --book <book-id> --section <section-id>
python scripts/pipeline.py extract --book <book-id> --all [--force]
```

**参数**：
- `--book`：book 目录名
- `--section`：单个小节 ID
- `--all`：批量处理所有有 pages 的小节
- `--force`：覆盖已有 source-slice（默认不覆盖）

**输出**：
```
pipeline-workspace/staging/<section-id>/source-slice.md
```

**source-slice.md 格式**：
```markdown
---
section_id: GTW-001-01
title: "章节标题"
source_file: "原始PDF文件名"
pages: "3-7"
extraction_mode: page-range
extraction_confidence: low|medium|high
needs_boundary_review: true|false
---

<从 PDF 提取的原始文本>
```

**置信度判定规则**：
- `extraction_confidence: high`：pages 连续，且 start_title/end_title 均在 PDF 文本中找到
- `extraction_confidence: medium`：pages 连续，但未做标题匹配（纯页码切片）
- `extraction_confidence: low`：pages 不连续或跨章节

**边界不确定处理**：
- 若 section 缺少 `source_locator.pages`，记录到 `reports/extraction-failures.md`，不生成文件
- 若无法确定 end_title，标记 `needs_boundary_review: true`
- 不乱切：宁可跳过也不生成错误边界的内容

**安全约束**：
- 不覆盖已有 source-slice.md，除非 `--force`
- 缺 pages 的 section 记录失败，不跳过不报错（批量时）

## 阶段边界

**Stage 3B 只做确定性脚本**：
- init-book：文件系统操作 + YAML 模板生成
- inventory：PDF 解析 + 报告生成（调用 pymupdf）
- extract：PDF 文本提取 + 文件写入（调用 pymupdf）

**不做的事**：
- 不调用 Claude 写讲义（那是 author skill 的事）
- 不生成 section-lesson-draft.md
- 不发布新小节
- 不运行 make-tasks

**Stage 3B 完成后**：
- `make-tasks` 能为已有 source-slice 的小节生成 author/review 任务包
- 用户可以用 `/section-lesson-authoring` 和 `/section-lesson-review` 批量处理

## 实现清单

### 扩展 scripts/pipeline.py

| 命令 | 优先级 | 依赖 |
|------|--------|------|
| init-book | P0 | 无 |
| inventory | P0 | analyze-pdf.py |
| extract | P0 | extract_source_slice.py, manifest |

### 测试计划

tests/test_manual.py 新增：

| 测试 | 验证点 |
|------|--------|
| test_status | 只读 smoke: section 数量正确 |
| test_coverage | 只读 smoke: 章节分组正确 |
| test_validate | 只读 smoke: 已发布讲义通过校验 |
| test_publish | tmp fixture + 只读 smoke: manifest 更新 + 门禁 |
| test_inventory_dryrun_real_book | 只读 smoke: 真实书籍 reports + manifest 零写入巡检 |
| test_init_book | tmp fixture: 创建完整目录结构 + 配置 |
| test_init_book_no_overwrite | tmp fixture: 拒绝覆盖已有 book |
| test_inventory | tmp fixture: 生成报告 + manifest 含 part + coverage 可分组 |
| test_make_tasks | tmp fixture: 生成/跳过/清理 |
| test_extract | tmp fixture: 生成 source-slice 格式正确 |
| test_extract_missing_pages | tmp fixture: 缺 pages 记录失败 |
| test_extract_no_overwrite | tmp fixture: 不覆盖已有 source-slice |
| test_extract_force | tmp fixture: --force 覆盖已有 source-slice |
| test_extract_all_summary | tmp fixture: --all 汇总 created/skipped/failed 正确 |

### 验证命令

```bash
python tests/test_manual.py
python -m pytest tests/test_manual.py -q
python scripts/pipeline.py status --book 博弈论白皮书
python scripts/pipeline.py coverage --book 博弈论白皮书
python scripts/pipeline.py validate --book 博弈论白皮书 --all --stage published
python scripts/pipeline.py inventory --book 博弈论白皮书
python scripts/pipeline.py extract --book 博弈论白皮书 --section GTW-001-01
```

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| inventory 误覆盖已有 manifest | 默认 dry-run，需显式 --write --force |
| extract 边界切错 | 记录 extraction_confidence，标记 needs_boundary_review |
| 中文路径在 pymupdf 中出问题 | 用 pathlib 处理路径，测试覆盖 |
| 已有 3 个 published 小节的 source-slice 被覆盖 | 默认不覆盖，需 --force |
