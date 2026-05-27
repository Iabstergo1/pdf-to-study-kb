"""Book-level deterministic orchestration command for Claude Code queues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from obsidian_output import ObsidianOutputGenerator
from run_state import RunStateManager


@dataclass
class RunBookConfig:
    executor: str = "claude-code-queue"
    publish_policy: str = "accepted-only"
    batch_size: int = 5
    max_revision_retry: int = 2


def cmd_run_book(args):
    """Entry point for pipeline.py run-book."""
    import pipeline

    _validate_args(args)
    book_root = pipeline.find_book_root(args.book)
    manifest = pipeline.load_manifest(book_root)
    sections = _selected_sections(manifest, getattr(args, "section", None))
    pending = [s for s in sections if not _is_published(s)]
    config = _config_from_args(args)

    if getattr(args, "resume", False):
        manager = RunStateManager(book_root)
        run_state = manager.load_latest_run(book_root)
        if run_state is None:
            raise SystemExit("错误：没有找到可恢复的 run")
        _print_resume(run_state, manager)
    elif getattr(args, "dry_run", False):
        run_state = None
    else:
        manager = RunStateManager(book_root)
        run_state = manager.create_run(
            book_id=args.book,
            config=config.__dict__,
            sections=manifest.get("sections", []),
        )

    if getattr(args, "dry_run", False):
        _print_dry_run(args, manifest, sections, pending)
        return

    _generate_claude_code_tasks(args, pending)
    _print_claude_code_queue(args, pending)

    latest_manifest = pipeline.load_manifest(book_root)
    ObsidianOutputGenerator(book_root, latest_manifest).generate_all()
    print(f"[OK] Obsidian 输出已更新: {book_root / 'study-kb'}")

    if run_state is not None:
        manager.update_stage(
            run_state,
            "claude_code_queue",
            "completed",
            pending_sections=len(pending),
        )
        manager.update_stage(run_state, "obsidian_output", "completed")


def _validate_args(args):
    if not getattr(args, "book", None):
        raise SystemExit("错误：必须指定 --book")
    if getattr(args, "executor", "claude-code-queue") != "claude-code-queue":
        raise SystemExit(f"错误：仅支持 --executor claude-code-queue，不支持 {args.executor}")
    if getattr(args, "batch_size", 1) < 1:
        raise SystemExit("错误：--batch-size 必须大于 0")


def _config_from_args(args) -> RunBookConfig:
    return RunBookConfig(
        executor=getattr(args, "executor", "claude-code-queue"),
        publish_policy=getattr(args, "publish", "accepted-only"),
        batch_size=getattr(args, "batch_size", 5),
        max_revision_retry=getattr(args, "max_revision_retry", 2),
    )


def _selected_sections(manifest: dict[str, Any], section_id: str | None) -> list[dict[str, Any]]:
    sections = manifest.get("sections", [])
    if not section_id:
        return sections
    section = next((s for s in sections if s.get("id") == section_id), None)
    if section is None:
        raise SystemExit(f"错误：未找到小节 {section_id}")
    return [section]


def _is_published(section: dict[str, Any]) -> bool:
    return section.get("status") == "published" or section.get("publish_status") == "published"


def _print_dry_run(args, manifest: dict[str, Any],
                   sections: list[dict[str, Any]], pending: list[dict[str, Any]]):
    total = len(sections)
    published = sum(1 for s in sections if _is_published(s))
    batch_size = getattr(args, "batch_size", 5)

    print(f"[DRY-RUN] 书籍：{args.book}")
    print(f"[DRY-RUN] 总小节：{total}")
    print(f"[DRY-RUN] 已 published：{published}")
    print(f"[DRY-RUN] 待处理：{len(pending)}")
    print(f"[DRY-RUN] executor: {getattr(args, 'executor', 'claude-code-queue')}")
    print(f"[DRY-RUN] publish-policy: {getattr(args, 'publish', 'accepted-only')}")
    print("[DRY-RUN]")
    print("[DRY-RUN] 将按以下顺序处理：")
    for idx, batch in enumerate(_batches(pending, batch_size), start=1):
        ids = ", ".join(s["id"] for s in batch)
        print(f"[DRY-RUN]   batch {idx}: {ids}")
    if not pending:
        print("[DRY-RUN]   无待处理小节")

    high_risk = [s for s in pending if s.get("formula_risk") == "high"]
    needs_human = [s for s in manifest.get("sections", []) if s.get("status") == "needs_human_review"]
    failed = [s for s in manifest.get("sections", []) if s.get("status") == "failed"]
    print("[DRY-RUN]")
    print(f"[DRY-RUN] 高风险小节（formula_risk=high）：{len(high_risk)} 个，将正常处理并在 MOC/风险清单/frontmatter 中标记")
    print(f"[DRY-RUN] 需人工处理：{len(needs_human)} 个小节")
    print(f"[DRY-RUN] 可重试的 failed：{len(failed)} 个小节")


def _generate_claude_code_tasks(args, pending: list[dict[str, Any]]):
    """Generate task JSON files for Claude Code to consume."""
    if not pending:
        return

    import argparse
    import pipeline

    if getattr(args, "section", None):
        pipeline.cmd_make_tasks(argparse.Namespace(
            book=args.book,
            section=args.section,
            all_registered=False,
        ))
        return

    pipeline.cmd_make_tasks(argparse.Namespace(
        book=args.book,
        section=None,
        all_registered=True,
    ))


def _print_claude_code_queue(args, pending: list[dict[str, Any]]):
    print(f"[CLAUDE-CODE] 书籍：{args.book}")
    print(f"[CLAUDE-CODE] 待处理 {len(pending)} 个小节")
    print("[CLAUDE-CODE]")
    print("[CLAUDE-CODE] 队列执行顺序（按 source_order）：")
    if not pending:
        print("[CLAUDE-CODE]   无待处理小节")
    for idx, section in enumerate(pending, start=1):
        sid = section["id"]
        print(f"[CLAUDE-CODE]   {idx}. {sid}: author -> section-lesson-authoring")
        print(f"[CLAUDE-CODE]   {idx}. {sid}: review -> section-lesson-review")
    print("[CLAUDE-CODE]")
    print("[CLAUDE-CODE] 在 Claude Code 中发送以下指令：")
    print(
        f"[CLAUDE-CODE]   请读取 books/{args.book}/pipeline-workspace/tasks/ 下的任务包，"
        "按小节顺序执行 author 和 review。每个小节必须先生成 draft，再生成 review-decision.yaml 和 review-report.md。"
    )
    print("[CLAUDE-CODE]")
    print("[CLAUDE-CODE] Claude Code 完成后运行：")
    print(f"[CLAUDE-CODE]   python scripts/pipeline.py mark-reviewed --book {args.book} --all-accepted")
    print(f"[CLAUDE-CODE]   python scripts/pipeline.py publish --book {args.book} --all-reviewed")
    print(f"[CLAUDE-CODE]   python scripts/pipeline.py run-book --book {args.book} --executor claude-code-queue")


def _print_resume(run_state, manager: RunStateManager):
    progress = manager.calculate_progress(run_state)
    print(f"[RESUME] run-id: {run_state.run_id}")
    print(f"[RESUME] 已 published: {progress['published']}")
    print(f"[RESUME] 待处理: {progress['not_started']}")
    print(f"[RESUME] 需人工: {progress['needs_human_review']}")
    print(f"[RESUME] failed: {progress['failed']}")


def _batches(items: list[dict[str, Any]], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]
