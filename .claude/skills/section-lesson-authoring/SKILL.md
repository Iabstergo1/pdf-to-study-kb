---
description: 为单个小节生成学习讲义草稿。当需要从 PDF 原文提取内容并生成结构化讲义时使用。
---

# section-lesson-authoring

## 触发条件

当 agent 需要为单个小节生成学习讲义时使用此 skill。

## 输入

- section-manifest 中的一个 section 条目（id, title, source_locator.pages, formula_risk）
- PDF 原文片段（对应页码范围）

## 输出

- 符合 `templates/section-lesson.template.md` 结构的 Markdown 讲义草稿
- 文件保存至 `books/<book-id>/pipeline-workspace/staging/<section-id>/section-lesson-draft.md`

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

### 3. 必备章节

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

### 4. Frontmatter 必填字段

```yaml
---
id: <section-id>           # 如 GTW-002-01
type: section-lesson
source_title: <资料标题>
source_locator:
  pages: [<起始页>, <结束页>]
book_order: "<章节序号>"     # 如 1.2.1
importance: <A|B|C>
difficulty: <1-5>
formula_risk: <low|medium|high>
review_status: draft
generation_stage: draft
---
```

### 5. 公式与图表风险处理

| 风险等级 | 处理方式 |
|----------|----------|
| low | 正常提取，无需特殊标记 |
| medium | 在"何时回原文"中注明需校验的公式 |
| high | 在"必须掌握"中标注 `[公式风险]`，在"何时回原文"中明确回原文条件 |

### 6. 禁止事项

- 不得直接写入 `study-kb/` 目录
- 不得在讲义中混入审校意见
- 不得凭空扩展概念（必须有原文依据）
- 不得省略"何时回原文"章节

## 参考文件

- `templates/section-lesson.template.md` - 讲义模板
- `schemas/section-lesson.schema.json` - 结构契约参考
- `books/<book-id>/config/section-manifest.yaml` - 小节清单
