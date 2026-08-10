"""overview 导航完备性：`overview-source-unlinked` 门禁 + `sync-overview-sources` 补救通道。

立法依据（prepush-audit-2026-08-08 / overview 导航缺口）：该缺口跨三本书复现
（mysql / llm-fundamentals / deep-learning），最后一次实测 22 个来源里 **15 个**在
overview.md 中没有任何入口——读者只能靠文件树发现它们。

机制上它必然反复：**overview.md 只有一条写入通道**——ingest 的 `write_scope`
（`workorder.py`）。`revise-adopted` 的 `_safe_rel` 显式拒它，`kb-save` 是 new-page-only。
所以 ingest 之后没有维护手段，每本书只顾自己那一源，缺口逐本累积。

因此本轮**同时**落两件事，缺一不可：门禁 + 补救通道。只加门禁不加通道，正犯核心约束⑦
（门禁不得在没有正当编辑通道时强制要求编辑）。
"""
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline  # noqa: E402
import wiki_gate  # noqa: E402


def _vault(tmp_path, *, overview_body: str, sources=("alpha", "beta")):
    vault = tmp_path / "wiki"
    (vault / "sources").mkdir(parents=True, exist_ok=True)
    for sid in sources:
        (vault / "sources" / f"{sid}.md").write_text(
            f"---\ntype: source\nsource_id: {sid}\ntitle: {sid.upper()} 教材\n"
            f"status: published\nmanaged_by: pipeline\n---\n台账正文。\n",
            encoding="utf-8")
    (vault / "overview.md").write_text(
        "---\ntype: overview\nstatus: published\nmanaged_by: pipeline\n"
        "title: 知识库总览\nsource_refs:\n- source: alpha\n---\n" + overview_body,
        encoding="utf-8")
    return vault


def test_unlinked_sources_detected(tmp_path):
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA 教材]]。\n")
    assert wiki_gate.overview_unlinked_sources(vault) == ["beta"]


@pytest.mark.parametrize("link", [
    "[[sources/beta]]",
    "[[sources/beta|别名]]",
    "[[sources/beta.md]]",
    "[[sources/beta.md|别名]]",
])
def test_both_wikilink_spellings_count_as_linked(tmp_path, link):
    """wikilink 可带或不带 `.md`、可带别名——四种写法都算命中。

    这条不是洁癖：阶段二核对入口覆盖时，我第一次拿带 `.md` 的键去比 overview 里不带后缀的
    wikilink，得到了假的"0 命中"。口径不归一就会误报整整一屏。
    """
    vault = _vault(tmp_path,
                   overview_body=f"[[sources/alpha|A]] 与 {link}。\n")
    assert wiki_gate.overview_unlinked_sources(vault) == []


def test_missing_overview_is_not_a_violation(tmp_path):
    """overview.md 不存在时不报——它的存在性由 overview-seed 与 init-vault/retract 保证。"""
    vault = tmp_path / "wiki"
    (vault / "sources").mkdir(parents=True)
    (vault / "sources" / "alpha.md").write_text(
        "---\ntype: source\nsource_id: alpha\nstatus: published\n---\n正文\n",
        encoding="utf-8")
    assert wiki_gate.overview_unlinked_sources(vault) == []


def test_rule_fires_in_phase_e_but_never_for_kb_save(tmp_path):
    """**核心约束⑦ 的边界**：这条门禁只在 ingest 阶段 E 生效。

    kb-save 是 new-page-only，改不了既有的 overview.md。对它设这条门就是"要求编辑却不给
    编辑通道"，正是本项目已经吃过两次亏的那类 mis-scoped 规则（broken-link 逼人凭空造页、
    L7-synthesis-missing 判错射程）。所以 phase_e=False 时必须一条都不报。
    """
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA]]。\n")
    pages = [{"rel_path": "domains/d/concepts/x.md", "body": "概念正文。",
              "meta": {"type": "concept", "status": "proposed"}}]

    fired = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=True, source="beta")
             if v["rule"] == "overview-source-unlinked"]
    assert len(fired) == 1 and "beta" in fired[0]["detail"]

    quiet = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=False, source="beta")
             if v["rule"] == "overview-source-unlinked"]
    assert quiet == [], "kb-save 会话不得被这条门禁拦住——它没有修复通道"


