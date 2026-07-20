"""Ingest source-fidelity doc contract (2026-07-19 mysql content-fidelity postmortem).

Guards the three A-group doc fixes in BOTH skill trees (byte parity itself is test_t2's job):
- A1 write-pages.md: broken-link remediation is rephrase-first — creating a page is only legal
  when the current window's source text actually covers the concept (the old create-first order
  was a risk amplifier for source-less page creation).
- A2 write-pages.md: explicit source-fidelity contract — no substantive coverage in the current
  source → no new page, no padding; general domain knowledge must never be presented as this
  source's content.
- A3 finish-lint.md / SKILL.md §9: pipeline completion ≠ content acceptance — lint/published is
  structural only; content acceptance needs an independent kb-qa pass and a human decision, and
  the ingesting session never declares it for itself.

2026-07-20 postmortem adds four judgement contracts + one legislation principle. They are NOT
skill-evolve proposals (skill-mine yields no signature — content-fidelity findings never become
lint violations, so they never reach the backlog); per the 2026-07-17 precedent they land as
ordinary dual-tree doc maintenance guarded here:
- A write-pages.md: no audit scars — a correction states the corrected knowledge, never narrates
  the correction or argues with the previous version (14 pages carried scars in one round).
- B finish-lint.md: rework termination — deleting an unsupported clause cannot introduce new
  unsupported content (diff-verifiable, closes the round); rewriting can, so it needs a fresh
  independent pass. Prefer the smallest diff-verifiable fix.
- C kb-qa SKILL.md: a "not in the source" verdict requires bilingual + variant search with the
  search terms recorded (the History链表 false positive came from an English-only search).
- D kb-qa SKILL.md: a sampling PASS covers only the assertions sampled; later rounds never
  inherit it as a page-level clean bill (three consecutive rounds overturned inherited CLEANs).
- E CLAUDE.md / AGENTS.md: a gate must not demand an edit in situations where no legitimate edit
  exists, or it manufactures content (broken-link → the source-less 死锁 page; L7 → touching an
  already-complete synthesis layer during a narrow rework).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = {"claude": ROOT / ".claude/skills", "agents": ROOT / ".agents/skills"}
TRUTHS = {"claude": ROOT / "CLAUDE.md", "agents": ROOT / "AGENTS.md"}


def _read(tree: Path, rel: str) -> str:
    return (tree / "ingest" / rel).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    # 断言不受折行影响：连续空白折叠成单空格
    return re.sub(r"\s+", " ", text)


def test_a1_broken_link_fix_is_rephrase_first():
    for tree_name, tree in TREES.items():
        wp = _flat(_read(tree, "references/write-pages.md"))
        # 旧的 create-first 顺序必须消失
        assert "create it via `resolve-concept` now, or rephrase as plain text" not in wp, \
            f"[{tree_name}] write-pages.md still orders create before rephrase (source-less page amplifier)"
        # 新契约：先核来源覆盖，rephrase 是默认；建页仅当来源确有实质覆盖
        assert "Never create a page just to satisfy `broken-link`" in wp, \
            f"[{tree_name}] write-pages.md missing the rephrase-first broken-link contract"


def test_a2_source_fidelity_contract_present():
    for tree_name, tree in TREES.items():
        wp = _flat(_read(tree, "references/write-pages.md"))
        assert "## Source-fidelity contract" in wp, \
            f"[{tree_name}] write-pages.md missing the Source-fidelity contract section"
        for marker in (
            "no substantive coverage in the current source",   # 无实质覆盖→不新建不扩写
            "general domain knowledge must never be presented", # 通用知识不得伪装成本源内容
            "keeps its original attribution",                   # 他源知识保留原归属
        ):
            assert marker in wp, \
                f"[{tree_name}] write-pages.md source-fidelity contract missing marker: {marker!r}"


def test_a3_finish_lint_separates_completion_from_acceptance():
    for tree_name, tree in TREES.items():
        fl_raw = _read(tree, "references/finish-lint.md")
        # 引用文件里的 "## Acceptance" 必须改名（机械发布 ≠ 验收）
        assert not re.search(r"(?m)^## Acceptance\s*$", fl_raw), \
            f"[{tree_name}] finish-lint.md still titles mechanical publish as Acceptance"
        fl = _flat(fl_raw)
        assert "content acceptance" in fl and "kb-qa" in fl, \
            f"[{tree_name}] finish-lint.md missing the content-acceptance / kb-qa distinction"
        assert "published, pending content acceptance" in fl, \
            f"[{tree_name}] finish-lint.md missing the executor's mandated status wording"


def test_a3_skill_md_section9_scoped_to_pipeline_completion():
    for tree_name, tree in TREES.items():
        md = _read(tree, "SKILL.md")
        # 固定章节标题保留（skill-standard.md 九节契约，test_t1 依赖）
        assert re.search(r"(?m)^## 9\. Acceptance criteria", md), \
            f"[{tree_name}] SKILL.md must keep the section-9 heading (skill-standard contract)"
        sec9 = md.split("## 9. Acceptance criteria", 1)[1]
        flat9 = _flat(sec9)
        assert "pipeline completion" in flat9, \
            f"[{tree_name}] SKILL.md §9 must scope itself to pipeline completion, not content acceptance"
        assert "kb-qa" in flat9, \
            f"[{tree_name}] SKILL.md §9 must point content acceptance at an independent kb-qa pass"


def test_delivery_counts_use_exact_page_inventory_not_window_estimate():
    """Reopen rounds can leave old pages outside this round's write ledger; delivery counts stay exact."""
    for tree_name, tree in TREES.items():
        finish = _flat(_read(tree, "references/finish-lint.md"))
        skill = _flat(_read(tree, "SKILL.md"))
        for name, text in (("finish-lint.md", finish), ("SKILL.md", skill)):
            assert "page_inventory" in text, \
                f"[{tree_name}] {name} must require the exact source-attributed page inventory"
            assert "never use `pages_estimate` as the delivery total" in text, \
                f"[{tree_name}] {name} must forbid reporting the window-ledger estimate as total pages"


