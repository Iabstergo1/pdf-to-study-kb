import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_verify_note_requires_each_core_claim_to_have_available_evidence():
    from evidence_verifier import verify_note

    context = {
        "evidence_candidates": [
            {"evidence_id": "E-section-3.1-0001"},
            {"evidence_id": "E-section-3.1-0002"},
        ]
    }
    draft = "\n".join([
        "结论一：平台先承诺制度设计。[E-section-3.1-0001]",
        "结论二：创作者随后选择策略。",
    ])

    result = verify_note(draft, context)

    assert result["passed"] is False
    assert "evidence_missing" in result["risk_flags"]
    assert result["missing_claims"] == ["结论二：创作者随后选择策略。"]


def test_verify_note_fallback_ignores_heading_when_body_has_evidence():
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-section-3.3-0006"}]}
    draft = "# 3.3 不同策略与定价制度下的均衡分类\n\n正文说明。[E-section-3.3-0006]"

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_verify_note_fallback_ignores_frontmatter_when_body_has_evidence():
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-section-3.3-0006"}]}
    draft = "---\nmanaged_by: pipeline\n---\n\n正文说明。[E-section-3.3-0006]"

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []
