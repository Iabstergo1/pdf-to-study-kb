import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm_provider import FakeChatProvider  # noqa: E402


def _unit():
    return {
        "unit_id": "U-001-01",
        "title": "Accepted Unit",
        "source_scope": {"pages": [1]},
        "risk_flags": [],
        "extraction_method": "text",
    }


def _context(**overrides):
    data = {
        "unit_id": "U-001-01",
        "source_pages": [1],
        "evidence_candidates": [
            {
                "evidence_id": "E-U-001-01-0001",
                "page": 1,
                "preview": "source evidence",
                "sha256": "abc",
                "evidence_type": "text",
            }
        ],
        "block_publish": False,
        "risk_flags": [],
    }
    data.update(overrides)
    return data


def _deps(provider, context, config=None, run_estimate=None):
    from langgraph_worker import RuntimeDeps, UnitWorkerConfig

    return RuntimeDeps(
        provider=provider,
        provider_config=SimpleNamespace(provider="fake", model="author", review_model="review", planner_model="planner"),
        config=config or UnitWorkerConfig(max_revision_retry=3),
        pdf_profile={"total_pages": 1, "pages": []},
        memory={},
        prepare_context_func=lambda book_root, unit, pdf_profile: context,
        run_estimate=run_estimate or {"tokens": 0, "cost": 0.0},
    )


def test_unit_graph_accept_writes_staging_review_and_published_note(tmp_path):
    from langgraph_worker import invoke_unit_graph

    provider = FakeChatProvider([
        {"draft": "---\nmanaged_by: pipeline\n---\n\n# Accepted Unit\n\nClaim [E-U-001-01-0001]"},
        {
            "decision": {"decision": "accept", "confidence": "high", "required_fixes": []},
            "report": "## 证据对照表\n\n| claim | evidence |\n| --- | --- |\n\n## 公式风险清单\n\n| risk | note |\n| --- | --- |",
        },
    ])
    book_root = tmp_path / "books" / "phase8-book"

    result = invoke_unit_graph(book_root, "run-1", "phase8-book", _unit(), _deps(provider, _context()))

    assert result["status"] == "published"
    assert (book_root / "pipeline-workspace" / "staging" / "U-001-01" / "section-lesson-draft.md").exists()
    assert (book_root / "pipeline-workspace" / "reviews" / "U-001-01" / "review-report.md").exists()
    assert (book_root / "study-kb" / "Section-Lessons" / "U-001-01.md").exists()
    assert (book_root / "pipeline-workspace" / "checkpoints" / "langgraph.sqlite").exists()
    assert (book_root / "pipeline-workspace" / "state" / "study-kb.sqlite").exists()


def test_unit_graph_context_block_goes_to_review_queue(tmp_path):
    from langgraph_worker import invoke_unit_graph

    provider = FakeChatProvider([])
    book_root = tmp_path / "books" / "phase8-book"
    blocked_context = _context(block_publish=True, risk_flags=["ocr_unavailable"])

    result = invoke_unit_graph(book_root, "run-1", "phase8-book", _unit(), _deps(provider, blocked_context))

    assert result["status"] == "needs_human_review"
    assert result["review_queue_reason"] == "context_blocked"
    assert (book_root / "study-kb" / "Review-Queue" / "U-001-01.md").exists()
    assert not (book_root / "study-kb" / "Section-Lessons" / "U-001-01.md").exists()


def test_unit_graph_revise_over_limit_goes_to_review_queue(tmp_path):
    from langgraph_worker import invoke_unit_graph

    revise_decision = {
        "decision": {"decision": "revise", "confidence": "high", "required_fixes": ["fix"]},
        "report": "## 证据对照表\n\n| claim | evidence |\n| --- | --- |\n\n## 公式风险清单\n\n| risk | note |\n| --- | --- |",
    }
    provider = FakeChatProvider([
        {"draft": "draft 0 [E-U-001-01-0001]"},
        revise_decision,
        {"draft": "draft 1 [E-U-001-01-0001]"},
        revise_decision,
        {"draft": "draft 2 [E-U-001-01-0001]"},
        revise_decision,
        {"draft": "draft 3 [E-U-001-01-0001]"},
        revise_decision,
    ])
    book_root = tmp_path / "books" / "phase8-book"

    result = invoke_unit_graph(book_root, "run-1", "phase8-book", _unit(), _deps(provider, _context()))

    assert result["status"] == "needs_human_review"
    assert result["review_queue_reason"] == "max_revise_attempts"
    assert result["revise_count"] == 3
    assert (book_root / "study-kb" / "Review-Queue" / "U-001-01.md").exists()


