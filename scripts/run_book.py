"""Book-level orchestration: invoke the unit LangGraph for each approved unit."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RunBookConfig:
    executor: str = "langgraph-worker"
    publish_policy: str = "accepted-only"
    batch_size: int = 5
    max_revision_retry: int = 2
    concurrency: int = 3


def cmd_run_book(args):
    """Entry point for ``pipeline.py run-book`` (semantic LangGraph flow only)."""
    import pipeline

    _validate_args(args)
    book_root = pipeline.find_book_root(args.book)
    config = _config_from_args(args)
    if not _semantic_plan_path(book_root).exists():
        raise SystemExit(
            "错误：未找到已审批的 config/semantic-unit-plan.yaml；"
            "请先运行 plan-units → validate-unit-plan → review-unit-plan。"
        )
    _cmd_run_semantic_book(args, book_root, config)


def _validate_args(args):
    if not getattr(args, "book", None):
        raise SystemExit("错误：必须指定 --book")
    executor = getattr(args, "executor", "langgraph-worker")
    if executor != "langgraph-worker":
        raise SystemExit(f"错误：仅支持 --executor langgraph-worker，不支持 {executor}")
    if getattr(args, "batch_size", 1) < 1:
        raise SystemExit("错误：--batch-size 必须大于 0")


def _config_from_args(args) -> RunBookConfig:
    # 并发度：命令行 --concurrency 优先，其次环境变量 RUN_BOOK_CONCURRENCY，默认 3。
    # 设为 1 即退回完全串行（rolling memory 逐 unit 链式，质量最高）。
    concurrency = getattr(args, "concurrency", None)
    if concurrency is None:
        concurrency = int(os.environ.get("RUN_BOOK_CONCURRENCY", "3"))
    concurrency = max(1, int(concurrency))
    return RunBookConfig(
        executor=getattr(args, "executor", "langgraph-worker"),
        publish_policy=getattr(args, "publish", "accepted-only"),
        batch_size=getattr(args, "batch_size", 5),
        max_revision_retry=getattr(args, "max_revision_retry", 2),
        concurrency=concurrency,
    )


def _semantic_plan_path(book_root: Path) -> Path:
    return book_root / "config" / "semantic-unit-plan.yaml"


def _cmd_run_semantic_book(args, book_root: Path, config: RunBookConfig) -> None:
    plan = _load_yaml(_semantic_plan_path(book_root))
    units = _selected_units(plan, getattr(args, "section", None))
    queue_plan = _build_semantic_queue_plan(units)
    if getattr(args, "dry_run", False):
        _print_semantic_dry_run(args, units, queue_plan, config)
        return

    from langgraph_worker import RuntimeDeps, UnitWorkerConfig, invoke_unit_graph
    from llm_provider import create_provider, load_provider_config
    from memory_store import merge_concurrent_memories, new_memory, reconstruct_memory_from_db
    from obsidian_indexes import build_obsidian_indexes

    provider_config = load_provider_config()
    pdf_profile = _load_yaml(book_root / "config" / "pdf-profile.yaml")
    run_id = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    _enable_checkpoint_wal(book_root)
    memory = new_memory()
    unit_config = UnitWorkerConfig(
        max_revision_retry=config.max_revision_retry,
        author_model=provider_config.model,
        review_model=provider_config.review_model,
        revise_model=getattr(provider_config, "revise_model", "") or provider_config.model,
    )

    def run_one_unit(unit: dict[str, Any], base_memory: dict[str, Any]) -> dict[str, Any]:
        # 每个 unit 用独立 provider 实例，避免并发线程共享 provider 内部可变状态（self.calls）
        deps = RuntimeDeps(
            provider=create_provider(provider_config),
            provider_config=provider_config,
            config=unit_config,
            pdf_profile=pdf_profile,
            memory=base_memory,
            run_estimate={"tokens": 0, "cost": 0.0},
        )
        try:
            return invoke_unit_graph(book_root, run_id, args.book, unit, deps)
        except Exception as exc:  # noqa: BLE001 — 单 unit 失败必须隔离，不能拖垮整本书
            # 任一 unit 的硬失败（LLM 非法 JSON、网络耗尽重试、节点异常等）只标记该 unit
            # 失败并落一张 Review-Queue 提示，整本书继续跑后面的 unit。
            # 返回结果不带 memory 键 → merge_concurrent_memories 会跳过它，不污染 rolling memory。
            _record_unit_failure(book_root, run_id, args.book, unit, exc)
            return {"status": "failed", "error": str(exc)}

    results = []
    queue = queue_plan["queue"]
    concurrency = max(1, config.concurrency)
    print(f"[run-book] {len(queue)} 个 unit，并发度={concurrency}")
    for batch in _batches(queue, concurrency):
        base_memory = memory  # 批内所有 unit 共享同一份 rolling memory 快照
        if concurrency == 1 or len(batch) == 1:
            batch_results = [run_one_unit(unit, base_memory) for unit in batch]
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                # map 保持队列顺序，便于 memory 按序合并
                batch_results = list(pool.map(lambda u: run_one_unit(u, base_memory), batch))
        # 批后按队列顺序合并各 unit 的 memory 增量，喂给下一批
        memory = merge_concurrent_memories(base_memory, batch_results)
        for unit, result in zip(batch, batch_results):
            results.append({"unit_id": unit["unit_id"], "status": result.get("status")})

    # 用从业务库重建的全书聚合 memory 构建索引，而非本次运行的进程内瞬时 memory：
    # 这样 --section 局部重跑 / 续跑也产出全书一致的 Glossary/Symbols/Claims/Formula-Ledger，
    # 不会用残缺 memory 覆盖全局索引。
    aggregate_memory = reconstruct_memory_from_db(book_root)
    build_obsidian_indexes(book_root, plan=plan, memory=aggregate_memory)
    summary_path = book_root / "pipeline-workspace" / "runs" / run_id / "semantic-run-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({
        "run_id": run_id,
        "book_id": args.book,
        "results": results,
        "blocked": queue_plan["blocked"],
        "skipped": queue_plan["skipped"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] semantic run summary: {summary_path}")


def _record_unit_failure(book_root: Path, run_id: str, book_id: str, unit: dict[str, Any], exc: Exception) -> None:
    """隔离单个 unit 的硬失败：记录事件 + 写 Review-Queue 提示，不抛出。"""
    import business_db

    unit_id = unit.get("unit_id", "unknown")
    reason = f"run_failed: {type(exc).__name__}: {exc}"
    print(f"[run-book] unit {unit_id} 失败，转入 Review-Queue：{reason}")
    try:
        business_db.start_run(book_root, run_id, book_id)
        business_db.record_event(book_root, run_id, unit_id, "run_unit", "failed", {"error": str(exc)})
    except Exception:  # noqa: BLE001 — 记录失败本身不能再抛
        pass
    try:
        review_queue_dir = book_root / "study-kb" / "Review-Queue"
        review_queue_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "type: review-queue",
            f"unit_id: {unit_id}",
            "reason: run_failed",
            "managed_by: pipeline",
            "---",
            "",
            f"# Review Queue: {unit_id}",
            "",
            f"- reason: {reason}",
            "- 处理建议：单独重跑该 unit "
            f"`python scripts/pipeline.py run-book --book {book_id} --section {unit_id} --concurrency 1`",
        ]
        (review_queue_dir / f"{unit_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _enable_checkpoint_wal(book_root: Path) -> None:
    """对 LangGraph checkpointer 的 SQLite 文件预启 WAL 模式。

    并发执行多个 unit 时，多个线程会各自打开同一 checkpoint 文件写入。WAL 是数据库
    文件级的持久属性，提前设置后，即使 langgraph 内部连接不显式设置，也能享受一写多读、
    减少 "database is locked"。"""
    path = book_root / "pipeline-workspace" / "checkpoints" / "langgraph.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path, timeout=30.0)
        # 先 busy_timeout 再 WAL：切 WAL 模式需短暂独占锁，顺序反了并发下会立刻报锁
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
    except sqlite3.Error:
        pass  # WAL 启用失败不致命，退回默认 journal 模式


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _selected_units(plan: dict[str, Any], unit_id: str | None) -> list[dict[str, Any]]:
    units = plan.get("units", [])
    if not unit_id:
        return units
    unit = next((item for item in units if item.get("unit_id") == unit_id), None)
    if unit is None:
        raise SystemExit(f"错误：未找到 unit {unit_id}")
    return [unit]


def _build_semantic_queue_plan(units: list[dict[str, Any]]) -> dict[str, Any]:
    queue = []
    blocked = []
    skipped = []
    for unit in units:
        unit_id = unit["unit_id"]
        if not unit.get("include", True):
            skipped.append({"unit_id": unit_id, "reason": unit.get("skip_reason", "include=false")})
            continue
        if unit.get("review_status") not in {"accepted", "edited"}:
            blocked.append({"unit_id": unit_id, "reason": f"review_status={unit.get('review_status')}"})
            continue
        queue.append(unit)
    return {"queue": queue, "blocked": blocked, "skipped": skipped}


def _print_semantic_dry_run(args, units: list[dict[str, Any]], plan: dict[str, Any], config: RunBookConfig) -> None:
    batch_size = config.batch_size
    print(f"[DRY-RUN] 书籍：{args.book}")
    print(f"[DRY-RUN] 总 semantic units：{len(units)}")
    print(f"[DRY-RUN] 可执行 units：{len(plan['queue'])}")
    print(f"[DRY-RUN] 阻塞 units：{len(plan['blocked'])}")
    print(f"[DRY-RUN] 跳过 units：{len(plan['skipped'])}")
    print(f"[DRY-RUN] executor: {config.executor}")
    print("[DRY-RUN]")
    print("[DRY-RUN] 将按以下 unit 顺序处理：")
    for idx, batch in enumerate(_batches(plan["queue"], batch_size), start=1):
        ids = ", ".join(unit["unit_id"] for unit in batch)
        print(f"[DRY-RUN]   batch {idx}: {ids}")
    if not plan["queue"]:
        print("[DRY-RUN]   无待处理 unit")
    if plan["blocked"]:
        print("[DRY-RUN] 阻塞 unit 清单：")
        for item in plan["blocked"]:
            print(f"[DRY-RUN]   {item['unit_id']}: {item['reason']}")


def _batches(items: list[dict[str, Any]], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]
