import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("query_session", ROOT / "scripts" / "query_session.py")
query_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query_session)


def _mk_session(tmp_path, *, with_save=False):
    d = tmp_path / "qs-001"
    d.mkdir()
    (d / "question.md").write_text("# 问题\n", encoding="utf-8")
    (d / "answer.md").write_text("# 回答\n", encoding="utf-8")
    if with_save:
        (d / "related_pages.json").write_text("[]", encoding="utf-8")
        (d / "candidate_write_set.json").write_text('["topics/t.md"]', encoding="utf-8")
        (d / "evidence_refs.json").write_text('[{"source": "wp", "sections": ["1"]}]',
                                              encoding="utf-8")
        (d / "decision.md").write_text("# 为什么保存\n", encoding="utf-8")
    return d


def test_query_session_ok(tmp_path):
    d = _mk_session(tmp_path)
    assert query_session.check_session(d, saved=False) == []


def test_query_session_missing_required(tmp_path):
    d = tmp_path / "qs-002"
    d.mkdir()
    problems = query_session.check_session(d, saved=False)
    assert any("question.md" in p for p in problems)
    assert any("answer.md" in p for p in problems)


def test_saved_session_ok(tmp_path):
    d = _mk_session(tmp_path, with_save=True)
    assert query_session.check_session(d, saved=True) == []


def test_saved_session_requires_decision_and_nonempty_sets(tmp_path):
    d = _mk_session(tmp_path)
    (d / "candidate_write_set.json").write_text("[]", encoding="utf-8")  # 空集不行
    (d / "evidence_refs.json").write_text("not json", encoding="utf-8")  # 坏 JSON 不行
    problems = query_session.check_session(d, saved=True)
    assert any("decision.md" in p for p in problems)
    assert any("candidate_write_set" in p for p in problems)
    assert any("evidence_refs" in p for p in problems)
    assert any("related_pages" in p for p in problems)


def test_missing_dir_is_problem(tmp_path):
    problems = query_session.check_session(tmp_path / "nope", saved=False)
    assert problems and "not found" in problems[0]
