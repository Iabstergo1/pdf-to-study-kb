---
section_id: {{section_id}}
reviewer: {{reviewer}}
review_date: {{review_date}}
decision: {{decision}}
---

# 审校报告：{{section_title}}

## 基本信息

| 项目 | 值 |
|------|-----|
| Section ID | {{section_id}} |
| 来源页码 | {{source_pages}} |
| 公式风险 | {{formula_risk}} |
| 审校日期 | {{review_date}} |

## 审校结论

**决策：{{decision}}**

### 评分汇总

| 维度 | 评分 | 说明 |
|------|------|------|
| 忠实性 | {{faithfulness_score}} | {{faithfulness_note}} |
| 可学习性 | {{learnability_score}} | {{learnability_note}} |
| 轻重分级 | {{importance_score}} | {{importance_note}} |
| 回原文条件 | {{source_return_score}} | {{source_return_note}} |
| 结构完整性 | {{structure_score}} | {{structure_note}} |

## 发现的问题

### 阻塞问题（必须修复）

1. {{issue_1}}
2. {{issue_2}}

### 非阻塞问题（建议修复）

1. {{warning_1}}
2. {{warning_2}}

## 修订要求

<!-- 当 decision 为 revise 时，列出具体修订项 -->

- [ ] {{fix_1}}
- [ ] {{fix_2}}
- [ ] {{fix_3}}

## 风险标记

| 风险类型 | 状态 | 说明 |
|----------|------|------|
| 公式风险 | {{formula_risk_status}} | {{formula_risk_note}} |
| 来源忠实性 | {{faithfulness_status}} | {{faithfulness_note}} |
| 桥接候选 | {{bridge_status}} | {{bridge_note}} |

## 备注

{{notes}}
