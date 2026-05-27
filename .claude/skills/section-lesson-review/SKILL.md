---
description: 审校小节讲义草稿的忠实性、可学习性和结构完整性。当需要对讲义进行质量检查时使用。
---

# section-lesson-review

## 触发条件

当 agent 需要审校一篇小节讲义草稿时使用此 skill。

## 输入

- 讲义草稿文件路径
- PDF 原文片段（对应页码范围）
- section-manifest 中的对应条目

## 输出

- `books/<book-id>/pipeline-workspace/reviews/<section-id>/review-report.md` - 审校报告
- `books/<book-id>/pipeline-workspace/reviews/<section-id>/review-decision.yaml` - 审校决策

## 审校维度

### 1. 忠实性审校

检查项：
- [ ] 核心概念是否准确反映原文
- [ ] 关键结论是否与原文一致
- [ ] 推导骨架是否正确
- [ ] 是否存在凭空扩展的内容

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
- [ ] section id 是否与 manifest 一致

评分：PASS / FAIL

## 决策规则

决策按优先级判定：先检查 reject 条件，再检查 revise 条件，最后 accept。

### reject（拒绝）- 最高优先级

条件（满足任一即 reject）：
- 忠实性 FAIL（核心内容与原文不符）
- 结构完整性 FAIL（缺失必备章节或 frontmatter 严重错误）
- 存在严重错误（如概念张冠李戴、推导根本性错误）

后续：
- 在 review-report.md 中说明拒绝理由
- 需要重新生成，不进入修订流程

### revise（需修订）- 次优先级

条件（满足任一即 revise）：
- 任意维度存在 FAIL（但不满足 reject 条件）
- WARN 项超过 3 个

后续：
- 在 review-report.md 中列出具体问题
- 在 review-decision.yaml 中记录 required_fixes
- 返回给 worker 修订

### accept（通过）- 最低优先级

条件（必须全部满足）：
- 不满足 reject 和 revise 条件
- 所有维度评分均为 PASS 或 WARN
- WARN 项不超过 3 个

后续：可进入下一阶段

## review-decision.yaml 格式

```yaml
section_id: <section-id>
reviewer: <reviewer-name>
review_date: <YYYY-MM-DD>
decision: <accept|revise|reject>
scores:
  faithfulness: <PASS|WARN|FAIL>
  learnability: <PASS|WARN|FAIL>
  importance: <PASS|WARN|FAIL>
  source_return: <PASS|WARN|FAIL>
  structure: <PASS|FAIL>
required_fixes: []
warnings: []
notes: ""
```

## 参考文件

- `templates/review-report.template.md` - 审校报告模板
- `templates/section-lesson.template.md` - 讲义模板
- `.claude/skills/section-lesson-authoring/SKILL.md` - 撰写规则
