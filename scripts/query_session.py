"""query-session 目录契约 + Q1 确定性检查（spec §7.1/§11；零 LLM）。

session 只落文件系统 pipeline-workspace/query-sessions/<run_id>/，不进 artifacts 表（spec §3.4）。
"""
from __future__ import annotations

import json
from pathlib import Path

_REQUIRED_QUERY = ["question.md", "answer.md"]
_REQUIRED_SAVED_FILES = ["decision.md"]
_REQUIRED_SAVED_LISTS = {  # 文件名 -> 是否必须非空
    "related_pages.json": False,
    "candidate_write_set.json": True,
    "evidence_refs.json": True,
}


def check_session(session_dir, *, saved: bool) -> list[str]:
    """返回问题清单；空列表 = Q1 通过。saved=True 时按 /kb-save 后的完整契约检查。"""
    d = Path(session_dir)
    if not d.is_dir():
        return [f"session dir not found: {d}"]
    problems: list[str] = []
    for name in _REQUIRED_QUERY:
        if not (d / name).exists():
            problems.append(f"missing {name}")
    if not saved:
        return problems
    for name in _REQUIRED_SAVED_FILES:
        if not (d / name).exists():
            problems.append(f"missing {name} (为什么保存/写了哪些页/证据/为何不污染概念)")
    for name, must_be_nonempty in _REQUIRED_SAVED_LISTS.items():
        f = d / name
        if not f.exists():
            problems.append(f"missing {name}")
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{name} is not valid JSON")
            continue
        if not isinstance(data, list):
            problems.append(f"{name} must be a JSON list")
        elif must_be_nonempty and not data:
            problems.append(f"{name} must be non-empty after save")
    return problems
