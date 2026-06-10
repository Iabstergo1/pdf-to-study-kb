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
