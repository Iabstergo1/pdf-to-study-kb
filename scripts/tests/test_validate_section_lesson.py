"""validate_section_lesson.py 的单元测试"""
import unittest
import sys
import os

# 添加 scripts 目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from validate_section_lesson import validate_section_lesson


VALID_LESSON = """---
id: GTW-002-01
type: section-lesson
source_title: 博弈论研究完全自学入门
source_locator:
  pages: [21, 22]
book_order: "1.2.1"
importance: A
difficulty: 2
formula_risk: low
review_status: draft
generation_stage: draft
---

# 到底什么是博弈

## 学习定位

本节是博弈论的入门概念。

## 先记住的结论

- 博弈由参与者、策略、收益三要素构成

## 必须掌握

参与者、策略、收益的定义。

## 首遍可略读

无。

## 核心概念

博弈、参与者、策略、收益。

## 模型结构、论证骨架或推导骨架

无推导，纯概念。

## 直觉解释

博弈就像一场游戏。

## 容易误解的点

博弈不一定是零和的。

## 与个人知识体系的连接候选

可与决策理论连接。

## 自测问题

1. 什么是博弈的三要素？

## 何时回原文

无需回原文。

## 原文定位

第 21-22 页，第 1.2.1 节。
"""


class TestValidateSectionLesson(unittest.TestCase):
    """测试小节讲义校验器"""

    def test_valid_lesson_passes(self):
        """有效讲义应通过校验"""
        result = validate_section_lesson(VALID_LESSON)
        self.assertTrue(result['passed'], f"Expected PASS but got: {result['errors']}")

    def test_missing_frontmatter_fails(self):
        """缺少 frontmatter 应失败"""
        content = """# 标题

## 学习定位

内容。
"""
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('frontmatter' in e.lower() for e in result['errors']))

    def test_missing_id_fails(self):
        """缺少 id 字段应失败"""
        content = """---
type: section-lesson
source_title: 测试
source_locator:
  pages: [1, 2]
book_order: "1.1"
importance: A
difficulty: 1
formula_risk: low
review_status: draft
generation_stage: draft
---

# 标题

## 学习定位

内容。

## 先记住的结论

内容。

## 必须掌握

内容。

## 首遍可略读

内容。

## 核心概念

内容。

## 模型结构、论证骨架或推导骨架

内容。

## 直觉解释

内容。

## 容易误解的点

内容。

## 与个人知识体系的连接候选

内容。

## 自测问题

内容。

## 何时回原文

内容。

## 原文定位

内容。
"""
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('id' in e.lower() for e in result['errors']))

    def test_missing_source_pages_fails(self):
        """缺少来源页码应失败"""
        content = """---
id: TEST-001
type: section-lesson
source_title: 测试
book_order: "1.1"
importance: A
difficulty: 1
formula_risk: low
review_status: draft
generation_stage: draft
---

# 标题

## 学习定位

内容。

## 先记住的结论

内容。

## 必须掌握

内容。

## 首遍可略读

内容。

## 核心概念

内容。

## 模型结构、论证骨架或推导骨架

内容。

## 直觉解释

内容。

## 容易误解的点

内容。

## 与个人知识体系的连接候选

内容。

## 自测问题

内容。

## 何时回原文

内容。

## 原文定位

内容。
"""
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('source_locator' in e.lower() or 'pages' in e.lower() for e in result['errors']))

    def test_invalid_review_status_fails(self):
        """非法 review_status 应失败"""
        content = VALID_LESSON.replace('review_status: draft', 'review_status: invalid_status')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('review_status' in e.lower() for e in result['errors']))

    def test_missing_required_heading_fails(self):
        """缺少必备标题应失败"""
        content = """---
id: GTW-002-01
type: section-lesson
source_title: 测试
source_locator:
  pages: [21, 22]
book_order: "1.2.1"
importance: A
difficulty: 2
formula_risk: low
review_status: draft
generation_stage: draft
---

# 标题

## 学习定位

内容。
"""
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('先记住的结论' in e for e in result['errors']))

    def test_wrong_type_fails(self):
        """type 不是 section-lesson 应失败"""
        content = VALID_LESSON.replace('type: section-lesson', 'type: summary')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('type' in e.lower() for e in result['errors']))

    def test_invalid_difficulty_fails(self):
        """difficulty 不是 1-5 整数应失败"""
        content = VALID_LESSON.replace('difficulty: 2', 'difficulty: 6')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('difficulty' in e.lower() for e in result['errors']))

    def test_difficulty_not_int_fails(self):
        """difficulty 不是整数应失败"""
        content = VALID_LESSON.replace('difficulty: 2', 'difficulty: high')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('difficulty' in e.lower() for e in result['errors']))

    def test_empty_pages_list_fails(self):
        """source_locator.pages 为空列表应失败"""
        content = VALID_LESSON.replace('pages: [21, 22]', 'pages: []')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('pages' in e.lower() for e in result['errors']))

    def test_missing_generation_stage_fails(self):
        """缺少 generation_stage 应失败"""
        content = VALID_LESSON.replace('generation_stage: draft\n---', '---')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('generation_stage' in e.lower() for e in result['errors']))

    def test_invalid_generation_stage_fails(self):
        """非法 generation_stage 应失败"""
        content = VALID_LESSON.replace('generation_stage: draft', 'generation_stage: invalid')
        result = validate_section_lesson(content)
        self.assertFalse(result['passed'])
        self.assertTrue(any('generation_stage' in e.lower() for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
