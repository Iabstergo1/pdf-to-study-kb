import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml


sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _valid_lesson(section_id="SEC-001", title="测试小节", marker=""):
    headings = [
        "学习定位",
        "先记住的结论",
        "必须掌握",
        "首遍可略读",
        "核心概念",
        "模型结构、论证骨架或推导骨架",
        "直觉解释",
        "容易误解的点",
        "与个人知识体系的连接候选",
        "自测问题",
        "何时回原文",
        "原文定位",
    ]
    body = "\n\n".join(f"## {heading}\n\n{marker or '内容'}" for heading in headings)
    return f"""---
id: {section_id}
type: section-lesson
source_title: {title}
source_locator:
  pages: [1, 2]
book_order: "1"
importance: A
difficulty: 2
formula_risk: low
review_status: draft
generation_stage: draft
---

# {title}

{body}
"""


def _make_book(tmp_path, sections=None):
    book_root = tmp_path / "books" / "test-book"
    for rel in [
        "input",
        "config",
        "pipeline-workspace/staging",
        "pipeline-workspace/reviews",
        "pipeline-workspace/tasks",
        "study-kb/Section-Lessons",
        "study-kb/Learning-Maps",
        "study-kb/Source-QA",
    ]:
        (book_root / rel).mkdir(parents=True, exist_ok=True)
    (book_root / "input" / "test.pdf").write_bytes(b"%PDF-1.4 fake")
    sections = sections or [
        {
            "id": "SEC-001",
            "source_order": "1",
            "title": "测试小节",
            "source_locator": {"pages": [1, 2]},
            "status": "registered",
            "formula_risk": "low",
            "publish_status": "not-published",
        }
    ]
    manifest = {
        "book_id": "test-book",
        "total_sections": len(sections),
        "source_pages": 2,
        "sections": sections,
    }
    (book_root / "config" / "section-manifest.yaml").write_text(
        yaml.dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (book_root / "config" / "book-profile.yaml").write_text(
        yaml.dump({"book_id": "test-book", "title": "测试书"}, allow_unicode=True),
        encoding="utf-8",
    )
    for section in sections:
        section_id = section["id"]
        slice_dir = book_root / "pipeline-workspace" / "staging" / section_id
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / "source-slice.md").write_text(
            f"---\nsection_id: {section_id}\nexpanded_pages: [1, 2]\n"
            "needs_boundary_review: false\n---\n\n## 原文内容\n\n"
            "这是一段足够长的测试原文，用于模拟 PDF 切片结果。"
            "这里继续补充若干句正文，确保无人值守执行不会因为内容过短而暂停。\n",
            encoding="utf-8",
        )
    return book_root


def test_provider_loads_openai_compatible_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "LLM_PROVIDER=deepseek",
            "LLM_API_KEY=sk-test",
            "LLM_BASE_URL=https://api.deepseek.com",
            "LLM_MODEL=deepseek-v4-flash",
            "LLM_REVIEW_MODEL=deepseek-v4-pro",
            "LLM_PLANNER_MODEL=deepseek-v4-flash",
        ]),
        encoding="utf-8",
    )

    from llm_provider import load_provider_config

    config = load_provider_config(env_file=env_file, environ={})

    assert config.provider == "deepseek"
    assert config.api_key == "sk-test"
    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-v4-flash"
    assert config.review_model == "deepseek-v4-pro"
    assert config.planner_model == "deepseek-v4-flash"


def test_real_provider_requires_api_key():
    from llm_provider import load_provider_config

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        load_provider_config(environ={
            "LLM_PROVIDER": "openai-compatible",
            "LLM_BASE_URL": "https://example.test/v1",
            "LLM_MODEL": "model",
        })


def test_llm_boundary_decision_low_confidence_requires_human_review():
    from llm_section_planner import apply_llm_decision_to_candidate

    candidate = {
        "title": "测试小节",
        "confidence": "medium",
        "review_status": "pending",
        "start_regex": "old-start",
        "end_regex": "old-end",
    }
    decision = {
        "action": "adjust-boundary",
        "confidence": "low",
        "reason": "相邻小节边界不清楚",
        "start_regex": "new-start",
        "end_regex": "new-end",
    }

    updated = apply_llm_decision_to_candidate(candidate, decision)

    assert updated["start_regex"] == "new-start"
    assert updated["end_regex"] == "new-end"
    assert updated["review_status"] == "needs_human_review"
    assert updated["llm_decision"]["action"] == "adjust-boundary"
    assert "相邻小节边界不清楚" in updated["notes"]


