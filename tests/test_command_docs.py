import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude/skills"


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _cli_subcommands() -> list[str]:
    """从 argparse --help 提取真实子命令清单（文档计数守卫的机器真值）。"""
    import re
    import sys
    out = subprocess.run([sys.executable, str(ROOT / "scripts/pipeline.py"), "--help"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    m = re.search(r"\{([a-z0-9,-]+)\}", out)
    assert m, "cannot extract subcommand list from pipeline.py --help"
    return m.group(1).split(",")


def _tracked_markdown() -> list:
    """git 追踪的全部 *.md（含 docs/skill-runtime、templates、双 skill 树）——文档守卫的扫描域。"""
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8").stdout
    return [ROOT / line for line in out.splitlines() if line.strip()]


def test_docs_command_count_matches_cli():
    # 文档守卫（复审教训：README/指南的命令数与 CLI 真值脱节无人发现）：
    # ① 两份指南里**每一处**"N 个子命令"声明都必须等于 argparse 真值（防某处更新、他处残留旧数）；
    # ② vault-lint 与 kb-save 会话发布路径必须在 CLI 命令表里有正式条目（表行），不只是正文提一句。
    import re
    n = len(_cli_subcommands())
    for rel in ["docs/user-guide.md", "docs/developer-guide.md"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        claims = [int(m) for m in re.findall(r"(\d+)\s*个\**\s*(?:CLI\s*)?子命令", text)]
        assert claims, f"{rel} 未声明子命令数"
        assert all(c == n for c in claims), f"{rel} 声称的子命令数 {claims} ≠ CLI 真值 {n}"
        table_rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("|")]
        assert any("vault-lint" in ln for ln in table_rows), f"{rel} 的命令表缺 vault-lint 条目"
        assert "--session" in text and "kb-save" in text, f"{rel} 缺 kb-save 会话发布路径说明"


def test_docs_no_hardcoded_test_counts():
    # 文档守卫（复审教训：精确测试数写进文档当场腐烂——连"只留一处快照"也在同一轮内过期）：
    # 五份文档一律不写精确测试计数，"以 pytest --collect-only 为准"。
    import re
    for rel in ["README.md", "docs/user-guide.md", "docs/developer-guide.md",
                "CLAUDE.md", "AGENTS.md"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        hits = re.findall(r"\d{3,}\s*(?:个测试|tests\b|测试\b)", text)
        assert not hits, f"{rel} 仍硬编码测试数量 {hits}（会随提交漂移；以 collect-only 为准）"


def test_docs_no_stale_source_image_or_scaffold_claims():
    # 文档守卫：git 追踪的全部 markdown（含 docs/skill-runtime、templates）不得再出现
    # "嵌原图/内嵌的源图/强制内嵌"这类肯定式嵌图措辞（D-1；明确的"禁止嵌入"说明不含这些词）；
    # 脚本不得输出可复制的源图嵌入串；开发指南不得把旧骨架当现行模板描述。
    for f in _tracked_markdown():
        text = f.read_text(encoding="utf-8")
        for bad in ("嵌原图", "内嵌的源图", "强制内嵌"):
            assert bad not in text, f"{f.relative_to(ROOT)} 出现肯定式嵌图措辞「{bad}」（D-1 违背）"
    for py in sorted((ROOT / "scripts").rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        assert "vault=![[" not in src, f"{py.relative_to(ROOT)} 仍输出可复制的源图嵌入串"
    dev = (ROOT / "docs/developer-guide.md").read_text(encoding="utf-8")
    assert "建议小节（一句话" not in dev, "开发指南仍把已废除的旧模板骨架当现行契约描述"


# 命令层已迁到 .claude/skills/；旧 .claude/commands/ 不存在这一断言并入 test_legacy_removed.py 的
# 统一 removed-artifacts guard；skill 集合与 frontmatter 由 test_skill_standard.py T1/T2 覆盖。
# ingest 的多条协议关键词 substring 测试已并入 T4 唯一 manifest（_PROTOCOL_KEYWORDS["ingest"]），
# 这里只保留 T1/T4 未覆盖的结构性契约：references/*.md 相位文件存在。

def test_ingest_skill_split_into_references():
    # 工程标准（结构性，T1/T4 未覆盖）：复杂 skill 把相位细节下放 references/*.md。
    refs = SKILLS / "ingest" / "references"
    for f in ["arbitrate.md", "preflight.md", "write-pages.md", "synthesis.md", "finish-lint.md"]:
        assert (refs / f).exists(), f"ingest missing references/{f}"


def test_routing_doc_has_negative_examples():
    text = (ROOT / "docs/skill-runtime/routing.md").read_text(encoding="utf-8")
    assert "ingest" in text and "Counter-examples" in text and "Summarize this article" in text


def test_schema_and_resolution_docs():
    schema = (ROOT / "docs/skill-runtime/schema.md").read_text(encoding="utf-8")
    assert "templates/" in schema and "page_rules" in schema and "proposed" in schema
    res = (ROOT / "docs/skill-runtime/concept-resolution.md").read_text(encoding="utf-8")
    assert "resolve-concept" in res and "never create" in res and "canonical_id" in res


def test_kb_query_skill_readonly_and_persists():
    text = _skill("kb-query")
    for must in ["read-only", "does not write", "query-sessions", "question.md", "answer.md",
                 "candidate_write_set", "evidence_refs", "index.generated.md"]:
        assert must in text, f"kb-query missing: {must}"


def test_kb_save_skill_gate_and_discipline():
    text = _skill("kb-save")
    for must in ["save-back-policy", "admission gate", "status: proposed", "decision.md",
                 "resolve-concept", "check-write", "check-session", "--saved", "lint"]:
        assert must in text, f"kb-save missing: {must}"


def test_kb_review_and_semantic_lint_skills():
    rev = _skill("kb-review")
    assert "Review-Queue" in rev and "review_proposals" in rev and "promotion-candidate" in rev
    sem = _skill("wiki-lint-semantic")
    for must in ["L4", "contradiction", "Q2", "proposal", "does not directly edit"]:
        assert must in sem, f"wiki-lint-semantic missing: {must}"


def test_save_back_policy_doc():
    text = (ROOT / "docs/skill-runtime/save-back-policy.md").read_text(encoding="utf-8")
    for must in ["admission gate", "At least one", "Do not save by default", "one-off fact",
                 "managed_by: human", "resolve_or_create_concept"]:
        assert must in text, f"save-back-policy.md missing: {must}"


def test_resume_ingest_codex_automation_uses_supported_writable_flags():
    script = (ROOT / "scripts" / "resume-ingest.ps1").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    combined = script + "\n" + readme
    assert "--full-auto" not in combined
    assert "--dangerously-bypass-approvals-and-sandbox" in script
    assert "--sandbox" in script and "workspace-write" in script
    # Shipped default = least-privilege workspace-write; bypass is the escape hatch.
    assert "defaults to `--sandbox workspace-write`" in readme
    # 真跑 pwsh + .cmd shim 的烟测拆在 test_resume_ingest_smoke.py（tier=cli）。
