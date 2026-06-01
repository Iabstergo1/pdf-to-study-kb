"""Token and cost budget guardrails."""

from __future__ import annotations

import json
from typing import Any


def estimate_unit_tokens(
    context: dict[str, Any],
    memory: dict[str, Any],
    output_limit: int,
) -> dict[str, int]:
    input_chars = len(json.dumps(context, ensure_ascii=False)) + len(json.dumps(memory, ensure_ascii=False))
    estimated_input_tokens = max(1, input_chars // 2)
    return {"input_tokens": estimated_input_tokens, "output_tokens": output_limit}


def enforce_budget(
    unit_estimate: dict[str, int],
    run_estimate: dict[str, float | int],
    config,
) -> dict[str, Any]:
    if unit_estimate["input_tokens"] > config.max_unit_input_tokens:
        return {"allowed": False, "scope": "unit", "reason": "max_unit_input_tokens"}
    if run_estimate["tokens"] > config.max_book_tokens:
        return {"allowed": False, "scope": "book", "reason": "max_book_tokens"}
    if run_estimate["cost"] > config.max_book_cost:
        return {"allowed": False, "scope": "book", "reason": "max_book_cost"}
    return {"allowed": True}
