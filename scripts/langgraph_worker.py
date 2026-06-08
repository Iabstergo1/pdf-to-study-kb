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
    concepts: list[dict[str, Any]]
    symbols: list[dict[str, Any]]
    questions: list[str]
    claims: list[dict[str, Any]]
    summary: str
    review_queue_reason: str
    budget_result: dict[str, Any]
    pause_reason: str


@dataclass
class UnitWorkerConfig:
    max_revision_retry: int = 3
    author_model: str | None = None
    review_model: str | None = None
    revise_model: str | None = None
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
    "你是 semantic unit 学习讲义作者。只输出 JSON 对象。"
    "draft 字段为完整 Markdown 讲义正文，不要包含 YAML frontmatter（由管线生成）。"
    "每个事实性段落和每个核心结论必须在句末引用至少一个 evidence_id，格式如 [E-section-3.1-0005]。"
    "只能引用 context.evidence_candidates 中存在的 evidence_id；不要编造、改写或省略 evidence_id。"
    "如果某个事实没有可用证据，写 [证据缺失] 并降低结论强度。"
    "【公式引用规则】正文中出现的每个公式，必须优先从 evidence_candidates 中找 evidence_type=ocr 的对应条目，"
    "将其 latex 字段的内容直接嵌入正文（行内公式用 $...$，独立公式用 $$...$$），并在句末引用该 OCR 证据的 evidence_id。"
    "若该公式只有 text 证据没有 ocr 证据，引用 text 证据并在公式后标注 [公式待核]。"
    "若两者都没有，写 [公式缺失]，禁止凭空补全或猜测公式内容。"
    "另外输出以下字段："
    "summary（本 unit 一两句话纯文本摘要，供 rolling memory）；"
    "concepts（数组，每项 {term, definition}，列出本 unit 引入的核心概念及简短定义，没有则空数组）；"
    "symbols（数组，每项 {symbol, meaning}，列出本 unit 出现的数学符号及含义，没有则空数组）；"
    "questions（数组，3-5 个针对本 unit 的自测问题字符串，没有则空数组）；"
    "claims（数组，逐条列出本 unit 的核心论断，每项 {statement, evidence_ids, type}："
    "statement=论断文本；evidence_ids=支撑它的 evidence_id 数组（只能取自 context.evidence_candidates）；"
    "type 三选一——source=对原文的压缩/复述（必须给至少一个有效 evidence_id）、"
    "explanation=你对原文的学习化解释或推导、bridge=个人桥接/类比/联想（可无 evidence_ids）。"
    "凡 type=source 的论断必须有有效 evidence_id；严禁给出 evidence_candidates 中不存在的 id）。"
)


