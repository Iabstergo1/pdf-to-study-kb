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


def test_routing_doc_has_negative_examples():
    text = (ROOT / "docs/skill-runtime/routing.md").read_text(encoding="utf-8")
    assert "/ingest" in text and "负例" in text and "总结这篇文章" in text


def test_schema_and_resolution_docs():
    schema = (ROOT / "docs/skill-runtime/schema.md").read_text(encoding="utf-8")
    assert "templates/" in schema and "page_rules" in schema and "proposed" in schema
    res = (ROOT / "docs/skill-runtime/concept-resolution.md").read_text(encoding="utf-8")
    assert "resolve-concept" in res and "绝不新建" in res and "canonical_id" in res
