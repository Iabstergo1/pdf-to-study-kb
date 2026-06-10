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
