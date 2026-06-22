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
    # Complex skills push phase detail to references/*.md (skill-standard.md); protocol words checked across both.
    parts = [_skill(name)]
    refs = SKILLS / name / "references"
    if refs.is_dir():
        for f in sorted(refs.glob("*.md")):
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_legacy_commands_dir_migrated_to_skills():
    # The command layer migrated to .claude/skills/; the old .claude/commands/ is gone.
    assert not (ROOT / ".claude/commands").exists(), "legacy .claude/commands/ should be deleted"
    for name in ["ingest", "kb-query", "kb-save", "kb-review", "wiki-lint-semantic"]:
        assert (SKILLS / name / "SKILL.md").exists(), f"missing skill: {name}"


def test_skills_have_name_and_description_frontmatter():
    for name in ["ingest", "kb-query", "kb-save", "kb-review", "wiki-lint-semantic"]:
        text = _skill(name)
        assert text.startswith("---"), f"{name} missing frontmatter"
        assert f"name: {name}" in text, f"{name} frontmatter missing name"
        assert "description:" in text, f"{name} frontmatter missing description"


def test_ingest_skill_protocol_complete():
    text = _skill_all("ingest")  # SKILL.md + references/*
    for must in ["workorder.yaml", "ingest-start", "show-window", "window-start", "window-done",
                 "resolve-concept", "check-write", "snapshot-page", "ingest-done",
                 "digest.md", "rolling digest", "status: proposed", "write_scope"]:
        assert must in text, f"ingest missing protocol element: {must}"
    # Derived files must not be hand-written.
    assert "_registry.yaml" in text and "aliases.md" in text and "index.generated.md" in text


def test_ingest_skill_orchestrates_full_pipeline():
    # The ingest skill orchestrates preprocessing + dual-audit + finishing lint, not just writing.
    text = _skill_all("ingest")
    for must in ["add-source", "profile", "source-convert", "source-audit", "windows", "workorder",
                 "init-vault", "lint"]:
        assert must in text, f"ingest missing end-to-end step: {must}"


def test_ingest_skill_dual_audit_wiring():
    # The dual-audit + evidence-assembly loop must be wired through the full ingest workflow
    # (preprocessing → auto-arbitration → materialization → closed-loop acceptance).
    text = _skill_all("ingest")
    for must in ["source-audit", "reconciliation.json", "dual-audit", "MinerU", "PyMuPDF",
                 "arbitration", "evidence.json", "arbitration-apply", "check_evidence_bundle"]:
        assert must in text, f"ingest missing dual-audit/evidence-loop element: {must}"
    assert (SKILLS / "ingest" / "references" / "arbitrate.md").exists(), "ingest missing references/arbitrate.md"


def test_ingest_skill_synthesis_duties():
    text = _skill_all("ingest")
    for must in ["synthesis duties", "overview.md", "concept map", "chapter list",
                 "topics/", "comparisons/", "follow the source TOC"]:
        assert must in text, f"ingest missing synthesis-duty element: {must}"


def test_ingest_skill_whole_book_chapter_map_and_typed_embed():
    # Whole-book understanding (chapters.json map / navigation spine) + embed source images by type.
    text = _skill_all("ingest")
    for must in ["chapters.json", "whole-book", "by type", "vector-figure", "navigation spine"]:
        assert must in text, f"ingest missing whole-book / typed-embed element: {must}"


def test_ingest_skill_window_asset_header():
    # show-window's hard-page asset header (route-b-assets + tier) is part of the ingest protocol.
    text = _skill_all("ingest")
    for must in ["route-b-assets", "tier=must"]:
        assert must in text, f"ingest missing window asset-header protocol: {must}"


def test_ingest_skill_split_into_references():
    # Engineering standard: a complex skill keeps orchestration in SKILL.md, phase detail in references/*.md.
    refs = SKILLS / "ingest" / "references"
    for f in ["preflight.md", "write-pages.md", "synthesis.md", "finish-lint.md"]:
        assert (refs / f).exists(), f"ingest missing references/{f}"
    # The main SKILL.md carries the nine-section headers.
    sk = _skill("ingest")
    for seg in ["Triggers / Non-triggers", "## 2. Inputs", "## 3. Outputs", "Acceptance criteria"]:
        assert seg in sk, f"ingest SKILL.md missing nine-section header: {seg}"


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
    # codex.cmd uses `echo %*`; non-ASCII prompts land in the console code page, so assert only ASCII flags.
    args = arg_log.read_text(encoding="utf-8", errors="replace")
    assert "exec --sandbox workspace-write" in args
    assert "dangerously-bypass" not in args
