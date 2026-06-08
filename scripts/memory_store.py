"""Rolling memory store for accepted semantic units."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

import business_db


DEFAULT_MEMORY = {
    "running_book_summary": "",
    "concept_index": {},
    "symbol_index": {},
    "evidence_ledger": [],
    "recent_accepted": [],
}


def new_memory() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_MEMORY)


def prompt_memory_view(memory: dict[str, Any] | None) -> dict[str, Any]:
    """供 LLM prompt 与预算估算使用的精简 rolling memory 投影。

    作者写第 N 个 unit 只需要：全书脉络（running_book_summary）、最近接受的 unit
    摘要、以及已建立的术语表/符号表（保持叙述连贯与术语一致）。它通过自身 context 的
    evidence_candidates 引用证据，**不需要**全书累积的 evidence_ledger——后者是供 run
    结束后 build_obsidian_indexes 建 Claims / Formula-Ledger 的账本，会随章节线性膨胀
    （长书可达数十万字符）。若把它塞进每个 unit 的作者 prompt 和 cost_guard 估算，后半本
    必然撑爆 max_unit_input_tokens 而被错误地挡进 Review-Queue。

    因此这里：丢弃 evidence_ledger；把概念/符号索引压成 term->定义、symbol->含义，
    去掉随书增长的 units 列表与 first_unit。stored memory 不受影响，仍保留完整结构。
    """
    memory = memory or {}

    def _flatten(index: dict[str, Any], value_key: str) -> dict[str, str]:
        flat = {}
        for name, info in (index or {}).items():
            if isinstance(info, dict):
                flat[name] = info.get(value_key, "")
            else:
                flat[name] = info
        return flat

    return {
        "running_book_summary": memory.get("running_book_summary", ""),
        "recent_accepted": memory.get("recent_accepted", []),
        "concept_index": _flatten(memory.get("concept_index", {}), "definition"),
        "symbol_index": _flatten(memory.get("symbol_index", {}), "meaning"),
    }


def compact_running_summary(
    provider,
    provider_config,
    current_summary: str,
    recent_accepted: list[dict[str, Any]],
    target_chars: int,
) -> str:
    response = provider.chat_json(
        system=(
            "你是学习知识库的rolling memory压缩器。"
            "只输出 JSON 对象，字段 running_book_summary。"
            "不得改写概念索引、符号索引或证据账本。"
        ),
        user=yaml.dump({
            "task": "compact_running_book_summary",
            "target_chars": target_chars,
            "current_summary": current_summary,
            "recent_accepted": recent_accepted,
            "constraints": [
                "保留章节顺序、核心概念、关键依赖和未解决风险",
                "删除重复表述和局部措辞",
                "不要引入新事实",
                "输出长度必须小于 target_chars",
            ],
            "output_schema": {"running_book_summary": "string"},
        }, allow_unicode=True, sort_keys=False),
        model=provider_config.planner_model,
        temperature=0.1,
    )
    compacted = response.get("running_book_summary", "").strip()
    if not compacted or len(compacted) > target_chars:
        raise ValueError("memory compaction failed target length")
    return compacted


def _append_unit_summary(memory: dict[str, Any], unit_id: str, summary: str) -> None:
    if summary:
        if memory["running_book_summary"]:
            memory["running_book_summary"] += "\n\n"
        memory["running_book_summary"] += f"[{unit_id}] {summary}"
    memory["recent_accepted"].append({"unit_id": unit_id, "summary": summary})
    memory["recent_accepted"] = memory["recent_accepted"][-2:]


def _merge_concepts(memory: dict[str, Any], unit_id: str, concepts: list[dict[str, Any]]) -> None:
    for concept in concepts:
        term = concept.get("term")
        if not term:
            continue
        entry = memory["concept_index"].setdefault(
            term,
            {
                "definition": concept.get("definition", ""),
                "first_unit": unit_id,
                "units": [],
            },
        )
        if unit_id not in entry["units"]:
            entry["units"].append(unit_id)


def _merge_symbols(memory: dict[str, Any], unit_id: str, symbols: list[dict[str, Any]]) -> None:
    for symbol in symbols:
        name = symbol.get("symbol")
        if not name:
            continue
        entry = memory["symbol_index"].setdefault(
            name,
            {
                "meaning": symbol.get("meaning", ""),
                "first_unit": unit_id,
                "units": [],
            },
        )
        if unit_id not in entry["units"]:
            entry["units"].append(unit_id)


def _append_evidence(
    book_root: Path,
    run_id: str,
    unit_id: str,
    memory: dict[str, Any],
    evidence_items: list[dict[str, Any]],
) -> None:
    for item in evidence_items:
        memory["evidence_ledger"].append(item)
        business_db.record_evidence(
            book_root,
            evidence_id=item["evidence_id"],
            run_id=run_id,
            unit_id=unit_id,
            claim=item.get("claim", ""),
            page=int(item.get("page", 0)),
            source_heading=item.get("source_heading"),
            evidence_type=item.get("evidence_type", "text"),
            payload=item.get("payload", item),
        )


def _load_default_provider():
    from llm_provider import create_provider, load_provider_config

    provider_config = load_provider_config()
    return create_provider(provider_config), provider_config


def merge_concurrent_memories(
    base: dict[str, Any],
    ordered_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """合并一批并发执行的 unit 产出的 memory。

    并发模式下，同一批的每个 unit 都从同一个 ``base`` 出发，各自得到 base + 自身增量。
    本函数按队列顺序把各 unit 的增量合并回 base：running_book_summary 和 recent_accepted
    顺序敏感（按队列序追加），concept_index / symbol_index / evidence_ledger 是可加的。

    不写数据库：evidence 已在各 unit 图内的 update_memory 记录过，这里只做内存合并，
    避免重复写入。"""
    merged = copy.deepcopy(base)
    for key, value in new_memory().items():
        merged.setdefault(key, copy.deepcopy(value))
    base_ev_len = len(base.get("evidence_ledger", []))

    for result in ordered_results:
        # 只有真正 publish（跑过 update_memory）的 unit 才贡献 rolling memory。
        # 进 Review-Queue 的 unit 返回的 memory 仍是未改动的 base（其 recent_accepted 末项是
        # 上一个 unit），若并入会把上一个 unit 的摘要重复追加、污染全书记忆；失败的 unit 则
        # 根本不带 memory 键。统一用 status 过滤。
        if result.get("status") != "published":
            continue
        mem = result.get("memory") or {}
        recent = mem.get("recent_accepted", [])
        if recent:
            entry = recent[-1]  # 每个 unit 的 recent_accepted 末项就是它自己
            _append_unit_summary(merged, entry.get("unit_id", ""), entry.get("summary", ""))
        for term, info in mem.get("concept_index", {}).items():
            if term not in merged["concept_index"]:
                merged["concept_index"][term] = copy.deepcopy(info)
            else:
                for u in info.get("units", []):
                    if u not in merged["concept_index"][term]["units"]:
                        merged["concept_index"][term]["units"].append(u)
        for name, info in mem.get("symbol_index", {}).items():
            if name not in merged["symbol_index"]:
                merged["symbol_index"][name] = copy.deepcopy(info)
            else:
                for u in info.get("units", []):
                    if u not in merged["symbol_index"][name]["units"]:
                        merged["symbol_index"][name]["units"].append(u)
        for item in mem.get("evidence_ledger", [])[base_ev_len:]:
            merged["evidence_ledger"].append(item)
    return merged


def reconstruct_memory_from_db(book_root: Path) -> dict[str, Any]:
    """从持久化业务库重建全书聚合 memory，供 run 结束后构建 Obsidian 索引。

    动机：``build_obsidian_indexes`` 的 Glossary/Symbols/Concept-Cards 来自 concept/symbol
    索引，Claims/Formula-Ledger 来自 evidence_ledger。若直接用某次运行的进程内瞬时 memory，
    则任何局部运行（如 ``--section`` 重跑单个 unit、续跑）都会用残缺 memory 覆盖全局索引。
    改为从业务库重建：
      - evidence_ledger：直接读表（INSERT OR REPLACE，按 evidence_id 去重保最新，覆盖全书）。
        evidence_id 按 unit 确定性生成，故重跑某 unit 会原位覆盖其证据，无陈旧残留。
      - concept_index / symbol_index：对「每个 unit 的最新 memory 快照」做并集去重，补齐所有
        unit 的贡献。

    局限：memory 快照是「累积」存储（每个 unit 存的是截至它为止的全量 memory），无法干净隔离
    单个 unit 的概念贡献。因此用 ``--section`` 局部重跑并**重命名/删除**某概念时，其它 unit 旧
    快照里仍含旧名，并集会把它带回来——直到下一次全书重跑刷新所有快照。证据账本不受此影响。
    """
    import business_db

    memory = new_memory()
    memory["evidence_ledger"] = business_db.load_evidence_ledger(book_root)
    for snapshot in business_db.load_latest_memory_snapshots(book_root):
        for term, info in (snapshot.get("concept_index") or {}).items():
            if not isinstance(info, dict):
                continue
            entry = memory["concept_index"].setdefault(
                term,
                {"definition": info.get("definition", ""),
                 "first_unit": info.get("first_unit", ""),
                 "units": []},
            )
            for u in info.get("units", []):
                if u not in entry["units"]:
                    entry["units"].append(u)
        for name, info in (snapshot.get("symbol_index") or {}).items():
            if not isinstance(info, dict):
                continue
            entry = memory["symbol_index"].setdefault(
                name,
                {"meaning": info.get("meaning", ""),
                 "first_unit": info.get("first_unit", ""),
                 "units": []},
            )
            for u in info.get("units", []):
                if u not in entry["units"]:
                    entry["units"].append(u)
    return memory


def update_memory(
    book_root: Path,
    run_id: str,
    unit_id: str,
    memory: dict[str, Any],
    unit_result: dict[str, Any],
    provider=None,
    provider_config=None,
    memory_compact_char_limit: int = 20000,
) -> dict[str, Any]:
    updated = copy.deepcopy(memory)
    for key, value in new_memory().items():
        updated.setdefault(key, copy.deepcopy(value))

    _append_unit_summary(updated, unit_id, unit_result.get("summary", ""))
    _merge_concepts(updated, unit_id, unit_result.get("concepts", []))
    _merge_symbols(updated, unit_id, unit_result.get("symbols", []))
    _append_evidence(book_root, run_id, unit_id, updated, unit_result.get("evidence", []))

    if len(updated["running_book_summary"]) > memory_compact_char_limit:
        if provider is None or provider_config is None:
            provider, provider_config = _load_default_provider()
        target_chars = min(12000, int(memory_compact_char_limit * 0.6))
        updated["running_book_summary"] = compact_running_summary(
            provider,
            provider_config,
            updated["running_book_summary"],
            updated["recent_accepted"],
            target_chars,
        )

    business_db.record_memory_snapshot(book_root, run_id, unit_id, updated)
    return updated
