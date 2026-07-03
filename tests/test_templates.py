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

# 只保留两个 runtime 模板：concept.md（resolve-concept 新建骨架）、overview.md（init-vault seed）。
# 其余页型正文由 purpose + 写作 LLM 决定（D-4：REQUIRED_SECTIONS 已空），不再提供固定模板。
RUNTIME_TEMPLATES = ["concept", "overview"]


def test_runtime_templates_exist_and_parse():
    for t in RUNTIME_TEMPLATES:
        meta, _ = mdpage.read_page(TEMPLATES / f"{t}.md")
        assert meta["type"] == t, f"{t}.md frontmatter type 不符"
        assert meta["managed_by"] == "pipeline"
    # concept 新建骨架是 proposed；overview 是 init-vault 发布态活入口页。
    assert mdpage.read_page(TEMPLATES / "concept.md")[0]["status"] == "proposed"
    assert mdpage.read_page(TEMPLATES / "overview.md")[0]["status"] == "published"


def test_no_stale_page_type_templates():
    # D-4 清理：source/lesson/topic/comparison/synthesis 不再有固定模板（正文交 purpose + 写作 LLM）。
    for t in ["source", "lesson", "topic", "comparison", "synthesis"]:
        assert not (TEMPLATES / f"{t}.md").exists(), \
            f"{t}.md 应已删除——页型正文不再由固定模板约束"


def test_required_sections_empty_for_all_types():
    # D-4：确定性层不再强制任何页型的正文小节标题；此测试锁定"不强制"这一契约。
    for t in ["source", "lesson", "concept", "topic", "comparison", "synthesis", "overview"]:
        assert page_rules.required_sections_for(t) == [], \
            f"{t} 不应再有必需小节（REQUIRED_SECTIONS 已清空）"


def test_runtime_templates_have_no_leading_h1():
    # B1：模板正文不应以一级标题开头（Obsidian 用文件名做内联标题，正文再放同名 H1 会重复渲染）
    for t in RUNTIME_TEMPLATES:
        _, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        assert not (first.startswith("# ") and not first.startswith("## ")), \
            f"{t}.md 正文以一级标题开头，应删除（Obsidian 内联标题已显示文件名）"