# ── ③ 硬门禁射程 = 本轮来源自己那一页 ──────────────────────────────────────────
#
# 旧射程是"扫全库"：第 23 本书落库时，前 22 本里任何一个没入口的来源都会让**本轮整批**
# 走 pipeline.cmd_lint 的无差别回滚（连 overview 自己这一轮的就地编辑一起还原）。
# 缺口的成因是"每次 ingest 忘掉自己那本"，射程就该精确对齐成因：本轮来源硬拦，
# 别人的欠账降级成软警告——否则是拿别人的债回滚我这批。


def test_hard_gate_covers_only_the_current_round_source(tmp_path):
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA]]。\n")
    pages = [{"rel_path": "domains/d/concepts/x.md", "body": "概念正文。",
              "meta": {"type": "concept", "status": "proposed"}}]

    # 本轮是 alpha，自己那页有入口 → 不得因 beta 的欠账拦住本批
    fired = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=True, source="alpha")
             if v["rule"] == "overview-source-unlinked"]
    assert fired == [], "别人的欠账不得回滚我这批"

    # 本轮是 beta，自己那页没入口 → 必须拦
    fired = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=True, source="beta")
             if v["rule"] == "overview-source-unlinked"]
    assert len(fired) == 1 and "beta" in fired[0]["detail"]


def test_source_without_a_ledger_page_is_not_hard_blocked(tmp_path):
    """本轮来源还没有台账页时不报这条——那是 `source-page-missing` 的射程，不是本条的。"""
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA]]。\n")
    pages = [{"rel_path": "domains/d/concepts/x.md", "body": "概念正文。",
              "meta": {"type": "concept", "status": "proposed"}}]
    fired = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=True, source="gamma")
             if v["rule"] == "overview-source-unlinked"]
    assert fired == []


def test_other_sources_debt_is_a_soft_warning(tmp_path):
    """别人的欠账仍要**看得见**，只是不阻断——降级不等于删掉信号。"""
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA]]。\n")
    msgs = wiki_gate.overview_unlinked_other_sources(vault, current_source="alpha")
    joined = "\n".join(msgs)
    assert "beta" in joined and "alpha" not in joined.replace("alpha|ALPHA", "")
    assert "sync-overview-sources" in joined, "软警告必须带上补救命令"

    # 本轮来源自己那条走硬门禁，不在软警告里重复报
    assert wiki_gate.overview_unlinked_other_sources(
        vault, current_source="beta") == []


# ── ④ 路线可点性：不再挂在小节标题上 ────────────────────────────────────────────


def test_routes_are_detected_without_the_canonical_heading(tmp_path):
    """D-4 明文不管小节标题，这条检查就不能挂在 `## 推荐学习路线` 这个精确字符串上。

    判据（含箭头且 wikilink 少于箭头）本身只会命中路线形态的行，不需要标题当锚。
    """
    vault = _vault(tmp_path, overview_body=(
        "## 学习路线\n\n1. **甲线**：一步 → 二步 → 三步。\n"))
    msgs = wiki_gate.overview_unclickable_routes(vault)
    assert msgs and "甲线" in "\n".join(msgs)


def test_routes_outside_any_heading_are_detected(tmp_path):
    vault = _vault(tmp_path, overview_body="1. **甲线**：一步 → 二步 → 三步。\n")
    assert wiki_gate.overview_unclickable_routes(vault)


def _with_topics(tmp_path, *, overview_body, topics):
    """topics = {主题名: [概念名...]}；主题页正文链其概念，归属由 topic_membership 算出。"""
    vault = _vault(tmp_path, overview_body=overview_body)
    for tname, concepts in topics.items():
        links = "".join(
            f"[[domains/d/concepts/{c}|{c}]]、" for c in concepts)
        (vault / "topics").mkdir(parents=True, exist_ok=True)
        (vault / "topics" / f"{tname}.md").write_text(
            f"---\ntype: topic\nstatus: published\nmanaged_by: pipeline\n"
            f"title: {tname}\nsource_refs:\n- source: alpha\n---\n本主题连接 {links}。\n",
            encoding="utf-8")
        for c in concepts:
            p = vault / "domains" / "d" / "concepts" / f"{c}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                f"---\ntype: concept\nstatus: published\nmanaged_by: pipeline\n"
                f"canonical_id: concept.d.{c}\ncanonical_name: {c}\ndomain: d\n"
                f"source_refs:\n- source: alpha\n---\n概念正文。\n", encoding="utf-8")
    return vault


