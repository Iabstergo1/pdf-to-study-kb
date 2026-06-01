"""LLM-assisted section planning helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


HIGH_CONFIDENCE = "high"
MEDIUM_CONFIDENCE = "medium"
LOW_CONFIDENCE = "low"


def apply_llm_decision_to_candidate(
    candidate: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Merge an LLM boundary decision into one deterministic candidate.

    v1 keeps structural merge/split as human-gated suggestions so the manifest
    contract remains stable while still allowing semantic intervention.
    """
    updated = deepcopy(candidate)
    action = str(decision.get("action", "keep")).strip() or "keep"
    confidence = str(decision.get("confidence", updated.get("confidence", "low"))).strip()
    reason = str(decision.get("reason", "")).strip()

    if decision.get("start_regex"):
        updated["start_regex"] = decision["start_regex"]
    if decision.get("end_regex"):
        updated["end_regex"] = decision["end_regex"]
    if confidence in {HIGH_CONFIDENCE, MEDIUM_CONFIDENCE, LOW_CONFIDENCE}:
        updated["confidence"] = confidence

    updated["llm_decision"] = {
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "suggested_pages": decision.get("suggested_pages"),
        "merge_with": decision.get("merge_with"),
        "split_into": decision.get("split_into"),
    }

    notes = [str(updated.get("notes", "")).strip()]
    if reason:
        notes.append(f"LLM: {reason}")
    updated["notes"] = "；".join(note for note in notes if note)

    if action in {"needs_human_review", "merge", "split"}:
        updated["review_status"] = "needs_human_review"
        return updated
    if confidence == LOW_CONFIDENCE:
        updated["review_status"] = "needs_human_review"
    elif confidence == MEDIUM_CONFIDENCE:
        updated["review_status"] = "pending"
    elif action in {"keep", "adjust-boundary"}:
        updated["review_status"] = "accepted"
    else:
        updated["review_status"] = "needs_human_review"
    return updated


def enhance_boundary_candidates(
    book_root: Path,
    hints_payload: dict[str, Any],
    provider,
    planner_model: str,
) -> dict[str, Any]:
    """Apply LLM semantic boundary decisions to generated candidates."""
    book_root = Path(book_root)
    sections = hints_payload.get("sections") or {}
    if not sections:
        return hints_payload

    enhanced = deepcopy(hints_payload)
    enhanced["planner"] = "hybrid-llm"
    enhanced["llm_policy"] = {
        "low_confidence": "needs_human_review",
        "merge_split": "needs_human_review",
    }
    enhanced_sections = {}
    ordered_items = list(sections.items())
    for idx, (section_id, candidate) in enumerate(ordered_items):
        prev_item = ordered_items[idx - 1][1] if idx > 0 else None
        next_item = ordered_items[idx + 1][1] if idx + 1 < len(ordered_items) else None
        decision = provider.chat_json(
            system=_planner_system_prompt(),
            user=_planner_user_prompt(section_id, candidate, prev_item, next_item),
            model=planner_model,
        )
        enhanced_sections[section_id] = apply_llm_decision_to_candidate(candidate, decision)
    enhanced["sections"] = enhanced_sections
    return enhanced


def write_enhanced_hints(path: Path, hints_payload: dict[str, Any]):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(hints_payload, f, allow_unicode=True, sort_keys=False)


def _planner_system_prompt() -> str:
    return (
        "你是 PDF 学习知识库的切片规划器。"
        "你只输出 JSON 对象，不输出 Markdown。"
        "允许 action: keep, adjust-boundary, merge, split, needs_human_review。"
        "confidence 只能是 high, medium, low。"
    )


def _planner_user_prompt(
    section_id: str,
    candidate: dict[str, Any],
    prev_item: dict[str, Any] | None,
    next_item: dict[str, Any] | None,
) -> str:
    payload = {
        "section_id": section_id,
        "candidate": candidate,
        "previous_title": prev_item.get("title") if prev_item else None,
        "next_title": next_item.get("title") if next_item else None,
        "output_schema": {
            "action": "keep|adjust-boundary|merge|split|needs_human_review",
            "confidence": "high|medium|low",
            "reason": "string",
            "start_regex": "optional string",
            "end_regex": "optional string",
            "suggested_pages": "optional [start,end]",
            "merge_with": "optional section id",
            "split_into": "optional list",
        },
    }
    return yaml.dump(payload, allow_unicode=True, sort_keys=False)
