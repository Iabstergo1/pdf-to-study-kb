#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI

统一入口，支持以下命令：
- init-book: 初始化书籍目录结构和最小配置
- profile-pdf: 分析 PDF TOC、页码、风险和每页摘要
- plan-units: 生成 semantic-unit-plan.candidates.yaml
- validate-unit-plan: 校验 semantic unit plan 覆盖率
- review-unit-plan: 人工审批 semantic unit plan
- run-book: 全书级编排（unit LangGraph 流程）

用法：
  python scripts/pipeline.py <command> --book <book-id> [options]
"""

import argparse
import sys
from pathlib import Path

import yaml


def find_book_root(book_id: str) -> Path:
    """查找书籍根目录"""
    book_root = Path("books") / book_id
    if not book_root.exists():
        print(f"错误：书籍目录不存在: {book_root}", file=sys.stderr)
        sys.exit(1)
    return book_root


def _ensure_dirs(book_root: Path):
    """创建 book 标准目录结构"""
    dirs = [
        "input",
        "config",
        "pipeline-workspace/reports",
        "pipeline-workspace/staging",
        "pipeline-workspace/reviews",
        "pipeline-workspace/runs",
        "pipeline-workspace/checkpoints",
        "pipeline-workspace/state",
        "pipeline-workspace/events",
        "study-kb/Section-Lessons",
        "study-kb/Concept-Cards",
        "study-kb/Glossary",
        "study-kb/Symbols",
        "study-kb/Formula-Ledger",
        "study-kb/Claims",
        "study-kb/Questions",
        "study-kb/Review-Queue",
        "study-kb/Learning-Maps",
        "study-kb/Source-QA",
        "study-kb/Dashboards",
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

    _ensure_dirs(book_root)

    import shutil
    pdf_dest = book_root / "input" / pdf_path.name
    shutil.copy2(str(pdf_path), str(pdf_dest))
    print(f"[OK] 已复制 PDF 到 {pdf_dest}")

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
    print(f"下一步: python scripts/pipeline.py profile-pdf --book {book_id}")


def cmd_profile_pdf(args):
    """转发到 PDF profile 模块。"""
    from pdf_profile import profile_pdf_command

    profile_pdf_command(find_book_root(args.book), force=getattr(args, "force", False))


def cmd_plan_units(args):
    """转发到 semantic unit planning 模块。"""
    from unit_plan import plan_units_command

    plan_units_command(find_book_root(args.book), force=getattr(args, "force", False))


def cmd_validate_unit_plan(args):
    """转发到 semantic unit plan 校验模块。"""
    from unit_plan import validate_unit_plan_command

    validate_unit_plan_command(find_book_root(args.book))


def cmd_review_unit_plan(args):
    """转发到 semantic unit plan 人工审批模块。"""
    from unit_plan import review_unit_plan_command

    review_unit_plan_command(find_book_root(args.book), list_only=getattr(args, "list", False))


def cmd_run_book(args):
    """转发到 run_book.py（语义 LangGraph 流程）。"""
    from run_book import cmd_run_book as _cmd_run_book
    _cmd_run_book(args)


def main():
    parser = argparse.ArgumentParser(description="PDF to Study KB 流水线 CLI")
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # init-book
    init_book_parser = subparsers.add_parser('init-book', help='初始化书籍目录结构')
    init_book_parser.add_argument('--book', required=True, help='书籍 ID（目录名）')
    init_book_parser.add_argument('--pdf', required=True, help='PDF 文件路径')
    init_book_parser.add_argument('--title', required=True, help='书籍标题')
    init_book_parser.add_argument('--force', action='store_true', help='覆盖已有书籍目录')

    # profile-pdf
    profile_pdf_parser = subparsers.add_parser("profile-pdf", help="分析 PDF TOC、页码、风险和每页摘要")
    profile_pdf_parser.add_argument("--book", required=True, help="书籍 ID")
    profile_pdf_parser.add_argument("--force", action="store_true", help="覆盖已有 profile 输出")

    # plan-units
    plan_units_parser = subparsers.add_parser("plan-units", help="生成 semantic-unit-plan.candidates.yaml")
    plan_units_parser.add_argument("--book", required=True, help="书籍 ID")
    plan_units_parser.add_argument("--force", action="store_true", help="覆盖已有候选规划")

    # validate-unit-plan
    validate_unit_plan_parser = subparsers.add_parser("validate-unit-plan", help="校验 semantic unit plan 覆盖率")
    validate_unit_plan_parser.add_argument("--book", required=True, help="书籍 ID")

    # review-unit-plan
    review_unit_plan_parser = subparsers.add_parser("review-unit-plan", help="人工审批 semantic unit plan")
    review_unit_plan_parser.add_argument("--book", required=True, help="书籍 ID")
    review_unit_plan_parser.add_argument("--list", action="store_true", help="只列出 unit，不进入交互")

    # run-book
    run_book_parser = subparsers.add_parser('run-book', help='全书自动编排（unit LangGraph 流程）')
    run_book_parser.add_argument('--book', required=True, help='书籍 ID')
    run_book_parser.add_argument('--executor', choices=['langgraph-worker'], default='langgraph-worker',
                                 help='执行器：langgraph-worker=直接执行 unit LLM 图')
    run_book_parser.add_argument('--section', help='只处理指定 unit_id')
    run_book_parser.add_argument('--dry-run', action='store_true', help='只显示计划，不执行')
    run_book_parser.add_argument('--batch-size', type=int, default=5, help='dry-run 分批显示的每批大小')
    run_book_parser.add_argument('--max-revision-retry', type=int, default=2, help='revise 重试次数')
    run_book_parser.add_argument('--concurrency', type=int, default=None,
                                 help='并发执行的 unit 数（默认读 RUN_BOOK_CONCURRENCY 或 3；1=完全串行）')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        'init-book': cmd_init_book,
        'profile-pdf': cmd_profile_pdf,
        'plan-units': cmd_plan_units,
        'validate-unit-plan': cmd_validate_unit_plan,
        'review-unit-plan': cmd_review_unit_plan,
        'run-book': cmd_run_book,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
