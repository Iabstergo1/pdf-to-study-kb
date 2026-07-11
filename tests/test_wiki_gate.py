from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
wiki_gate = _load("wiki_gate")


def _page(vault, rel, meta, body):
    mdpage.write_page(Path(vault) / rel, meta, body)


GOOD_LESSON = ("# 5.2 信号博弈\n\n这一节讲分离均衡的识别条件，足够长的干净散文正文，"
               "解释为什么发送者类型可以被唯一推断出来。[^e1]\n\n"
               "[^e1]: 证据：wp §5.2\n")


def test_concepts_without_synthesis_warns():
    # 产出 concept 但无综合层页 → 返回 concept 数（cmd_lint 据此打非阻断 [warn]）
    pages = [{"meta": {"type": "concept"}}, {"meta": {"type": "concept"}},
             {"meta": {"type": "lesson"}}]
    assert wiki_gate.concepts_without_synthesis(pages) == 2
    # 有任一综合层页 → 不提醒
    for t in ("overview", "topic", "comparison", "synthesis"):
        assert wiki_gate.concepts_without_synthesis(pages + [{"meta": {"type": t}}]) == 0
    # 没产出 concept（纯 lesson 小源）→ 不提醒
    assert wiki_gate.concepts_without_synthesis([{"meta": {"type": "lesson"}}]) == 0


def test_risk_traceability_fails_without_block_refs():
    pages = [{"rel_path": "domains/d/lessons/l1.md",
              "meta": {"type": "lesson", "source": "s1", "source_refs": []}, "body": "x"}]
    vs = wiki_gate.lint_risk_traceability(pages, source_id="s1", risk_block_ids={"b000003"},
                                          written={"domains/d/lessons/l1.md"})
    assert any(v["rule"] == "risk-traceability" for v in vs)


def test_risk_traceability_passes_with_block_refs():
    refs = [{"source": "s1", "window": "w0001", "pages": [2], "block_ids": ["b000003"]}]
    pages = [{"rel_path": "domains/d/lessons/l1.md",
              "meta": {"type": "lesson", "source": "s1", "source_refs": refs}, "body": "x"}]
    vs = wiki_gate.lint_risk_traceability(pages, source_id="s1", risk_block_ids={"b000003"},
                                          written=set())
    assert vs == []


def test_risk_traceability_no_risk_windows_skips():
    pages = [{"rel_path": "domains/d/lessons/l1.md",
              "meta": {"type": "lesson", "source": "s1", "source_refs": []}, "body": "x"}]
    vs = wiki_gate.lint_risk_traceability(pages, source_id="s1", risk_block_ids=set(), written=set())
    assert vs == []


def test_risk_traceability_only_lessons_not_concepts():
    pages = [{"rel_path": "concepts/c1.md",
              "meta": {"type": "concept", "source": "s1", "source_refs": []}, "body": "x"}]
    vs = wiki_gate.lint_risk_traceability(pages, source_id="s1", risk_block_ids={"b1"},
                                          written={"concepts/c1.md"})
    assert vs == []