def test_section_graph_accept_writes_draft_and_review(tmp_path):
    from langgraph_worker import WorkerConfig, run_section_graph
    from llm_provider import FakeChatProvider

    book_root = _make_book(tmp_path)
    section = yaml.safe_load(
        (book_root / "config" / "section-manifest.yaml").read_text(encoding="utf-8")
    )["sections"][0]
    provider = FakeChatProvider([
        {"draft": _valid_lesson()},
        {
            "decision": {
                "section_id": "SEC-001",
                "reviewer": "fake",
                "review_date": "2026-05-28",
                "decision": "accept",
                "confidence": "high",
                "scores": {
                    "faithfulness": "PASS",
                    "learnability": "PASS",
                    "importance": "PASS",
                    "source_return": "PASS",
                    "structure": "PASS",
                },
                "required_fixes": [],
                "warnings": [],
                "notes": "",
            },
            "report": "# Review\n\nPASS",
        },
    ])

    result = run_section_graph(
        book_root=book_root,
        book_id="test-book",
        section=section,
        provider=provider,
        config=WorkerConfig(max_revision_retry=1),
        run_dir=tmp_path / "run",
    )

    assert result["status"] == "reviewed"
    assert (book_root / "pipeline-workspace" / "staging" / "SEC-001" / "section-lesson-draft.md").exists()
    decision = yaml.safe_load(
        (book_root / "pipeline-workspace" / "reviews" / "SEC-001" / "review-decision.yaml")
        .read_text(encoding="utf-8")
    )
    assert decision["decision"] == "accept"
    checkpoint = json.loads(
        (tmp_path / "run" / "langgraph-checkpoints" / "SEC-001.json").read_text(encoding="utf-8")
    )
    assert checkpoint["node"] == "write_output"


def test_section_graph_revise_until_retry_limit_then_human_gate(tmp_path):
    from langgraph_worker import WorkerConfig, run_section_graph
    from llm_provider import FakeChatProvider

    book_root = _make_book(tmp_path)
    section = yaml.safe_load(
        (book_root / "config" / "section-manifest.yaml").read_text(encoding="utf-8")
    )["sections"][0]
    revise_decision = {
        "decision": {
            "section_id": "SEC-001",
            "decision": "revise",
            "confidence": "high",
            "scores": {
                "faithfulness": "WARN",
                "learnability": "PASS",
                "importance": "PASS",
                "source_return": "PASS",
                "structure": "PASS",
            },
            "required_fixes": ["补充核心结论"],
            "warnings": [],
            "notes": "需要修订",
        },
        "report": "# Review\n\n需要修订",
    }
    provider = FakeChatProvider([
        {"draft": _valid_lesson(marker="初稿")},
        revise_decision,
        {"draft": _valid_lesson(marker="修订稿")},
        revise_decision,
    ])

    result = run_section_graph(
        book_root=book_root,
        book_id="test-book",
        section=section,
        provider=provider,
        config=WorkerConfig(max_revision_retry=1),
        run_dir=tmp_path / "run",
    )

    assert result["status"] == "needs_human_review"
    assert result["revise_count"] == 1
    assert "超过修订上限" in result["error"]


def test_run_book_langgraph_worker_uses_fake_provider(tmp_path, monkeypatch):
    import pipeline
    from run_book import cmd_run_book

    book_root = _make_book(tmp_path)
    responses = [
        {"draft": _valid_lesson()},
        {
            "decision": {
                "section_id": "SEC-001",
                "decision": "accept",
                "confidence": "high",
                "scores": {
                    "faithfulness": "PASS",
                    "learnability": "PASS",
                    "importance": "PASS",
                    "source_return": "PASS",
                    "structure": "PASS",
                },
                "required_fixes": [],
                "warnings": [],
                "notes": "",
            },
            "report": "# Review\n\nPASS",
        },
    ]
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_FAKE_RESPONSES_JSON", json.dumps(responses, ensure_ascii=False))

    orig_find = pipeline.find_book_root
    orig_load = pipeline.load_manifest
    pipeline.find_book_root = lambda _book: book_root
    pipeline.load_manifest = lambda _root: orig_load(book_root)
    try:
        args = argparse.Namespace(
            book="test-book",
            pdf=None,
            title=None,
            executor="langgraph-worker",
            publish="accepted-only",
            section=None,
            resume=False,
            dry_run=False,
            batch_size=3,
            max_revision_retry=1,
        )
        cmd_run_book(args)
    finally:
        pipeline.find_book_root = orig_find
        pipeline.load_manifest = orig_load

    assert (book_root / "pipeline-workspace" / "staging" / "SEC-001" / "section-lesson-draft.md").exists()
    assert (book_root / "pipeline-workspace" / "reviews" / "SEC-001" / "review-decision.yaml").exists()
    runs = sorted((book_root / "pipeline-workspace" / "runs").iterdir())
    assert (runs[-1] / "langgraph-checkpoints" / "SEC-001.json").exists()
