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


def _workspace_root() -> Path:
    """状态库/staging 锚点：默认 repo 根；STUDY_KB_ROOT 覆盖（测试隔离/多库场景）。"""
    import os
    env = os.environ.get("STUDY_KB_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[1]


def _vault_state_db() -> Path:
    """vault 级单库（不接 --book）；路径 pipeline-workspace/state/study-kb.sqlite。"""
    return _workspace_root() / "pipeline-workspace/state/study-kb.sqlite"


def _staging_dir(source_id: str) -> Path:
    return _workspace_root() / "pipeline-workspace/staging" / source_id


def cmd_add_source(args):
    """注册一个来源到状态库（记 raw 路径为 artifact）。"""
    import state_store
    import hashlib
    db = _vault_state_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    state_store.init_db(db)
    state_store.register_source(db, args.source, domain=args.domain, fmt=args.fmt)
    raw = Path(args.path)
    sha = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else ""
    state_store.record_artifact(db, args.source, kind="raw_source", path=str(raw), sha256=sha)
    print(f"[OK] registered source '{args.source}' (domain={args.domain}, fmt={args.fmt})")


def _raw_path(db, state_store, source_id: str) -> Path:
    for a in state_store.list_artifacts(db, source_id):
        if a["kind"] == "raw_source":
            return Path(a["path"])
    raise SystemExit(f"no raw_source artifact for {source_id}; run add-source first")


def cmd_profile(args):
    """逐页 profile（产出 staging/<source>/pages.jsonl，needs_vision 标记）。"""
    import state_store
    import source_profile
    import json
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "profiled", input_hash=ihash):
        print("[skip] profiled up-to-date")
        return
    state_store.start_stage(db, args.source, "profiled", input_hash=ihash)
    try:
        pages = source_profile.profile_source(raw, fmt=src_row["format"])
        out = _staging_dir(args.source)
        out.mkdir(parents=True, exist_ok=True)
        pages_path = out / "pages.jsonl"
        pages_path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pages),
                              encoding="utf-8")
        ohash = hashlib.sha256(pages_path.read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="pages", path=str(pages_path), sha256=ohash)
        state_store.complete_stage(db, args.source, "profiled", output_hash=ohash)
        n_vision = sum(1 for p in pages if p.get("needs_vision"))
        print(f"[OK] profiled → {len(pages)} pages ({n_vision} needs_vision)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "profiled", error=str(e))
        raise


def cmd_source_convert(args):
    """source-convert：raw → staging/<source>/source.md + 难页 PNG。"""
    import state_store
    import source_convert
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "converted", input_hash=ihash):
        print("[skip] converted up-to-date")
        return
    state_store.start_stage(db, args.source, "converted", input_hash=ihash)
    try:
        out = _staging_dir(args.source)
        res = source_convert.convert(raw, out_dir=out, fmt=src_row["format"])
        # pages.jsonl 已由 profile 阶段产出；convert 内部用同一批纯函数复算 needs_vision，结果一致
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.complete_stage(db, args.source, "converted", output_hash=res["sha256"])
        print(f"[OK] converted → {res['source_md']} (needs_vision pages: {res['needs_vision_pages']})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "converted", error=str(e))
        raise


def cmd_windows(args):
    """确定性 processing windows：source.md → windows.jsonl。"""
    import state_store
    import windowing
    import json
    import hashlib
    db = _vault_state_db()
    out = _staging_dir(args.source)
    source_md = out / "source.md"
    if not source_md.exists():
        raise SystemExit("run source-convert first")
    md = source_md.read_text(encoding="utf-8")
    ihash = hashlib.sha256(md.encode("utf-8")).hexdigest()
    if not state_store.should_run_stage(db, args.source, "windowed", input_hash=ihash):
        print("[skip] windowed up-to-date")
        return
    state_store.start_stage(db, args.source, "windowed", input_hash=ihash)
    try:
        ws = windowing.build_windows(md)
        (out / "windows.jsonl").write_text(
            "\n".join(json.dumps(w, ensure_ascii=False) for w in ws), encoding="utf-8")
        state_store.record_artifact(db, args.source, kind="windows",
                                    path=str(out / "windows.jsonl"), sha256=ihash)
        state_store.complete_stage(db, args.source, "windowed", output_hash=ihash)
        print(f"[OK] windowed → {len(ws)} windows")
    except Exception as e:
        state_store.fail_stage(db, args.source, "windowed", error=str(e))
        raise


def _vault_dir() -> Path:
    """新架构输出 vault（spec §4），与状态库同锚点。"""
    return _workspace_root() / "wiki"


def cmd_rebuild_registry(args):
    """从概念页 frontmatter 确定性重建 concepts/_registry.yaml + aliases.md（派生，勿手改）。"""
    import concept_store
    vault = _vault_dir()
    if not vault.exists():
        print("no wiki/ vault yet")
        return
    metas = concept_store.scan_concept_pages(vault)
    registry, errors, warnings = concept_store.build_registry(metas)
    for w in warnings:
        print(f"[warn] {w}")
    if errors:
        for e in errors:
            print(f"[error] {e}", file=sys.stderr)
        raise SystemExit("registry not written (fix duplicate/missing canonical_id first)")
    sha = concept_store.write_registry(vault, registry)
    concept_store.write_aliases(vault, registry)
    shared = sum(1 for e in registry.values() if e["scope"] == "shared")
    print(f"[OK] registry: {len(registry)} concepts ({shared} shared), sha256={sha[:12]}")


def cmd_workorder(args):
    """生成 source 级 ingest work order（spec §9）：windowed → workorder_ready。"""
    import state_store
    import workorder
    import json
    import hashlib
    db = _vault_state_db()
    src_row = state_store.get_source(db, args.source)
    if src_row is None:
        raise SystemExit(f"unknown source: {args.source}")
    staging = _staging_dir(args.source)
    windows_file = staging / "windows.jsonl"
    if not windows_file.exists():
        raise SystemExit("run windows first")
    ihash = hashlib.sha256(windows_file.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "workorder_ready", input_hash=ihash):
        print("[skip] workorder up-to-date")
        return
    state_store.start_stage(db, args.source, "workorder_ready", input_hash=ihash)
    try:
        wo = workorder.build_workorder(_vault_dir(), source_id=args.source,
                                       domain=src_row["domain"], staging_dir=staging)
        path = workorder.write_workorder(staging, wo)
        ohash = hashlib.sha256(path.read_bytes()).hexdigest()
        state_store.record_work_order(db, args.source, path=str(path),
                                      registry_hash=wo["registry"]["hash"],
                                      write_scope_json=json.dumps(wo["write_scope"]))
        state_store.record_artifact(db, args.source, kind="workorder", path=str(path), sha256=ohash)
        state_store.complete_stage(db, args.source, "workorder_ready", output_hash=ohash)
        print(f"[OK] workorder → {path} (registry {wo['registry']['hash'][:12]})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "workorder_ready", error=str(e))
        raise


def cmd_show_window(args):
    """打印指定 processing window 的源文本（/ingest 逐窗读取用）。"""
    import json
    staging = _staging_dir(args.source)
    md = (staging / "source.md").read_text(encoding="utf-8")
    for line in (staging / "windows.jsonl").read_text(encoding="utf-8").splitlines():
        w = json.loads(line)
        if w["window_id"] == args.window:
            print(md[w["char_start"]:w["char_end"]])
            return
    raise SystemExit(f"window not found: {args.window}")


def cmd_fail(args):
    """维护命令：把崩溃残留的 running 阶段标记为 failed（之后可重跑该阶段）。"""
    import state_store
    db = _vault_state_db()
    state_store.fail_stage(db, args.source, args.stage, error=args.error)
    print(f"[OK] {args.source}/{args.stage} marked failed: {args.error}")


def cmd_status(args):
    """列出每个 source 的阶段/状态（vault 级单库）。"""
    import state_store

    db = _vault_state_db()
    if not db.exists():
        print("no state db yet (run a source through preprocess first)")
        return
    for r in state_store.status_rows(db):
        print(f"{r['source_id']:<28} {r['domain']:<14} {r['current_stage']:<16} {r['current_status']}")


def cmd_next(args):
    """列出每个 source 的下一步人工动作（vault 级单库）。"""
    import state_store

    db = _vault_state_db()
    if not db.exists():
        print("no state db yet")
        return
    for r in state_store.next_actions(db):
        print(f"{r['source_id']:<28} {r['current_stage']:<16} -> {r['next_action']}")


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

    # status / next（新架构：vault 级单库状态视图，不接 --book）
    subparsers.add_parser("status", help="列出每个 source 的阶段/状态（vault 级单库）")
    subparsers.add_parser("next", help="列出每个 source 的下一步人工动作")

    # P1 新架构预处理阶段（vault 级单库，不接 --book）
    asp = subparsers.add_parser("add-source", help="注册一个来源到状态库")
    asp.add_argument("--source", required=True, help="source_id")
    asp.add_argument("--domain", required=True, help="所属领域")
    asp.add_argument("--path", required=True, help="原始文件路径")
    asp.add_argument("--fmt", required=True, choices=["pdf", "md", "docx", "pptx"], help="来源格式")
    for name, help_text in [("profile", "逐页 profile + needs_vision 标记"),
                            ("source-convert", "转成 staging/<source>/source.md + 难页 PNG"),
                            ("windows", "生成确定性 processing windows")]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--source", required=True, help="source_id")
    subparsers.add_parser("rebuild-registry", help="从概念页 frontmatter 重建 _registry.yaml + aliases.md")
    wop = subparsers.add_parser("workorder", help="生成 source 级 ingest work order")
    wop.add_argument("--source", required=True)
    swp = subparsers.add_parser("show-window", help="打印指定 window 的源文本")
    swp.add_argument("--source", required=True)
    swp.add_argument("--window", required=True)
    fp = subparsers.add_parser("fail", help="维护：把崩溃残留的 running 阶段标记为 failed")
    fp.add_argument("--source", required=True, help="source_id")
    fp.add_argument("--stage", required=True, help="卡死的 stage 名")
    fp.add_argument("--error", required=True, help="失败原因（记入 source_stage_runs.error）")

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
        'status': cmd_status,
        'next': cmd_next,
        'add-source': cmd_add_source,
        'profile': cmd_profile,
        'source-convert': cmd_source_convert,
        'windows': cmd_windows,
        'fail': cmd_fail,
        'rebuild-registry': cmd_rebuild_registry,
        'workorder': cmd_workorder,
        'show-window': cmd_show_window,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