def test_unlinked_topics_are_a_soft_signal_not_a_gate(tmp_path):
    """主题缺入口**只提示、不阻断**——与来源台账那条硬门禁刻意分级。

    硬拦主题会要求"每个主题都得在 overview 里有一段介绍"，冷门主题会被逼出填充式
    文字，那是制造内容而非保证质量（核心约束⑦）。所以它走 `[warn]`，不进违规列表。
    """
    vault = _with_topics(
        tmp_path,
        overview_body="导航：[[topics/甲主题|甲主题]]。来源：[[sources/alpha|A]]、[[sources/beta|B]]。\n",
        topics={"甲主题": ["c1"], "乙主题": ["c2", "c3"], "丙主题": ["c4"]})

    msgs = wiki_gate.overview_unlinked_topics(vault)
    joined = "\n".join(msgs)
    assert "乙主题" in joined and "丙主题" in joined
    assert "甲主题" not in joined, "已有入口的主题不该被提示"
    assert "2 个主题" in msgs[0] and "3 个概念" in msgs[0], msgs[0]
    assert msgs.index([m for m in msgs if "乙主题" in m][0]) < \
        msgs.index([m for m in msgs if "丙主题" in m][0]), "缺口大的排前面"

    # 关键：它绝不能变成违规
    pages = [{"rel_path": "domains/d/concepts/c1.md", "body": "正文",
              "meta": {"type": "concept", "status": "proposed"}}]
    rules = {v["rule"] for v in wiki_gate.lint_pages(vault, pages, phase_e=True)}
    assert not any("topic" in r and "unlinked" in r for r in rules), rules


def test_no_unlinked_topics_means_silence(tmp_path):
    """全部有入口时一个字都不打——软警告不能变成常年噪音。"""
    vault = _with_topics(
        tmp_path,
        overview_body="导航：[[topics/甲主题|甲]]、[[topics/乙主题|乙]]。"
                      "来源：[[sources/alpha|A]]、[[sources/beta|B]]。\n",
        topics={"甲主题": ["c1"], "乙主题": ["c2"]})
    assert wiki_gate.overview_unlinked_topics(vault) == []


def _routes(tmp_path, lines):
    body = ("导航：[[topics/甲主题|甲]]。来源：[[sources/alpha|A]]、[[sources/beta|B]]。\n\n"
            "## 推荐学习路线\n\n" + "\n".join(lines) + "\n")
    return _vault(tmp_path, overview_body=body)


def test_unclickable_routes_are_flagged(tmp_path):
    """整条纯文本的路线要被点名；判据只数箭头与链接，不切分步骤。"""
    vault = _routes(tmp_path, [
        "1. **甲线**（读完能做甲）：一步 → 二步 → 三步。",
        "2. **乙线**（读完能做乙）：[[topics/甲主题|一步]] → [[topics/乙主题|二步]] → "
        "[[topics/丙主题|三步]]。",
    ])
    msgs = wiki_gate.overview_unclickable_routes(vault)
    joined = "\n".join(msgs)
    assert "甲线" in joined and "乙线" not in joined, joined
    assert "2 个箭头，仅 0 个链接" in joined


def test_partially_linked_route_still_flagged(tmp_path):
    """只链了首尾、中间几步仍是纯文本 —— 这正是本库修复前的真实形态。"""
    vault = _routes(tmp_path, [
        "1. **甲线**：[[topics/甲主题|起点]] → 二步 → 三步 → 四步 → [[topics/乙主题|终点]]。",
    ])
    msgs = wiki_gate.overview_unclickable_routes(vault)
    assert msgs and "4 个箭头，仅 2 个链接" in "\n".join(msgs)


def test_fully_linked_routes_are_silent(tmp_path):
    """全可点时一个字不打 —— 软警告不能变成常年噪音。"""
    vault = _routes(tmp_path, [
        "1. **甲线**：[[topics/甲主题|一]] → [[topics/乙主题|二]] → [[topics/丙主题|三]]。",
        "2. **乙线**：只有一个入口 [[topics/甲主题|甲]]，无箭头。",
    ])
    assert wiki_gate.overview_unclickable_routes(vault) == []


