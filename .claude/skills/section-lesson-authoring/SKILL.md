---
description: 为单个语义单元生成学习讲义草稿。当 LangGraph generate_note 节点需要生成讲义时使用。
---

# section-lesson-authoring

## 触发条件

当 unit-level LangGraph 图的 `generate_note` 节点需要为一个语义单元生成学习讲义时使用此 skill。

## 输入

- 语义单元计划条目（unit_id, title, unit_type, source_scope.pages, source_scope.headings, extraction_method, formula_risk, depends_on）
- PDF 内容（由 `prepare_context` 节点根据 extraction_method 组装：文本块或截图 OCR 结果）
- rolling memory 摘要（running_book_summary、最近 2 个 accepted unit 摘要）
- 相关术语/符号/公式索引（concept_index、symbol_index 中与本 unit 相关的条目）

## 输出

- 符合 `templates/section-lesson.template.md` 结构的 Markdown 讲义草稿
- 文件保存至 `books/<book-id>/pipeline-workspace/staging/<unit_id>/section-lesson-draft.md`

## 核心规则

### 1. 目标是学习讲义，不是摘要

讲义必须帮助读者：
- 快速定位本节要解决的问题
- 区分必须掌握、首遍可略读和需要回原文精读的内容
- 理解核心概念、直觉、模型结构或推导骨架
- 在需要时准确回到原文页码

### 2. 内容分层

每篇讲义必须明确区分三种内容：

| 类型 | 标记方式 | 说明 |
|------|----------|------|
| 来源忠实压缩 | 无特殊标记 | 直接反映原文内容 |
| 学习解释补充 | `[学习补充]` 前缀 | 为学习而做的额外解释 |
| 个人桥接候选 | 放入"与个人知识体系的连接候选"章节 | 可能值得桥接到个人知识库的内容 |

### 3. 公式与符号保真

- **禁止凭空补全公式**：如果 PDF 抽取结果中某个变量/公式缺失，必须在讲义中标注 `[公式缺失：原文第X页未提取到此公式]`，而不是自行补全
- 高公式风险 unit 的每个公式必须注明来源页码
- 符号表必须与 rolling memory 的 symbol_index 一致；新符号必须声明

### 4. 利用上下文

- 如果 rolling memory 中已有相关概念/符号的定义，引用而非重复定义
- 如果 depends_on 中的 unit 已生成，确保术语和推导与之一致
- 适当使用 running_book_summary 做前后文衔接

### 5. 必备章节

讲义必须包含以下章节（按顺序）：

1. `## 学习定位` - 本节解决什么问题，在全书中的位置
2. `## 先记住的结论` - 最重要的 2-3 个结论
3. `## 必须掌握` - 首次阅读必须理解的核心内容
4. `## 首遍可略读` - 可以快速跳过的内容
5. `## 核心概念` - 本节引入或重点使用的关键概念
6. `## 模型结构、论证骨架或推导骨架` - 逻辑主线
7. `## 直觉解释` - 帮助建立直觉的类比或通俗解释
8. `## 容易误解的点` - 常见误解、易混淆概念
9. `## 与个人知识体系的连接候选` - 可能值得桥接的内容
10. `## 自测问题` - 3-5 个检验理解的问题
11. `## 何时回原文` - 什么情况下应回到原始 PDF
12. `## 原文定位` - 本讲义对应的原文页码和章节

### 6. Frontmatter 必填字段

```yaml
---
id: <unit_id>               # 如 GTW-002-01
type: section-lesson
source_title: <资料标题>
source_locator:
  pages: [<页码列表>]
  headings: [<原文章节标题>]
book_order: "<章节序号>"      # 如 2.3
chapter: "<所属章节>"
importance: <A|B|C>
difficulty: <1-5>
formula_risk: <low|medium|high>
review_status: draft
generation_stage: draft
status: draft
concepts: [<概念列表>]
symbols: [<符号列表>]
depends_on: [<依赖unit_id>]
source_pdf: <PDF文件名>
source_pages: [<页码>]
risk_flags: [<风险标记>]
managed_by: pipeline
---
```

### 7. 禁止事项

- 不得直接写入 `study-kb/` 目录
- 不得在讲义中混入审校意见
- 不得凭空扩展概念（必须有原文依据）
- 不得凭空补全缺失的公式或变量定义
- 不得省略"何时回原文"章节

## 参考文件

- `templates/section-lesson.template.md` - 讲义模板
- `schemas/section-lesson.schema.json` - 结构契约参考
- `books/<book-id>/config/semantic-unit-plan.yaml` - 语义单元规划
