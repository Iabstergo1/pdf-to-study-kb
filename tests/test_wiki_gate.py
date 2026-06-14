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


def test_lint_l2_concept_missing_sections(tmp_path):
    _page(tmp_path, "domains/d/concepts/x.md",
          {"type": "concept", "status": "proposed", "canonical_id": "concept.d.x",
           "canonical_name": "X", "domain": "d"}, "# X\n\n## 直觉\n\n只有直觉一节\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "L2" for v in vs)


def test_lint_formula_lesson_needs_screenshot(tmp_path):
    _page(tmp_path, "domains/d/lessons/f.md",
          {"type": "lesson", "status": "proposed"}, "公式推导正文足够长足够长足够长足够长足够长足够长"
          "足够长足够长：\n\n$$u = px$$\n")
    vs = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert any(v["rule"] == "formula-screenshot" for v in vs)
    # 有截图则通过该规则
    _page(tmp_path, "domains/d/lessons/f.md",
          {"type": "lesson", "status": "proposed"}, "公式推导正文足够长足够长足够长足够长足够长足够长"
          "足够长足够长：\n\n$$u = px$$\n\n![[assets/wp/p0001.png]]\n")
    vs2 = wiki_gate.lint_pages(tmp_path, wiki_gate.collect_proposed(tmp_path))
    assert not any(v["rule"] == "formula-screenshot" for v in vs2)


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
          {"type": "lesson", "status": "proposed"},
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


def test_promote_flips_status(tmp_path):
    _page(tmp_path, "domains/d/lessons/a.md",
          {"type": "lesson", "status": "proposed"}, GOOD_LESSON)
    pages = wiki_gate.collect_proposed(tmp_path)
    n = wiki_gate.promote(tmp_path, pages)
    assert n == 1
    meta, _ = mdpage.read_page(tmp_path / "domains/d/lessons/a.md")
    assert meta["status"] == "published"
    assert wiki_gate.collect_proposed(tmp_path) == []
