from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("page_rules", ROOT / "scripts" / "page_rules.py")
page_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page_rules)


def test_find_bare_evidence_ids():
    body = "结论 A [E-p12-3]，结论 B。\n\n另见 [E-fig_4]。\n"
    assert page_rules.find_bare_evidence_ids(body) == ["[E-p12-3]", "[E-fig_4]"]


def test_clean_prose_has_no_bare_ids():
    body = "干净的散文，证据在脚注。[^e1]\n\n[^e1]: 证据：whitepaper §5.2\n"
    assert page_rules.find_bare_evidence_ids(body) == []


def test_footnote_refs_and_defs():
    body = "论断一。[^e1] 论断二。[^e2]\n\n[^e1]: 证据一\n"
    assert page_rules.footnote_refs(body) == {"e1", "e2"}
    assert page_rules.footnote_defs(body) == {"e1"}
    assert page_rules.missing_footnote_defs(body) == {"e2"}


def test_footnote_def_line_not_counted_as_ref():
    body = "[^e1]: 只有定义没有引用\n"
    assert page_rules.footnote_refs(body) == set()
    assert page_rules.missing_footnote_defs(body) == set()


def test_required_sections_for_concept_matches_spec8():
    secs = page_rules.required_sections_for("concept")
    assert "## 直觉" in secs and "## 形式化" in secs and "## 各章如何处理" in secs
    assert "## 与其他概念的关系" in secs


def test_missing_sections_reports_absent_only():
    body = "# X\n\n## 直觉\n\n说明\n\n## 形式化\n\n$$x$$\n"
    missing = page_rules.missing_sections(body, ["## 直觉", "## 形式化", "## 各章如何处理"])
    assert missing == ["## 各章如何处理"]


def test_missing_sections_requires_heading_line_not_substring():
    body = "正文里提到 ## 直觉 三个字但不是标题行\n"
    assert page_rules.missing_sections(body, ["## 直觉"]) == ["## 直觉"]


def test_unknown_page_type_raises():
    try:
        page_rules.required_sections_for("nonsense")
        assert False, "should raise"
    except KeyError:
        pass


def test_overview_required_sections_l5():
    secs = page_rules.required_sections_for("overview")
    assert "## 核心概念地图" in secs and "## 推荐学习路线" in secs and "## 模型家族对比" in secs
