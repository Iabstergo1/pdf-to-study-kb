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


# D-4：正文小节标题不再强制。确定性层只守安全/溯源/完整性（见 wiki_gate 硬规则白名单），
# 结构交写作 LLM 依 purpose + 来源类型自然组织。此表保留为已知页型登记（值恒为空），
# required_sections_for 因此返回空、门禁不再据此阻断；模板里的小节是建议性脚手架而非强制。
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "source": [], "lesson": [], "concept": [], "topic": [],
    "comparison": [], "synthesis": [], "overview": [],
}


def required_sections_for(page_type: str) -> list[str]:
    return list(REQUIRED_SECTIONS[page_type])  # 未知类型 KeyError 即报错


# G2：各页型必备 frontmatter（按页型分表）。核心收紧＝**非 source 综合页必带 source_refs**（吸收 D3
# 派生页强制溯源）；source 页不要 source_refs——它本身就是来源，用 source_id/title/domain/format 标识。
# lesson 归属靠 source 字段 / window write_set（见 belongs_to_source），不在此强制 source_refs（风险页
# 溯源由 risk-traceability 单独把关）。缺键或值为空（[]/""/None）均记缺。
_FM_COMMON = {"type", "status", "managed_by"}
REQUIRED_FRONTMATTER: dict[str, set[str]] = {
    "source": _FM_COMMON | {"source_id", "title", "domain", "format"},
    "concept": _FM_COMMON | {"canonical_id", "canonical_name", "domain"},
    "lesson": _FM_COMMON,  # 归属靠 source 字段 / window write_set，不强制 domain（可由路径推断）
    "topic": _FM_COMMON | {"source_refs"},
    "comparison": _FM_COMMON | {"source_refs"},
    "synthesis": _FM_COMMON | {"source_refs"},
    "overview": _FM_COMMON | {"source_refs"},
}


def missing_frontmatter(meta: dict, page_type: str) -> list[str]:
    """返回该页型缺失或为空的必备 frontmatter 字段（G2）；未知页型只查通用四项。纯函数、无 I/O。"""
    req = REQUIRED_FRONTMATTER.get(page_type, _FM_COMMON)
    return [k for k in sorted(req) if not meta.get(k)]


def missing_sections(body: str, required: list[str]) -> list[str]:
    present = {ln.strip() for ln in body.splitlines() if ln.lstrip().startswith("#")}
    return [s for s in required if s not in present]


def leading_h1_duplicates_filename(body: str, filename: str) -> bool:
    """正文首个内容行是与文件名同名的一级标题 `# X` 时返回 True（B1）。
    Obsidian 默认「显示内联标题」会把文件名渲染成大标题，正文若再放同名 `# X`，
    标题就出现两次。只针对首行同名 H1——其它 H1 或散文开头不算。纯函数、无 I/O。"""
    stem = filename[:-3] if filename.endswith(".md") else filename
    stem = stem.replace("\\", "/").rsplit("/", 1)[-1]
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip() == stem
        return False  # 首个非空行不是一级标题 → 无同名 H1 重复
    return False


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
