import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _plan():
    return {
        "book_id": "phase10-book",
        "total_pages": 2,
        "units": [
            {
                "unit_id": "U-001-01",
                "title": "第一单元",
                "unit_type": "concept",
                "include": True,
                "source_scope": {"pages": [1, 2]},
                "extraction_method": "text",
                "depends_on": [],
                "risk_flags": ["formula_heavy"],
                "formula_risk": "high",
                "planner_confidence": "high",
                "review_status": "accepted",
                "output_targets": ["section-lesson"],
            }
        ],
    }


def _memory():
    return {
        "concept_index": {
            "Nash Equilibrium": {
                "definition": "A stable strategic profile.",
                "first_unit": "U-001-01",
                "units": ["U-001-01"],
            }
        },
        "symbol_index": {
            "x": {
                "meaning": "state variable",
                "first_unit": "U-001-01",
                "units": ["U-001-01"],
            }
        },
        "evidence_ledger": [
            {
                "evidence_id": "E-U-001-01-0001",
                "unit_id": "U-001-01",
                "claim": "Claim text",
                "page": 1,
                "source_heading": "Heading",
                "evidence_type": "text",
            }
        ],
    }


def test_build_obsidian_indexes_generates_full_vault_and_frontmatter(tmp_path):
    from obsidian_indexes import build_obsidian_indexes

    book_root = tmp_path / "books" / "phase10-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump(_plan(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (book_root / "config" / "pdf-profile.yaml").write_text(
        yaml.dump({"source_pdf": "sample.pdf"}, allow_unicode=True),
        encoding="utf-8",
    )
    staging_dir = book_root / "pipeline-workspace" / "staging" / "U-001-01"
    staging_dir.mkdir(parents=True)
    (staging_dir / "section-lesson-draft.md").write_text("# 第一单元\n\n正文", encoding="utf-8")
    review_dir = book_root / "pipeline-workspace" / "reviews" / "U-001-01"
    review_dir.mkdir(parents=True)
    (review_dir / "review-decision.yaml").write_text(
        yaml.dump({"decision": "accept", "confidence": "high"}, allow_unicode=True),
        encoding="utf-8",
    )

    build_obsidian_indexes(book_root, memory=_memory())

    expected = [
        "Home.md",
        "Section-Lessons/U-001-01.md",
        "Concept-Cards/Nash Equilibrium.md",
        "Glossary/Nash Equilibrium.md",
        "Symbols/x.md",
        "Formula-Ledger/U-001-01.md",
        "Claims/U-001-01.md",
        "Questions/U-001-01.md",
        "Learning-Maps/MOC-全书学习地图.md",
        "Source-QA/覆盖率报告.md",
        "Source-QA/高风险清单.md",
        "Dashboards/质量看板.md",
    ]
    for rel in expected:
        assert (book_root / "study-kb" / rel).exists(), f"missing {rel}"

    lesson = (book_root / "study-kb" / "Section-Lessons" / "U-001-01.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(lesson.split("---\n", 2)[1])
    assert frontmatter["type"] == "section-lesson"
    assert frontmatter["unit_id"] == "U-001-01"
    assert frontmatter["status"] == "published"
    assert frontmatter["source_pdf"] == "sample.pdf"
    assert frontmatter["source_pages"] == [1, 2]
    assert frontmatter["managed_by"] == "pipeline"


def test_build_obsidian_indexes_does_not_publish_placeholder_without_draft(tmp_path):
    from obsidian_indexes import build_obsidian_indexes

    book_root = tmp_path / "books" / "phase10-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump(_plan(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    stale_lesson = book_root / "study-kb" / "Section-Lessons" / "U-001-01.md"
    stale_lesson.parent.mkdir(parents=True)
    stale_lesson.write_text("---\nmanaged_by: pipeline\n---\n\n待生成。", encoding="utf-8")

    build_obsidian_indexes(book_root, memory=_memory())

    assert not stale_lesson.exists()
    assert not (book_root / "study-kb" / "Review-Queue" / "U-001-01.md").exists()


def test_build_obsidian_indexes_does_not_publish_review_queue_draft(tmp_path):
    from obsidian_indexes import build_obsidian_indexes

    book_root = tmp_path / "books" / "phase10-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump(_plan(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    staging_dir = book_root / "pipeline-workspace" / "staging" / "U-001-01"
    staging_dir.mkdir(parents=True)
    (staging_dir / "section-lesson-draft.md").write_text("# Draft queued for review", encoding="utf-8")
    review_queue = book_root / "study-kb" / "Review-Queue" / "U-001-01.md"
    review_queue.parent.mkdir(parents=True)
    review_queue.write_text("---\nmanaged_by: pipeline\n---\n\nneeds review", encoding="utf-8")

    build_obsidian_indexes(book_root, memory=_memory())

    assert review_queue.exists()
    assert not (book_root / "study-kb" / "Section-Lessons" / "U-001-01.md").exists()


def test_build_obsidian_indexes_removes_stale_review_queue_outside_plan(tmp_path):
    from obsidian_indexes import build_obsidian_indexes

    book_root = tmp_path / "books" / "phase10-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump(_plan(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    stale = book_root / "study-kb" / "Review-Queue" / "OLD-UNIT.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("---\nmanaged_by: pipeline\n---\n\nstale", encoding="utf-8")

    build_obsidian_indexes(book_root, memory=_memory())

    assert not stale.exists()


def test_build_obsidian_indexes_does_not_overwrite_human_file(tmp_path):
    from obsidian_indexes import build_obsidian_indexes

    book_root = tmp_path / "books" / "phase10-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump(_plan(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    concept_path = book_root / "study-kb" / "Concept-Cards" / "Nash Equilibrium.md"
    concept_path.parent.mkdir(parents=True)
    concept_path.write_text("# Human note\n\nDo not overwrite.", encoding="utf-8")

    build_obsidian_indexes(book_root, memory=_memory())

    assert concept_path.read_text(encoding="utf-8") == "# Human note\n\nDo not overwrite."
