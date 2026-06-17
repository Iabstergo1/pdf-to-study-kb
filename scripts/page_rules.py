"""干净正文的确定性文本规则（spec §10/§11 原语；纯函数、无 I/O；门禁组装在 P6）。"""
from __future__ import annotations

import re

# 裸 E-ID：旧管线的内联证据标记，正文里一律不许出现（L1）
_BARE_EVIDENCE = re.compile(r"\[E-[A-Za-z0-9_.\-]+\]")
# 围栏代码块 ```...``` 与行内代码 `...`
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def strip_code_blocks(text: str) -> str:
    """剔除围栏代码块与行内代码，返回仅含散文/标记的文本。
    用于 prose-markup 检查（裸 E-ID / 脚注引用 / wikilink）——编程类页面的代码示例里常含
    正则负字符类 `[^a-z]`（会被脚注引用正则 `[^...]` 误判）、`[E.. ` 字面量、`[[ ` 等，
    它们是代码而非 wiki 标记，须先剔除避免 fail-closed 误拦（对任意含代码的来源通用）。"""
    return _INLINE_CODE.sub(" ", _FENCED_CODE.sub("\n", text))
# 脚注引用 [^e1]（行内）；(?!:) 排除定义行
_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_\-]+)\](?!:)")
# 脚注定义行 [^e1]: …
_FOOTNOTE_DEF = re.compile(r"^\[\^([A-Za-z0-9_\-]+)\]:", re.MULTILINE)


def find_bare_evidence_ids(body: str) -> list[str]:
    return _BARE_EVIDENCE.findall(body)


def footnote_refs(body: str) -> set[str]:
    return set(_FOOTNOTE_REF.findall(body))


def footnote_defs(body: str) -> set[str]:
    return set(_FOOTNOTE_DEF.findall(body))


def missing_footnote_defs(body: str) -> set[str]:
    return footnote_refs(body) - footnote_defs(body)


# 各页面类型的必需小节（spec §8；P6 门禁选择阻断子集：L2=concept、L3=topic、L5=overview）
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "source": ["## 一句话总结", "## 核心观点", "## 关键概念",
               "## 与其他来源的关联", "## 精彩摘录", "## 相关页面"],
    "lesson": [],  # 干净散文，无强制小节；约束是无裸 E-ID + 脚注配对
    "concept": ["## 一句话", "## 直觉", "## 形式化", "## 各章如何处理",
                "## 与其他概念的关系", "## 自测"],
    "topic": ["## 核心综合", "## 各来源贡献", "## 未解决问题"],
    "comparison": ["## 结论", "## 对比维度", "## 适用场景", "## 相关概念"],
    "synthesis": ["## 核心洞见", "## 关键决策", "## 涉及概念", "## 待跟进"],
    "overview": ["## 核心概念地图", "## 推荐学习路线", "## 模型家族对比"],
}


def required_sections_for(page_type: str) -> list[str]:
    return list(REQUIRED_SECTIONS[page_type])  # 未知类型 KeyError 即报错


def missing_sections(body: str, required: list[str]) -> list[str]:
    present = {ln.strip() for ln in body.splitlines() if ln.lstrip().startswith("#")}
    return [s for s in required if s not in present]


# 表格单元格里的行内/块公式含未转义的 `|`：GFM 会把它当列分隔符，撕碎公式、KaTeX 无法渲染
_MATH_SPAN = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$")


def katex_pipe_in_table(body: str) -> list[str]:
    """检测 Markdown 表格行的单元格内，公式 $...$ / $$...$$ 含未转义的 `|`（如 \\frac{|S|...}）。
    表格里裸 `|` 会被当成列分隔符，把公式拆进相邻列、KaTeX 渲染失败（spec §10 公式保真）。
    修法：集合基数用 \\lvert S \\rvert 代替 |S|，或把 `|` 转义为 \\|，或把公式移出表格放下方。
    返回命中行（截断 120 字）；空列表 = 无此问题。纯函数、无 I/O。"""
    bad: list[str] = []
    for line in body.splitlines():
        spans = _MATH_SPAN.findall(line)
        if not spans:
            continue
        # 该行剔除公式与行内代码后若仍含 `|`，即为表格行（存在结构性列分隔符）
        masked = _INLINE_CODE.sub(" ", _MATH_SPAN.sub(" ", line))
        if "|" not in masked:
            continue
        for span in spans:
            if "|" in re.sub(r"\\\|", "", span):  # 去掉转义的 \| 后仍有裸 |
                bad.append(line.strip()[:120])
                break
    return bad
