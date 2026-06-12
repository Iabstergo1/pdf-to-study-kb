"""T1–T5：skill 工程标准合规套件（docs/skill-runtime/skill-standard.md §测试口径）。

遍历 .claude/skills 与 .agents/skills 两棵树，把双 agent 协作的人工核验固化为自动测试：
- T1 九段契约合规（两树）
- T2 双 agent 对等（skill 集合一致 + 内容对等，仅 per-agent 真值指针可不同）
- T3 卫生（无死 spec/ADR/superpowers 指针、无 pythonProject、无 .Codex）
- T4 协议词不丢（跨 SKILL.md + references）
- T5 source-xray 守卫显式声明
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
    # 复杂 skill 阶段细节拆到 references/*.md；协议词跨 SKILL.md + references 校验。
    parts = [_skill_md(tree, name)]
    refs = tree / name / "references"
    if refs.is_dir():
        for f in sorted(refs.glob("*.md")):
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _norm_agent_truth(text: str) -> str:
    # 双树唯一合法差异：per-agent 真值文件指针（Claude 指 CLAUDE.md / Codex 指 AGENTS.md）。
    return text.replace("CLAUDE.md", "<AGENT_TRUTH>").replace("AGENTS.md", "<AGENT_TRUTH>")


# 九段必填项（第 7 段「阶段拆解」对简单 skill 可省，故不强制）。
_MANDATORY_SECTIONS = {1: "触发", 2: "输入", 3: "输出", 4: "依赖",
                       5: "持久化", 6: "CLI", 8: "停止", 9: "验收"}


def test_t1_nine_section_contract_both_trees():
    for tree_name, tree in TREES.items():
        names = _skills_in(tree)
        assert names, f"{tree_name} 树没有任何 skill"
        for name in sorted(names):
            md = _skill_md(tree, name)
            assert md.startswith("---"), f"[{tree_name}/{name}] 缺 frontmatter"
            assert f"name: {name}" in md, f"[{tree_name}/{name}] frontmatter name 不匹配目录"
            assert "description:" in md, f"[{tree_name}/{name}] 缺 description"
            for num, kw in _MANDATORY_SECTIONS.items():
                assert re.search(rf"(?m)^## {num}\. .*{kw}", md), \
                    f"[{tree_name}/{name}] 缺第 {num} 段（关键词 {kw}）"
            assert "负样本" in md, f"[{tree_name}/{name}] 第 1 段缺负样本"


def test_t2_dual_agent_parity():
    claude_set = _skills_in(TREES["claude"])
    agents_set = _skills_in(TREES["agents"])
    assert claude_set == agents_set, (
        f"两树 skill 集合不一致：仅 claude={claude_set - agents_set} "
        f"仅 agents={agents_set - claude_set}")
    for name in sorted(claude_set):
        c = _norm_agent_truth(_skill_all(TREES["claude"], name))
        a = _norm_agent_truth(_skill_all(TREES["agents"], name))
        assert c == a, f"[{name}] 两树内容不对等（归一 per-agent 真值指针后仍有差异）"


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
            assert bad not in text, f"{f} 含死指针/禁用 token：{bad!r}"


_PROTOCOL_KEYWORDS = {
    "ingest": ["workorder.yaml", "resolve-concept", "check-write", "window-done",
               "status: proposed", "lint"],
    "kb-query": ["check-session", "query-session", "candidate_write_set", "evidence_refs"],
    "kb-save": ["resolve-concept", "check-write", "check-session", "save-back-policy",
                "status: proposed"],
    "kb-review": ["Review-Queue", "review_proposals", "promote-concept"],
    "wiki-lint-semantic": ["L4", "矛盾", "Q2", "proposal"],
    "source-preflight": ["workorder", "source-convert", "windows", "write_scope", "零 LLM"],
    "kb-qa": ["Q 链", "Review-Queue", "覆盖率", "互斥"],
    "source-xray": ["reports/source-xray", "已发布", "kb-save"],
}


def test_t4_protocol_keywords_present_both_trees():
    for tree_name, tree in TREES.items():
        present = _skills_in(tree)
        for name, keywords in _PROTOCOL_KEYWORDS.items():
            assert name in present, f"[{tree_name}] 缺 skill：{name}"
            text = _skill_all(tree, name)
            for kw in keywords:
                assert kw in text, f"[{tree_name}/{name}] 丢协议词：{kw!r}"


# source-xray 守卫（frontmatter 与 §9 措辞不同，用宽松子串）。
_XRAY_GUARDS = ["不参与预处理", "不决定窗口", "不决定写页范围", "合并概念页",
                "只基于已发布内容", "不写 vault"]


def test_t5_source_xray_guard_declared_both_trees():
    for tree_name, tree in TREES.items():
        md = _skill_md(tree, "source-xray")
        for guard in _XRAY_GUARDS:
            assert guard in md, f"[{tree_name}/source-xray] 缺守卫声明：{guard!r}"
