"""提取 PDF 原文片段，按标题边界裁剪，生成 source-slice.md

CLI 调用：
  python extract_source_slice.py \\
    --pdf <pdf_path> \\
    --pages <start>-<end> \\
    --start-title <regex> \\
    --end-title <regex> \\
    --output <output_path> \\
    --section-id <id> \\
    --section-title <title>
"""
import argparse
import re
import sys

import fitz


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='提取 PDF 原文片段')
    parser.add_argument('--pdf', required=True, help='PDF 文件路径')
    parser.add_argument('--pages', required=True, help='页码范围，如 84-86')
    parser.add_argument('--start-title', required=True, help='起点标题正则')
    parser.add_argument('--end-title', required=True, help='终点标题正则')
    parser.add_argument('--output', required=True, help='输出文件路径')
    parser.add_argument('--section-id', required=True, help='小节 ID')
    parser.add_argument('--section-title', required=True, help='小节标题')
    return parser.parse_args(argv)


def extract_raw_text(pdf_path, pages_str):
    start, end = pages_str.split('-')
    start_page = int(start) - 1  # 0-indexed
    end_page = int(end)  # inclusive, so range end is exclusive

    doc = fitz.open(pdf_path)
    raw_text = ""
    for page_num in range(start_page, end_page):
        page = doc[page_num]
        raw_text += page.get_text() + "\n"
    doc.close()
    return raw_text


def slice_text(raw_text, start_pattern, end_pattern):
    start_match = re.search(start_pattern, raw_text)
    if not start_match:
        raise ValueError(f"未找到起点标题: {start_pattern}")

    end_match = re.search(end_pattern, raw_text)
    if not end_match:
        raise ValueError(f"未找到终点标题: {end_pattern}")
    start_pos = start_match.start()
    end_pos = end_match.start()

    return raw_text[start_pos:end_pos].strip()


def build_metadata_header(section_id, source_file, pages, start_pattern, end_pattern, section_title):
    return (
        f"# {section_id} 原文片段\n\n"
        f"- 来源：{source_file}\n"
        f"- 粗页码范围：{pages}\n"
        f"- 小节标题：{section_title}\n"
        f"- 裁剪起点正则：{start_pattern}\n"
        f"- 裁剪终点正则：{end_pattern}\n\n"
        f"## 原文内容\n\n"
    )


def main():
    args = parse_args()

    source_file = args.pdf.split('/')[-1].split('\\')[-1]
    raw_text = extract_raw_text(args.pdf, args.pages)
    sliced_text = slice_text(raw_text, args.start_title, args.end_title)

    header = build_metadata_header(
        section_id=args.section_id,
        source_file=source_file,
        pages=args.pages,
        start_pattern=args.start_title,
        end_pattern=args.end_title,
        section_title=args.section_title,
    )

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(header + sliced_text)

    print(f"Source slice 已保存：{args.output}")
    print(f"长度：{len(sliced_text)} 字符")


if __name__ == '__main__':
    main()