def test_route_warning_never_becomes_a_violation(tmp_path):
    """与主题入口那条同理：只提示，绝不进违规列表。"""
    vault = _routes(tmp_path, ["1. **甲线**：一步 → 二步 → 三步。"])
    pages = [{"rel_path": "domains/d/concepts/c1.md", "body": "正文",
              "meta": {"type": "concept", "status": "proposed"}}]
    rules = {v["rule"] for v in wiki_gate.lint_pages(vault, pages, phase_e=True)}
    assert not any("route" in r or "clickable" in r for r in rules), rules


def test_arrows_inside_the_aside_do_not_count(tmp_path):
    """路线说明里的箭头是叙述，不是步骤分隔。

    真实库上当场误报过一次：「（读完能说清 n-grams→RNN→Transformer 为何依次被取代…）」
    自带两个箭头，把一条全可点的路线判成了纯文本。判据必须先剥括号说明再数。
    """
    vault = _routes(tmp_path, [
        "1. **甲线**（读完能说清 a→b→c 为何依次被取代）："
        "[[topics/甲主题|一]] → [[topics/乙主题|二]] → [[topics/丙主题|三]]。",
    ])
    assert wiki_gate.overview_unclickable_routes(vault) == []


def test_page_without_any_numbered_list_is_silent(tmp_path):
    """没有编号列表项就没有路线可判——静默（结构由内容决定，D-4 不强制小节）。"""
    vault = _vault(tmp_path, overview_body="只有导航：[[sources/alpha|A]]、[[sources/beta|B]]。\n")
    assert wiki_gate.overview_unclickable_routes(vault) == []


def test_sync_dry_run_writes_nothing(tmp_path, monkeypatch):
    vault = _vault(tmp_path, overview_body="只提到 [[sources/alpha|ALPHA]]。\n")
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    before = (vault / "overview.md").read_bytes()
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=False))
    assert (vault / "overview.md").read_bytes() == before, "默认 dry-run 必须零写入"


def test_sync_apply_adds_block_and_is_idempotent(tmp_path, monkeypatch):
    """--apply 补齐后门禁转绿；重复跑是 byte no-op；**块外正文一字不动**。"""
    prose = "这是人写的导语，不该被机器动。\n\n只提到 [[sources/alpha|ALPHA]]。\n"
    vault = _vault(tmp_path, overview_body=prose)
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))

    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))
    text = (vault / "overview.md").read_text(encoding="utf-8")
    assert "这是人写的导语，不该被机器动。" in text, "块外正文必须原样保留"
    assert "[[sources/beta|BETA 教材]]" in text
    assert wiki_gate.overview_unlinked_sources(vault) == []

    after_first = (vault / "overview.md").read_bytes()
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))
    assert (vault / "overview.md").read_bytes() == after_first, "重复 apply 必须 byte no-op"


def test_sync_rewrites_existing_block_instead_of_appending(tmp_path, monkeypatch):
    """块已存在时就地重写，不追加第二个块（否则每来一本书就多一段）。"""
    vault = _vault(tmp_path, overview_body="导语。\n\n[[sources/alpha|ALPHA]]\n")
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))

    (vault / "sources" / "gamma.md").write_text(
        "---\ntype: source\nsource_id: gamma\ntitle: GAMMA 教材\nstatus: published\n---\n台账\n",
        encoding="utf-8")
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))

    text = (vault / "overview.md").read_text(encoding="utf-8")
    assert text.count(pipeline._OVERVIEW_BLOCK_START) == 1, "不得追加第二个块"
    assert "[[sources/gamma|GAMMA 教材]]" in text
    assert wiki_gate.overview_unlinked_sources(vault) == []


# ── ① 块边界 fail-closed ────────────────────────────────────────────────────────
#
# 本命令是**唯一**会写 published 内容页的路径，overview.md 无快照、`wiki/` 不进 git，
# 写坏了不可恢复。所以块形态不合法时宁可罢工，绝不猜。
# 实证（navigation-review-verdict-2026-08-10 F1）：旧实现只查两个标记"各自存在"，
# 遇到"START 丢了 END + 后面又有一个完整块"时，把夹在两块之间的人写正文整段删掉。

_S, _E = pipeline._OVERVIEW_BLOCK_START, pipeline._OVERVIEW_BLOCK_END

