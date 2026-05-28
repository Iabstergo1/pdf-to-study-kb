#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI

统一入口，支持以下命令：
- init-book: 初始化书籍目录结构和最小配置
- inventory: 分析 PDF 结构，生成报告和可选 manifest
- extract: 按 manifest 批量生成 source-slice.md
- plan-sections: 生成 section 拆分和标题边界候选
- review-sections: 交互式审核 section 边界候选
- apply-section-plan: 应用已审核 section 规划
- status: 显示项目状态
- validate: 校验讲义文件
- coverage: 显示覆盖报告
- publish: 发布讲义到 study-kb
- make-tasks: 生成 Claude Code 任务包
- run-book: 全书级编排入口

用法：
  python scripts/pipeline.py <command> --book <book-id> [options]
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
import yaml


def find_book_root(book_id: str) -> Path:
    """查找书籍根目录"""
    book_root = Path("books") / book_id
    if not book_root.exists():
        print(f"错误：书籍目录不存在: {book_root}", file=sys.stderr)
        sys.exit(1)
    return book_root


def load_manifest(book_root: Path) -> dict:
    """加载 section-manifest.yaml"""
    manifest_path = book_root / "config" / "section-manifest.yaml"
    if not manifest_path.exists():
        print(f"错误：manifest 文件不存在: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def expand_page_locator(pages: list) -> list[int]:
    """Normalize manifest page locator.

    Project docs use ``pages: [start, end]`` for an inclusive page range.
    Longer lists are treated as explicit page numbers.
    """
    if not pages:
        return []
    pages = [int(p) for p in pages]
    if len(pages) == 2 and pages[0] <= pages[1]:
        return list(range(pages[0], pages[1] + 1))
    return pages


def load_boundary_hints(book_root: Path) -> dict:
    """Load optional section title-boundary hints for source slicing."""
    hints_path = book_root / "config" / "source-boundary-hints.yaml"
    if not hints_path.exists():
        return {}
    with open(hints_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    sections = data.get('sections') or {}
    if not isinstance(sections, dict):
        return {}
    return sections


def apply_boundary_hint(raw_text: str, hint: dict) -> str:
    """Slice raw text using start/end regex from a boundary hint."""
    import re

    start_regex = hint.get('start_regex')
    end_regex = hint.get('end_regex')
    if not start_regex or not end_regex:
        raise ValueError('boundary hint 缺少 start_regex 或 end_regex')

    start_match = re.search(start_regex, raw_text, flags=re.MULTILINE)
    if not start_match:
        raise ValueError(f'未找到起点边界: {start_regex}')

    end_match = re.search(end_regex, raw_text[start_match.start():], flags=re.MULTILINE)
    if not end_match:
        raise ValueError(f'未找到终点边界: {end_regex}')

    start_pos = start_match.start()
    end_pos = start_pos + end_match.start()
    if end_pos <= start_pos:
        raise ValueError('终点边界早于或等于起点边界')

    return raw_text[start_pos:end_pos].strip()


def parse_chapter_groups(book_root: Path) -> dict:
    """从 manifest 解析章节分组。

    优先从 # ===== 第X部分 ===== 注释块解析；
    若无注释块，从每个 section 的 part 字段读取。

    Returns:
        dict: {section_id: part_title}
    """
    import re
    manifest_path = book_root / "config" / "section-manifest.yaml"
    with open(manifest_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    section_to_part = {}
    current_part = '未知章节'
    found_comment_blocks = False

    for line in lines:
        line = line.strip()
        # 匹配章节注释：# ===== 第X部分：XXX ===== 或 # ===== 第X部分 XXX =====
        part_match = re.match(r'^#\s*=====\s*(第.+?部分.*?)\s*=====', line)
        if part_match:
            current_part = part_match.group(1)
            found_comment_blocks = True
            continue

        # 匹配 section id
        id_match = re.match(r'^-\s*id:\s*(\S+)', line)
        if id_match:
            section_to_part[id_match.group(1)] = current_part

    # 如果没找到注释块，从 YAML 数据的 part 字段读取
    if not found_comment_blocks:
        manifest_data = load_manifest(book_root)
        for section in manifest_data.get('sections', []):
            sid = section.get('id')
            part = section.get('part')
            if sid and part:
                section_to_part[sid] = part

    return section_to_part


def get_chapter_from_section_id(section_id: str, book_root: Path = None) -> str:
    """从 section-id 获取章节名称

    优先从 manifest 注释块解析，如果 book_root 为 None 则返回未知章节
    """
    if book_root is None:
        return '未知章节'

    section_to_part = parse_chapter_groups(book_root)
    return section_to_part.get(section_id, '未知章节')


def _ensure_dirs(book_root: Path):
    """创建 book 标准目录结构"""
    dirs = [
        "input",
        "config",
        "pipeline-workspace/reports",
        "pipeline-workspace/staging",
        "pipeline-workspace/reviews",
        "pipeline-workspace/tasks",
        "study-kb/Section-Lessons",
        "study-kb/Learning-Maps",
        "study-kb/Source-QA",
    ]
    for d in dirs:
        (book_root / d).mkdir(parents=True, exist_ok=True)


def cmd_init_book(args):
    """初始化一本书的完整目录结构和最小配置"""
    book_id = args.book
    pdf_path = Path(args.pdf)
    title = args.title
    force = getattr(args, 'force', False)

    if not pdf_path.exists():
        print(f"错误：PDF 文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    book_root = Path("books") / book_id

    if book_root.exists() and not force:
        print(f"错误：书籍目录已存在: {book_root}，使用 --force 覆盖", file=sys.stderr)
        sys.exit(1)

    # 创建目录结构
    _ensure_dirs(book_root)

    # 复制 PDF 到 input/
    import shutil
    pdf_dest = book_root / "input" / pdf_path.name
    shutil.copy2(str(pdf_path), str(pdf_dest))
    print(f"[OK] 已复制 PDF 到 {pdf_dest}")

    # 生成 book-profile.yaml
    book_profile = {
        'book_id': book_id,
        'title': title,
        'source_type': 'pdf',
        'language': 'zh',
        'domain': 'unknown',
        'source_authority': 'original-source',
        'expected_structure': {
            'unit': 'section',
            'preserve_source_order': True,
        },
        'risk_flags': {
            'formulas': 'unknown',
            'tables': 'unknown',
            'scanned_pages': 'unknown',
        },
    }
    bp_path = book_root / "config" / "book-profile.yaml"
    if not bp_path.exists() or force:
        with open(bp_path, 'w', encoding='utf-8') as f:
            yaml.dump(book_profile, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[OK] 已生成 {bp_path}")

    # 生成 study-profile.yaml
    study_profile = {
        'lesson_style': {
            'primary_unit': 'section-lesson',
            'density': 'medium',
            'preserve_context': True,
            'allow_concept_cards': True,
        },
        'importance_levels': ['A', 'B', 'C'],
        'reading_routes': ['source-order', 'beginner-fast-path', 'difficult-topics'],
        'must_include': [
            'learning-position',
            'core-takeaways',
            'must-master',
            'first-pass-skippable',
            'intuitive-explanation',
            'misunderstandings',
            'source-return-conditions',
        ],
    }
    sp_path = book_root / "config" / "study-profile.yaml"
    if not sp_path.exists() or force:
        with open(sp_path, 'w', encoding='utf-8') as f:
            yaml.dump(study_profile, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[OK] 已生成 {sp_path}")

    # 生成 personal-context.yaml
    personal_context = {
        'bridge_policy': {
            'generate_bridge_notes': True,
            'do_not_modify_existing_personal_kb': True,
            'output_mode': 'suggestions-first',
        },
        'contexts': [],
        'bridge_targets': [
            'concepts-worth-keeping',
            'project-relevance',
            'writing-material',
            'open-questions',
            'follow-up-reading',
        ],
    }
    pc_path = book_root / "config" / "personal-context.yaml"
    if not pc_path.exists() or force:
        with open(pc_path, 'w', encoding='utf-8') as f:
            yaml.dump(personal_context, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[OK] 已生成 {pc_path}")

    print(f"\n初始化完成: {book_root}")
    print(f"下一步: python scripts/pipeline.py inventory --book {book_id}")


def cmd_inventory(args):
    """分析 PDF 结构，生成报告和可选 manifest"""
    book_id = args.book
    book_root = find_book_root(book_id)
    write_manifest = getattr(args, 'write', False)
    force = getattr(args, 'force', False)

    # 找到 PDF
    input_dir = book_root / "input"
    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"错误：input 目录中没有 PDF 文件: {input_dir}", file=sys.stderr)
        sys.exit(1)
    pdf_path = pdf_files[0]

    # 调用 analyze_pdf（文件名含连字符，用 importlib 加载）
    import importlib.util
    analyze_pdf_path = Path("scripts") / "analyze-pdf.py"
    spec = importlib.util.spec_from_file_location("analyze_pdf", analyze_pdf_path)
    analyze_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyze_mod)
    analyze_pdf = analyze_mod.analyze_pdf

    print(f"正在分析 PDF: {pdf_path}")
    result = analyze_pdf(str(pdf_path))

    # 写入原始 JSON
    reports_dir = book_root / "pipeline-workspace" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    raw_path = reports_dir / "pdf-structure-raw.json"
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已生成 {raw_path}")

    # 生成可读报告
    report_lines = []
    report_lines.append("# PDF 结构分析报告\n")
    report_lines.append(f"- 文件：{result['file']}")
    report_lines.append(f"- 总页数：{result['total_pages']}")
    report_lines.append(f"- 平均文本长度：{result['extraction_quality']['avg_text_length']:.0f} 字符/页")
    report_lines.append(f"- 有文本的页：{result['extraction_quality']['pages_with_text']}")
    report_lines.append(f"- 空白页：{result['extraction_quality']['empty_pages']}")
    report_lines.append(f"- 含公式的页：{result['extraction_quality']['pages_with_formulas']}")
    report_lines.append(f"- 含表格的页：{result['extraction_quality']['pages_with_tables']}")
    report_lines.append(f"- 含图片的页：{result['extraction_quality']['pages_with_images']}")
    report_lines.append("")

    if result['risks']:
        report_lines.append("## 风险标记\n")
        for risk in result['risks']:
            report_lines.append(f"- {risk}")
        report_lines.append("")

    toc = result.get('toc', [])
    if toc:
        report_lines.append(f"## 内置目录 ({len(toc)} 条)\n")
        for entry in toc:
            indent = "  " * (entry['level'] - 1)
            report_lines.append(f"{indent}- [{entry['level']}] {entry['title']} (p.{entry['page']})")
        report_lines.append("")
    else:
        report_lines.append("## 内置目录\n")
        report_lines.append("**PDF 无内置目录**，需手动创建 section-manifest.yaml。")
        report_lines.append("")

    report_path = reports_dir / "pdf-structure-report.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    print(f"[OK] 已生成 {report_path}")

    # 判断是否有 TOC
    has_toc = bool(toc) and not any('NO_TOC' in r for r in result['risks'])

    if not has_toc:
        print("\nPDF 无内置目录，无法自动生成 manifest。")
        print("请手动创建 config/section-manifest.yaml。")
        return

    # 有 TOC，尝试生成 manifest
    manifest_path = book_root / "config" / "section-manifest.yaml"
    if manifest_path.exists() and not force:
        print(f"\nmanifest 已存在: {manifest_path}")
        print("使用 --write --force 覆盖，或手动编辑。")
        # 打印 TOC 预览
        print(f"\nTOC 预览（{len(toc)} 条，仅供参考）:")
        for entry in toc[:20]:
            indent = "  " * (entry['level'] - 1)
            print(f"  {indent}[L{entry['level']}] {entry['title']} (p.{entry['page']})")
        if len(toc) > 20:
            print(f"  ... 还有 {len(toc) - 20} 条")
        return

    if not write_manifest:
        print(f"\nTOC 共 {len(toc)} 条。使用 --write 写入 manifest。")
        # 打印预览
        for entry in toc[:20]:
            indent = "  " * (entry['level'] - 1)
            print(f"  {indent}[L{entry['level']}] {entry['title']} (p.{entry['page']})")
        if len(toc) > 20:
            print(f"  ... 还有 {len(toc) - 20} 条")
        return

    # 生成 manifest
    # 只取 level 2 或 level 3 的条目作为 sections
    # 跳过 level 1（通常是书名或大标题）
    section_entries = [e for e in toc if e['level'] >= 2]

    if not section_entries:
        print("\nTOC 条目层级不足，无法自动生成 sections。")
        print("请手动编辑 manifest。")
        return

    # 构建 level 1 条目到 part 名称的映射（按页码范围）
    level1_entries = [e for e in toc if e['level'] == 1]
    level1_ranges = []
    for i, entry in enumerate(level1_entries):
        start = entry['page']
        end = level1_entries[i + 1]['page'] - 1 if i + 1 < len(level1_entries) else result['total_pages']
        level1_ranges.append((start, end, entry['title']))

    def find_part(page_num):
        for start, end, title in level1_ranges:
            if start <= page_num <= end:
                return title
        return '未知章节'

    # 计算每个 section 的页码范围
    sections = []
    book_id_slug = book_id.replace(' ', '-')
    # 简单的 ID 生成：用序号
    for idx, entry in enumerate(section_entries):
        start_page = entry['page']
        # end_page = next entry's start_page - 1, or last page
        if idx + 1 < len(section_entries):
            end_page = section_entries[idx + 1]['page'] - 1
        else:
            end_page = result['total_pages']

        if end_page < start_page:
            end_page = start_page

        section_id = f"SEC-{idx + 1:03d}"
        sections.append({
            'id': section_id,
            'source_order': str(idx + 1),
            'title': entry['title'],
            'part': find_part(start_page),
            'source_locator': {
                'pages': list(range(start_page, end_page + 1)),
            },
            'status': 'registered',
            'extraction_risk': 'unknown',
            'formula_risk': 'unknown',
            'publish_status': 'not-published',
        })

    manifest_data = {
        'book_id': book_id,
        'generated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total_sections': len(sections),
        'source_pages': result['total_pages'],
        'toc_source': 'pdf-embedded',
        'sections': sections,
    }

    with open(manifest_path, 'w', encoding='utf-8') as f:
        yaml.dump(manifest_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"\n[OK] 已生成 manifest: {manifest_path} ({len(sections)} 个小节)")


def cmd_extract(args):
    """按 manifest 批量生成 source-slice.md"""
    book_id = args.book
    book_root = find_book_root(book_id)
    manifest = load_manifest(book_root)
    force = getattr(args, 'force', False)

    # 找到 PDF
    input_dir = book_root / "input"
    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"错误：input 目录中没有 PDF 文件: {input_dir}", file=sys.stderr)
        sys.exit(1)
    pdf_path = pdf_files[0]

    # 确定要处理的小节
    if args.all:
        sections = manifest.get('sections', [])
    elif args.section:
        section = next((s for s in manifest.get('sections', []) if s['id'] == args.section), None)
        if not section:
            print(f"错误：未找到小节 {args.section}", file=sys.stderr)
            sys.exit(1)
        sections = [section]
    else:
        print("错误：必须指定 --section 或 --all", file=sys.stderr)
        sys.exit(1)

    # 加载 pymupdf
    import fitz

    staging_dir = book_root / "pipeline-workspace" / "staging"
    reports_dir = book_root / "pipeline-workspace" / "reports"
    boundary_hints = load_boundary_hints(book_root)
    failures = []
    created = 0
    skipped = 0

    for section in sections:
        section_id = section['id']
        title = section.get('title', '无标题')
        pages = expand_page_locator(section.get('source_locator', {}).get('pages', []))

        # 检查 source-slice 是否已存在
        slice_dir = staging_dir / section_id
        slice_path = slice_dir / "source-slice.md"

        if slice_path.exists() and not force:
            skipped += 1
            print(f"[SKIP] {section_id}: source-slice.md 已存在")
            continue

        # 检查 pages
        if not pages:
            failures.append({
                'section_id': section_id,
                'title': title,
                'reason': '缺少 source_locator.pages',
            })
            print(f"[FAIL] {section_id}: 缺少 pages")
            continue

        # 提取文本
        try:
            doc = fitz.open(str(pdf_path))
            # 使用标题边界裁剪时，扩展页码范围以包含下一节标题所在页
            extract_pages = list(pages)
            hint = boundary_hints.get(section_id)
            if hint and hint.get('boundary_mode') == 'title-range' and pages:
                last_page = pages[-1]
                if last_page < len(doc) and last_page not in extract_pages:
                    pass  # pages already includes it
                # 确保至少多取1页来捕获 end_regex 对应的标题
                extended = last_page + 1
                if extended <= len(doc) and extended not in extract_pages:
                    extract_pages.append(extended)

            raw_text = ""
            for page_num in extract_pages:
                if 1 <= page_num <= len(doc):
                    raw_text += doc[page_num - 1].get_text() + "\n"
                else:
                    failures.append({
                        'section_id': section_id,
                        'title': title,
                        'reason': f'页码 {page_num} 超出范围 (1-{len(doc)})',
                    })
                    raw_text = None
                    break
            doc.close()

            if raw_text is None:
                continue

        except Exception as e:
            failures.append({
                'section_id': section_id,
                'title': title,
                'reason': f'PDF 读取失败: {e}',
            })
            print(f"[FAIL] {section_id}: PDF 读取失败 - {e}")
            continue

        hint = boundary_hints.get(section_id)
        extraction_mode = "page-range"
        boundary_hint_status = None
        if hint and hint.get('boundary_mode') == 'title-range':
            try:
                raw_text = apply_boundary_hint(raw_text, hint)
            except ValueError as e:
                failures.append({
                    'section_id': section_id,
                    'title': title,
                    'reason': f'标题边界裁剪失败: {e}',
                })
                print(f"[FAIL] {section_id}: 标题边界裁剪失败 - {e}")
                continue
            extraction_mode = "title-range"
            boundary_hint_status = hint.get('status', 'unknown')

        # 判断置信度
        pages_str = ",".join(str(p) for p in pages)
        is_continuous = bool(pages) and pages == list(range(pages[0], pages[-1] + 1))
        if extraction_mode == "title-range":
            confidence = "high"
            needs_review = False
        elif is_continuous:
            confidence = "medium"  # 纯页码切片，未做标题匹配
            needs_review = False
        else:
            confidence = "low"
            needs_review = True

        # 生成 source-slice.md
        slice_dir.mkdir(parents=True, exist_ok=True)
        source_filename = pdf_path.name

        content_parts = [
            "---",
            f"section_id: {section_id}",
            f'title: "{title}"',
            f'source_file: "{source_filename}"',
            f'pages: "{pages[0]}-{pages[-1]}"',
            f"expanded_pages: [{', '.join(str(p) for p in pages)}]",
            f"extraction_mode: {extraction_mode}",
            f"extraction_confidence: {confidence}",
            f"needs_boundary_review: {'true' if needs_review else 'false'}",
        ]
        if hint and extraction_mode == "title-range":
            content_parts.extend([
                f"boundary_hint_status: {boundary_hint_status}",
                f"start_regex: {json.dumps(hint.get('start_regex', ''), ensure_ascii=False)}",
                f"end_regex: {json.dumps(hint.get('end_regex', ''), ensure_ascii=False)}",
            ])
        content_parts.extend([
            "---",
            "",
            f"# {section_id} 原文片段",
            "",
            f"- 来源：{source_filename}",
            f"- 页码范围：{pages[0]}-{pages[-1]}",
            f"- 小节标题：{title}",
            f"- 提取模式：{extraction_mode}",
            f"- 置信度：{confidence}",
            "",
            "## 原文内容",
            "",
            raw_text.strip(),
        ])

        with open(slice_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content_parts) + '\n')

        created += 1
        print(f"[OK] {section_id}: 已生成 source-slice.md ({len(raw_text)} 字符, {confidence})")

    # 报告失败
    if failures:
        failure_report = ["# 提取失败报告\n", f"生成时间：{datetime.now().isoformat()}\n"]
        failure_report.append("| 小节 ID | 标题 | 失败原因 |")
        failure_report.append("|---------|------|----------|")
        for f in failures:
            failure_report.append(f"| {f['section_id']} | {f['title']} | {f['reason']} |")

        failure_path = reports_dir / "extraction-failures.md"
        # 追加模式：如果已存在则追加
        if failure_path.exists() and not force:
            with open(failure_path, 'r', encoding='utf-8') as f:
                existing = f.read()
            with open(failure_path, 'a', encoding='utf-8') as f:
                f.write('\n\n' + '\n'.join(failure_report[2:]))  # 追加表格行
        else:
            with open(failure_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(failure_report))
        print(f"\n[WARN] {len(failures)} 个小节提取失败，详见 {failure_path}")

    # 汇总
    print(f"\n提取完成: {created} 新生成, {skipped} 跳过(已存在), {len(failures)} 失败")


def cmd_status(args):
    """显示项目状态"""
    book_root = find_book_root(args.book)
    manifest = load_manifest(book_root)

    sections = manifest.get('sections', [])
    total = len(sections)

    # 统计各状态
    status_counts = {}
    for section in sections:
        status = section.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1

    # 统计已发布
    published = [s for s in sections if s.get('publish_status') == 'published']

    print(f"书籍: {args.book}")
    print(f"总小节数: {total}")
    print(f"\n状态分布:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"\n已发布: {len(published)}")
    if published:
        print("已发布小节:")
        for s in published:
            print(f"  - {s['id']}: {s.get('title', '无标题')}")


def cmd_validate(args):
    """校验讲义文件"""
    book_root = find_book_root(args.book)
    stage = getattr(args, 'stage', 'all')  # 默认 all

    # 导入校验函数
    sys.path.insert(0, str(Path("scripts")))
    from validate_section_lesson import validate_section_lesson

    sections_to_validate = []

    def find_draft(section_id):
        """查找 staging draft"""
        return book_root / "pipeline-workspace" / "staging" / section_id / "section-lesson-draft.md"

    def find_published(section_id):
        """查找 published lesson"""
        return book_root / "study-kb" / "Section-Lessons" / f"{section_id}.md"

    if args.all:
        # 校验所有小节
        manifest = load_manifest(book_root)
        for section in manifest['sections']:
            section_id = section['id']
            if stage in ('draft', 'all'):
                draft_path = find_draft(section_id)
                if draft_path.exists():
                    sections_to_validate.append(draft_path)
            if stage in ('published', 'all'):
                published_path = find_published(section_id)
                if published_path.exists():
                    sections_to_validate.append(published_path)
    elif args.section:
        # 校验指定小节
        found = False
        if stage in ('draft', 'all'):
            draft_path = find_draft(args.section)
            if draft_path.exists():
                sections_to_validate.append(draft_path)
                found = True
        if stage in ('published', 'all'):
            published_path = find_published(args.section)
            if published_path.exists():
                sections_to_validate.append(published_path)
                found = True
        if not found:
            print(f"错误：讲义文件不存在（stage={stage}）", file=sys.stderr)
            sys.exit(1)
    else:
        print("错误：必须指定 --section 或 --all", file=sys.stderr)
        sys.exit(1)

    if not sections_to_validate:
        print("没有找到需要校验的文件")
        return

    results = []
    for file_path in sections_to_validate:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        result = validate_section_lesson(content)
        result['file'] = str(file_path)
        results.append(result)

    # 输出结果
    passed = sum(1 for r in results if r['passed'])
    failed = len(results) - passed

    print(f"校验完成: {passed} 通过, {failed} 失败")
    for r in results:
        status = "[PASS]" if r['passed'] else "[FAIL]"
        print(f"  {status} {r['file']}")
        if not r['passed']:
            for error in r['errors']:
                print(f"    - {error}")

    sys.exit(0 if failed == 0 else 1)


def cmd_coverage(args):
    """显示覆盖报告"""
    book_root = find_book_root(args.book)
    manifest = load_manifest(book_root)

    sections = manifest.get('sections', [])
    total = len(sections)
    published = [s for s in sections if s.get('publish_status') == 'published']

    # 按章节统计（从 manifest 注释块解析）
    chapter_stats = {}
    for section in sections:
        chapter = get_chapter_from_section_id(section['id'], book_root)
        if chapter not in chapter_stats:
            chapter_stats[chapter] = {'total': 0, 'published': 0}
        chapter_stats[chapter]['total'] += 1
        if section.get('publish_status') == 'published':
            chapter_stats[chapter]['published'] += 1

    print(f"书籍: {args.book}")
    print(f"总小节数: {total}")
    print(f"已发布: {len(published)}")
    print(f"覆盖率: {len(published)/total*100:.1f}%")
    print(f"\n按章节统计:")
    print(f"{'章节':<30} {'已发布':>6} {'总数':>6} {'状态':<10}")
    print("-" * 60)

    total_published = 0
    total_sections = 0
    for chapter, stats in sorted(chapter_stats.items()):
        status = "部分发布" if 0 < stats['published'] < stats['total'] else \
                 "已发布" if stats['published'] == stats['total'] else "未发布"
        print(f"{chapter:<30} {stats['published']:>6} {stats['total']:>6} {status:<10}")
        total_published += stats['published']
        total_sections += stats['total']

    print("-" * 60)
    print(f"{'合计':<30} {total_published:>6} {total_sections:>6}")


def _update_manifest_block(lines: list, section_id: str) -> tuple:
    """更新 manifest 中单个小节块的 status 和 publish_status 为 published。

    返回 (new_lines, status_replace_count, publish_status_replace_count)。
    小节块以 '- id: {section_id}' 开始，到下一个 '- id:' 或文件末尾结束。
    """
    import re
    id_pattern = re.compile(rf'^\s*-\s+id:\s+{re.escape(section_id)}\s*$')
    status_pattern = re.compile(r'^(\s+)status:\s+\S+')
    publish_pattern = re.compile(r'^(\s+)publish_status:\s+\S+')

    new_lines = []
    in_block = False
    n_status = 0
    n_publish = 0

    for line in lines:
        stripped = line.rstrip('\n')

        if not in_block:
            if id_pattern.match(stripped):
                in_block = True
            new_lines.append(line)
            continue

        # 已进入目标小节块
        # 遇到下一个同级 '- id:' 则结束块
        if re.match(r'^\s*-\s+id:\s+', stripped) and not id_pattern.match(stripped):
            in_block = False
            new_lines.append(line)
            continue

        m_status = status_pattern.match(stripped)
        m_publish = publish_pattern.match(stripped)

        if m_status and not m_publish:
            indent = m_status.group(1)
            new_lines.append(f'{indent}status: published\n')
            n_status += 1
        elif m_publish:
            indent = m_publish.group(1)
            new_lines.append(f'{indent}publish_status: published\n')
            n_publish += 1
        else:
            new_lines.append(line)

    return new_lines, n_status, n_publish


def _update_manifest_status_only(lines: list, section_id: str, target_status: str) -> tuple:
    """更新 manifest 中单个小节块的 status 为目标值，不动 publish_status。

    返回 (new_lines, status_replace_count)。
    """
    import re
    id_pattern = re.compile(rf'^\s*-\s+id:\s+{re.escape(section_id)}\s*$')
    status_pattern = re.compile(r'^(\s+)status:\s+\S+')

    new_lines = []
    in_block = False
    n_status = 0

    for line in lines:
        stripped = line.rstrip('\n')

        if not in_block:
            if id_pattern.match(stripped):
                in_block = True
            new_lines.append(line)
            continue

        if re.match(r'^\s*-\s+id:\s+', stripped) and not id_pattern.match(stripped):
            in_block = False
            new_lines.append(line)
            continue

        m_status = status_pattern.match(stripped)
        if m_status:
            indent = m_status.group(1)
            new_lines.append(f'{indent}status: {target_status}\n')
            n_status += 1
        else:
            new_lines.append(line)

    return new_lines, n_status


def cmd_mark_reviewed(args):
    """把已通过 review 的小节从 registered/drafted 更新为 reviewed"""
    book_root = find_book_root(args.book)
    manifest = load_manifest(book_root)

    # 导入校验函数
    sys.path.insert(0, str(Path("scripts")))
    from validate_section_lesson import validate_section_lesson

    sections = []
    if args.all_accepted:
        sections = [s for s in manifest['sections'] if s.get('status') != 'published']
    elif args.section:
        section = next((s for s in manifest['sections'] if s['id'] == args.section), None)
        if not section:
            print(f"错误：未找到小节 {args.section}", file=sys.stderr)
            sys.exit(1)
        sections = [section]
    else:
        print("错误：必须指定 --section 或 --all-accepted", file=sys.stderr)
        sys.exit(1)

    updated_ids = []
    skipped = []

    for section in sections:
        section_id = section['id']
        current_status = section.get('status', 'unknown')

        # 规则 8: 已 published 小节跳过
        if current_status == 'published':
            skipped.append({"section_id": section_id, "reason": "已 published，跳过"})
            continue

        # 规则 1: review-decision.yaml 必须存在且 decision=accept
        review_path = book_root / "pipeline-workspace" / "reviews" / section_id / "review-decision.yaml"
        if not review_path.exists():
            skipped.append({"section_id": section_id, "reason": "review-decision.yaml 不存在"})
            continue

        with open(review_path, 'r', encoding='utf-8') as f:
            review_decision = yaml.safe_load(f)

        if review_decision.get('decision') != 'accept':
            skipped.append({"section_id": section_id,
                            "reason": f"decision={review_decision.get('decision')}"})
            continue

        # 规则 2: required_fixes 必须为空
        required_fixes = review_decision.get('required_fixes') or []
        if required_fixes:
            skipped.append({"section_id": section_id,
                            "reason": f"required_fixes 非空: {required_fixes}"})
            continue

        # 规则 3: draft 必须存在
        draft_path = book_root / "pipeline-workspace" / "staging" / section_id / "section-lesson-draft.md"
        if not draft_path.exists():
            skipped.append({"section_id": section_id, "reason": "section-lesson-draft.md 不存在"})
            continue

        # 规则 4: validate 必须通过
        with open(draft_path, 'r', encoding='utf-8') as f:
            content = f.read()
        result = validate_section_lesson(content)
        if not result['passed']:
            skipped.append({"section_id": section_id,
                            "reason": f"validate 失败: {result['errors']}"})
            continue

        updated_ids.append(section_id)

    # 规则 5+6+9: 批量更新 manifest status → reviewed，不动 publish_status
    if updated_ids:
        manifest_path = book_root / "config" / "section-manifest.yaml"
        with open(manifest_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for section_id in updated_ids:
            lines, n_status = _update_manifest_status_only(lines, section_id, 'reviewed')
            if n_status != 1:
                print(f"错误：{section_id} manifest status 替换 {n_status} 次，期望 1 次",
                      file=sys.stderr)
                sys.exit(1)

        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"已更新 manifest: {len(updated_ids)} 个小节 status → reviewed")

    # 报告
    print(f"mark-reviewed 完成: {len(updated_ids)} 更新, {len(skipped)} 跳过")
    if skipped:
        for s in skipped:
            print(f"  [SKIP] {s['section_id']}: {s['reason']}")

    if not updated_ids and args.section:
        sys.exit(1)


def cmd_publish(args):
    """发布讲义到 study-kb"""
    book_root = find_book_root(args.book)
    manifest = load_manifest(book_root)

    sections_to_publish = []

    if args.all_reviewed:
        # 发布所有已审校小节
        sections_to_publish = [s for s in manifest['sections'] if s.get('status') == 'reviewed']
    elif args.section:
        # 发布指定小节
        section = next((s for s in manifest['sections'] if s['id'] == args.section), None)
        if not section:
            print(f"错误：未找到小节 {args.section}", file=sys.stderr)
            sys.exit(1)
        if section.get('status') != 'reviewed':
            print(f"错误：小节 {args.section} 状态不是 reviewed，无法发布", file=sys.stderr)
            sys.exit(1)
        sections_to_publish = [section]
    else:
        print("错误：必须指定 --section 或 --all-reviewed", file=sys.stderr)
        sys.exit(1)

    if not sections_to_publish:
        print("没有找到需要发布的小节")
        return

    # 检查门禁
    published_section_ids = []  # 只跟踪实际成功的小节
    for section in sections_to_publish:
        section_id = section['id']

        # 检查 review-decision
        review_path = book_root / "pipeline-workspace" / "reviews" / section_id / "review-decision.yaml"
        if not review_path.exists():
            print(f"跳过 {section_id}: review-decision.yaml 不存在")
            continue

        with open(review_path, 'r', encoding='utf-8') as f:
            review_decision = yaml.safe_load(f)

        if review_decision.get('decision') != 'accept':
            print(f"跳过 {section_id}: decision={review_decision.get('decision')}")
            continue

        # 检查草稿存在
        draft_path = book_root / "pipeline-workspace" / "staging" / section_id / "section-lesson-draft.md"
        if not draft_path.exists():
            print(f"跳过 {section_id}: section-lesson-draft.md 不存在")
            continue

        # 发布转换
        with open(draft_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 更新 frontmatter
        import re
        def update_frontmatter(match):
            fm_content = match.group(1)
            # 替换 review_status
            fm_content = re.sub(r'review_status:.*', 'review_status: reviewed', fm_content)
            # 替换 generation_stage
            fm_content = re.sub(r'generation_stage:.*', 'generation_stage: published', fm_content)
            return f'---\n{fm_content}\n---'

        new_content = re.sub(r'^---\s*\n(.*?)\n---', update_frontmatter, content, count=1, flags=re.DOTALL)

        # 写入 study-kb
        output_path = book_root / "study-kb" / "Section-Lessons" / f"{section_id}.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        # 记录成功发布的小节
        published_section_ids.append(section_id)
        print(f"[OK] 已发布 {section_id}")

    # 只更新实际成功的小节的 manifest
    if published_section_ids:
        manifest_path = book_root / "config" / "section-manifest.yaml"
        with open(manifest_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for section_id in published_section_ids:
            lines, n_status, n_publish = _update_manifest_block(lines, section_id)
            if n_status != 1 or n_publish != 1:
                print(f"错误：{section_id} manifest 更新异常 "
                      f"(status替换{n_status}次, publish_status替换{n_publish}次，期望各1次)",
                      file=sys.stderr)
                sys.exit(1)

        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"\n已更新 manifest: {len(published_section_ids)} 个小节状态更新为 published")


def cmd_make_tasks(args):
    """生成 Claude Code 任务包"""
    book_root = find_book_root(args.book)
    manifest = load_manifest(book_root)

    sections = []

    if args.all_registered:
        sections = [s for s in manifest['sections'] if s.get('status') == 'registered']
    elif args.section:
        section = next((s for s in manifest['sections'] if s['id'] == args.section), None)
        if not section:
            print(f"错误：未找到小节 {args.section}", file=sys.stderr)
            sys.exit(1)
        sections = [section]
    else:
        print("错误：必须指定 --section 或 --all-registered", file=sys.stderr)
        sys.exit(1)

    if not sections:
        print("没有找到需要生成任务的小节")
        return

    tasks = []
    skipped = []
    output_dir = book_root / "pipeline-workspace" / "tasks"

    for section in sections:
        section_id = section['id']

        # 检查 source-slice 是否存在
        source_slice_path = book_root / "pipeline-workspace" / "staging" / section_id / "source-slice.md"
        if not source_slice_path.exists():
            skipped.append({"section_id": section_id, "reason": "source-slice.md 不存在"})
            print(f"[SKIP] {section_id}: source-slice.md 不存在，请先运行 extract")
            # 清理该小节已有的旧任务包
            for suffix in ('author', 'review'):
                stale = output_dir / f"{section_id}_{suffix}.json"
                if stale.exists():
                    stale.unlink()
                    print(f"  [CLEANUP] 已删除旧任务包 {stale.name}")
            continue

        # author 任务包
        author_task = {
            "task_type": "author",
            "section_id": section_id,
            "book_id": args.book,
            "section_title": section.get('title', ''),
            "inputs": {
                "source_slice": f"books/{args.book}/pipeline-workspace/staging/{section_id}/source-slice.md",
                "manifest": f"books/{args.book}/config/section-manifest.yaml",
                "template": "templates/section-lesson.template.md"
            },
            "outputs": {
                "draft": f"books/{args.book}/pipeline-workspace/staging/{section_id}/section-lesson-draft.md"
            },
            "success_criteria": [
                "frontmatter 包含所有必填字段（id, type, source_title, book_order, importance, difficulty, formula_risk, review_status, generation_stage）",
                "包含 12 个必备章节标题（学习定位、先记住的结论、必须掌握、首遍可略读、核心概念、模型结构/论证骨架/推导骨架、直觉解释、容易误解的点、与个人知识体系的连接候选、自测问题、何时回原文、原文定位）",
                "通过 validate_section_lesson.py 校验"
            ],
            "skill": "section-lesson-authoring"
        }

        # review 任务包
        review_task = {
            "task_type": "review",
            "section_id": section_id,
            "book_id": args.book,
            "section_title": section.get('title', ''),
            "inputs": {
                "draft": f"books/{args.book}/pipeline-workspace/staging/{section_id}/section-lesson-draft.md",
                "source_slice": f"books/{args.book}/pipeline-workspace/staging/{section_id}/source-slice.md"
            },
            "outputs": {
                "review_decision": f"books/{args.book}/pipeline-workspace/reviews/{section_id}/review-decision.yaml",
                "review_report": f"books/{args.book}/pipeline-workspace/reviews/{section_id}/review-report.md"
            },
            "success_criteria": [
                "review-decision.yaml 中 decision=accept",
                "review-report.md 包含忠实性、可学习性、结构完整性评分",
                "无 required_fixes"
            ],
            "skill": "section-lesson-review"
        }

        tasks.append(author_task)
        tasks.append(review_task)

    # 输出任务包
    output_dir.mkdir(parents=True, exist_ok=True)

    for task in tasks:
        filename = f"{task['section_id']}_{task['task_type']}.json"
        output_path = output_dir / filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(task, f, ensure_ascii=False, indent=2)

        print(f"[OK] 已生成 {output_path}")

    # 报告统计
    generated_count = len(tasks) // 2  # 每个小节 2 个任务
    skipped_count = len(skipped)
    print(f"\n生成完成: {generated_count} 个小节生成任务包, {skipped_count} 个小节跳过")
    if skipped:
        print("跳过的小节（缺少 source-slice）:")
        for s in skipped:
            print(f"  - {s['section_id']}: {s['reason']}")
    print(f"任务包位置: {output_dir}")

    # 显式指定 --section 但 0 个任务生成，返回非 0
    if args.section and len(tasks) == 0:
        sys.exit(1)


def cmd_run_book(args):
    """转发到 run_book.py"""
    from run_book import cmd_run_book as _cmd_run_book
    _cmd_run_book(args)


def cmd_plan_sections(args):
    """生成 section manifest 和标题边界候选方案"""
    from section_planner import plan_sections

    book_root = find_book_root(args.book)
    planner = getattr(args, 'planner', 'deterministic')
    provider = None
    planner_model = None
    if planner == 'hybrid-llm':
        from llm_provider import create_provider, load_provider_config
        provider_config = load_provider_config()
        provider = create_provider(provider_config)
        planner_model = provider_config.planner_model

    result = plan_sections(
        book_root,
        force=getattr(args, 'force', False),
        auto_accept_high=not getattr(args, 'no_auto_accept_high', False),
        planner=planner,
        provider=provider,
        planner_model=planner_model,
    )
    counts = result["counts"]
    print(f"[OK] 已生成候选 manifest: {result['manifest_candidate']}")
    print(f"[OK] 已生成候选边界: {result['hints_candidate']}")
    print(f"[OK] 已生成规划报告: {result['report']}")
    print(f"planner: {planner}")
    print(f"置信度统计: high={counts['high']}, medium={counts['medium']}, low={counts['low']}")


def cmd_review_sections(args):
    """交互式审核 section 边界候选"""
    from section_planner import review_sections

    book_root = find_book_root(args.book)
    result = review_sections(
        book_root,
        list_only=getattr(args, 'list', False),
        section_id=getattr(args, 'section', None),
    )
    print(f"[OK] 已更新 {result['updated']} 条候选: {result['path']}")


def cmd_apply_section_plan(args):
    """应用已审核的 section 规划候选"""
    from section_planner import apply_section_plan

    book_root = find_book_root(args.book)
    result = apply_section_plan(
        book_root,
        allow_pending=getattr(args, 'allow_pending', False),
        force=getattr(args, 'force', False),
    )
    print(f"[OK] 已应用 manifest: {result['manifest']}")
    print(f"[OK] 已应用边界提示: {result['hints']}")
    print(f"已接受边界: {result['accepted_count']}，未审核: {result['pending_count']}")


def main():
    parser = argparse.ArgumentParser(description="PDF to Study KB 流水线 CLI")
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # status 命令
    status_parser = subparsers.add_parser('status', help='显示项目状态')
    status_parser.add_argument('--book', required=True, help='书籍 ID')

    # validate 命令
    validate_parser = subparsers.add_parser('validate', help='校验讲义文件')
    validate_parser.add_argument('--book', required=True, help='书籍 ID')
    validate_parser.add_argument('--section', help='小节 ID')
    validate_parser.add_argument('--all', action='store_true', help='校验所有讲义')
    validate_parser.add_argument('--stage', choices=['draft', 'published', 'all'], default='all',
                                 help='校验阶段：draft=staging草稿，published=已发布，all=全部')

    # coverage 命令
    coverage_parser = subparsers.add_parser('coverage', help='显示覆盖报告')
    coverage_parser.add_argument('--book', required=True, help='书籍 ID')

    # publish 命令
    publish_parser = subparsers.add_parser('publish', help='发布讲义到 study-kb')
    publish_parser.add_argument('--book', required=True, help='书籍 ID')
    publish_parser.add_argument('--section', help='小节 ID')
    publish_parser.add_argument('--all-reviewed', action='store_true', help='发布所有已审校讲义')

    # make-tasks 命令
    make_tasks_parser = subparsers.add_parser('make-tasks', help='生成 Claude Code 任务包')
    make_tasks_parser.add_argument('--book', required=True, help='书籍 ID')
    make_tasks_parser.add_argument('--section', help='小节 ID')
    make_tasks_parser.add_argument('--all-registered', action='store_true', help='为所有未发布小节生成任务包')

    # init-book 命令
    init_book_parser = subparsers.add_parser('init-book', help='初始化书籍目录结构')
    init_book_parser.add_argument('--book', required=True, help='书籍 ID（目录名）')
    init_book_parser.add_argument('--pdf', required=True, help='PDF 文件路径')
    init_book_parser.add_argument('--title', required=True, help='书籍标题')
    init_book_parser.add_argument('--force', action='store_true', help='覆盖已有书籍目录')

    # inventory 命令
    inventory_parser = subparsers.add_parser('inventory', help='分析 PDF 结构，生成报告')
    inventory_parser.add_argument('--book', required=True, help='书籍 ID')
    inventory_parser.add_argument('--write', action='store_true', help='写入 section-manifest.yaml')
    inventory_parser.add_argument('--force', action='store_true', help='覆盖已有 manifest')

    # extract 命令
    extract_parser = subparsers.add_parser('extract', help='按 manifest 生成 source-slice')
    extract_parser.add_argument('--book', required=True, help='书籍 ID')
    extract_parser.add_argument('--section', help='小节 ID')
    extract_parser.add_argument('--all', action='store_true', help='批量处理所有小节')
    extract_parser.add_argument('--force', action='store_true', help='覆盖已有 source-slice')

    # mark-reviewed 命令
    mark_reviewed_parser = subparsers.add_parser('mark-reviewed', help='将已通过 review 的小节更新为 reviewed')
    mark_reviewed_parser.add_argument('--book', required=True, help='书籍 ID')
    mark_reviewed_parser.add_argument('--section', help='小节 ID')
    mark_reviewed_parser.add_argument('--all-accepted', action='store_true', help='更新所有 decision=accept 的小节')

    # run-book 命令
    run_book_parser = subparsers.add_parser('run-book', help='全书自动编排')
    run_book_parser.add_argument('--book', required=True, help='书籍 ID')
    run_book_parser.add_argument('--pdf', help='PDF 文件路径（首次运行时使用）')
    run_book_parser.add_argument('--title', help='书籍标题（首次运行时使用）')
    run_book_parser.add_argument('--executor', choices=['claude-code-queue', 'langgraph-worker'],
                                 default='claude-code-queue',
                                 help='执行器：claude-code-queue=生成任务队列，langgraph-worker=直接执行 LLM 图')
    run_book_parser.add_argument('--publish', choices=['accepted-only', 'manual'],
                                 default='accepted-only', help='发布策略')
    run_book_parser.add_argument('--section', help='只处理指定小节')
    run_book_parser.add_argument('--resume', action='store_true', help='从上次中断处继续')
    run_book_parser.add_argument('--dry-run', action='store_true', help='只显示计划，不执行')
    run_book_parser.add_argument('--batch-size', type=int, default=5, help='每批小节数')
    run_book_parser.add_argument('--max-revision-retry', type=int, default=2, help='revise 重试次数')

    # plan-sections 命令
    plan_sections_parser = subparsers.add_parser('plan-sections', help='生成 section 拆分和边界候选')
    plan_sections_parser.add_argument('--book', required=True, help='书籍 ID')
    plan_sections_parser.add_argument('--force', action='store_true', help='覆盖已有候选文件')
    plan_sections_parser.add_argument('--planner', choices=['deterministic', 'hybrid-llm'],
                                      default='deterministic',
                                      help='section 规划器：deterministic=标题规则，hybrid-llm=规则候选+LLM语义修正')
    plan_sections_parser.add_argument('--no-auto-accept-high', action='store_true',
                                      help='不自动接受 high 置信度候选')

    # review-sections 命令
    review_sections_parser = subparsers.add_parser('review-sections', help='审核 section 边界候选')
    review_sections_parser.add_argument('--book', required=True, help='书籍 ID')
    review_sections_parser.add_argument('--section', help='只审核指定小节')
    review_sections_parser.add_argument('--list', action='store_true', help='只列出候选，不进入交互')

    # apply-section-plan 命令
    apply_plan_parser = subparsers.add_parser('apply-section-plan', help='应用已审核的 section 规划候选')
    apply_plan_parser.add_argument('--book', required=True, help='书籍 ID')
    apply_plan_parser.add_argument('--allow-pending', action='store_true',
                                   help='允许仍有未审核中低置信度候选时应用')
    apply_plan_parser.add_argument('--force', action='store_true', help='覆盖已有 source-boundary-hints.yaml')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 执行命令
    commands = {
        'status': cmd_status,
        'validate': cmd_validate,
        'coverage': cmd_coverage,
        'publish': cmd_publish,
        'make-tasks': cmd_make_tasks,
        'init-book': cmd_init_book,
        'inventory': cmd_inventory,
        'extract': cmd_extract,
        'mark-reviewed': cmd_mark_reviewed,
        'run-book': cmd_run_book,
        'plan-sections': cmd_plan_sections,
        'review-sections': cmd_review_sections,
        'apply-section-plan': cmd_apply_section_plan,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
