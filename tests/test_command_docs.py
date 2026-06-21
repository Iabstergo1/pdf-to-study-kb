import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


def test_ingest_skill_whole_book_chapter_map_and_typed_embed():
    # Stage 2/3：全书理解（chapters.json 章节图/导航脊柱）+ 按类型嵌入原图（图/表/公式）须在 ingest 协议里
    text = _skill_all("ingest")
    for must in ["chapters.json", "全书", "按类型", "vector-figure", "导航脊柱"]:
        assert must in text, f"ingest 缺 Stage2/3 协议要素: {must}"


def test_ingest_skill_window_asset_header():
    # Phase 2：show-window 默认难页资产头（route-b-assets + tier）须进 ingest 协议（双树同源）
    text = _skill_all("ingest")
    for must in ["route-b-assets", "tier=must"]:
        assert must in text, f"ingest 缺窗口难页资产头协议: {must}"


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


def test_resume_ingest_codex_automation_uses_supported_writable_flags():
    script = (ROOT / "scripts" / "resume-ingest.ps1").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    combined = script + "\n" + readme
    assert "--full-auto" not in combined
    assert "--dangerously-bypass-approvals-and-sandbox" in script
    assert "--sandbox" in script and "workspace-write" in script
    # 交付默认 = 最小权限 workspace-write；bypass 降为 -Bypass 逃生开关
    assert "默认用 `--sandbox workspace-write`" in readme


def test_resume_ingest_detects_active_ingest_with_lock_status_line(tmp_path):
    if os.name != "nt":
        pytest.skip("resume-ingest.ps1 smoke uses Windows .cmd shims")
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is required for resume-ingest.ps1 smoke")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "echo note                         misc           ingesting        running\r\n"
        "echo [lock] vault held by note since 2026-06-15T00:00:00+00:00\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    arg_log = tmp_path / "codex.args.txt"
    fake_codex = bin_dir / "codex.cmd"
    fake_codex.write_text(
        "@echo off\r\n"
        "echo %*>>\"%CODEX_ARG_LOG%\"\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CODEX_ARG_LOG": str(arg_log),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }

    r = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(ROOT / "scripts" / "resume-ingest.ps1"),
         "-Agent", "codex", "-Python", str(fake_python)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert r.returncode == 0, r.stdout + r.stderr
    # codex.cmd 用 `echo %*`，prompt 含非 ASCII 时按 cmd 控制台本地码页（非 UTF-8）落盘；
    # 断言只查 ASCII 标志，故宽松解码（替换非 UTF-8 字节）以免被非 ASCII 噪声卡住。
    args = arg_log.read_text(encoding="utf-8", errors="replace")
    assert "exec --sandbox workspace-write" in args
    assert "dangerously-bypass" not in args