# ── 2026-07-20 postmortem 契约（A–E）─────────────────────────────────────────────

def test_a_audit_scar_ban_in_write_pages():
    """A：修正只写正确知识，不叙述修正过程、不反驳旧版本（读者没见过错版）。"""
    for tree_name, tree in TREES.items():
        wp = _flat(_read(tree, "references/write-pages.md"))
        assert "## No audit scars" in wp, \
            f"[{tree_name}] write-pages.md missing the audit-scar section"
        for marker in (
            "Never narrate the correction",          # 不叙述修正过程
            "the reader never saw the wrong version",  # 读者没见过错版（禁令的理由）
        ):
            assert marker in wp, f"[{tree_name}] write-pages.md missing marker: {marker!r}"


def test_b_rework_termination_rule_in_finish_lint():
    """B：删除 vs 重写的风险性质不同——优先可被 diff 验证的最小改动。"""
    for tree_name, tree in TREES.items():
        fl = _flat(_read(tree, "references/finish-lint.md"))
        assert "## Fixing audit findings" in fl, \
            f"[{tree_name}] finish-lint.md missing the rework-termination section"
        for marker in (
            "cannot introduce new unsupported content",  # 删除的风险性质
            "needs a fresh independent pass",            # 重写的义务
        ):
            assert marker in fl, f"[{tree_name}] finish-lint.md missing marker: {marker!r}"


def _kb_qa(tree: Path) -> str:
    return _flat((tree / "kb-qa" / "SKILL.md").read_text(encoding="utf-8"))


def test_c_zero_hit_verdict_requires_bilingual_search():
    """C：判"来源中没有"前必须双语+变体检索并写明检索词。"""
    for tree_name, tree in TREES.items():
        md = _kb_qa(tree)
        assert "record every search term" in md, \
            f"[{tree_name}] kb-qa SKILL.md must require recording the search terms"
        assert "single-language search" in md, \
            f"[{tree_name}] kb-qa SKILL.md must name the single-language search as the failure"


def test_d_sampling_pass_is_not_inheritable():
    """D：抽样 PASS 只覆盖被抽查的断言，后续轮次不得继承为整页结论。"""
    for tree_name, tree in TREES.items():
        md = _kb_qa(tree)
        assert "only the assertions actually sampled" in md, \
            f"[{tree_name}] kb-qa SKILL.md must scope a sampling PASS to the sampled assertions"
        assert "never inherit" in md, \
            f"[{tree_name}] kb-qa SKILL.md must forbid inheriting an earlier round's clean bill"


def test_e_gate_legislation_principle_in_both_truths():
    """E：门禁不得在无正当改动时强制要求改动（否则制造内容）——两份项目真值都要有。"""
    for name, path in TRUTHS.items():
        t = _flat(path.read_text(encoding="utf-8"))
        assert "manufactures content" in t, \
            f"[{name}] project truth missing the gate-legislation principle"
        assert "no legitimate edit exists" in t, \
            f"[{name}] project truth must state the no-legitimate-edit condition"