def test_collect_proposed_filters_correctly(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed", "managed_by": "pipeline"}, GOOD_LESSON)
    _page(tmp_path, "domains/d/lessons/b.md",
          {"type": "lesson", "status": "published", "managed_by": "pipeline"}, GOOD_LESSON)
    _page(tmp_path, "Review-Queue/x-proposal.md", {"status": "proposed"}, "ignored\n")
    (tmp_path / "log.md").write_text("no frontmatter\n", encoding="utf-8")
    pages = wiki_gate.collect_proposed(tmp_path)
    assert [p["rel_path"] for p in pages] == ["domains/d/lessons/a.md"]


def test_lint_l1_bare_evidence_id(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed"}, "结论 [E-p3-1]。这是一段足够长的正文用来绕过空课检查，"
          "再加一些字数凑够长度阈值即可。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "L1" for v in vs)


def test_lint_missing_footnote_def(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed"}, "论断没有证据定义但是正文足够长足够长足够长足够长"
          "足够长足够长足够长足够长足够长。[^e9]\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "evidence-footnote" for v in vs)


def test_lint_concept_sections_not_required(tmp_path):
    # D-4：正文小节标题不再强制——只有连贯散文、不套固定小节的概念页不再产生 L2/sections
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d", "source_refs": [{"source": "s"}]},
          "均衡是一组相互一致的策略，这里用连贯的学术散文把直觉、形式与边界讲清楚，"
          "不套任何固定小节标题，长度也足够充实充实充实充实。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] in ("L2", "sections") for v in vs)


def test_lint_formula_lesson_no_image_required(tmp_path):
    # D-1：formula-screenshot 已删——公式 lesson 不再要求内嵌源图（源图退出发布产物）
    _page(tmp_path, "domains/d/lessons/f.md",
          {"type": "lesson", "status": "proposed"}, "反应函数推导正文足够长足够长足够长足够长足够长"
          "足够长足够长：\n\n$$u = px$$\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "formula-screenshot" for v in vs)


def test_lint_source_image_embed_blocked(tmp_path):
    # D-1/G1：published 正文禁嵌源图；对本轮 proposed 批（status 无关）也必须拦
    _page(tmp_path, "domains/d/lessons/f.md",
          {"type": "lesson", "status": "proposed"}, "反应函数推导正文足够长足够长足够长足够长足够长"
          "足够长足够长：\n\n$$u = px$$\n\n![[assets/wp/p0001.png]]\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "source-image-embed" for v in vs)
    # 覆盖同一文件为「不嵌源图、只用原生 KaTeX」→ 不触发
    _page(tmp_path, "domains/d/lessons/f.md",
          {"type": "lesson", "status": "proposed"}, "同样的推导但不贴源图正文足够长足够长足够长足够长"
          "足够长足够长：\n\n$$u = px$$\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "source-image-embed" for v in vs2)


def test_lint_short_content_blocked(tmp_path):
    # P2：concept/topic/comparison 正文过短 → content-too-short 阻断（防残次页；讲透优先）
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d", "source_refs": [{"source": "s"}]},
          "太短的概念。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "content-too-short" for v in vs)
    # 覆盖为充实正文 → 不触发
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d", "source_refs": [{"source": "s"}]},
          "均衡是一组相互一致的策略与信念，" * 10 + "\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "content-too-short" for v in vs2)


def test_lint_broken_link_and_l6(tmp_path):
    _page(tmp_path, "domains/d/lessons/short.md",
          {"type": "lesson", "status": "proposed"}, "太短\n")
    _page(tmp_path, "topics/t.md",
          {"type": "topic", "status": "proposed"},
          "# T\n\n## 核心综合\n\n够长的综合正文，链接 [[domains/d/concepts/不存在的页|X]]。\n\n"
          "## 各来源贡献\n\n| 来源 | 章节 | 贡献 |\n|---|---|---|\n| wp | 1 | x |\n\n## 未解决问题\n\n- 无\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "L6-empty-lesson" for v in vs)
    assert any(v["rule"] == "broken-link" for v in vs)


def test_lint_ignores_markup_inside_code_blocks(tmp_path):
    # 通用回归：编程页代码示例里的正则 [^...]、[E.. 字面量、[[ 不应被当成
    # 脚注引用/裸 E-ID/wikilink 而误拦 lint（剔除代码块后检查）。
    _page(tmp_path, "domains/d/lessons/code.md",
          {"type": "lesson", "status": "proposed", "managed_by": "pipeline", "domain": "d",
           "page_path": "domains/d/lessons/code.md", "source_refs": [{"source": "cookbook"}]},
          "这一节讲文本清洗，足够长的干净散文正文用来绕过空课检查再多写一些字。[^e1]\n\n"
          "```python\nimport re\nh = re.sub('[^a-zA-Z_]', '_', name)   # [E-x] 不是证据\n"
          "link = '[[not a wikilink]]'\n```\n\n行内 `[^0-9]+` 同理。\n\n[^e1]: 证据：cookbook §6.1\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert vs == [], f"代码块内标记不应触发 lint，实得 {vs}"


def test_build_index_only_published(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "published", "title": "A 课"}, GOOD_LESSON)
    _page(tmp_path, "domains/d/lessons/b.md",
          {"type": "lesson", "status": "proposed", "title": "B 课"}, GOOD_LESSON)
    _page(tmp_path, "topics/t.md", {"type": "topic", "status": "published", "title": "主题T"}, "# T\n")
    text = wiki_gate.build_index(tmp_path)
    assert "domains/d/lessons/a.md" in text and "topics/t.md" in text
    assert "lessons/b.md" not in text  # proposed 不上 index
    wiki_gate.write_index(tmp_path)
    assert (tmp_path / "index.generated.md").exists()


def test_belongs_to_source_cross_domain_concept():
    # G3：跨域概念页（domain=research-method）经 source_refs 归属发起源 → 随本批 lint/promote，不落孤儿
    meta = {"type": "concept", "domain": "research-method",
            "source_refs": [{"source": "game-theory", "sections": ["8.2"]}]}
    assert wiki_gate.belongs_to_source(
        "domains/research-method/concepts/研究问题.md", meta, "game-theory", set()) is True
    # 也可经 window write_set 归属
    assert wiki_gate.belongs_to_source(
        "domains/research-method/concepts/研究问题.md",
        {"type": "concept", "domain": "research-method"},
        "game-theory", {"domains/research-method/concepts/研究问题.md"}) is True


def test_lint_frontmatter_incomplete_topic_without_source_refs(tmp_path):
    # G2/D3：非 source 综合页缺 source_refs → frontmatter-incomplete（吸收派生页强制溯源）
    _page(tmp_path, "topics/主题.md",
          {"type": "topic", "status": "proposed", "page_path": "topics/主题.md",
           "managed_by": "pipeline"}, "综合正文" * 40 + "\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "frontmatter-incomplete" and "source_refs" in v["detail"] for v in vs)


def test_lint_source_page_needs_no_source_refs(tmp_path):
    # G2：source 页不要求 source_refs（它本身就是来源），但要 source_id/title/domain/format
    _page(tmp_path, "sources/wp.md",
          {"type": "source", "status": "proposed", "managed_by": "pipeline",
           "source_id": "wp", "title": "白皮书", "domain": "d", "format": "pdf"},
          "## 一句话总结\n\n" + "来源综述" * 40 + "\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "frontmatter-incomplete" for v in vs)


def test_build_index_display_name_falls_back_to_basename(tmp_path):
    # B3：无 title/canonical_name 的 topic/comparison/lesson，显示名用 basename，不显示完整路径
    _page(tmp_path, "topics/核心模型谱系.md", {"type": "topic", "status": "published"}, "综合正文\n")
    _page(tmp_path, "comparisons/甲 vs 乙.md", {"type": "comparison", "status": "published"}, "对比正文\n")
    text = wiki_gate.build_index(tmp_path)
    assert "[[topics/核心模型谱系.md|核心模型谱系]]" in text
    assert "[[comparisons/甲 vs 乙.md|甲 vs 乙]]" in text
    assert "|topics/核心模型谱系.md]]" not in text    # 不把完整路径当显示名


def test_lint_formula_table_pipe(tmp_path):
    # 表格单元格内公式含裸 | → 硬拦（fail-closed），任意页类型
    _page(tmp_path, "domains/d/lessons/coop.md",
          {"type": "lesson", "status": "proposed"},
          "合作博弈核心工具，足够长的干净散文正文用来绕过空课检查再多写一些字凑够长度阈值：\n\n"
          "| 工具 | 公式 |\n|---|---|\n| 夏普里值 | $\\phi=\\frac{|S|!}{n!}$ |\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "formula-table-pipe" for v in vs)


def test_lint_blocks_concept_without_synthesis(tmp_path):
    # 阶段 E 强制（一等产物）：产出 concept 但无综合层页 → 阻断发布
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d"},
          "# X\n\n## 一句话\n\nx\n\n## 直觉\n\nx\n\n## 形式化\n\nx\n\n"
          "## 各章如何处理\n\nx\n\n## 与其他概念的关系\n\nx\n\n## 自测\n\n1?\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "L7-synthesis-missing" for v in vs)
    # 补一个 overview 综合页 → 该规则不再触发
    _page(tmp_path, "overview.md", {"type": "overview", "status": "proposed"},
          "# O\n\n## 核心概念地图\n\nx\n\n## 推荐学习路线\n\nx\n\n## 模型家族对比\n\nx\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "L7-synthesis-missing" for v in vs2)


def test_lint_blocks_concept_heavy_without_topic(tmp_path):
    # ≥6 concept 却无 topic 主题页 → 阻断（扁平概念缺分类导航层）
    CSEC = ("## 一句话\n\nx\n\n## 直觉\n\nx\n\n## 形式化\n\nx\n\n"
            "## 各章如何处理\n\nx\n\n## 与其他概念的关系\n\nx\n\n## 自测\n\n1?\n")
    for i in range(6):
        _page(tmp_path, f"domains/d/concepts/c{i}.md",
              {"type": "concept", "status": "proposed", "canonical_id": f"concept.d.c{i}",
               "canonical_name": f"C{i}", "domain": "d"}, f"# C{i}\n\n{CSEC}")
    # 加 overview 满足 L7（综合层有页），但仍缺 topic → 命中 topics-missing
    _page(tmp_path, "overview.md", {"type": "overview", "status": "proposed"},
          "# O\n\n## 核心概念地图\n\nx\n\n## 推荐学习路线\n\nx\n\n## 模型家族对比\n\nx\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "topics-missing" for v in vs)
    assert not any(v["rule"] == "L7-synthesis-missing" for v in vs)  # overview 已满足 L7
    # 加一个 topic 页 → 不再触发
    _page(tmp_path, "topics/t.md", {"type": "topic", "status": "proposed"},
          "# T\n\n## 核心综合\n\nx\n\n## 各来源贡献\n\nx\n\n## 未解决问题\n\nx\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "topics-missing" for v in vs2)


_FILLED = ("## 一句话\n\n真实的一句话定义\n\n## 直觉\n\n真实直觉\n\n## 形式化\n\n$s\\in S$\n\n"
           "## 各章如何处理\n\n§1 提到\n\n## 与其他概念的关系\n\n- 与别的有关\n\n## 自测\n\n1. 问题？\n")
_PLACE = ("## 一句话\n\n（待 /ingest 填写）\n\n## 直觉\n\n（待 /ingest 填写）\n\n## 形式化\n\n（待 /ingest 填写）\n\n"
          "## 各章如何处理\n\n（待 /ingest 填写）\n\n## 与其他概念的关系\n\n（待 /ingest 填写）\n\n"
          "## 自测\n\n（待 /ingest 填写：1–3 个自测问题）\n")
_TOPIC_OK = "# T\n\n## 核心综合\n\nx\n\n## 各来源贡献\n\nx\n\n## 未解决问题\n\nx\n"


def test_lint_blocks_unfilled_placeholder_concept(tmp_path):
    # A1：概念页 6 小节都在、但内容仍是"（待 /ingest 填写）"占位 → 阻断（不许静默发布半成品）
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d"}, f"# X\n\n{_PLACE}")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "placeholder-unfilled" for v in vs)
    # 填好正文 → 该规则不再触发
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d"}, f"# X\n\n{_FILLED}")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "placeholder-unfilled" for v in vs2)


def test_lint_blocks_concepts_uncovered_by_topic(tmp_path):
    # A2：concept-heavy 域里有 topic、但只收编一部分概念 → 未收编者阻断（消灭 canvas"未分类"）
    for i in range(6):
        _page(tmp_path, f"domains/d/concepts/c{i}.md",
              {"type": "concept", "status": "proposed", "canonical_id": f"concept.d.c{i}",
               "canonical_name": f"C{i}", "domain": "d"}, f"# C{i}\n\n{_FILLED}")
    _page(tmp_path, "topics/t.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": ["concept.d.c0", "concept.d.c1"]}, _TOPIC_OK)   # 只收 c0,c1
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "concepts-uncovered" for v in vs)
    # 收编全部 6 个 → 不再触发
    _page(tmp_path, "topics/t.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": [f"concept.d.c{i}" for i in range(6)]}, _TOPIC_OK)
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "concepts-uncovered" for v in vs2)


def test_render_safety_violations_shared_rules():
    # 渲染安全唯一实现：proposed 批与 published preflight 共用
    # ① 非 Obsidian 数学分隔符（按行去重；`\\[4pt]` 的换行间距不误报；行内代码不误报）
    vs = wiki_gate.render_safety_violations("x.md", "行内 \\(a+b\\) 应写 $a+b$。\n")
    assert [v["rule"] for v in vs] == ["math-delimiter-nonobsidian"]
    assert wiki_gate.render_safety_violations("x.md", "$$a \\\\[4pt] b$$\n") == []
    assert wiki_gate.render_safety_violations("x.md", "代码 `\\(x\\)` 不算。\n") == []
    # ② 空题干
    vs = wiki_gate.render_safety_violations("x.md", "> [!question]\n")
    assert [v["rule"] for v in vs] == ["question-stem-empty"]
    # ③ 坏嵌套 + 未知类型仍走同一入口
    bad = "> [!question] 自测\n> 一？\n>\n> [!danger] 假\n> 内容。\n"
    rules = {v["rule"] for v in wiki_gate.render_safety_violations("x.md", bad)}
    assert rules == {"callout-nested-malformed", "callout-unknown"}


def test_vault_render_safety_scans_published_with_owner(tmp_path):
    # published 旧伤复检：带 owner（source_refs 首源），proposed 页不在 published 扫描范围
    _page(tmp_path, "domains/d/lessons/old.md",
          {"type": "lesson", "status": "published", "source": "oldsrc"},
          GOOD_LESSON + "\n> [!question] 自测\n> 一？\n>\n> [!success]- 答\n> 内容。\n")
    _page(tmp_path, "domains/d/lessons/new.md",
          {"type": "lesson", "status": "proposed", "source": "newsrc"},
          "> [!question]\n")
    vs = wiki_gate.vault_render_safety(tmp_path)
    assert [(v["rule"], v["path"], v["owner"]) for v in vs] == \
        [("callout-nested-malformed", "domains/d/lessons/old.md", "oldsrc")]
    # vault-lint 口径（published ∪ proposed）能同时看到两处
    both = wiki_gate.vault_render_safety(tmp_path, statuses=("published", "proposed"))
    assert {(v["rule"], v["path"]) for v in both} == {
        ("callout-nested-malformed", "domains/d/lessons/old.md"),
        ("question-stem-empty", "domains/d/lessons/new.md")}


def test_topic_coverage_monopoly_soft_signal(tmp_path):
    # ②（软警告，非阻断）：单 topic 收编域内过高比例概念 = 链接倾倒糊弄 A2 的征兆
    for i in range(10):
        _page(tmp_path, f"domains/d/concepts/c{i}.md",
              {"type": "concept", "status": "proposed", "canonical_id": f"concept.d.c{i}",
               "canonical_name": f"C{i}", "domain": "d"}, f"# C{i}\n\n{_FILLED}")
    _page(tmp_path, "topics/t1.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": [f"concept.d.c{i}" for i in range(10)]}, _TOPIC_OK)  # 一页收编全部
    warns = wiki_gate.topic_coverage_monopoly(tmp_path)
    assert warns and "topics/t1.md" in warns[0] and "10/10" in warns[0]
    # 拆成两个 topic 各收一半 → 不再触发
    _page(tmp_path, "topics/t1.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": [f"concept.d.c{i}" for i in range(5)]}, _TOPIC_OK)
    _page(tmp_path, "topics/t2.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": [f"concept.d.c{i}" for i in range(5, 10)]}, _TOPIC_OK)
    assert wiki_gate.topic_coverage_monopoly(tmp_path) == []
    # 概念数不足 TOPIC_THRESHOLD 的小域不检查（单 topic 收编全部本来就合理）
    for i in range(4, 10):
        (tmp_path / "domains" / "d" / "concepts" / f"c{i}.md").unlink()
    (tmp_path / "topics" / "t2.md").unlink()
    _page(tmp_path, "topics/t1.md",
          {"type": "topic", "status": "proposed", "domains": ["d"],
           "related_concepts": [f"concept.d.c{i}" for i in range(4)]}, _TOPIC_OK)
    assert wiki_gate.topic_coverage_monopoly(tmp_path) == []


def test_lint_catches_published_placeholder_page(tmp_path):
    # A3：已发布页含占位（首轮漏发布的半成品，后续永不复检的洞）→ 仍要被抓出
    _page(tmp_path, "domains/d/concepts/old.md",
          {"type": "concept", "status": "published", "canonical_id": "concept.d.old",
           "canonical_name": "Old", "domain": "d"}, f"# Old\n\n{_PLACE}")
    # 本轮 proposed 是别的页；published 的 old 仍应被占位检查命中
    _page(tmp_path, "domains/d/lessons/a.md", {"type": "lesson", "status": "proposed"}, GOOD_LESSON)
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "placeholder-unfilled" and "old.md" in v["path"] for v in vs)


def test_lint_title_duplicate_h1(tmp_path):
    # B1：正文首行是与文件名同名的 # H1 → 阻断（Obsidian 内联标题 + 同名 H1 = 标题显示两次）
    _page(tmp_path, "domains/d/concepts/均衡.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.eq",
           "canonical_name": "均衡", "domain": "d"},
          "# 均衡\n\n均衡是一组相互一致的策略，足够长的正文用来避免其它规则干扰对本条的判断。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "title-duplicate-h1" for v in vs)
    # 正文直接从散文开始（无同名 H1）→ 不触发
    _page(tmp_path, "domains/d/concepts/均衡.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.eq",
           "canonical_name": "均衡", "domain": "d"},
          "均衡是一组相互一致的策略，直接从散文开始，没有重复标题的正文内容。\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "title-duplicate-h1" for v in vs2)


def test_stray_files_lists_empty_and_png_md(tmp_path):
    # C4：0 字节 .md 与 *.png.md（Obsidian 点坏链误建）应被列出；正常页不列
    (tmp_path / "domains/d/concepts").mkdir(parents=True)
    (tmp_path / "domains/d/concepts/empty.md").write_text("", encoding="utf-8")
    (tmp_path / "assets/d").mkdir(parents=True)
    (tmp_path / "assets/d/p0001.png.md").write_text("", encoding="utf-8")
    _page(tmp_path, "domains/d/concepts/ok.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.ok",
           "canonical_name": "Ok", "domain": "d"}, f"# Ok\n\n{_FILLED}")
    stray = wiki_gate.stray_files(tmp_path)
    assert "domains/d/concepts/empty.md" in stray
    assert "assets/d/p0001.png.md" in stray          # *.png.md 即使非 assets 顶层排除外也能命中
    assert "domains/d/concepts/ok.md" not in stray


def test_promote_flips_status(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed"}, GOOD_LESSON)
    pages = wiki_gate.collect_proposed(tmp_path)
    n = wiki_gate.promote(tmp_path, pages)
    assert n == 1
    meta, _ = mdpage.read_page(tmp_path / "domains/d/lessons/a.md")
    assert meta["status"] == "published"
    assert wiki_gate.collect_proposed(tmp_path) == []


def test_build_quiz_index_collects_published_questions(tmp_path):
    # quiz 索引：只收 published 页的 [!question] 题干 + 回链；proposed 页与派生/排除目录不收；不含答案正文。
    q_body = ("概念正文。\n\n> [!question] 自测\n> 为什么价格压到边际成本？\n"
              "> > [!success]- 参考答案\n> > 因为无差异商品的伯特兰竞争。\n")
    _page(tmp_path, "domains/game-theory/concepts/伯特兰模型.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.game-theory.bertrand", "canonical_name": "伯特兰模型",
           "domain": "game-theory"}, q_body)
    _page(tmp_path, "topics/模型库.md",
          {"type": "topic", "status": "published", "managed_by": "pipeline", "title": "模型库",
           "source_refs": [{"source": "s"}]},
          "主题正文。\n\n> [!question] 综合演练\n> 三个模型的策略变量各是什么？\n"
          "> > [!tip]- 提示\n> > 数量/价格/位置。\n")
    _page(tmp_path, "domains/game-theory/concepts/草稿.md",
          {"type": "concept", "status": "proposed", "managed_by": "pipeline",
           "canonical_id": "concept.game-theory.draft", "canonical_name": "草稿",
           "domain": "game-theory"},
          "> [!question] 自测\n> proposed 页的题不该出现。\n")
    text = wiki_gate.build_quiz_index(tmp_path)
    assert "为什么价格压到边际成本？" in text
    assert "[[domains/game-theory/concepts/伯特兰模型.md|伯特兰模型]]" in text
    assert "三个模型的策略变量各是什么？" in text
    assert "proposed 页的题不该出现" not in text
    assert "因为无差异商品的伯特兰竞争" not in text  # 不泄露答案
    # 落盘 + 幂等：write_quiz_index 写 quiz-index.generated.md；该文件在 _DERIVED 中，
    # 重建时不会把索引自己再收进去
    wiki_gate.write_quiz_index(tmp_path)
    assert (tmp_path / "quiz-index.generated.md").exists()
    text2 = wiki_gate.build_quiz_index(tmp_path)
    assert text2.count("为什么价格压到边际成本？") == 1


def test_build_propositions_index_published_only(tmp_path):
    # 命题总表：只收 published 页；按域分组；域内重名可检出（软警告用）
    _page(tmp_path, "domains/game-theory/concepts/斯塔克尔伯格模型.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.game-theory.stackelberg", "canonical_name": "斯塔克尔伯格模型",
           "domain": "game-theory"},
          "正文。**命题（先发优势）**：领导者产量为古诺的 1.5 倍。\n")
    _page(tmp_path, "domains/game-theory/concepts/草稿页.md",
          {"type": "concept", "status": "proposed", "managed_by": "pipeline",
           "canonical_id": "concept.game-theory.draft2", "canonical_name": "草稿页",
           "domain": "game-theory"},
          "**命题（不该出现）**：proposed 页的命题不收。\n")
    text = wiki_gate.build_propositions_index(tmp_path)
    assert "**先发优势** — 领导者产量为古诺的 1.5 倍。" in text
    assert "[[domains/game-theory/concepts/斯塔克尔伯格模型.md|斯塔克尔伯格模型]]" in text
    assert "不该出现" not in text
    wiki_gate.write_propositions_index(tmp_path)
    assert (tmp_path / "propositions.generated.md").exists()
    # 域内重名 → 检出
    props = wiki_gate.collect_propositions(tmp_path)
    props.append(dict(props[0]))
    assert wiki_gate.duplicate_proposition_names(props) == ["game-theory/命题（先发优势）"]


CONCEPT_BODY = ("这一概念解释策略互动中决策主体如何选择最优行动，并给出一个足够长的干净散文正文："
                "先说直觉，再给形式化定义，最后给一个可核对的最小例子，长度超过残次页底线。\n")
SEED_OVERVIEW = ("<这是 vault 入口：由 /ingest 随每次 ingest 增量维护的活综合页。>\n\n"
                 "## 核心概念地图\n\n<按领域组织的概念网络：wikilink 链到概念页>\n")


def test_lint_overview_seed_blocked_for_concept_batch(tmp_path):
    # 回归（两本书连踩：重写被 lint 失败回滚吃掉后无人复查，published overview 始终是种子）：
    # 本批产出 concept 而 overview.md 仍含种子尖括号占位 → 阻断
    _page(tmp_path, "overview.md",
          {"type": "overview", "status": "published", "managed_by": "pipeline"}, SEED_OVERVIEW)
    _page(tmp_path, "domains/d/concepts/概念甲.md",
          {"type": "concept", "status": "proposed", "managed_by": "pipeline",
           "canonical_id": "concept.d.jia", "canonical_name": "概念甲", "domain": "d"}, CONCEPT_BODY)
    _page(tmp_path, "topics/主题一.md",
          {"type": "topic", "status": "proposed", "managed_by": "pipeline", "title": "主题一",
           "source_refs": [{"source": "s"}]},
          "主题正文，链入 [[domains/d/concepts/概念甲|概念甲]]，长度足够通过残次页检查，"
          "并把该概念收编进本主题的叙述脉络之中。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert [v for v in vs if v["rule"] == "overview-seed"], vs
    # 填充 overview（无占位符）→ 放行
    _page(tmp_path, "overview.md",
          {"type": "overview", "status": "published", "managed_by": "pipeline",
           "source_refs": [{"source": "s"}]},
          "## 主题导航\n\n从 [[topics/主题一|主题一]] 进入概念网络，按需深入各域。\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert [v for v in vs2 if v["rule"] == "overview-seed"] == []


def test_lint_overview_seed_not_triggered_without_concepts(tmp_path):
    # lesson-only 小源（无 concept）不强制填 overview；无 overview 文件也不触发
    _page(tmp_path, "overview.md",
          {"type": "overview", "status": "published", "managed_by": "pipeline"}, SEED_OVERVIEW)
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
           "title": "A", "source": "s"},
          "一段足够长的 lesson 正文，讲清楚直觉与依赖关系，避免触发空课代理长度检查。"
          "再补一句让它稳超八十字符的底线要求，保证本测试只考察 overview 规则本身。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert [v for v in vs if v["rule"] == "overview-seed"] == []


def test_lint_accepts_obsidian_escaped_pipe_wikilink_in_table(tmp_path):
    # 表格单元格内 Obsidian 标准写法 [[path\|alias]]：不得判 broken-link（曾把尾部 \ 捕进目标）；
    # 裸 | 写法虽过 lint 但会撕碎 Obsidian 表格渲染——正确姿势是转义，门禁必须认可转义。
    _page(tmp_path, "domains/d/concepts/概念甲.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.d.jia", "canonical_name": "概念甲", "domain": "d"},
          CONCEPT_BODY)
    _page(tmp_path, "comparisons/对比页.md",
          {"type": "comparison", "status": "proposed", "managed_by": "pipeline",
           "title": "对比页", "source_refs": [{"source": "s"}]},
          "| 维度 | 甲 |\n|---|---|\n| 定义 | [[domains/d/concepts/概念甲\\|概念甲]] |\n\n"
          "表格外的散文补充说明两者取舍，篇幅足以通过残次页底线检查；这里再补一句让对比页"
          "的正文长度稳超一百二十个字符，确保本测试只考察转义竖线的链接解析行为本身。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert [v for v in vs if v["rule"] == "broken-link"] == [], vs


def test_lint_blocks_bare_pipe_wikilink_in_table(tmp_path):
    # 裸竖线别名 wikilink 落在表格行 → table-wikilink-pipe 阻断（曾骗过 lint 却撕碎 Obsidian 渲染）
    _page(tmp_path, "domains/d/concepts/概念甲.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.d.jia", "canonical_name": "概念甲", "domain": "d"},
          CONCEPT_BODY)
    _page(tmp_path, "comparisons/裸竖线对比页.md",
          {"type": "comparison", "status": "proposed", "managed_by": "pipeline",
           "title": "裸竖线对比页", "source_refs": [{"source": "s"}]},
          "| 维度 | 甲 |\n|---|---|\n| 定义 | [[domains/d/concepts/概念甲|概念甲]] |\n\n"
          "表格外的散文补充说明两者取舍，篇幅足以通过残次页底线检查；这里再补一句让对比页"
          "的正文长度稳超一百二十个字符，确保本测试只考察表格内裸竖线的检测行为本身。\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert [v for v in vs if v["rule"] == "table-wikilink-pipe"], vs
