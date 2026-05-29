---
description: 审校单元讲义草稿的忠实性、可学习性和证据覆盖。当 LangGraph review_note 节点需要审校时使用。
---

# section-lesson-review

## 触发条件

当 unit-level LangGraph 图的 `review_note` 节点需要审校一篇讲义草稿时使用此 skill。

## 输入

- 讲义草稿文件路径
- PDF 内容（由 prepare_context 组装的本 unit 原文）
- 语义单元计划中的对应条目
- rolling memory 中的概念/符号/证据索引

## 输出

- `books/<book-id>/pipeline-workspace/reviews/<unit_id>/review-report.md` - 审校报告
- `books/<book-id>/pipeline-workspace/reviews/<unit_id>/review-decision.yaml` - 审校决策

## 审校维度

### 1. 忠实性审校

检查项：
- [ ] 核心概念是否准确反映原文
- [ ] 关键结论是否与原文一致
- [ ] 推导骨架是否正确
- [ ] 是否存在凭空扩展的内容
- [ ] 公式/变量是否与原文一致（不允许凭空补全）

评分：PASS / WARN / FAIL

### 2. 可学习性审校

检查项：
- [ ] "学习定位"是否清晰
- [ ] "先记住的结论"是否有锚点作用
- [ ] "直觉解释"是否易懂
- [ ] "自测问题"是否能检验理解

评分：PASS / WARN / FAIL

### 3. 轻重分级审校

检查项：
- [ ] importance (A/B/C) 是否合理
- [ ] "必须掌握"与 importance 是否匹配
- [ ] "首遍可略读"是否确实可略

评分：PASS / WARN / FAIL

### 4. 回原文条件审校

检查项：
- [ ] "何时回原文"是否明确
- [ ] 公式风险是否正确标记
- [ ] 高风险内容是否有回原文提示

评分：PASS / WARN / FAIL

### 5. 结构完整性审校

检查项：
- [ ] 12 个必备章节是否全部存在
- [ ] frontmatter 必填字段是否完整
- [ ] unit_id 是否与规划一致

评分：PASS / FAIL

### 6. 证据覆盖审校（强制）

检查项：
- [ ] 每个核心结论是否有至少一条原文证据
- [ ] 公式/符号/变量定义是否有来源页码
- [ ] 是否存在"原文空白但讲义补全公式"的情况

评分：PASS / WARN / FAIL

## 决策输出格式

审校报告**必须**包含以下两个表格，缺少任一表格时 review 结果强制视为 `reject`：

### 证据对照表

| 讲义结论 | 原文证据片段 | 页码 | 证据类型 |
|----------|-------------|------|----------|
| （每行一个结论） | （原文中的具体语句） | （页码） | 定义/公式/推导/策略/例证 |

### 公式风险清单

| 变量/符号 | 讲义中的表达 | 原文中的表达 | 一致性 |
|----------|-------------|-------------|--------|
| （每个公式变量一行） | （讲义中的写法） | （原文中的写法，或"原文空白"） | 一致/不一致/模型补全 |

## 决策规则

决策按优先级判定：先检查 reject 条件，再检查 revise 条件，最后 accept。

### reject（拒绝）- 最高优先级

条件（满足任一即 reject）：
- 忠实性 FAIL（核心内容与原文不符）
- 结构完整性 FAIL（缺失必备章节或 frontmatter 严重错误）
- 存在严重错误（如概念张冠李戴、推导根本性错误）
- **缺少证据对照表或公式风险清单**
- **发现"原文空白但讲义补全公式"且未标注 `[公式缺失]`**

后续：
- 在 review-report.md 中说明拒绝理由
- 需要重新生成，不进入修订流程

### revise（需修订）- 次优先级

条件（满足任一即 revise）：
- 任意维度存在 FAIL（但不满足 reject 条件）
- WARN 项超过 3 个
- 证据覆盖不完整（部分结论缺少证据）

后续：
- 在 review-report.md 中列出具体问题
- 在 review-decision.yaml 中记录 required_fixes
- 返回给 worker 修订

### accept（通过）- 最低优先级

条件（必须全部满足）：
- 不满足 reject 和 revise 条件
- 所有维度评分均为 PASS 或 WARN
- WARN 项不超过 3 个
- 证据对照表完整，公式风险清单无严重不一致

后续：可进入记忆更新和发布

## review-decision.yaml 格式

```yaml
unit_id: <unit-id>
reviewer: <reviewer-name>
review_date: <YYYY-MM-DD>
decision: <accept|revise|reject>
confidence: <high|medium|low>
scores:
  faithfulness: <PASS|WARN|FAIL>
  learnability: <PASS|WARN|FAIL>
  importance: <PASS|WARN|FAIL>
  source_return: <PASS|WARN|FAIL>
  structure: <PASS|FAIL>
  evidence: <PASS|WARN|FAIL>
required_fixes: []
warnings: []
notes: ""
```

## 参考文件

- `templates/review-report.template.md` - 审校报告模板
- `templates/section-lesson.template.md` - 讲义模板
- `.claude/skills/section-lesson-authoring/SKILL.md` - 撰写规则
