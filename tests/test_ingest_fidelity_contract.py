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
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = {"claude": ROOT / ".claude/skills", "agents": ROOT / ".agents/skills"}


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
