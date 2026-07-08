from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("page_rules", ROOT / "scripts" / "page_rules.py")
page_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page_rules)


def test_find_bare_evidence_ids():
    body = "结论 A [E-p12-3]，结论 B。\n\n另见 [E-fig_4]。\n"
    assert page_rules.find_bare_evidence_ids(body) == ["[E-p12-3]", "[E-fig_4]"]


def test_clean_prose_has_no_bare_ids():
    body = "干净的散文，证据在脚注。[^e1]\n\n[^e1]: 证据：whitepaper §5.2\n"
    assert page_rules.find_bare_evidence_ids(body) == []


def test_footnote_refs_and_defs():
    body = "论断一。[^e1] 论断二。[^e2]\n\n[^e1]: 证据一\n"
    assert page_rules.footnote_refs(body) == {"e1", "e2"}
    assert page_rules.footnote_defs(body) == {"e1"}
    assert page_rules.missing_footnote_defs(body) == {"e2"}


def test_footnote_def_line_not_counted_as_ref():
    body = "[^e1]: 只有定义没有引用\n"
    assert page_rules.footnote_refs(body) == set()
    assert page_rules.missing_footnote_defs(body) == set()


def test_strip_code_blocks_removes_fenced_and_inline():
    body = ("散文 [^e1] 论断。\n\n```python\nre.sub('[^a-zA-Z_]', '_', h)  # [E-x] 字面量\n```\n\n"
            "行内 `[^0-9]` 也要剔除。\n\n[^e1]: 证据\n")
    prose = page_rules.strip_code_blocks(body)
    # 代码块里的负字符类与 E-ID 字面量都被剔除，不再污染 prose-markup 检查
    assert "[^a-zA-Z_]" not in prose and "[E-x]" not in prose and "[^0-9]" not in prose
    # 真正的散文脚注引用与定义仍保留
    assert "[^e1]" in prose
    # 经剔除后：无裸 E-ID、无悬空脚注
    assert page_rules.find_bare_evidence_ids(prose) == []
    refs = page_rules.footnote_refs(prose)
    assert refs == {"e1"} and (refs - page_rules.footnote_defs(body)) == set()


def test_required_sections_cleared_d4():
    # D-4：必需小节已全清空——各页型不再强制任何逐字小节标题
    for t in ("concept", "topic", "comparison", "overview", "source"):
        assert page_rules.required_sections_for(t) == []


def test_missing_sections_reports_absent_only():
    body = "# X\n\n## 直觉\n\n说明\n\n## 形式化\n\n$$x$$\n"
    missing = page_rules.missing_sections(body, ["## 直觉", "## 形式化", "## 各章如何处理"])
    assert missing == ["## 各章如何处理"]


def test_missing_sections_requires_heading_line_not_substring():
    body = "正文里提到 ## 直觉 三个字但不是标题行\n"
    assert page_rules.missing_sections(body, ["## 直觉"]) == ["## 直觉"]


def test_unknown_page_type_raises():
    try:
        page_rules.required_sections_for("nonsense")
        assert False, "should raise"
    except KeyError:
        pass


def test_missing_sections_pure_helper_still_works():
    # missing_sections 纯函数保留（供写作层/测试参考），只是不再被门禁调用强制
    assert page_rules.missing_sections("## A\n\nx\n", ["## A", "## B"]) == ["## B"]


def test_missing_frontmatter_per_type():
    # G2：source 要 source_id/title/domain/format，不要 source_refs
    assert page_rules.missing_frontmatter(
        {"type": "source", "status": "s", "managed_by": "m",
         "source_id": "x", "title": "t", "domain": "d", "format": "pdf"}, "source") == []
    assert "format" in page_rules.missing_frontmatter(
        {"type": "source", "status": "s", "managed_by": "m",
         "source_id": "x", "title": "t", "domain": "d"}, "source")
    assert "source_refs" not in page_rules.missing_frontmatter(
        {"type": "source", "status": "s", "managed_by": "m",
         "source_id": "x", "title": "t", "domain": "d", "format": "pdf"}, "source")
    # 非 source 综合页：要 source_refs（缺或空都算缺）
    assert "source_refs" in page_rules.missing_frontmatter(
        {"type": "topic", "status": "s", "managed_by": "m", "page_path": "p"}, "topic")
    assert "source_refs" in page_rules.missing_frontmatter(
        {"type": "topic", "status": "s", "managed_by": "m", "page_path": "p", "source_refs": []}, "topic")
    # 完整 topic → []
    assert page_rules.missing_frontmatter(
        {"type": "topic", "status": "s", "managed_by": "m", "page_path": "p",
         "source_refs": [{"source": "x"}]}, "topic") == []


def test_leading_h1_duplicates_filename():
    # 正文首行是与文件名同名的 # H1 → True（Obsidian 内联标题会与之重复渲染）
    assert page_rules.leading_h1_duplicates_filename("# 均衡\n\n正文\n", "均衡.md") is True
    # 传入的是全路径也应取 basename 比对
    assert page_rules.leading_h1_duplicates_filename("# 均衡\n\n正文\n", "domains/x/concepts/均衡.md") is True
    # 正文直接从散文开始（无同名 H1）→ False
    assert page_rules.leading_h1_duplicates_filename("均衡是一组策略。\n", "均衡.md") is False
    # 首个标题是 ## 小节而非 # 同名 → False
    assert page_rules.leading_h1_duplicates_filename("## 直觉\n\n说明\n", "均衡.md") is False
    # H1 文本与文件名不同 → False（只禁同名重复，不禁一切 H1）
    assert page_rules.leading_h1_duplicates_filename("# 别的标题\n\n正文\n", "均衡.md") is False


