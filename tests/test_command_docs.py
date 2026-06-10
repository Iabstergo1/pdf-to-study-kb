from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ingest_command_doc_protocol_complete():
    text = (ROOT / ".claude/commands/ingest.md").read_text(encoding="utf-8")
    for must in ["workorder.yaml", "ingest-start", "show-window", "window-start", "window-done",
                 "resolve-concept", "check-write", "snapshot-page", "ingest-done",
                 "digest.md", "滚动摘要", "status: proposed", "write_scope"]:
        assert must in text, f"ingest.md 缺协议要素: {must}"
    # 派生文件禁写
    assert "_registry.yaml" in text and "aliases.md" in text and "index.generated.md" in text


def test_ingest_doc_synthesis_duties():
    text = (ROOT / ".claude/commands/ingest.md").read_text(encoding="utf-8")
    for must in ["综合层职责", "overview.md", "核心概念地图", "章节清单",
                 "topics/", "comparisons/", "跟随源 TOC"]:
        assert must in text, f"ingest.md 缺综合层职责要素: {must}"


def test_routing_doc_has_negative_examples():
    text = (ROOT / "docs/skill-runtime/routing.md").read_text(encoding="utf-8")
    assert "/ingest" in text and "负例" in text and "总结这篇文章" in text


def test_schema_and_resolution_docs():
    schema = (ROOT / "docs/skill-runtime/schema.md").read_text(encoding="utf-8")
    assert "templates/" in schema and "page_rules" in schema and "proposed" in schema
    res = (ROOT / "docs/skill-runtime/concept-resolution.md").read_text(encoding="utf-8")
    assert "resolve-concept" in res and "绝不新建" in res and "canonical_id" in res


def test_kb_query_doc_readonly_and_persists():
    text = (ROOT / ".claude/commands/kb-query.md").read_text(encoding="utf-8")
    for must in ["只读", "不写 vault", "query-sessions", "question.md", "answer.md",
                 "candidate_write_set", "evidence_refs", "index.generated.md"]:
        assert must in text, f"kb-query.md 缺: {must}"


def test_kb_save_doc_gate_and_discipline():
    text = (ROOT / ".claude/commands/kb-save.md").read_text(encoding="utf-8")
    for must in ["save-back-policy", "准入门槛", "status: proposed", "decision.md",
                 "resolve-concept", "check-write", "check-session", "--saved", "lint"]:
        assert must in text, f"kb-save.md 缺: {must}"


def test_kb_review_and_semantic_lint_docs():
    rev = (ROOT / ".claude/commands/kb-review.md").read_text(encoding="utf-8")
    assert "Review-Queue" in rev and "review_proposals" in rev and "promotion-candidate" in rev
    sem = (ROOT / ".claude/commands/wiki-lint-semantic.md").read_text(encoding="utf-8")
    for must in ["L4", "矛盾", "Q2", "proposal", "不直接改写"]:
        assert must in sem, f"wiki-lint-semantic.md 缺: {must}"


def test_save_back_policy_doc():
    text = (ROOT / "docs/skill-runtime/save-back-policy.md").read_text(encoding="utf-8")
    for must in ["准入门槛", "至少满足一项", "默认不保存", "一次性事实查询",
                 "managed_by: human", "resolve_or_create_concept"]:
        assert must in text, f"save-back-policy.md 缺: {must}"
