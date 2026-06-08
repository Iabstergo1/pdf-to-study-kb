import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def unit(unit_id, pages, include=True, **overrides):
    data = {
        "unit_id": unit_id,
        "title": f"Unit {unit_id}",
        "unit_type": "concept",
        "include": include,
        "source_scope": {"pages": pages},
        "extraction_method": "text",
        "depends_on": [],
        "risk_flags": [],
        "formula_risk": "low",
        "planner_confidence": "high",
        "review_status": "pending",
        "output_targets": ["section-lesson"],
    }
    if not include:
        data["skip_reason"] = overrides.pop("skip_reason", "目录页")
        data["output_targets"] = []
    data.update(overrides)
    return data


def test_include_false_pages_still_count_as_covered():
    from unit_plan import validate_unit_plan

    plan = {"units": [
        unit("U-001-01", [1], include=False, skip_reason="目录页"),
        unit("U-001-02", [2, 3], include=True),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert result["passed"]


def test_missing_page_blocks_validation():
    from unit_plan import validate_unit_plan

    plan = {"units": [unit("U-001-01", [1], include=True)]}
    result = validate_unit_plan(plan, total_pages=2)
    assert not result["passed"]
    assert "missing pages" in "; ".join(result["errors"])


def test_overlap_without_reason_blocks_validation():
    from unit_plan import validate_unit_plan

    plan = {"units": [
        unit("U-001-01", [1, 2], include=True),
        unit("U-001-02", [2, 3], include=True),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert not result["passed"]
    assert "unexplained overlaps" in "; ".join(result["errors"])


def test_overlap_with_reason_passes():
    from unit_plan import validate_unit_plan

    plan = {"units": [
        unit("U-001-01", [1, 2], include=True, overlap_reason="跨页标题"),
        unit("U-001-02", [2, 3], include=True, overlap_reason="跨页标题"),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert result["passed"]


def test_invalid_fields_block_validation():
    from unit_plan import validate_unit_plan

    plan = {"units": [
        unit(
            "U-001-01",
            [1],
            include=True,
            unit_type="invalid",
            extraction_method="scan",
            output_targets=[],
            depends_on=["U-404-01"],
        )
    ]}
    result = validate_unit_plan(plan, total_pages=1)
    joined = "; ".join(result["errors"])
    assert not result["passed"]
    assert "unit_type" in joined
    assert "extraction_method" in joined
    assert "output_targets" in joined
    assert "unknown depends_on" in joined


def _write_phase3_book(book_root):
    (book_root / "config").mkdir(parents=True)
    (book_root / "pipeline-workspace" / "reports").mkdir(parents=True)
    (book_root / "config" / "pdf-profile.yaml").write_text(
        yaml.dump({
            "book_id": book_root.name,
            "source_pdf": "sample.pdf",
            "total_pages": 2,
            "toc": [{"level": 1, "title": "Intro", "page": 1}],
            "pages": [
                {
                    "page": 1,
                    "summary_200": "intro",
                    "text_length": 100,
                    "formula_risk": "low",
                    "table_risk": "low",
                    "image_count": 0,
                    "blank_variable_risk": "low",
                },
                {
                    "page": 2,
                    "summary_200": "formula",
                    "text_length": 120,
                    "formula_risk": "high",
                    "table_risk": "low",
                    "image_count": 0,
                    "blank_variable_risk": "low",
                },
            ],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (book_root / "config" / "book-profile.yaml").write_text(
        yaml.dump({"book_id": book_root.name, "title": "Sample", "language": "zh"}, allow_unicode=True),
        encoding="utf-8",
    )
    (book_root / "config" / "study-profile.yaml").write_text(
        yaml.dump({"lesson_style": {"density": "medium"}}, allow_unicode=True),
        encoding="utf-8",
    )


def test_plan_units_command_writes_candidates_and_report(monkeypatch, tmp_path):
    from unit_plan import plan_units_command

    book_root = tmp_path / "books" / "phase3-book"
    _write_phase3_book(book_root)
    response = {
        "units": [
            unit("U-001-01", [1], include=True),
            unit("U-001-02", [2], include=True, extraction_method="hybrid", formula_risk="high"),
        ]
    }
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_PLANNER_MODEL", "fake-planner")
    monkeypatch.setenv("LLM_FAKE_RESPONSES_JSON", json.dumps([response], ensure_ascii=False))

    plan_units_command(book_root, force=True)

    candidates_path = book_root / "config" / "semantic-unit-plan.candidates.yaml"
    report_path = book_root / "pipeline-workspace" / "reports" / "unit-planning-report.md"
    assert candidates_path.exists()
    assert report_path.exists()
    candidates = yaml.safe_load(candidates_path.read_text(encoding="utf-8"))
    assert candidates["book_id"] == "phase3-book"
    assert candidates["planner_model"] == "fake-planner"
    assert candidates["total_pages"] == 2
    assert candidates["coverage_validation"]["passed"] is True
    assert "validation: passed" in report_path.read_text(encoding="utf-8")


def test_plan_units_command_normalizes_real_planner_alias_fields(monkeypatch, tmp_path):
    from unit_plan import plan_units_command

    book_root = tmp_path / "books" / "phase3-book"
    _write_phase3_book(book_root)
    response = {
        "planner_model": "semantic-planner-v1",
        "units": [
            {
                "id": "unit-001",
                "title": "模型设定",
                "pages": [1],
                "include": True,
                "depends_on": [],
                "risk_flags": ["formula_risk_high", "table_risk_high"],
                "output_targets": ["lesson_note"],
            },
            {
                "unit_id": "unit-002",
                "title": "同页补充小节",
                "pages": [1, 2],
                "include": True,
                "depends_on": ["unit-001"],
                "risk_flags": {"formula": "low", "table": "high", "image": 0},
                "output_targets": {
                    "core_takeaways": "参与方与时序",
                    "must_master": "角色关系",
                },
            },
        ],
    }
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_PLANNER_MODEL", "fake-planner")
    monkeypatch.setenv("LLM_FAKE_RESPONSES_JSON", json.dumps([response], ensure_ascii=False))

    plan_units_command(book_root, force=True)

    candidates = yaml.safe_load(
        (book_root / "config" / "semantic-unit-plan.candidates.yaml").read_text(encoding="utf-8")
    )
    first, second = candidates["units"]
    assert first["source_scope"]["pages"] == [1]
    assert first["output_targets"] == ["section-lesson"]
    assert first["formula_risk"] == "high"
    assert first["extraction_method"] == "hybrid"
    assert first["review_status"] == "pending"
    assert first["overlap_reason"] == "同一 PDF 页包含多个语义小节"
    assert second["source_scope"]["pages"] == [1, 2]
    assert second["learning_targets"]["core_takeaways"] == "参与方与时序"
    # page 2 is formula_risk=high in the profile, so the profile-aware upgrade
    # promotes the unit covering it to high / hybrid.
    assert second["formula_risk"] == "high"
    assert second["extraction_method"] == "hybrid"
    assert second["overlap_reason"] == "同一 PDF 页包含多个语义小节"
    assert candidates["coverage_validation"]["passed"] is True


def test_validate_unit_plan_command_exits_nonzero_on_missing_page(tmp_path):
    from unit_plan import validate_unit_plan_command

    book_root = tmp_path / "books" / "phase3-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "pipeline-workspace" / "reports").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.candidates.yaml").write_text(
        yaml.dump({
            "book_id": "phase3-book",
            "total_pages": 2,
            "units": [unit("U-001-01", [1], include=True)],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        validate_unit_plan_command(book_root)

    report = (
        book_root / "pipeline-workspace" / "reports" / "unit-plan-validation.md"
    ).read_text(encoding="utf-8")
    assert "passed: false" in report
    assert "missing pages" in report


def _write_candidate_plan(book_root, units):
    (book_root / "config").mkdir(parents=True, exist_ok=True)
    (book_root / "pipeline-workspace" / "reports").mkdir(parents=True, exist_ok=True)
    (book_root / "config" / "semantic-unit-plan.candidates.yaml").write_text(
        yaml.dump({
            "book_id": book_root.name,
            "generated_at": "2026-06-01T00:00:00Z",
            "planner_model": "fake-planner",
            "total_pages": 2,
            "units": units,
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_review_unit_plan_list_prints_unit_summary(capsys, tmp_path):
    from unit_plan import review_unit_plan_command

    book_root = tmp_path / "books" / "phase4-book"
    _write_candidate_plan(book_root, [
        unit("U-001-01", [1], include=True),
        unit("U-001-02", [2], include=False, skip_reason="目录页"),
    ])

    review_unit_plan_command(book_root, list_only=True)

    output = capsys.readouterr().out
    assert "U-001-01" in output
    assert "pages=[1]" in output
    assert "include=True" in output
    assert "formula=low" in output


def test_review_unit_plan_accepts_pending_units_and_writes_formal_plan(monkeypatch, tmp_path):
    from unit_plan import review_unit_plan_command

    book_root = tmp_path / "books" / "phase4-book"
    _write_candidate_plan(book_root, [
        unit("U-001-01", [1], include=True),
        unit("U-001-02", [2], include=True),
    ])
    choices = iter(["a", "a"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    review_unit_plan_command(book_root, list_only=False)

    formal_path = book_root / "config" / "semantic-unit-plan.yaml"
    assert formal_path.exists()
    formal = yaml.safe_load(formal_path.read_text(encoding="utf-8"))
    assert [item["review_status"] for item in formal["units"]] == ["accepted", "accepted"]
    assert formal["coverage_validation"]["passed"] is True


def test_review_unit_plan_validation_failure_does_not_write_formal_plan(monkeypatch, tmp_path):
    from unit_plan import review_unit_plan_command

    book_root = tmp_path / "books" / "phase4-book"
    _write_candidate_plan(book_root, [
        unit("U-001-01", [1], include=True),
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")

    with pytest.raises(SystemExit):
        review_unit_plan_command(book_root, list_only=False)

    assert not (book_root / "config" / "semantic-unit-plan.yaml").exists()
    report = (
        book_root / "pipeline-workspace" / "reports" / "unit-plan-validation.md"
    ).read_text(encoding="utf-8")
    assert "missing pages" in report


def test_fill_coverage_gaps_attaches_missing_page_to_previous_unit():
    from unit_plan import fill_coverage_gaps, validate_unit_plan

    # 漏覆盖的章节尾页 3，夹在 [1,2] 和 [4,5] 之间，应并入前驱并压成连续区间
    plan = {"units": [
        unit("U-001-01", [1, 2], include=True),
        unit("U-001-02", [4, 5], include=True),
    ]}
    fill_coverage_gaps(plan, total_pages=5)
    assert plan["units"][0]["source_scope"]["pages"] == [1, 3]
    result = validate_unit_plan(plan, total_pages=5)
    assert result["missing_pages"] == []


def test_fill_coverage_gaps_uses_following_unit_when_no_predecessor():
    from unit_plan import fill_coverage_gaps

    # 缺页 1 在所有 unit 之前，应归给最近后继 unit
    plan = {"units": [unit("U-001-01", [2, 3], include=True)]}
    fill_coverage_gaps(plan, total_pages=3)
    assert plan["units"][0]["source_scope"]["pages"] == [1, 3]


def test_parse_pages_input_accepts_ranges_and_commas():
    from unit_plan import parse_pages_input

    assert parse_pages_input("1-3, 3, 5，7") == [1, 2, 3, 5, 7]