def test_katex_pipe_in_table_flags_unescaped_pipe():
    # 表格单元格内公式含裸 |（如 \frac{|S|...}）→ 命中（会撕碎表格 / KaTeX 失败）
    assert page_rules.katex_pipe_in_table(
        "| 工具 | 公式 |\n|---|---|\n| 夏普里值 | $\\phi=\\frac{|S|!}{n!}$ |\n")
    # 行内公式不在表格里（无结构性 |）→ 不报
    assert page_rules.katex_pipe_in_table("正文里 $|S|$ 不在表格。\n") == []
    # 表格内用 \lvert\rvert（无裸 |）→ 不报
    assert page_rules.katex_pipe_in_table("| a | $\\lvert S\\rvert$ |\n") == []
    # 表格内把 | 转义为 \| → 不报
    assert page_rules.katex_pipe_in_table("| a | $x \\| y$ |\n") == []


def test_katex_pipe_ignores_wikilink_display_pipe():
    # 回归（2026-07-04 误拦 信号博弈.md）：wikilink 显示名的 | + 公式内裸 | 同行的散文行，
    # 不是表格行——masking 须把 [[...|...]] 一并剔除。
    line = ("由 [[domains/game-theory/concepts/贝叶斯博弈|贝叶斯博弈]] 的更新规则给出 "
            "$\\mu(t|m)=p(m|t)$。\n")
    assert page_rules.katex_pipe_in_table(line) == []
    # 真表格行里同样公式仍要命中（wikilink 剔除后结构性 | 仍在）
    assert page_rules.katex_pipe_in_table(
        "| [[domains/x/concepts/贝叶斯博弈|贝叶斯博弈]] | $p(m|t)$ |\n")


def test_unanswered_question_stems():
    answered = ("> [!question] 自测\n"
                "> 为什么（抵赖, 抵赖）不是纳什均衡？\n"
                "> > [!success]- 参考答案\n"
                "> > 因为存在单方面偏离动机。\n")
    assert page_rules.unanswered_question_stems(answered) == []
    # 块内有指向解答的 wikilink 也算已闭环
    linked = ("> [!question] 自测\n"
              "> 先猜猜结论？解答见 [[domains/x/concepts/均衡|均衡]]。\n")
    assert page_rules.unanswered_question_stems(linked) == []
    # 有题无解 → 返回题干
    bare = ("> [!question] 自测\n"
            "> 为什么价格会压到边际成本？\n\n后续散文。\n")
    assert page_rules.unanswered_question_stems(bare) == ["为什么价格会压到边际成本？"]
    # 无 question callout → []
    assert page_rules.unanswered_question_stems("普通正文 $x$。\n") == []


def test_extract_question_stems():
    body = ("开头散文。\n\n"
            "> [!question] 自测\n"
            "> 为什么（抵赖, 抵赖）不是纳什均衡？\n"
            "> > [!success]- 参考答案\n"
            "> > 因为存在单方面偏离动机。\n\n"
            "中间散文。\n\n"
            "> [!question]- 进阶\n"
            "> 三家企业时结论如何变化？\n")
    assert page_rules.extract_question_stems(body) == [
        "为什么（抵赖, 抵赖）不是纳什均衡？",
        "三家企业时结论如何变化？",
    ]
    # 只有标题行没有正文行 → 用标题文本兜底
    assert page_rules.extract_question_stems("> [!question] 请推导最优反应函数\n") == ["请推导最优反应函数"]
    assert page_rules.extract_question_stems("无题正文。\n") == []


def test_extract_propositions():
    body = ("正文。**命题（先发优势）**：斯塔克尔伯格领导者产量为古诺的 1.5 倍，利润严格更高。\n\n"
            "另一段 **命题（伯特兰悖论）**: 两家同质竞争即可把价格压至边际成本。\n")
    assert page_rules.extract_propositions(body) == [
        ("先发优势", "斯塔克尔伯格领导者产量为古诺的 1.5 倍，利润严格更高。"),
        ("伯特兰悖论", "两家同质竞争即可把价格压至边际成本。"),
    ]
    # 无命题 → []；加粗普通文本不误捕
    assert page_rules.extract_propositions("**重点**：这是普通强调。\n") == []


def test_bare_pipe_wikilink_in_table():
    # 表格行内裸别名竖线 wikilink → 命中（渲染会撕碎表格列）；转义 \\| → 不报；散文行不报
    assert page_rules.bare_pipe_wikilink_in_table(
        "| 维度 | [[domains/d/concepts/甲|甲]] |\n")
    assert page_rules.bare_pipe_wikilink_in_table(
        "| 维度 | [[domains/d/concepts/甲\\|甲]] |\n") == []
    assert page_rules.bare_pipe_wikilink_in_table(
        "散文里 [[domains/d/concepts/甲|甲]] 的竖线是链接语法，不在表格行。\n") == []
    # 无别名的全路径链接（无竖线）在表格里也合法
    assert page_rules.bare_pipe_wikilink_in_table("| a | [[domains/d/concepts/甲]] |\n") == []
