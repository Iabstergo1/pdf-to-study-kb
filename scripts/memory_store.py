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
