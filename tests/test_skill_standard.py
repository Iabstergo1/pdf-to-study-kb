"""T1–T5: skill engineering-standard compliance (docs/skill-runtime/skill-standard.md test rubric).

Walks both `.claude/skills` and `.agents/skills`, turning the dual-agent review into automated tests:
- T1 nine-section contract (both trees)
- T2 dual-agent parity (same skill set + byte-equivalent content, modulo the per-agent truth pointer)
- T3 hygiene (no dead spec/ADR/superpowers pointers, no pythonProject, no .Codex)
- T4 protocol keywords intact (across SKILL.md + references)
- T5 source-xray guard declared
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = {"claude": ROOT / ".claude/skills", "agents": ROOT / ".agents/skills"}
ROUTING = ROOT / "docs/skill-runtime/routing.md"


def _skills_in(tree: Path) -> set:
    if not tree.is_dir():
        return set()
    return {d.name for d in tree.iterdir() if d.is_dir() and (d / "SKILL.md").exists()}


def _skill_md(tree: Path, name: str) -> str:
    return (tree / name / "SKILL.md").read_text(encoding="utf-8")


def _skill_all(tree: Path, name: str) -> str:
    # Complex skills push phase detail to references/*.md; protocol words are checked across both.
    parts = [_skill_md(tree, name)]
    refs = tree / name / "references"
    if refs.is_dir():
        for f in sorted(refs.glob("*.md")):
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _norm_agent_truth(text: str) -> str:
    # The only legitimate cross-tree difference: the per-agent truth pointer (Claude → CLAUDE.md / Codex → AGENTS.md).
    return text.replace("CLAUDE.md", "<AGENT_TRUTH>").replace("AGENTS.md", "<AGENT_TRUTH>")


# Mandatory sections (section 7 "Workflow" is optional for simple skills).
_MANDATORY_SECTIONS = {1: "Triggers", 2: "Inputs", 3: "Outputs", 4: "Dependencies",
                       5: "Persisted", 6: "CLI", 8: "Failure", 9: "Acceptance"}


def test_t1_nine_section_contract_both_trees():
    for tree_name, tree in TREES.items():
        names = _skills_in(tree)
        assert names, f"{tree_name} tree has no skills"
        for name in sorted(names):
            md = _skill_md(tree, name)
            assert md.startswith("---"), f"[{tree_name}/{name}] missing frontmatter"
            assert f"name: {name}" in md, f"[{tree_name}/{name}] frontmatter name != directory"
            assert "description:" in md, f"[{tree_name}/{name}] missing description"
            for num, kw in _MANDATORY_SECTIONS.items():
                assert re.search(rf"(?m)^## {num}\. .*{kw}", md), \
                    f"[{tree_name}/{name}] missing section {num} (keyword {kw})"
            assert "Non-triggers" in md, f"[{tree_name}/{name}] section 1 missing Non-triggers"


def test_t2_dual_agent_parity():
    claude_set = _skills_in(TREES["claude"])
    agents_set = _skills_in(TREES["agents"])
    assert claude_set == agents_set, (
        f"skill sets differ: claude-only={claude_set - agents_set} "
        f"agents-only={agents_set - claude_set}")
    for name in sorted(claude_set):
        c = _norm_agent_truth(_skill_all(TREES["claude"], name))
        a = _norm_agent_truth(_skill_all(TREES["agents"], name))
        assert c == a, f"[{name}] trees not equivalent (still differ after normalizing the truth pointer)"


_FORBIDDEN = ["docs/superpowers", "docs/adr", "docs/agents",
              "pythonProject", ".Codex", "ADR-0", "spec §"]


def test_t3_hygiene_no_dead_pointers():
    files = []
    for tree in TREES.values():
        files.extend(tree.rglob("*.md"))
    if ROUTING.exists():
        files.append(ROUTING)
    for f in files:
        text = f.read_text(encoding="utf-8")
        for bad in _FORBIDDEN:
            assert bad not in text, f"{f} contains a dead pointer / forbidden token: {bad!r}"


_PROTOCOL_KEYWORDS = {
    "ingest": ["workorder.yaml", "resolve-concept", "check-write", "window-done",
               "status: proposed", "lint", "source-audit"],
    "kb-query": ["check-session", "query-session", "candidate_write_set", "evidence_refs"],
    "kb-save": ["resolve-concept", "check-write", "check-session", "save-back-policy",
                "status: proposed"],
    "kb-review": ["Review-Queue", "review_proposals", "promote-concept"],
    "wiki-lint-semantic": ["L4", "contradiction", "Q2", "proposal"],
    "source-preflight": ["workorder", "source-convert", "source-audit", "windows", "write_scope", "zero-LLM"],
    "kb-qa": ["Q-chain", "Review-Queue", "coverage", "mutually exclusive"],
    "source-xray": ["reports/source-xray", "published", "kb-save"],
    "skill-evolve": ["skill-mine", "skill-gate", "skill-stage", "skill-adopt", "backlog"],
}


def test_t4_protocol_keywords_present_both_trees():
    for tree_name, tree in TREES.items():
        present = _skills_in(tree)
        for name, keywords in _PROTOCOL_KEYWORDS.items():
            assert name in present, f"[{tree_name}] missing skill: {name}"
            text = _skill_all(tree, name)
            for kw in keywords:
                assert kw in text, f"[{tree_name}/{name}] lost protocol word: {kw!r}"


# source-xray guard (frontmatter and §9 phrase it slightly differently; use loose substrings).
_XRAY_GUARDS = ["does not preprocess", "does not decide windows", "does not decide write scope",
                "merge concept pages", "published content only", "does not write the vault"]


def test_t5_source_xray_guard_declared_both_trees():
    for tree_name, tree in TREES.items():
        md = _skill_md(tree, "source-xray")
        for guard in _XRAY_GUARDS:
            assert guard in md, f"[{tree_name}/source-xray] missing guard declaration: {guard!r}"
