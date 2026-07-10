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
# wikilink 整体（含显示名竖线 [[path|display]]）——它的 | 是链接语法而非表格列分隔符
_WIKILINK_SPAN = re.compile(r"\[\[[^\]\n]*\]\]")


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
        # 该行剔除公式、行内代码、wikilink 后若仍含 `|`，即为表格行（存在结构性列分隔符）
        # ——wikilink 显示名的 | 是链接语法，曾把「wikilink+公式」的散文行误判成表格行
        masked = _WIKILINK_SPAN.sub(" ", _INLINE_CODE.sub(" ", _MATH_SPAN.sub(" ", line)))
        if "|" not in masked:
            continue
        for span in spans:
            if "|" in re.sub(r"\\\|", "", span):  # 去掉转义的 \| 后仍有裸 |
                bad.append(line.strip()[:120])
                break
    return bad


# 表格行内的 wikilink 全形（含别名）。裸别名竖线 [[path|alias]] 会被 GFM 当列分隔符
# 撕碎表格与链接——Obsidian 标准写法是转义 [[path\|alias]]（断链检查已认可转义写法）。
_WIKILINK_FULL = re.compile(r"\[\[([^\]\n]+)\]\]")
_BARE_PIPE = re.compile(r"[^\\]\|")


def bare_pipe_wikilink_in_table(body: str) -> list[str]:
    """检测表格行单元格内含未转义 `|` 的 wikilink。修法：转义为 [[path\\|alias]]，
    或把链接移出表格放进散文（单元格保留纯文本）。返回命中行（截断 120 字）。纯函数、无 I/O。"""
    bad: list[str] = []
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for m in _WIKILINK_FULL.finditer(line):
            if _BARE_PIPE.search(m.group(1)):
                bad.append(line.strip()[:120])
                break
    return bad


# 自测题 callout（`> [!question]`，可带折叠 `-` 与标题文本）。学习闭环要求有题必有解
# （嵌套折叠答案 `> > [!success]-` / 块内 wikilink 指向解答），见 ingest write-pages 写作纪律。
_QUESTION_HEAD = re.compile(r"^>\s*\[!question\]-?\s*(.*)$", re.IGNORECASE)


def _question_blocks(body: str) -> list[tuple[str, list[str], str]]:
    """切出每个 [!question] callout：(标题文本, 块内行, 块后首个非空行)。
    块 = 标题行之后连续以 > 开头的行（Obsidian callout 以空行结束）。纯函数、无 I/O。"""
    out: list[tuple[str, list[str], str]] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        m = _QUESTION_HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        block: list[str] = []
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            block.append(lines[i])
            i += 1
        following = ""
        for ln in lines[i:]:
            if ln.strip():
                following = ln.strip()
                break
        out.append((m.group(1).strip(), block, following))
    return out


def _question_stem(title: str, block: list[str]) -> str:
    """题干 = 块内首个非嵌套、非空的引用行；没有正文行时回退标题文本。"""
    for ln in block:
        s = ln.strip()
        if s.startswith((">>", "> >")):
            break  # 进入嵌套答案区，题干已结束
        text = s.lstrip(">").strip()
        if text:
            return text
    return title


def extract_question_stems(body: str) -> list[str]:
    """返回正文中每个 [!question] callout 的题干（quiz 索引原语）。纯函数、无 I/O。"""
    return [_question_stem(t, b) for t, b, _f in _question_blocks(body)]


# 具名命题：库内承重结论的稳定锚点（`**命题（先发优势）**：一句话结论`）。名字即锚点、域内唯一，
# v1 不做数字编号（编号需持久注册表，重建即重排会断引用）；收尾收割成 propositions.generated.md。
_PROPOSITION = re.compile(r"\*\*命题（([^）\n]{1,24})）\*\*[：:]\s*(.+)")


def extract_propositions(body: str) -> list[tuple[str, str]]:
    """返回正文中的具名命题 [(名, 结论句)]（命题总表原语）。纯函数、无 I/O。"""
    return [(m.group(1).strip(), m.group(2).strip()) for m in _PROPOSITION.finditer(body)]


_DERIVATION_FOLD = re.compile(r"^>\s*\[!abstract\]-", re.IGNORECASE | re.MULTILINE)


def device_usage(body: str) -> dict:
    """页内写作装置计数（复盘 proxy 指标原语，不进门禁）：具名命题/推导折叠/自测题。
    单页各项为零都合法；整本书全部归零是"写作偏好未被执行"的强信号。纯函数、无 I/O。"""
    return {"propositions": len(extract_propositions(body)),
            "derivation_folds": len(_DERIVATION_FOLD.findall(body)),
            "questions": len(_question_blocks(body))}


def misplaced_question_stems(body: str) -> list[str]:
    """题干疑似写进 callout 标题的 [!question]（软警告原语）：标题以问号结尾、块内又有
    正文行——quiz 收割取块内首行当题干，会把答案收进索引。标准写法：标题只放「自测」类
    短语，题干做块内首行，答案进嵌套折叠 `> > [!success]-`。纯函数、无 I/O。"""
    out: list[str] = []
    for title, block, _f in _question_blocks(body):
        if title.rstrip().endswith(("？", "?")) and _question_stem(title, block) != title:
            out.append(title)
    return out


def unanswered_question_stems(body: str) -> list[str]:
    """有题无解的 [!question] 题干（软警告原语）：块内既无嵌套/相邻 callout 答案、
    也无 wikilink 指向解答（"never questions with no resolution"）。纯函数、无 I/O。"""
    out: list[str] = []
    for title, block, following in _question_blocks(body):
        text = "\n".join(block)
        if "[[" in text:
            continue  # 链接到解答所在页/小节
        if any("[!" in ln for ln in block):
            continue  # 块内嵌套（> > [!success]-）或紧贴的同级答案 callout
        if following.startswith(">") and "[!" in following:
            continue  # 隔一个空行的同级答案 callout
        out.append(_question_stem(title, block))
    return out
