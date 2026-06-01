import argparse
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_run_book_langgraph_dry_run_uses_semantic_unit_plan(monkeypatch, capsys, tmp_path):
    import pipeline
    from run_book import cmd_run_book

    book_root = tmp_path / "books" / "phase-run-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump({
            "book_id": "phase-run-book",
            "total_pages": 2,
            "units": [
                {
                    "unit_id": "U-001-01",
                    "title": "Semantic Unit",
                    "include": True,
                    "review_status": "accepted",
                    "planner_confidence": "high",
                    "source_scope": {"pages": [1, 2]},
                    "formula_risk": "low",
                    "risk_flags": [],
                    "extraction_method": "text",
                }
            ],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (book_root / "config" / "section-manifest.yaml").write_text(
        yaml.dump({
            "book_id": "phase-run-book",
            "sections": [{"id": "SEC-001", "status": "registered"}],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "find_book_root", lambda _book: book_root)

    args = argparse.Namespace(
        book="phase-run-book",
        executor="langgraph-worker",
        publish="accepted-only",
        section=None,
        resume=False,
        dry_run=True,
        batch_size=5,
        max_revision_retry=3,
    )
    cmd_run_book(args)

    output = capsys.readouterr().out
    assert "总 semantic units：1" in output
    assert "U-001-01" in output
    assert "SEC-001" not in output
