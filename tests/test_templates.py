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


def test_lesson_template_clean_prose_contract():
    _, body = mdpage.read_page(TEMPLATES / "lesson.md")
    assert page_rules.find_bare_evidence_ids(body) == []      # 无裸 E-ID
    assert page_rules.missing_footnote_defs(body) == set()    # 示例脚注引用均有定义
    assert "$$" in body                                        # KaTeX 示例
    assert "![[" in body                                       # 源页截图内嵌示例
