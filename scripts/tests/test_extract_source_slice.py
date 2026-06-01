"""extract_source_slice.py 参数化泛化的单元测试"""
import pytest
import unittest
import sys
import os
import tempfile

pytestmark = pytest.mark.legacy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extract_source_slice import parse_args, slice_text, build_metadata_header


class TestParseArgs(unittest.TestCase):
    """测试 CLI 参数解析"""

    def test_all_required_args(self):
        args = parse_args([
            '--pdf', 'test.pdf',
            '--pages', '21-22',
            '--start-title', '2\\.1\\s+开始',
            '--end-title', '2\\.2\\s+结束',
            '--output', 'out.md',
            '--section-id', 'TEST-001',
            '--section-title', '测试标题',
        ])
        self.assertEqual(args.pdf, 'test.pdf')
        self.assertEqual(args.pages, '21-22')
        self.assertEqual(args.start_title, '2\\.1\\s+开始')
        self.assertEqual(args.end_title, '2\\.2\\s+结束')
        self.assertEqual(args.output, 'out.md')
        self.assertEqual(args.section_id, 'TEST-001')
        self.assertEqual(args.section_title, '测试标题')

    def test_pages_parsing(self):
        args = parse_args([
            '--pdf', 'x.pdf', '--pages', '84-86',
            '--start-title', 'a', '--end-title', 'b',
            '--output', 'o.md', '--section-id', 'S', '--section-title', 'T',
        ])
        start, end = args.pages.split('-')
        self.assertEqual(int(start), 84)
        self.assertEqual(int(end), 86)


class TestSliceText(unittest.TestCase):
    """测试标题裁剪逻辑"""

    def test_basic_slice(self):
        raw = "前言内容\n2.1 开始标题\n正文内容\n更多正文\n2.2 结束标题\n后续内容"
        result = slice_text(raw, r'2\.1\s+开始标题', r'2\.2\s+结束标题')
        self.assertIn('开始标题', result)
        self.assertIn('正文内容', result)
        self.assertNotIn('结束标题', result)
        self.assertNotIn('前言内容', result)
        self.assertNotIn('后续内容', result)

    def test_start_not_found_raises(self):
        raw = "一些内容\n没有匹配的标题"
        with self.assertRaises(ValueError):
            slice_text(raw, r'不存在的标题', r'结束')

    def test_end_not_found_raises(self):
        raw = "前言\n2.1 开始\n所有后续内容直到末尾"
        with self.assertRaises(ValueError):
            slice_text(raw, r'2\.1\s+开始', r'不存在的结束标题')

    def test_chinese_title_with_parentheses(self):
        raw = "8.1 古诺 (Cournot) 模型\n需求函数\n伯特兰 (Bertrand) 模型\n更多"
        result = slice_text(raw, r'古诺.*Cournot', r'伯特兰.*Bertrand')
        self.assertIn('古诺', result)
        self.assertIn('需求函数', result)
        self.assertNotIn('伯特兰', result)


class TestBuildMetadataHeader(unittest.TestCase):
    """测试元数据头生成"""

    def test_header_contains_required_fields(self):
        header = build_metadata_header(
            section_id='GTW-005-01',
            source_file='test.pdf',
            pages='84-86',
            start_pattern='2\\.3\\.1',
            end_pattern='2\\.3\\.2',
            section_title='古诺模型',
        )
        self.assertIn('GTW-005-01', header)
        self.assertIn('84-86', header)
        self.assertIn('古诺模型', header)
        self.assertIn('## 原文内容', header)


if __name__ == '__main__':
    unittest.main()