_MALFORMED = {
    "START 丢了 END，后面还有个完整块": (
        f"导语一\n\n{_S}\n\n旧块\n\n人写的正文——夹在两块之间\n\n{_S}\n\n新块\n\n{_E}\n\n结尾\n"),
    "两个 START 两个 END": (
        f"导语\n\n{_S}\n块一\n{_E}\n\n中间正文\n\n{_S}\n块二\n{_E}\n"),
    "只有 START": f"导语\n\n{_S}\n\n块内容但没有结束标记\n",
    "只有 END": f"导语\n\n没有开始标记\n\n{_E}\n",
    "END 在 START 之前": f"导语\n\n{_E}\n\n次序颠倒\n\n{_S}\n",
}


@pytest.mark.parametrize("shape", sorted(_MALFORMED))
@pytest.mark.parametrize("apply", [False, True])
def test_sync_refuses_malformed_block_and_writes_nothing(tmp_path, monkeypatch, shape, apply):
    vault = _vault(tmp_path, overview_body=_MALFORMED[shape])
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    before = (vault / "overview.md").read_bytes()

    with pytest.raises(SystemExit) as exc:
        pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=apply))

    assert "sources-index" in str(exc.value)
    assert (vault / "overview.md").read_bytes() == before, "罢工必须零写入"


def test_sync_dry_run_prints_the_region_it_would_replace(tmp_path, monkeypatch, capsys):
    """dry-run 必须让人**看见会删什么**——只说"将重写既有块"，人无从判断风险。"""
    vault = _vault(tmp_path, overview_body=(
        f"导语。\n\n{_S}\n\n旧块正文：只列了 [[sources/alpha|ALPHA]]。\n\n{_E}\n"))
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    before = (vault / "overview.md").read_bytes()

    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=False))

    out = capsys.readouterr().out
    assert "旧块正文：只列了 [[sources/alpha|ALPHA]]。" in out, "被替换区间的原文必须原样打印"
    assert (vault / "overview.md").read_bytes() == before


# ── ② 触发判据 = 块 ≠ 期望值（不再是 missing 非空）────────────────────────────


def test_sync_cleans_a_stale_link_left_by_retraction(tmp_path, monkeypatch):
    """撤库后 CLI 自己写进块里的链接会变成断链，而 broken-link 是硬门禁。

    旧判据（missing 非空才动）在这里直接报"无需改动"，把一个自己造的断链留在
    published 页上，且 overview 的写入通道只有 ingest —— 补救通道只会加、不会清
    （F2）。判据改成"块 ≠ 期望值"后，同一条命令顺带清掉它。
    """
    vault = _vault(tmp_path, overview_body="导语。\n")
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))
    assert "[[sources/beta|BETA 教材]]" in (vault / "overview.md").read_text(encoding="utf-8")

    (vault / "sources" / "beta.md").unlink()          # 模拟 retract-source
    assert wiki_gate.overview_unlinked_sources(vault) == [], "缺口为空——旧判据据此罢手"

    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))
    text = (vault / "overview.md").read_text(encoding="utf-8")
    assert "sources/beta" not in text, "撤库残留必须被清掉，否则 broken-link 硬拦且无通道可修"
    assert "[[sources/alpha|ALPHA 教材]]" in text


def test_sync_refreshes_a_stale_title(tmp_path, monkeypatch):
    """书名改了、缺口仍为空——旧判据同样罢手，块里留着陈旧书名。"""
    vault = _vault(tmp_path, overview_body="导语。\n")
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))

    (vault / "sources" / "beta.md").write_text(
        "---\ntype: source\nsource_id: beta\ntitle: 改过的书名\nstatus: published\n---\n台账\n",
        encoding="utf-8")
    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))
    assert "[[sources/beta|改过的书名]]" in (vault / "overview.md").read_text(encoding="utf-8")


def test_sync_never_inserts_a_block_into_a_clean_overview(tmp_path, monkeypatch):
    """块不存在且无缺口 → no-op。不往人写得好好的 overview 里硬塞一个机器块。"""
    vault = _vault(tmp_path, overview_body=(
        "人写的导航：[[sources/alpha|ALPHA]] 与 [[sources/beta|BETA]]。\n"))
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    before = (vault / "overview.md").read_bytes()

    pipeline.cmd_sync_overview_sources(SimpleNamespace(apply=True))

    text = (vault / "overview.md").read_text(encoding="utf-8")
    assert pipeline._OVERVIEW_BLOCK_START not in text
    assert (vault / "overview.md").read_bytes() == before
