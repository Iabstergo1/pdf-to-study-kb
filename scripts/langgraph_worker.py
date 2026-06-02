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
from typing import Any, Callable, TypedDict

import yaml

import business_db
import memory_store
from llm_provider import create_provider, load_provider_config
from validate_section_lesson import validate_section_lesson


@dataclass
class WorkerConfig:
    max_revision_retry: int = 2
    min_source_chars: int = 50
    author_model: str | None = None
    review_model: str | None = None


class UnitGraphState(TypedDict, total=False):
    run_id: str
    book_id: str
    unit_id: str
    unit: dict[str, Any]
    context: dict[str, Any]
    memory: dict[str, Any]
    draft: str
    validation: dict[str, Any]
    review_decision: dict[str, Any]
    review_report: str
    revise_count: int
    status: str
    risk_flags: list[str]
    errors: list[str]
    review_queue_reason: str
    budget_result: dict[str, Any]
    pause_reason: str


@dataclass
class UnitWorkerConfig:
    max_revision_retry: int = 3
    author_model: str | None = None
    review_model: str | None = None
    max_unit_input_tokens: int = 200000
    max_unit_output_tokens: int = 8000
    max_book_tokens: int = 10000000
    max_book_cost: float = 1000.0


@dataclass
class RuntimeDeps:
    provider: Any
    provider_config: Any
    config: UnitWorkerConfig
    pdf_profile: dict[str, Any]
    memory: dict[str, Any] | None = None
    prepare_context_func: Callable[[Path, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    run_estimate: dict[str, float | int] | None = None


AUTHOR_EVIDENCE_SYSTEM = (
    "你是 semantic unit 学习讲义作者。只输出 JSON，字段 draft 为完整 Markdown。"
    "每个事实性段落和每个核心结论必须在句末引用至少一个 evidence_id，格式如 [E-section-3.1-0005]。"
    "只能引用 context.evidence_candidates 中存在的 evidence_id；不要编造、改写或省略 evidence_id。"
    "如果某个事实没有可用证据，写 [证据缺失] 并降低结论强度。"
)


REVISER_EVIDENCE_SYSTEM = (
    "你是 semantic unit 讲义修订员。只输出 JSON，字段 draft 为修订后的完整 Markdown。"
    "修订时必须保留或补齐每个事实性段落和核心结论句末的 evidence_id。"
    "只能引用 context.evidence_candidates 中存在的 evidence_id。"
)


REVIEW_SYSTEM = (
    "你是 semantic unit 审校员。只输出 JSON，包含 decision 对象和 report Markdown。"
    "decision.decision 必须且只能是 accept、revise 或 reject；不要使用 status=approved。"
    "decision.confidence 必须且只能是 high、medium 或 low；decision.decision=accept 时 confidence 必须是 high 或 medium。"
    "report Markdown 必须包含标题 `## 证据对照表` 和 `## 公式风险清单`，并在每个标题下给出 Markdown 表格。"
)


def _evidence_prompt_items(context: dict[str, Any], limit: int = 80) -> list[dict[str, Any]]:
    items = []
    for item in context.get("evidence_candidates", [])[:limit]:
        if not item.get("evidence_id"):
            continue
        items.append({
            "evidence_id": item["evidence_id"],
            "page": item.get("page"),
            "evidence_type": item.get("evidence_type", "text"),
            "preview": item.get("preview", ""),
        })
    return items


def _author_payload(state: UnitGraphState) -> dict[str, Any]:
    context = state.get("context", {})
    return {
        "unit": state["unit"],
        "context": context,
        "evidence_usage_rules": {
            "required": True,
            "format": "[E-...]",
            "scope": "每个事实性段落和每个核心结论句末至少一个 evidence_id",
            "allowed_evidence": _evidence_prompt_items(context),
        },
        "memory": state.get("memory", {}),
    }


def invoke_unit_graph(
    book_root: Path,
    run_id: str,
    book_id: str,
    unit: dict[str, Any],
    deps: RuntimeDeps,
) -> dict[str, Any]:
    thread_id = f"{run_id}:{unit['unit_id']}"
    checkpoint_path = book_root / "pipeline-workspace" / "checkpoints" / "langgraph.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    business_db.start_run(book_root, run_id, book_id)
    initial_state: UnitGraphState = {
        "run_id": run_id,
        "book_id": book_id,
        "unit_id": unit["unit_id"],
        "unit": unit,
        "revise_count": 0,
        "risk_flags": list(unit.get("risk_flags", [])),
        "errors": [],
        "memory": deps.memory or memory_store.new_memory(),
    }
    with _sqlite_checkpointer(checkpoint_path) as checkpointer:
        checkpointer.setup()
        graph = build_unit_graph(book_root, deps).compile(checkpointer=checkpointer)
        return graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )


def prepare_context(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    if deps.prepare_context_func is not None:
        context = deps.prepare_context_func(book_root, state["unit"], deps.pdf_profile)
    else:
        from unit_context import prepare_unit_context

        context = prepare_unit_context(book_root, state["unit"], deps.pdf_profile)
    risk_flags = sorted(set(state.get("risk_flags", []) + context.get("risk_flags", [])))
    business_db.record_event(book_root, state["run_id"], state["unit_id"], "prepare_context", "ok", {
        "block_publish": context.get("block_publish", False),
        "risk_flags": risk_flags,
    })
    return {"context": context, "risk_flags": risk_flags}


def enforce_unit_budget(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    from cost_guard import enforce_budget, estimate_unit_tokens

    unit_estimate = estimate_unit_tokens(
        state.get("context", {}),
        state.get("memory", {}),
        deps.config.max_unit_output_tokens,
    )
    run_estimate = deps.run_estimate or {"tokens": 0, "cost": 0.0}
    result = enforce_budget(unit_estimate, run_estimate, deps.config)
    business_db.record_event(book_root, state["run_id"], state["unit_id"], "cost_guard", "ok" if result["allowed"] else "blocked", {
        "unit_estimate": unit_estimate,
        "run_estimate": run_estimate,
        "result": result,
    })
    return result


def generate_note(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    response = deps.provider.chat_json(
        system=AUTHOR_EVIDENCE_SYSTEM,
        user=yaml.dump(_author_payload(state), allow_unicode=True, sort_keys=False),
        model=deps.config.author_model or getattr(deps.provider_config, "model", None),
    )
    draft = response.get("draft") or response.get("content")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("author 输出缺少 draft")
    business_db.record_model_call(
        book_root,
        state["run_id"],
        state["unit_id"],
        "generate_note",
        getattr(deps.provider_config, "provider", "unknown"),
        deps.config.author_model or getattr(deps.provider_config, "model", ""),
    )
    return {"draft": draft, "status": "drafted"}


def verify_evidence(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    from evidence_verifier import verify_note

    validation = verify_note(state.get("draft", ""), state.get("context", {}))
    risk_flags = sorted(set(state.get("risk_flags", []) + validation.get("risk_flags", [])))
    validation = {**validation, "risk_flags": risk_flags, "passed": not set(risk_flags).intersection({
        "formula_loss_risk",
        "screenshot_ocr_failed",
        "evidence_missing",
    })}
    business_db.record_event(book_root, state["run_id"], state["unit_id"], "verify_evidence", "ok", validation)
    return {"validation": validation, "risk_flags": risk_flags}


def review_note(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    response = deps.provider.chat_json(
        system=REVIEW_SYSTEM,
        user=yaml.dump({
            "unit": state["unit"],
            "context": state["context"],
            "draft": state["draft"],
            "validation": state.get("validation", {}),
            "required_output_schema": {
                "decision": {
                    "unit_id": state["unit_id"],
                    "decision": "accept|revise|reject",
                    "confidence": "high|medium|low",
                    "required_fixes": [],
                    "warnings": [],
                    "risk_flags": [],
                    "notes": "",
                },
                "report": "必须包含 ## 证据对照表 和 ## 公式风险清单 两个表格",
            },
        }, allow_unicode=True, sort_keys=False),
        model=deps.config.review_model or getattr(deps.provider_config, "review_model", None),
    )
    from review_gate import apply_review_gate

    decision = _normalize_unit_decision(response.get("decision") or response, state["unit_id"])
    report = response.get("report") or response.get("review_report") or "# Review\n\n未提供审校报告"
    decision = apply_review_gate(decision, report)
    risk_flags = sorted(set(state.get("risk_flags", []) + decision.get("risk_flags", [])))
    business_db.record_model_call(
        book_root,
        state["run_id"],
        state["unit_id"],
        "review_note",
        getattr(deps.provider_config, "provider", "unknown"),
        deps.config.review_model or getattr(deps.provider_config, "review_model", ""),
    )
    return {"review_decision": decision, "review_report": report, "risk_flags": risk_flags}


def revise_note(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    response = deps.provider.chat_json(
        system=REVISER_EVIDENCE_SYSTEM,
        user=yaml.dump({
            "unit": state["unit"],
            "context": state.get("context", {}),
            "evidence_usage_rules": _author_payload(state)["evidence_usage_rules"],
            "draft": state["draft"],
            "validation": state.get("validation", {}),
            "review_decision": state["review_decision"],
            "review_report": state.get("review_report", ""),
        }, allow_unicode=True, sort_keys=False),
        model=deps.config.author_model or getattr(deps.provider_config, "model", None),
    )
    draft = response.get("draft") or response.get("content")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("revise 输出缺少 draft")
    business_db.record_model_call(
        book_root,
        state["run_id"],
        state["unit_id"],
        "revise_note",
        getattr(deps.provider_config, "provider", "unknown"),
        deps.config.author_model or getattr(deps.provider_config, "model", ""),
    )
    return {"draft": draft, "revise_count": state.get("revise_count", 0) + 1}


def update_memory(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    memory = memory_store.update_memory(
        book_root,
        state["run_id"],
        state["unit_id"],
        state.get("memory", memory_store.new_memory()),
        {
            "summary": state["draft"][:500],
            "concepts": [],
            "symbols": [],
            "evidence": [],
        },
    )
    business_db.record_event(book_root, state["run_id"], state["unit_id"], "update_memory", "ok", {})
    return {"memory": memory}


def publish_note(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    unit_id = state["unit_id"]
    lesson_dir = book_root / "study-kb" / "Section-Lessons"
    lesson_dir.mkdir(parents=True, exist_ok=True)
    _write_unit_artifacts(book_root, state)
    (lesson_dir / f"{unit_id}.md").write_text(state["draft"], encoding="utf-8")
    _remove_managed_review_queue_note(book_root, unit_id)
    business_db.record_event(book_root, state["run_id"], unit_id, "publish_note", "ok", {})
    return {"status": "published"}


def _remove_managed_review_queue_note(book_root: Path, unit_id: str) -> None:
    path = book_root / "study-kb" / "Review-Queue" / f"{unit_id}.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if "managed_by: pipeline" in text[:1000]:
        path.unlink()


def _write_unit_artifacts(book_root: Path, state: UnitGraphState) -> None:
    unit_id = state["unit_id"]
    staging_dir = book_root / "pipeline-workspace" / "staging" / unit_id
    review_dir = book_root / "pipeline-workspace" / "reviews" / unit_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    if state.get("draft"):
        (staging_dir / "section-lesson-draft.md").write_text(state["draft"], encoding="utf-8")
    if state.get("review_decision"):
        (review_dir / "review-decision.yaml").write_text(
            yaml.dump(state["review_decision"], allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    if state.get("review_report"):
        (review_dir / "review-report.md").write_text(state.get("review_report", ""), encoding="utf-8")


def route_to_review_queue(book_root: Path, state: UnitGraphState, reason: str) -> dict[str, Any]:
    unit_id = state["unit_id"]
    review_queue_dir = book_root / "study-kb" / "Review-Queue"
    review_queue_dir.mkdir(parents=True, exist_ok=True)
    _write_unit_artifacts(book_root, state)
    lines = [
        "---",
        "type: review-queue",
        f"unit_id: {unit_id}",
        f"reason: {reason}",
        "managed_by: pipeline",
        "---",
        "",
        f"# Review Queue: {unit_id}",
        "",
        f"- reason: {reason}",
        f"- risk_flags: {state.get('risk_flags', [])}",
    ]
    (review_queue_dir / f"{unit_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    business_db.record_event(book_root, state["run_id"], unit_id, "route_to_review_queue", reason, {
        "risk_flags": state.get("risk_flags", []),
    })
    state["status"] = "needs_human_review"
    state["review_queue_reason"] = reason
    return dict(state)


def _sqlite_checkpointer(checkpoint_path: Path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    return SqliteSaver.from_conn_string(str(checkpoint_path))


def build_unit_graph(book_root: Path, deps: RuntimeDeps):
    from langgraph.graph import END, StateGraph

    builder = StateGraph(UnitGraphState)
    builder.add_node("prepare_context", lambda state: prepare_context(book_root, state, deps))
    builder.add_node("cost_guard", lambda state: cost_guard_node(book_root, state, deps))
    builder.add_node("generate_note", lambda state: generate_note(book_root, state, deps))
    builder.add_node("verify_evidence", lambda state: verify_evidence(book_root, state, deps))
    builder.add_node("review_note", lambda state: review_note(book_root, state, deps))
    builder.add_node("revise_note", lambda state: revise_note(book_root, state, deps))
    builder.add_node("update_memory", lambda state: update_memory(book_root, state, deps))
    builder.add_node("publish_note", lambda state: publish_note(book_root, state, deps))
    builder.add_node(
        "route_to_review_queue",
        lambda state: route_to_review_queue(book_root, state, reason=_auto_review_queue_reason(state)),
    )

    builder.set_entry_point("prepare_context")
    builder.add_conditional_edges(
        "prepare_context",
        route_after_prepare_context,
        {"generate": "cost_guard", "review_queue": "route_to_review_queue"},
    )
    builder.add_conditional_edges(
        "cost_guard",
        route_after_cost_guard,
        {"generate": "generate_note", "review_queue": "route_to_review_queue", "paused": END},
    )
    builder.add_edge("generate_note", "verify_evidence")
    builder.add_conditional_edges(
        "verify_evidence",
        route_after_verify_evidence,
        {"review": "review_note", "review_queue": "route_to_review_queue"},
    )
    builder.add_conditional_edges(
        "review_note",
        route_after_review,
        {"accept": "update_memory", "revise": "revise_note", "review_queue": "route_to_review_queue"},
    )
    builder.add_edge("revise_note", "verify_evidence")
    builder.add_edge("update_memory", "publish_note")
    builder.add_edge("publish_note", END)
    builder.add_edge("route_to_review_queue", END)
    return builder


def cost_guard_node(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    budget = enforce_unit_budget(book_root, state, deps)
    if budget.get("allowed"):
        return {"budget_result": budget}
    if budget.get("scope") == "book":
        business_db.finish_run(book_root, state["run_id"], "paused")
        business_db.record_event(book_root, state["run_id"], state["unit_id"], "cost_guard", "paused", budget)
        return {"budget_result": budget, "status": "paused", "pause_reason": budget["reason"]}
    return {"budget_result": budget, "review_queue_reason": budget["reason"]}


def route_after_prepare_context(state: UnitGraphState) -> str:
    if state.get("context", {}).get("block_publish"):
        return "review_queue"
    return "generate"


def route_after_cost_guard(state: UnitGraphState) -> str:
    budget = state.get("budget_result", {"allowed": True})
    if budget.get("allowed"):
        return "generate"
    if budget.get("scope") == "book":
        return "paused"
    return "review_queue"


def route_after_verify_evidence(state: UnitGraphState) -> str:
    blocking = {"formula_loss_risk", "screenshot_ocr_failed", "evidence_missing"}
    if blocking.intersection(set(state.get("risk_flags", []))):
        return "review_queue"
    return "review"


def route_after_review(state: UnitGraphState) -> str:
    blocking = {"formula_loss_risk", "screenshot_ocr_failed", "evidence_missing"}
    if blocking.intersection(set(state.get("risk_flags", []))):
        return "review_queue"
    decision = state.get("review_decision", {}).get("decision", "reject")
    confidence = state.get("review_decision", {}).get("confidence", "low")
    if decision == "accept" and confidence != "low":
        return "accept"
    if decision == "revise" and confidence != "low" and state.get("revise_count", 0) < 3:
        return "revise"
    return "review_queue"


def _auto_review_queue_reason(state: UnitGraphState) -> str:
    if state.get("review_queue_reason"):
        return state["review_queue_reason"]
    if state.get("context", {}).get("block_publish"):
        return "context_blocked"
    if route_after_verify_evidence(state) == "review_queue":
        return _first_blocking_reason(state)
    return _review_queue_reason_after_review(state)


def _first_blocking_reason(state: UnitGraphState) -> str:
    for reason in ["formula_loss_risk", "screenshot_ocr_failed", "evidence_missing"]:
        if reason in state.get("risk_flags", []):
            return reason
    return "validation_failed"


def _review_queue_reason_after_review(state: UnitGraphState) -> str:
    if state.get("review_decision", {}).get("decision") == "revise" and state.get("revise_count", 0) >= 3:
        return "max_revise_attempts"
    return "review_rejected"


def _normalize_unit_decision(raw: dict[str, Any], unit_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("review decision 必须是 JSON 对象")
    decision = dict(raw)
    status = str(decision.get("status", "")).strip().lower()
    status_decision_map = {
        "approved": "accept",
        "approve": "accept",
        "accepted": "accept",
        "pass": "accept",
        "passed": "accept",
        "needs_revision": "revise",
        "needs-revision": "revise",
        "revise": "revise",
        "rejected": "reject",
        "reject": "reject",
        "failed": "reject",
    }
    if "decision" not in decision and status in status_decision_map:
        decision["decision"] = status_decision_map[status]
    decision.setdefault("unit_id", unit_id)
    decision.setdefault("reviewer", "langgraph-worker")
    decision.setdefault("review_date", date.today().isoformat())
    decision.setdefault("decision", "reject")
    decision.setdefault("confidence", "low")
    decision.setdefault("required_fixes", [])
    decision.setdefault("warnings", [])
    decision.setdefault("notes", "")
    if decision["decision"] not in {"accept", "revise", "reject"}:
        decision["decision"] = status_decision_map.get(status, "reject")
    if decision["confidence"] not in {"high", "medium", "low"}:
        decision["confidence"] = "low"
    if (
        decision["decision"] == "accept"
        and decision["confidence"] == "low"
        and status in {"approved", "approve", "accepted", "pass", "passed"}
        and not decision.get("required_fixes")
    ):
        decision["confidence"] = "medium"
    return decision


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
