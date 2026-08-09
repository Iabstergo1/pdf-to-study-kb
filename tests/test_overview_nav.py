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

    fired = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=True)
             if v["rule"] == "overview-source-unlinked"]
    assert len(fired) == 1 and "beta" in fired[0]["detail"]

    quiet = [v for v in wiki_gate.lint_pages(vault, pages, phase_e=False)
             if v["rule"] == "overview-source-unlinked"]
    assert quiet == [], "kb-save 会话不得被这条门禁拦住——它没有修复通道"


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
