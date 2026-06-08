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
