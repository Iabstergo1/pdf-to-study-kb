import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_verify_note_without_structured_claims_treats_regex_as_advisory():
    """③ 无结构化 claims 时：正则发现的未引用结论只作 advisory，不阻塞（正文已有有效引用）。"""
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

    assert result["passed"] is True  # 整篇已落地证据，单句未引用只作提示
    assert "结论二：创作者随后选择策略。" in result["advisory_uncited"]


def test_verify_note_structured_source_claim_without_evidence_blocks():
    """② source 类论断缺有效证据 → evidence_missing（确定性、阻塞）。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-3.1-0001"}]}
    draft = "正文。[E-3.1-0001]"
    claims = [
        {"statement": "纳什均衡在有限博弈中总存在", "evidence_ids": [], "type": "source"},
    ]

    result = verify_note(draft, context, claims=claims)

    assert result["passed"] is False
    assert "evidence_missing" in result["risk_flags"]
    assert result["missing_claims"] == ["纳什均衡在有限博弈中总存在"]


def test_verify_note_bridge_claim_without_evidence_passes():
    """② bridge/explanation 类是个人桥接/学习解释，无证据不拦。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-3.1-0001"}]}
    draft = "正文。[E-3.1-0001]"
    claims = [
        {"statement": "可以把它想象成多人剪刀石头布", "evidence_ids": [], "type": "bridge"},
        {"statement": "原文给出存在性证明", "evidence_ids": ["E-3.1-0001"], "type": "source"},
    ]

    result = verify_note(draft, context, claims=claims)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_verify_note_flags_hallucinated_evidence_id():
    """① 引用 evidence_candidates 中不存在的 id → evidence_hallucinated（确定性、阻塞）。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-3.1-0001"}]}
    draft = "正文引用了不存在的证据。[E-3.1-9999]"

    result = verify_note(draft, context)

    assert result["passed"] is False
    assert "evidence_hallucinated" in result["risk_flags"]
    assert result["hallucinated_evidence"] == ["E-3.1-9999"]


def test_verify_note_flags_hallucinated_id_inside_structured_claim():
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-3.1-0001"}]}
    draft = "正文。[E-3.1-0001]"
    claims = [{"statement": "论断", "evidence_ids": ["E-3.1-0001", "E-9-0404"], "type": "source"}]

    result = verify_note(draft, context, claims=claims)

    assert "evidence_hallucinated" in result["risk_flags"]
    assert result["hallucinated_evidence"] == ["E-9-0404"]


def test_verify_note_empty_structured_claims_with_no_citations_blocks():
    """回归：模型输出空 claims（claims==[]）且正文零有效引用时，零落地兜底仍须触发。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-1"}]}
    draft = "## 标题\n\n完全没有任何证据引用的实质正文。"

    result = verify_note(draft, context, claims=[])

    assert result["passed"] is False
    assert "evidence_missing" in result["risk_flags"]


def test_verify_note_empty_structured_claims_with_valid_citation_passes():
    """claims==[] 但正文有有效引用 → 视为有据，不拦。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-1"}]}
    draft = "## 标题\n\n有据的正文。[E-1]"

    result = verify_note(draft, context, claims=[])

    assert result["passed"] is True


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


def test_verify_note_fallback_keeps_trailing_evidence_on_long_line():
    """回归：无显式结论时，正文含有效证据引用即视为有据。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-preface-0009"}]}
    draft = "# 前言\n\n" + ("叙述性正文" * 60) + " [E-preface-0009]"

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_normalize_claims_infers_type_and_filters_invalid():
    from evidence_verifier import normalize_claims

    assert normalize_claims(None) is None          # 未提供 → None（触发正则回退）
    assert normalize_claims("nope") is None
    out = normalize_claims([
        {"statement": "有证据未标类型", "evidence_ids": ["E-1"]},        # 推断 source
        {"statement": "无证据未标类型"},                                  # 推断 explanation
        {"statement": "  ", "evidence_ids": ["E-2"]},                    # 空 statement → 丢弃
        {"text": "用 text 字段", "evidence_id": "E-3", "type": "BRIDGE"},  # 同义字段 + 大小写
        "not a dict",
    ])
    assert [c["type"] for c in out] == ["source", "explanation", "bridge"]
    assert out[0]["evidence_ids"] == ["E-1"]
    assert out[2]["statement"] == "用 text 字段" and out[2]["evidence_ids"] == ["E-3"]


def test_verify_note_ignores_bold_subtitle_containing_claim_keyword():
    """整行加粗的小标题（如 **第1类：核心命题的完整证明**）不是核心结论，不应要求证据。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-14.9-0017"}]}
    draft = "\n".join([
        "## 14.9 附录写作法",
        "**第1类：核心命题的完整证明（Proofs of Main Propositions）**",
        "正文中每个核心命题都必须有详尽的数学证明作为支撑。[E-14.9-0017]",
    ])

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_verify_note_passes_keywordless_draft_when_body_has_evidence():
    """无“结论/命题”关键词、但正文每行都带证据的章节引言不应被误拦。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-5-0-0012"}]}
    draft = "\n".join([
        "第五章 核心博弈模型分析",
        "## 5.1 基础模型",
        "当市场上只有少数参与者时，他们应选择数量竞争还是价格竞争？[E-5-0-0012]",
    ])

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_verify_note_still_flags_real_uncited_conclusion():
    """真·未引用的核心结论句仍须被拦（门禁不被削弱）。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-3.2-0001"}]}
    draft = "\n".join([
        "## 3.2 目标函数",
        "**核心结论**：目标函数的内核是“收益-成本”分析，没有给出任何来源。",
    ])

    result = verify_note(draft, context)

    assert result["passed"] is False
    assert "evidence_missing" in result["risk_flags"]


def test_verify_note_ignores_bold_list_label_with_keyword():
    """带列表前缀的加粗标签（如 ``- **针对“结论与讨论”的提问：**``）是小标题，不是结论。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-15.2-0003"}]}
    draft = "\n".join([
        "## 15.2 自测问题",
        "- **针对“结论与讨论”的提问：**",
        "  - 这个模型的核心假设是否稳健？[E-15.2-0003]",
    ])

    result = verify_note(draft, context)

    assert result["passed"] is True
    assert result["missing_claims"] == []


def test_verify_note_flags_keywordless_draft_without_any_evidence():
    """无关键词且正文完全无证据引用的非空草稿仍算缺证据。"""
    from evidence_verifier import verify_note

    context = {"evidence_candidates": [{"evidence_id": "E-x-0001"}]}
    draft = "## 标题\n\n这是一段完全没有任何证据引用的正文。"

    result = verify_note(draft, context)

    assert result["passed"] is False
    assert "evidence_missing" in result["risk_flags"]