REVISER_EVIDENCE_SYSTEM = (
    "你是 semantic unit 讲义修订员。只输出 JSON，字段 draft 为修订后的完整 Markdown。"
    "修订时必须保留或补齐每个事实性段落和核心结论句末的 evidence_id。"
    "只能引用 context.evidence_candidates 中存在的 evidence_id。"
    "同时重新输出 claims 数组（每项 {statement, evidence_ids, type}，type 为 source/explanation/bridge），"
    "使其与修订后的正文一致；type=source 的论断必须有有效 evidence_id，且不得引用不存在的 id。"
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
        prompt_item = {
            "evidence_id": item["evidence_id"],
            "page": item.get("page"),
            "evidence_type": item.get("evidence_type", "text"),
            "preview": item.get("preview", ""),
        }
        # OCR 证据附带 LaTeX 全文，author 据此把公式嵌入正文（$...$）并引用该证据
        if item.get("evidence_type") == "ocr" and item.get("latex"):
            prompt_item["latex"] = item["latex"]
        items.append(prompt_item)
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
        # 只传精简 memory 视图（去掉随书膨胀的 evidence_ledger），避免后半本撑爆 token 预算
        "memory": memory_store.prompt_memory_view(state.get("memory", {})),
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

    # 用与作者 prompt 一致的精简 memory 视图估算，预算判断才与真实输入相符
    unit_estimate = estimate_unit_tokens(
        state.get("context", {}),
        memory_store.prompt_memory_view(state.get("memory", {})),
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
    from evidence_verifier import normalize_claims

    return {
        "draft": draft,
        "status": "drafted",
        "summary": _coerce_str(response.get("summary")),
        "concepts": _coerce_dict_list(response.get("concepts")),
        "symbols": _coerce_dict_list(response.get("symbols")),
        "questions": _coerce_str_list(response.get("questions")),
        # None 表示模型未输出结构化 claims → verify 退回正则 advisory；list 则走结构化校验
        "claims": normalize_claims(response.get("claims")),
    }


def _coerce_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def verify_evidence(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    from evidence_verifier import verify_note

    validation = verify_note(state.get("draft", ""), state.get("context", {}), claims=state.get("claims"))
    risk_flags = sorted(set(state.get("risk_flags", []) + validation.get("risk_flags", [])))
    validation = {**validation, "risk_flags": risk_flags, "passed": not set(risk_flags).intersection({
        "formula_loss_risk",
        "screenshot_ocr_failed",
        "evidence_missing",
        "evidence_hallucinated",
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


def _revise_model(deps: RuntimeDeps) -> str | None:
    return (
        deps.config.revise_model
        or getattr(deps.provider_config, "revise_model", None)
        or deps.config.author_model
        or getattr(deps.provider_config, "model", None)
    )


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
        model=_revise_model(deps),
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
        _revise_model(deps) or "",
    )
    from evidence_verifier import normalize_claims

    # 始终以修订后的 claims 为准（漏输出则置 None）。不能沿用上一版：旧 source 论断可能已不在
    # 新正文里，却因 evidence_id 仍有效而蒙混过 verify。置 None 后 verify 走回退路径，在新正文
    # 上重新做幻觉 + 零落地校验。
    return {
        "draft": draft,
        "claims": normalize_claims(response.get("claims")),
        "revise_count": state.get("revise_count", 0) + 1,
    }


def update_memory(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    memory = memory_store.update_memory(
        book_root,
        state["run_id"],
        state["unit_id"],
        state.get("memory", memory_store.new_memory()),
        {
            "summary": state.get("summary") or state["draft"][:500],
            "concepts": state.get("concepts", []),
            "symbols": state.get("symbols", []),
            "evidence": _referenced_evidence(state),
        },
        provider=deps.provider,
        provider_config=deps.provider_config,
    )
    business_db.record_event(book_root, state["run_id"], state["unit_id"], "update_memory", "ok", {})
    return {"memory": memory}


def _referenced_evidence(state: UnitGraphState) -> list[dict[str, Any]]:
    """Build evidence-ledger items from evidence_ids actually cited in the draft."""
    from evidence_verifier import extract_evidence_refs

    refs = extract_evidence_refs(state.get("draft", ""))
    candidates = {
        item.get("evidence_id"): item
        for item in state.get("context", {}).get("evidence_candidates", [])
        if item.get("evidence_id")
    }
    items = []
    for evidence_id in sorted(refs):
        candidate = candidates.get(evidence_id)
        if not candidate:
            continue
        items.append({
            "evidence_id": evidence_id,
            "unit_id": state["unit_id"],
            "claim": candidate.get("preview", ""),
            "page": candidate.get("page", 0) or 0,
            "source_heading": None,
            "evidence_type": candidate.get("evidence_type", "text"),
            "payload": candidate,
        })
    return items


def publish_note(book_root: Path, state: UnitGraphState, deps: RuntimeDeps) -> dict[str, Any]:
    from obsidian_indexes import render_lesson

    unit_id = state["unit_id"]
    lesson_dir = book_root / "study-kb" / "Section-Lessons"
    lesson_dir.mkdir(parents=True, exist_ok=True)
    _write_unit_artifacts(book_root, state)
    _write_unit_questions(book_root, unit_id, state.get("questions", []))
    _write_unit_claims(book_root, unit_id, state.get("claims") or [])
    source_pdf = (deps.pdf_profile or {}).get("source_pdf", "")
    content = render_lesson(state["unit"], source_pdf, state.get("memory", {}), state["draft"])
    (lesson_dir / f"{unit_id}.md").write_text(content, encoding="utf-8")
    _remove_managed_review_queue_note(book_root, unit_id)
    business_db.record_event(book_root, state["run_id"], unit_id, "publish_note", "ok", {})
    return {"status": "published"}


def _write_unit_questions(book_root: Path, unit_id: str, questions: list[str]) -> None:
    staging_dir = book_root / "pipeline-workspace" / "staging" / unit_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_unit_claims(book_root: Path, unit_id: str, claims: list[dict[str, Any]]) -> None:
    """落盘结构化 claims，供 Claims 笔记直接渲染（区分 source/explanation/bridge）。"""
    staging_dir = book_root / "pipeline-workspace" / "staging" / unit_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "claims.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
    blocking = {"formula_loss_risk", "screenshot_ocr_failed", "evidence_missing", "evidence_hallucinated"}
    if blocking.intersection(set(state.get("risk_flags", []))):
        return "review_queue"
    return "review"


def route_after_review(state: UnitGraphState) -> str:
    blocking = {"formula_loss_risk", "screenshot_ocr_failed", "evidence_missing", "evidence_hallucinated"}
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
    for reason in ["formula_loss_risk", "screenshot_ocr_failed", "evidence_missing", "evidence_hallucinated"]:
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
