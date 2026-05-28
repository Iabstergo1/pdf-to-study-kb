"""Book-level deterministic orchestration command for Claude Code queues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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
    config = _config_from_args(args)
    manager = RunStateManager(book_root)

    if getattr(args, "resume", False):
        run_state = manager.load_latest_run(book_root)
        if run_state is None:
            raise SystemExit("错误：没有找到可恢复的 run")
        manager.sync_with_manifest(run_state, manifest.get("sections", []))
        _print_resume(run_state, manager)
    elif getattr(args, "dry_run", False):
        run_state = None
    else:
        run_state = manager.create_run(
            book_id=args.book,
            config=config.__dict__,
            sections=manifest.get("sections", []),
        )

    plan = _build_queue_plan(book_root, sections, run_state)

    if getattr(args, "dry_run", False):
        _print_dry_run(args, manifest, sections, plan)
        return

    if config.executor == "langgraph-worker":
        from langgraph_worker import run_langgraph_worker
        summary = run_langgraph_worker(book_root, args, run_state, plan, manager)
        latest_manifest = pipeline.load_manifest(book_root)
        ObsidianOutputGenerator(book_root, latest_manifest).generate_all()
        print(f"[OK] Obsidian 输出已更新: {book_root / 'study-kb'}")
        manager.update_stage(run_state, "langgraph_worker", "completed", **summary)
        if plan["blocked"] or summary.get("needs_human_review"):
            manager.update_stage(
                run_state,
                "automation_readiness",
                "blocked",
                blocked_sections=len(plan["blocked"]) + summary.get("needs_human_review", 0),
            )
        else:
            manager.update_stage(run_state, "automation_readiness", "ready")
        manager.update_stage(run_state, "obsidian_output", "completed")
        return

    _generate_claude_code_tasks(args, plan["queue"])
    queue_meta = _write_queue_files(book_root, args, run_state, plan, config)
    _print_claude_code_queue(args, plan, queue_meta)

    latest_manifest = pipeline.load_manifest(book_root)
    ObsidianOutputGenerator(book_root, latest_manifest).generate_all()
    print(f"[OK] Obsidian 输出已更新: {book_root / 'study-kb'}")

    if run_state is not None:
        manager.update_stage(
            run_state,
            "claude_code_queue",
            "completed",
            queued_sections=len(plan["queue"]),
            blocked_sections=len(plan["blocked"]),
            batch_count=queue_meta["batch_count"],
        )
        if plan["blocked"]:
            manager.update_stage(
                run_state,
                "automation_readiness",
                "blocked",
                blocked_sections=len(plan["blocked"]),
            )
        else:
            manager.update_stage(run_state, "automation_readiness", "ready")
        manager.update_stage(run_state, "obsidian_output", "completed")


def _validate_args(args):
    if not getattr(args, "book", None):
        raise SystemExit("错误：必须指定 --book")
    if getattr(args, "executor", "claude-code-queue") not in {
        "claude-code-queue",
        "langgraph-worker",
    }:
        raise SystemExit(
            f"错误：仅支持 --executor claude-code-queue 或 langgraph-worker，不支持 {args.executor}"
        )
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
                   sections: list[dict[str, Any]], plan: dict[str, Any]):
    total = len(sections)
    published = sum(1 for s in sections if _is_published(s))
    batch_size = getattr(args, "batch_size", 5)

    print(f"[DRY-RUN] 书籍：{args.book}")
    print(f"[DRY-RUN] 总小节：{total}")
    print(f"[DRY-RUN] 已 published：{published}")
    print(f"[DRY-RUN] 可入队：{len(plan['queue'])}")
    print(f"[DRY-RUN] 阻塞：{len(plan['blocked'])}")
    print(f"[DRY-RUN] 待发布 reviewed：{len(plan['publishable'])}")
    print(f"[DRY-RUN] executor: {getattr(args, 'executor', 'claude-code-queue')}")
    print(f"[DRY-RUN] publish-policy: {getattr(args, 'publish', 'accepted-only')}")
    print("[DRY-RUN]")
    print("[DRY-RUN] 将按以下顺序处理：")
    for idx, batch in enumerate(_batches(plan["queue"], batch_size), start=1):
        ids = ", ".join(s["id"] for s in batch)
        print(f"[DRY-RUN]   batch {idx}: {ids}")
    if not plan["queue"]:
        print("[DRY-RUN]   无待处理小节")

    high_risk = [s for s in plan["queue"] if s.get("formula_risk") == "high"]
    needs_human = [s for s in manifest.get("sections", []) if s.get("status") == "needs_human_review"]
    failed = [s for s in manifest.get("sections", []) if s.get("status") == "failed"]
    print("[DRY-RUN]")
    print(f"[DRY-RUN] 高风险小节（formula_risk=high）：{len(high_risk)} 个，将正常处理并在 MOC/风险清单/frontmatter 中标记")
    print(f"[DRY-RUN] 需人工处理：{len(needs_human)} 个小节")
    print(f"[DRY-RUN] 可重试的 failed：{len(failed)} 个小节")
    if plan["blocked"]:
        print("[DRY-RUN] 阻塞清单：")
        for item in plan["blocked"]:
            print(f"[DRY-RUN]   {item['section_id']}: {item['reason']}")


def _generate_claude_code_tasks(args, pending: list[dict[str, Any]]):
    """Generate task JSON files for Claude Code to consume."""
    if not pending:
        return

    import argparse
    import pipeline

    for section in pending:
        pipeline.cmd_make_tasks(argparse.Namespace(
            book=args.book,
            section=section["id"],
            all_registered=False,
        ))


def _print_claude_code_queue(args, plan: dict[str, Any], queue_meta: dict[str, Any]):
    pending = plan["queue"]
    print(f"[CLAUDE-CODE] 书籍：{args.book}")
    print(f"[CLAUDE-CODE] 可入队 {len(pending)} 个小节")
    print(f"[CLAUDE-CODE] 阻塞 {len(plan['blocked'])} 个小节")
    print(f"[CLAUDE-CODE] batch 文件：{queue_meta['batches_dir']}")
    print(f"[CLAUDE-CODE] readiness 报告：{queue_meta['readiness_path']}")
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
        f"[CLAUDE-CODE]   请读取 {queue_meta['queue_path']}，按 batch 顺序执行每个小节的 "
        "author_task 和 review_task。每个小节必须先生成 draft，再生成 review-decision.yaml 和 review-report.md。"
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


def _build_queue_plan(book_root, sections: list[dict[str, Any]], run_state) -> dict[str, Any]:
    queue = []
    blocked = []
    publishable = []
    skipped = []

    for section in sections:
        section_id = section["id"]
        state = run_state.section_states.get(section_id) if run_state else None
        status = _effective_status(section, state)

        if _is_published(section) or status == "published":
            skipped.append({"section_id": section_id, "reason": "已 published"})
            continue

        if status == "reviewed":
            publishable.append(section)
            continue

        if status == "needs_human_review":
            blocked.append({
                "section_id": section_id,
                "reason": "status=needs_human_review",
            })
            continue

        if status == "failed" and state and state.current_attempt >= state.max_attempt:
            blocked.append({
                "section_id": section_id,
                "reason": (
                    f"failed 重试次数已达上限 "
                    f"({state.current_attempt}/{state.max_attempt})"
                ),
            })
            continue

        blocker_reason = _source_slice_blocker(book_root, section)
        if blocker_reason:
            blocked.append({
                "section_id": section_id,
                "reason": blocker_reason,
            })
            continue

        queue.append(section)

    return {
        "queue": queue,
        "blocked": blocked,
        "publishable": publishable,
        "skipped": skipped,
    }


def _effective_status(section: dict[str, Any], state) -> str:
    if _is_published(section):
        return "published"
    manifest_status = section.get("status", "registered")
    if manifest_status in {"reviewed", "needs_human_review", "published"}:
        return manifest_status
    if state is not None and state.status in {
        "failed", "authoring", "validating", "reviewing", "publishing",
        "reviewed", "needs_human_review", "published",
    }:
        return state.status
    if manifest_status == "failed":
        return "failed"
    return "not_started"


def _source_slice_blocker(book_root, section: dict[str, Any]) -> str | None:
    section_id = section["id"]
    path = book_root / "pipeline-workspace" / "staging" / section_id / "source-slice.md"
    if not path.exists():
        return "source-slice.md 不存在，请先运行 extract"

    text = path.read_text(encoding="utf-8", errors="replace")
    if _requires_expanded_page_metadata(section) and "expanded_pages:" not in text[:1000]:
        return "source-slice 缺少 expanded_pages 元数据，可能由旧版 extract 生成，请运行 extract --force"
    if "needs_boundary_review: true" in text[:1000] and not _pages_are_continuous(section):
        return "source-slice 标记 needs_boundary_review=true，需先人工确认边界"
    if "## 原文内容" not in text:
        return "source-slice.md 缺少 ## 原文内容"
    source_text = text.split("## 原文内容", 1)[1].strip()
    if len(source_text) < 50:
        return "source-slice 原文内容过短，需确认 PDF 提取是否成功"
    return None


def _pages_are_continuous(section: dict[str, Any]) -> bool:
    pages = _expanded_pages(section.get("source_locator", {}).get("pages", []))
    if not pages:
        return False
    return pages == list(range(pages[0], pages[-1] + 1))


def _requires_expanded_page_metadata(section: dict[str, Any]) -> bool:
    raw_pages = section.get("source_locator", {}).get("pages", [])
    expanded_pages = _expanded_pages(raw_pages)
    return len(raw_pages) == 2 and len(expanded_pages) > 2


def _expanded_pages(pages: list) -> list[int]:
    if not pages:
        return []
    pages = [int(p) for p in pages]
    if len(pages) == 2 and pages[0] <= pages[1]:
        return list(range(pages[0], pages[1] + 1))
    return pages


def _write_queue_files(book_root, args, run_state, plan: dict[str, Any],
                       config: RunBookConfig) -> dict[str, Any]:
    if run_state is None:
        raise RuntimeError("run_state is required when writing queue files")

    batches_dir = run_state.run_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    for path in batches_dir.glob("batch-*.json"):
        path.unlink()

    batches = list(_batches(plan["queue"], config.batch_size))
    batch_files = []
    for idx, batch in enumerate(batches, start=1):
        batch_path = batches_dir / f"batch-{idx:03d}.json"
        payload = {
            "run_id": run_state.run_id,
            "book_id": args.book,
            "batch_index": idx,
            "batch_count": len(batches),
            "generated_at": _now(),
            "sections": [_task_entry(args.book, section) for section in batch],
            "instructions": [
                "按 sections 顺序执行。",
                "每个 section 先执行 author_task，再执行 review_task。",
                "不要直接写入 study-kb，发布必须通过 pipeline.py publish。",
            ],
        }
        batch_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        batch_files.append(batch_path)

    queue_path = run_state.run_dir / "claude-code-queue.json"
    queue_payload = {
        "run_id": run_state.run_id,
        "book_id": args.book,
        "generated_at": _now(),
        "batch_size": config.batch_size,
        "queue_count": len(plan["queue"]),
        "blocked_count": len(plan["blocked"]),
        "publishable_count": len(plan["publishable"]),
        "batches": [
            {
                "batch_index": idx,
                "path": _as_posix(path),
                "section_ids": [section["id"] for section in batch],
            }
            for idx, (path, batch) in enumerate(zip(batch_files, batches), start=1)
        ],
        "blocked": plan["blocked"],
        "publishable": [
            {"section_id": section["id"], "title": section.get("title", "")}
            for section in plan["publishable"]
        ],
    }
    queue_path.write_text(
        json.dumps(queue_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readiness_path = run_state.run_dir / "automation-readiness.md"
    readiness_path.write_text(
        _render_readiness_report(args, run_state, plan, batch_files, batches),
        encoding="utf-8",
    )

    return {
        "queue_path": _as_posix(queue_path),
        "readiness_path": _as_posix(readiness_path),
        "batches_dir": _as_posix(batches_dir),
        "batch_count": len(batches),
    }


def _task_entry(book_id: str, section: dict[str, Any]) -> dict[str, Any]:
    section_id = section["id"]
    return {
        "section_id": section_id,
        "source_order": section.get("source_order", ""),
        "title": section.get("title", ""),
        "author_task": f"books/{book_id}/pipeline-workspace/tasks/{section_id}_author.json",
        "review_task": f"books/{book_id}/pipeline-workspace/tasks/{section_id}_review.json",
    }


def _render_readiness_report(args, run_state, plan: dict[str, Any],
                             batch_files: list, batches: list[list[dict[str, Any]]]) -> str:
    lines = [
        "# 自动运行就绪报告",
        "",
        f"- run_id: {run_state.run_id}",
        f"- book_id: {args.book}",
        f"- generated_at: {_now()}",
        f"- 可入队小节: {len(plan['queue'])}",
        f"- 阻塞小节: {len(plan['blocked'])}",
        f"- 待发布 reviewed 小节: {len(plan['publishable'])}",
        "",
        "## Batch 队列",
        "",
    ]
    if not batches:
        lines.append("- 无可执行 batch")
    for idx, (path, batch) in enumerate(zip(batch_files, batches), start=1):
        ids = ", ".join(section["id"] for section in batch)
        lines.append(f"- batch {idx:03d}: `{_as_posix(path)}` — {ids}")

    lines.extend(["", "## 阻塞小节", ""])
    if not plan["blocked"]:
        lines.append("- 无")
    for item in plan["blocked"]:
        lines.append(f"- {item['section_id']}: {item['reason']}")

    lines.extend(["", "## 待发布 reviewed 小节", ""])
    if not plan["publishable"]:
        lines.append("- 无")
    for section in plan["publishable"]:
        lines.append(f"- {section['id']}: {section.get('title', '')}")

    lines.extend([
        "",
        "## 后续命令",
        "",
        "```powershell",
        f"python scripts/pipeline.py mark-reviewed --book {args.book} --all-accepted",
        f"python scripts/pipeline.py publish --book {args.book} --all-reviewed",
        f"python scripts/pipeline.py run-book --book {args.book} --executor claude-code-queue --resume",
        "```",
        "",
    ])
    return "\n".join(lines)


def _as_posix(path) -> str:
    return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
