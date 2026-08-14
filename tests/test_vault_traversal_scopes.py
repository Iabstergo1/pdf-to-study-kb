"""vault 遍历口径矩阵：六个模块各自的顶层排除集与派生文件名单。

**为什么要有这个文件**（prepush-audit-2026-08-08 F4 / F10）：

`review-coverage` 与 `legacy_revision` 曾各写各的内容页遍历、docstring 都声称"同一口径"，
实际分叉了两条规则，后果是"该审的页"（覆盖率分母）与"能改的页"（修订射程）不是同一集合。
排查时发现这不是孤例：`wiki_gate` / `graph_model` / `graph_lint` / `retraction` /
`source_reuse` / `legacy_revision` **各有一份手写的 `_EXCLUDE_TOP` 与 `_DERIVED`**，
六份互不相同。

这些遍历服务于不同目的（门禁范围、图谱节点、撤库范围、复用证据、修订射程），
**分歧本身是合法的**——所以本文件不断言它们相等。它断言的是：
**分歧必须是有意的**。任何一处改动都会让这里变红，改的人必须回来更新这份矩阵并说明理由。

这是"把机械模式当语义判据"的反面：不假设"看起来一样就该合并"，只保证"不一样时有人知道"。
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import graph_lint  # noqa: E402
import graph_model  # noqa: E402
import legacy_revision  # noqa: E402
import retraction  # noqa: E402
import source_reuse  # noqa: E402
import wiki_gate  # noqa: E402

#: 所有遍历共有的基础排除。`.obsidian` 曾只在三处出现（wiki_gate/graph_model/graph_lint
#: 缺席），靠 frontmatter 过滤兜住，属潜伏陷阱——插件塞一个 README.md 就会进入遍历。
_BASE = {"Review-Queue", "_meta", "assets", ".obsidian"}


@pytest.mark.parametrize("name,module", [
    ("wiki_gate", "wiki_gate"),
    ("graph_model", "graph_model"),
    ("graph_lint", "graph_lint"),
    ("retraction", "retraction"),
    ("source_reuse", "source_reuse"),
])
def test_exclude_top_shares_one_base_set(name, module):
    """六处 `_EXCLUDE_TOP` 的**基础集**必须一致；差异只允许出现在基础集之外。"""
    mod = {"wiki_gate": wiki_gate, "graph_model": graph_model, "graph_lint": graph_lint,
           "retraction": retraction, "source_reuse": source_reuse}[module]
    actual = set(mod._EXCLUDE_TOP)
    missing = sorted(_BASE - actual)
    assert not missing, (
        f"{name}._EXCLUDE_TOP 缺基础排除 {missing}；"
        f"若确有理由让它进入遍历，请改本测试并写明理由")


def test_legacy_revision_base_matches_and_content_set_derives_from_it():
    """修订侧：基础集与其余一致；内容页集合**派生**自基础集，不得再手抄一份。"""
    assert set(legacy_revision._EXCLUDED_TOP) == _BASE
    assert set(legacy_revision._CONTENT_EXCLUDED_TOP) == _BASE | {
        "concepts", "graph", "sources"}


def test_derived_name_lists_are_pinned_with_reasons():
    """`_DERIVED` 分块不同——**这是有意的**，钉死当前形态防止静默漂移。

    差异理由（改动时连同本注释一起更新）：
    - `wiki_gate`      只管 *.md 遍历，故不含 .json/.html 两个非 md 派生物；含已退休的
                       `aliases.md` 是为了继续把残留文件当派生物清理掉；P2 增
                       `source-images.generated.md` 同属 *.md 派生阅读层。
    - `graph_model` /
      `graph_lint`     图谱侧同样只做 *.md 遍历，现已与 wiki_gate 对齐（P2 顺手补齐
                       曾缺失的 quiz/propositions、清掉已废的 canvas 与非 md 死条目）。
    - `retraction`     撤库要认全部派生物（含 quiz / propositions），否则会当成独占页删掉。
    - `source_reuse`   复用证据额外把 `log.md` 视为派生（它是 vault 级台账，不进证据集）。
    - `legacy_revision` 修订 overlay 要认 `concepts/_registry.yaml`（其余模块不遍历它）。
    """
    assert set(wiki_gate._DERIVED) == {
        "index.generated.md", "aliases.md",
        "quiz-index.generated.md", "propositions.generated.md",
        "source-images.generated.md"}
    assert set(graph_model._DERIVED) == set(graph_lint._DERIVED)
    assert set(graph_model._DERIVED) == set(wiki_gate._DERIVED)
    assert "quiz-index.generated.md" in retraction._DERIVED
    assert "source-images.generated.md" in retraction._DERIVED
    assert "log.md" in source_reuse._DERIVED
    assert "source-images.generated.md" in source_reuse._DERIVED
    assert "concepts/_registry.yaml" in legacy_revision._DERIVED
    assert "source-images.generated.md" in legacy_revision._DERIVED


# ── review-coverage 的按来源整源认证统计 ───────────────────────────────────────
#
# 它回答的是"哪本书整本没人逐页读过"——按页算的覆盖率看不出这件事。
# 实测（2026-08-12 复盘）：768 个内容页里 483 页来自 6 个做过整源认证的来源，
# 而 17 个来源从未做过整源认证。判据只认台账条目自己的 `scope` 字段，不猜。


def _src_page(vault, rel, sources, ptype="concept"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    refs = "".join(f"- source: {s}\n" for s in sources)
    p.write_text(f"---\ntype: {ptype}\nstatus: published\nsource_refs:\n{refs}---\n正文\n",
                 encoding="utf-8")
    return rel


def test_source_coverage_counts_unique_pages_not_per_source_sum(tmp_path):
    """跨源共享页不得按来源相加——那会让"没人读过的页数"虚高。

    实测触发点：deep-learning 的概念页常带 4 个 `source_refs`（教材+课件+论文合并），
    按来源相加得 442，去重后实为 285。一个虚高的数字与本项目要消灭的误导数同类。
    """
    import pipeline
    vault = tmp_path / "wiki"
    shared = _src_page(vault, "domains/d/concepts/共享.md", ["a", "b", "c"])
    only_a = _src_page(vault, "domains/d/concepts/独有.md", ["a"])
    pages = [shared, only_a]

    rows = pipeline._review_source_coverage(vault, pages, {})
    by = {r["source"]: r for r in rows}
    assert by["a"]["owned"] == 2 and by["b"]["owned"] == 1 and by["c"]["owned"] == 1
    # 三个来源 owned 相加 = 4，但去重后只有 2 个页
    assert len(set().union(*(r["pages"] for r in rows))) == 2


def test_source_coverage_reads_scope_field_not_mere_registration(tmp_path):
    """只有 `scope: whole-source` 算整源认证；抽样登记（per-page）不算。"""
    import pipeline
    vault = tmp_path / "wiki"
    p1 = _src_page(vault, "domains/d/concepts/一.md", ["book"])
    p2 = _src_page(vault, "domains/d/concepts/二.md", ["book"])
    entries = {
        p1: [{"date": "2026-08-12", "outcome": "no-findings", "scope": "whole-source"}],
        p2: [{"date": "2026-08-12", "outcome": "no-findings", "scope": "per-page"}],
    }
    row = pipeline._review_source_coverage(vault, [p1, p2], entries)[0]
    assert row["owned"] == 2 and row["whole"] == 1 and row["sampled"] == 1


def test_source_coverage_excludes_overview(tmp_path):
    """overview.md 带全部来源的 source_refs，算进任何单一来源都失真——与台账写入口径一致。"""
    import pipeline
    vault = tmp_path / "wiki"
    ov = _src_page(vault, "overview.md", ["a", "b"], ptype="overview")
    c = _src_page(vault, "domains/d/concepts/x.md", ["a"])
    rows = pipeline._review_source_coverage(vault, [ov, c], {})
    assert [r["source"] for r in rows] == ["a"], rows
    assert rows[0]["owned"] == 1