def test_unit_graph_missing_evidence_rejects(tmp_path):
    from langgraph_worker import invoke_unit_graph

    provider = FakeChatProvider([
        {"draft": "Claim without evidence"},
    ])
    book_root = tmp_path / "books" / "phase9-book"

    result = invoke_unit_graph(
        book_root,
        "run-1",
        "phase9-book",
        _unit(),
        _deps(provider, _context(evidence_candidates=[])),
    )

    assert result["status"] == "needs_human_review"
    assert result["review_queue_reason"] == "evidence_missing"
    assert not (book_root / "study-kb" / "Section-Lessons" / "U-001-01.md").exists()


def test_review_without_evidence_table_rejects(tmp_path):
    from langgraph_worker import invoke_unit_graph

    provider = FakeChatProvider([
        {"draft": "Claim [E-U-001-01-0001]"},
        {
            "decision": {"decision": "accept", "confidence": "high"},
            "report": "## 公式风险清单\n\n| risk | note |\n| --- | --- |",
        },
    ])
    book_root = tmp_path / "books" / "phase9-book"

    result = invoke_unit_graph(book_root, "run-1", "phase9-book", _unit(), _deps(provider, _context()))

    assert result["status"] == "needs_human_review"
    assert result["review_decision"]["decision"] == "reject"
    assert result["review_decision"]["confidence"] == "low"
    assert result["review_queue_reason"] == "review_rejected"


def test_review_without_formula_table_rejects(tmp_path):
    from langgraph_worker import invoke_unit_graph

    provider = FakeChatProvider([
        {"draft": "Claim [E-U-001-01-0001]"},
        {
            "decision": {"decision": "accept", "confidence": "high"},
            "report": "## 证据对照表\n\n| claim | evidence |\n| --- | --- |",
        },
    ])
    book_root = tmp_path / "books" / "phase9-book"

    result = invoke_unit_graph(book_root, "run-1", "phase9-book", _unit(), _deps(provider, _context()))

    assert result["status"] == "needs_human_review"
    assert result["review_decision"]["decision"] == "reject"
    assert result["review_decision"]["confidence"] == "low"
    assert result["review_queue_reason"] == "review_rejected"


def test_unit_budget_over_limit_goes_to_review_queue(tmp_path):
    from langgraph_worker import UnitWorkerConfig, invoke_unit_graph

    provider = FakeChatProvider([])
    book_root = tmp_path / "books" / "phase12-book"
    config = UnitWorkerConfig(max_revision_retry=3, max_unit_input_tokens=1)
    large_context = _context(text_blocks=[{"text_preview": "x" * 1000}])

    result = invoke_unit_graph(
        book_root,
        "run-1",
        "phase12-book",
        _unit(),
        _deps(provider, large_context, config=config),
    )

    assert result["status"] == "needs_human_review"
    assert result["review_queue_reason"] == "max_unit_input_tokens"
    assert (book_root / "study-kb" / "Review-Queue" / "U-001-01.md").exists()


def test_book_budget_over_limit_pauses_run(tmp_path):
    from langgraph_worker import UnitWorkerConfig, invoke_unit_graph

    provider = FakeChatProvider([])
    book_root = tmp_path / "books" / "phase12-book"
    config = UnitWorkerConfig(max_revision_retry=3, max_book_tokens=10)

    result = invoke_unit_graph(
        book_root,
        "run-1",
        "phase12-book",
        _unit(),
        _deps(provider, _context(), config=config, run_estimate={"tokens": 11, "cost": 0.0}),
    )

    assert result["status"] == "paused"
    assert result["pause_reason"] == "max_book_tokens"
