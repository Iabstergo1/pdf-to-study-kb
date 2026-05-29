---
unit_id: {{unit_id}}
reviewer: {{reviewer}}
review_date: {{review_date}}
decision: {{decision}}
confidence: {{confidence}}
---

# 审校报告：{{unit_title}}

## 基本信息

| 项目 | 值 |
|------|-----|
| Unit ID | {{unit_id}} |
| 来源页码 | {{source_pages}} |
| 提取方式 | {{extraction_method}} |
| 公式风险 | {{formula_risk}} |
| 审校日期 | {{review_date}} |

## 审校结论

**决策：{{decision}}**
**置信度：{{confidence}}**

### 评分汇总

| 维度 | 评分 | 说明 |
|------|------|------|
| 忠实性 | {{faithfulness_score}} | {{faithfulness_note}} |
| 可学习性 | {{learnability_score}} | {{learnability_note}} |
| 轻重分级 | {{importance_score}} | {{importance_note}} |
| 回原文条件 | {{source_return_score}} | {{source_return_note}} |
| 结构完整性 | {{structure_score}} | {{structure_note}} |

## 证据对照表

> **门禁要求**：每个核心结论必须有至少一条原文证据。缺少证据表时，review 结果强制视为 reject。

| 讲义结论 | 原文证据片段 | 页码 | 证据类型 |
|----------|-------------|------|----------|
| {{claim_1}} | {{evidence_1}} | {{page_1}} | {{type_1}} |
| {{claim_2}} | {{evidence_2}} | {{page_2}} | {{type_2}} |

## 公式风险清单

> **门禁要求**：公式密集 unit 必须逐项对比。发现"原文空白但讲义补全公式"时进入 formula risk。

| 变量/符号 | 讲义中的表达 | 原文中的表达 | 一致性 |
|----------|-------------|-------------|--------|
| {{var_1}} | {{lesson_expr_1}} | {{source_expr_1}} | {{consistency_1}} |
| {{var_2}} | {{lesson_expr_2}} | {{source_expr_2}} | {{consistency_2}} |

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
| 证据覆盖 | {{evidence_status}} | {{evidence_note}} |
| 桥接候选 | {{bridge_status}} | {{bridge_note}} |

## 备注

{{notes}}
