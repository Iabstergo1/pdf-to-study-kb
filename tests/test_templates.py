from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
page_rules = _load("page_rules")

TYPES = ["source", "lesson", "concept", "topic", "comparison", "synthesis"]


def test_all_six_templates_exist_and_parse():
    for t in TYPES:
        meta, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        assert meta["type"] == t, f"{t}.md frontmatter type 不符"
        assert meta["status"] == "proposed" and meta["managed_by"] == "pipeline"


def test_templates_contain_required_sections():
    for t in TYPES:
        _, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        assert page_rules.missing_sections(body, page_rules.required_sections_for(t)) == [], \
            f"{t}.md 缺必需小节"


def test_overview_template_exists_with_l5_sections():
    meta, body = mdpage.read_page(TEMPLATES / "overview.md")
    assert meta["type"] == "overview" and meta["managed_by"] == "pipeline"
    assert page_rules.missing_sections(body, page_rules.required_sections_for("overview")) == []


def test_templates_have_no_leading_h1():
    # B1：模板正文不应以一级标题开头（Obsidian 用文件名做内联标题，正文再放同名 H1 会重复渲染）
    for t in TYPES + ["overview"]:
        _, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        assert not (first.startswith("# ") and not first.startswith("## ")), \
            f"{t}.md 正文以一级标题开头，应删除（Obsidian 内联标题已显示文件名）"


def test_lesson_template_clean_prose_contract():
    _, body = mdpage.read_page(TEMPLATES / "lesson.md")
    assert page_rules.find_bare_evidence_ids(body) == []      # 无裸 E-ID
    assert "$$" in body                                        # 原生 KaTeX 示例
    assert "![[assets/" not in body                            # D-1：模板不再示范内嵌源图
    assert "[^" not in body                                    # D-5：模板不再用脚注机制
