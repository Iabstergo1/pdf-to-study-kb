"""LangGraph-style section worker for unattended author/review execution.

The node functions are dependency-light and testable with a fake provider. When
the optional langgraph package is installed, this module remains the execution
boundary used by ``run-book --executor langgraph-worker``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from llm_provider import create_provider, load_provider_config
from validate_section_lesson import validate_section_lesson


@dataclass
class WorkerConfig:
    max_revision_retry: int = 2
    min_source_chars: int = 50
    author_model: str | None = None
    review_model: str | None = None


def run_langgraph_worker(book_root: Path, args, run_state, plan: dict[str, Any], manager):
    provider_config = load_provider_config()
    provider = create_provider(provider_config)
    config = WorkerConfig(
        max_revision_retry=int(getattr(args, "max_revision_retry", 2)),
        author_model=provider_config.model,
        review_model=provider_config.review_model,
    )
    queue = plan["queue"]
    reviewed = 0
    human = 0
    failed = 0
    skipped = 0

    print(f"[LANGGRAPH] 书籍：{args.book}")
    print(f"[LANGGRAPH] 待执行 {len(queue)} 个小节")
    print(f"[LANGGRAPH] 阻塞 {len(plan['blocked'])} 个小节")

    for section in queue:
        section_id = section["id"]
        if _accepted_outputs_exist(book_root, section_id):
            skipped += 1
            manager.update_section(run_state, section_id, "langgraph", "reviewed", skipped_existing=True)
            continue
        try:
            result = run_section_graph(
                book_root=book_root,
                book_id=args.book,
                section=section,
                provider=provider,
                config=config,
                run_dir=run_state.run_dir,
            )
        except Exception as exc:
            failed += 1
            manager.update_section(run_state, section_id, "langgraph", "failed", error=str(exc))
            print(f"[LANGGRAPH] [FAIL] {section_id}: {exc}")
            continue

        status = result["status"]
        if status == "reviewed":
            reviewed += 1
            manager.update_section(run_state, section_id, "langgraph", "reviewed")
            print(f"[LANGGRAPH] [OK] {section_id}: reviewed")
        elif status == "needs_human_review":
            human += 1
            manager.update_section(
                run_state,
                section_id,
                "langgraph",
                "needs_human_review",
                error=result.get("error", ""),
            )
            print(f"[LANGGRAPH] [HUMAN] {section_id}: {result.get('error', '')}")
        else:
            failed += 1
            manager.update_section(run_state, section_id, "langgraph", "failed", error=result.get("error", ""))
            print(f"[LANGGRAPH] [FAIL] {section_id}: {result.get('error', '')}")

    summary = {
        "reviewed": reviewed,
        "needs_human_review": human,
        "failed": failed,
        "skipped_existing": skipped,
    }
    summary_path = run_state.run_dir / "langgraph-worker-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[LANGGRAPH] summary: reviewed={reviewed}, human={human}, failed={failed}, skipped={skipped}")
    return summary


def run_section_graph(
    book_root: Path,
    book_id: str,
    section: dict[str, Any],
    provider,
    config: WorkerConfig,
    run_dir: Path,
) -> dict[str, Any]:
    """Run one section through load -> author -> validate -> review -> revise."""
    state = {
        "book_id": book_id,
        "section_id": section["id"],
        "section": section,
        "revise_count": 0,
        "status": "not_started",
        "error": "",
    }

    loaded = _load_section(book_root, section, config)
    state.update(loaded)
    if loaded["status"] == "needs_human_review":
        return _checkpoint(run_dir, {**state, "node": "human_interrupt"})
    _checkpoint(run_dir, {**state, "node": "load_section", "status": "loaded"})

    author = _author(provider, config, state)
    state.update(author)
    _checkpoint(run_dir, {**state, "node": "author", "status": "authored"})

    while True:
        validation = validate_section_lesson(state["draft"])
        state["validation"] = validation
        if not validation["passed"]:
            state["status"] = "needs_human_review"
            state["error"] = "validate 失败: " + "; ".join(validation["errors"])
            return _checkpoint(run_dir, {**state, "node": "human_interrupt"})
        _checkpoint(run_dir, {**state, "node": "validate", "status": "validated"})

        review = _review(provider, config, state)
        state.update(review)
        decision = state["review_decision"].get("decision")
        confidence = state["review_decision"].get("confidence", "high")
        _checkpoint(run_dir, {**state, "node": "review", "status": decision})

        if decision == "accept" and confidence != "low":
            _write_outputs(book_root, state)
            state["status"] = "reviewed"
            return _checkpoint(run_dir, {**state, "node": "write_output"})

        if decision == "revise" and confidence != "low":
            if state["revise_count"] >= config.max_revision_retry:
                state["status"] = "needs_human_review"
                state["error"] = "超过修订上限"
                return _checkpoint(run_dir, {**state, "node": "human_interrupt"})
            revised = _revise(provider, config, state)
            state.update(revised)
            state["revise_count"] += 1
            _checkpoint(run_dir, {**state, "node": "revise", "status": "revised"})
            continue

        state["status"] = "needs_human_review"
        state["error"] = f"review decision={decision}, confidence={confidence}"
        return _checkpoint(run_dir, {**state, "node": "human_interrupt"})


def _load_section(book_root: Path, section: dict[str, Any], config: WorkerConfig) -> dict[str, Any]:
    section_id = section["id"]
    path = book_root / "pipeline-workspace" / "staging" / section_id / "source-slice.md"
    if not path.exists():
        return {"status": "needs_human_review", "error": "source-slice.md 不存在"}
    content = path.read_text(encoding="utf-8", errors="replace")
    if "needs_boundary_review: true" in content[:1000]:
        return {"status": "needs_human_review", "error": "source-slice 标记 needs_boundary_review=true"}
    if "## 原文内容" not in content:
        return {"status": "needs_human_review", "error": "source-slice.md 缺少 ## 原文内容"}
    source_content = content.split("## 原文内容", 1)[1].strip()
    if len(source_content) < config.min_source_chars:
        return {"status": "needs_human_review", "error": "source-slice 原文内容过短"}
    return {
        "source_slice_path": str(path),
        "source_content": source_content,
        "source_slice": content,
        "status": "loaded",
    }


def _author(provider, config: WorkerConfig, state: dict[str, Any]) -> dict[str, Any]:
    response = provider.chat_json(
        system="你是学习讲义作者。只输出 JSON，字段 draft 为完整 Markdown 讲义。",
        user=_author_prompt(state),
        model=config.author_model,
    )
    draft = response.get("draft") or response.get("content")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("author 输出缺少 draft")
    return {"draft": draft}


def _review(provider, config: WorkerConfig, state: dict[str, Any]) -> dict[str, Any]:
    response = provider.chat_json(
        system="你是学习讲义审校员。只输出 JSON，包含 decision 对象和 report Markdown。",
        user=_review_prompt(state),
        model=config.review_model,
    )
    decision = _normalize_decision(response.get("decision") or response, state["section_id"])
    report = response.get("report") or response.get("review_report") or "# Review\n\n未提供审校报告"
    return {"review_decision": decision, "review_report": report}


def _revise(provider, config: WorkerConfig, state: dict[str, Any]) -> dict[str, Any]:
    response = provider.chat_json(
        system="你是学习讲义修订员。只输出 JSON，字段 draft 为修订后的完整 Markdown 讲义。",
        user=_revise_prompt(state),
        model=config.author_model,
    )
    draft = response.get("draft") or response.get("content")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("revise 输出缺少 draft")
    return {"draft": draft}


def _normalize_decision(raw: dict[str, Any], section_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("review decision 必须是 JSON 对象")
    decision = dict(raw)
    decision.setdefault("section_id", section_id)
    decision.setdefault("reviewer", "langgraph-worker")
    decision.setdefault("review_date", date.today().isoformat())
    decision.setdefault("decision", "reject")
    decision.setdefault("confidence", "low")
    decision.setdefault("scores", {})
    decision.setdefault("required_fixes", [])
    decision.setdefault("warnings", [])
    decision.setdefault("notes", "")
    if decision["decision"] not in {"accept", "revise", "reject"}:
        decision["decision"] = "reject"
    return decision


def _write_outputs(book_root: Path, state: dict[str, Any]):
    section_id = state["section_id"]
    draft_dir = book_root / "pipeline-workspace" / "staging" / section_id
    review_dir = book_root / "pipeline-workspace" / "reviews" / section_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "section-lesson-draft.md").write_text(state["draft"], encoding="utf-8")
    (review_dir / "review-decision.yaml").write_text(
        yaml.dump(state["review_decision"], allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (review_dir / "review-report.md").write_text(state["review_report"], encoding="utf-8")


def _checkpoint(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    checkpoint_dir = Path(run_dir) / "langgraph-checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    serializable = _checkpoint_view(state)
    serializable["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = checkpoint_dir / f"{state['section_id']}.json"
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    return serializable


def _checkpoint_view(state: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "book_id", "section_id", "node", "status", "error", "revise_count",
        "validation", "review_decision", "source_slice_path",
    ]
    return {key: state.get(key) for key in keys if key in state}


def _accepted_outputs_exist(book_root: Path, section_id: str) -> bool:
    draft = book_root / "pipeline-workspace" / "staging" / section_id / "section-lesson-draft.md"
    decision_path = book_root / "pipeline-workspace" / "reviews" / section_id / "review-decision.yaml"
    if not draft.exists() or not decision_path.exists():
        return False
    try:
        decision = yaml.safe_load(decision_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    return decision.get("decision") == "accept" and not (decision.get("required_fixes") or [])


def _author_prompt(state: dict[str, Any]) -> str:
    section = state["section"]
    book_id = state["book_id"]
    formula_risk = section.get("formula_risk", "unknown")
    if formula_risk not in ("low", "medium", "high"):
        formula_risk = "medium"
    pages = section.get("source_locator", {}).get("pages", [])
    return yaml.dump({
        "task": "根据 source_content 生成完整小节学习讲义",
        "section": section,
        "source_content": state["source_content"],
        "required_output": {
            "draft": (
                "完整 Markdown，必须以 YAML frontmatter 开头（--- 包裹），"
                "然后是 12 个必备章节。格式如下：\n\n"
                "---\n"
                f"id: {section['id']}\n"
                "type: section-lesson\n"
                f"source_title: \"{book_id}\"\n"
                "source_locator:\n"
                f"  pages: {pages}\n"
                f"book_order: \"{section.get('source_order', '')}\"\n"
                "importance: B\n"
                "difficulty: 3\n"
                f"formula_risk: {formula_risk}\n"
                "review_status: draft\n"
                "generation_stage: draft\n"
                "---\n\n"
                f"# {section.get('title', section['id'])}\n\n"
                "## 学习定位\n...\n\n"
                "## 先记住的结论\n...\n\n"
                "## 必须掌握\n...\n\n"
                "## 首遍可略读\n...\n\n"
                "## 核心概念\n...\n\n"
                "## 模型结构、论证骨架或推导骨架\n...\n\n"
                "## 直觉解释\n...\n\n"
                "## 容易误解的点\n...\n\n"
                "## 与个人知识体系的连接候选\n...\n\n"
                "## 自测问题\n...\n\n"
                "## 何时回原文\n...\n\n"
                "## 原文定位\n..."
            ),
        },
    }, allow_unicode=True, sort_keys=False)


def _review_prompt(state: dict[str, Any]) -> str:
    return yaml.dump({
        "task": "审校 draft 是否忠实、可学习、结构完整",
        "section": state["section"],
        "source_content": state["source_content"],
        "draft": state["draft"],
        "required_output": {
            "decision": {
                "decision": "accept|revise|reject",
                "confidence": "high|medium|low",
                "scores": "faithfulness/learnability/importance/source_return/structure",
                "required_fixes": [],
                "warnings": [],
                "notes": "",
            },
            "report": "Markdown 审校报告",
        },
    }, allow_unicode=True, sort_keys=False)


def _revise_prompt(state: dict[str, Any]) -> str:
    return yaml.dump({
        "task": "根据审校意见修订 draft，返回完整 Markdown",
        "section": state["section"],
        "source_content": state["source_content"],
        "draft": state["draft"],
        "review_decision": state["review_decision"],
        "review_report": state.get("review_report", ""),
        "required_output": {
            "draft": "完整 Markdown，必须保留 YAML frontmatter（--- 包裹）和全部 12 个必备章节标题",
        },
    }, allow_unicode=True, sort_keys=False)
