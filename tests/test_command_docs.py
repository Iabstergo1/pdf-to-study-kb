from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude/skills"


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _skill_all(name: str) -> str:
    # 复杂 skill 把阶段细节拆到 references/*.md（skill-standard.md）；协议词跨 SKILL.md + references 校验。
    parts = [_skill(name)]
    refs = SKILLS / name / "references"
    if refs.is_dir():
        for f in sorted(refs.glob("*.md")):
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_legacy_commands_dir_migrated_to_skills():
    # 命令层已迁移为 .claude/skills/，旧 .claude/commands/ 不再存在
    assert not (ROOT / ".claude/commands").exists(), "legacy .claude/commands/ 应已删除"
    for name in ["ingest", "kb-query", "kb-save", "kb-review", "wiki-lint-semantic"]:
        assert (SKILLS / name / "SKILL.md").exists(), f"缺 skill: {name}"


def test_skills_have_name_and_description_frontmatter():
    for name in ["ingest", "kb-query", "kb-save", "kb-review", "wiki-lint-semantic"]:
        text = _skill(name)
        assert text.startswith("---"), f"{name} 缺 frontmatter"
        assert f"name: {name}" in text, f"{name} frontmatter 缺 name"
        assert "description:" in text, f"{name} frontmatter 缺 description"


def test_ingest_skill_protocol_complete():
    text = _skill_all("ingest")  # SKILL.md + references/*
    for must in ["workorder.yaml", "ingest-start", "show-window", "window-start", "window-done",
                 "resolve-concept", "check-write", "snapshot-page", "ingest-done",
                 "digest.md", "滚动摘要", "status: proposed", "write_scope"]:
        assert must in text, f"ingest 缺协议要素: {must}"
    # 派生文件禁写
    assert "_registry.yaml" in text and "aliases.md" in text and "index.generated.md" in text


def test_ingest_skill_orchestrates_full_pipeline():
    # ingest skill 端到端编排预处理 + 收尾 lint，不止写库（见 CLAUDE.md / AGENTS.md）
    text = _skill_all("ingest")
    for must in ["add-source", "profile", "source-convert", "windows", "workorder",
                 "init-vault", "lint"]:
        assert must in text, f"ingest 缺端到端编排步骤: {must}"


def test_ingest_skill_synthesis_duties():
    text = _skill_all("ingest")
    for must in ["综合层职责", "overview.md", "核心概念地图", "章节清单",
                 "topics/", "comparisons/", "跟随源 TOC"]:
        assert must in text, f"ingest 缺综合层职责要素: {must}"


def test_ingest_skill_split_into_references():
    # 工程标准：复杂 skill 主文件做编排，阶段细节拆到 references/*.md
    refs = SKILLS / "ingest" / "references"
    for f in ["preflight.md", "write-pages.md", "synthesis.md", "finish-lint.md"]:
        assert (refs / f).exists(), f"ingest 缺 references/{f}"
    # 主 SKILL.md 含九段契约的关键段标题（触发/输入/输出/验收）
    sk = _skill("ingest")
    for seg in ["触发 / 负样本", "## 2. 输入", "## 3. 输出", "验收清单"]:
        assert seg in sk, f"ingest SKILL.md 缺九段契约段: {seg}"


def test_routing_doc_has_negative_examples():
    text = (ROOT / "docs/skill-runtime/routing.md").read_text(encoding="utf-8")
    assert "ingest" in text and "负例" in text and "总结这篇文章" in text


def test_schema_and_resolution_docs():
    schema = (ROOT / "docs/skill-runtime/schema.md").read_text(encoding="utf-8")
    assert "templates/" in schema and "page_rules" in schema and "proposed" in schema
    res = (ROOT / "docs/skill-runtime/concept-resolution.md").read_text(encoding="utf-8")
    assert "resolve-concept" in res and "绝不新建" in res and "canonical_id" in res


def test_kb_query_skill_readonly_and_persists():
    text = _skill("kb-query")
    for must in ["只读", "不写 vault", "query-sessions", "question.md", "answer.md",
                 "candidate_write_set", "evidence_refs", "index.generated.md"]:
        assert must in text, f"kb-query 缺: {must}"


def test_kb_save_skill_gate_and_discipline():
    text = _skill("kb-save")
    for must in ["save-back-policy", "准入门槛", "status: proposed", "decision.md",
                 "resolve-concept", "check-write", "check-session", "--saved", "lint"]:
        assert must in text, f"kb-save 缺: {must}"


def test_kb_review_and_semantic_lint_skills():
    rev = _skill("kb-review")
    assert "Review-Queue" in rev and "review_proposals" in rev and "promotion-candidate" in rev
    sem = _skill("wiki-lint-semantic")
    for must in ["L4", "矛盾", "Q2", "proposal", "不直接改写"]:
        assert must in sem, f"wiki-lint-semantic 缺: {must}"


def test_save_back_policy_doc():
    text = (ROOT / "docs/skill-runtime/save-back-policy.md").read_text(encoding="utf-8")
    for must in ["准入门槛", "至少满足一项", "默认不保存", "一次性事实查询",
                 "managed_by: human", "resolve_or_create_concept"]:
        assert must in text, f"save-back-policy.md 缺: {must}"
