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


# ── 统一 blockquote/callout 结构解析器（唯一实现）────────────────────────────
# lint 的坏嵌套/类型检查、quiz 收割、有题必有解 全部消费同一份解析结果——三套正则各自
# 为政曾让"被吞的第二题"在收割里静默消失。契约：**错误不吞节点**（同级 head 记结构错误的
# 同时仍登记为可定位节点）；真空行（非引用行）结束整个块，单独一行 `>` 不结束；
# 前导 ≤3 空格与 CRLF 容忍；fenced code 内不解析。纯函数、无 I/O。

_QUOTE_PREFIX = re.compile(r"^ {0,3}((?:> ?)+)")
_CALLOUT_HEAD = re.compile(r"\[!(\w+)\](-?)[ \t]*(.*)$")


def parse_callouts(body: str) -> tuple[list[dict], list[dict]]:
    """返回 (nodes, errors)。node: {type, folded, title, depth, line, body[(depth,text)],
    parent(节点下标|None), children[下标]}；error: {kind, line, text, type}，kind ∈
    same-depth-callout-inside-active-block（渲染成字面量的同级 head）/
    callout-depth-jump（嵌套跳级）/ empty-question-stem（无标题也无正文行的自测题）。"""
    nodes: list[dict] = []
    errors: list[dict] = []
    open_stack: list[int] = []          # 尚未被真空行/浅层 head 关闭的节点下标（内层在后）
    in_fence = False
    for i, raw in enumerate(body.splitlines()):
        line = raw.rstrip("\r")
        m = _QUOTE_PREFIX.match(line)
        stripped = line[m.end():].strip() if m else line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not m:
            open_stack = []             # 真空行或普通正文行：整个引用块结束
            continue
        depth = m.group(1).count(">")
        hm = _CALLOUT_HEAD.match(stripped)
        if hm:
            ctype, folded, title = hm.group(1).lower(), hm.group(2) == "-", hm.group(3).strip()
            parent = None
            if open_stack:
                inner = nodes[open_stack[-1]]
                if depth == inner["depth"] + 1:
                    parent = open_stack[-1]
                elif depth <= inner["depth"]:
                    # 同级/更浅的 head 出现在未结束的块内 → Obsidian 渲染为字面量文本
                    errors.append({"kind": "same-depth-callout-inside-active-block",
                                   "line": i + 1, "text": stripped[:80], "type": ctype})
                    while open_stack and nodes[open_stack[-1]]["depth"] >= depth:
                        open_stack.pop()
                    parent = open_stack[-1] if open_stack else None
                else:
                    errors.append({"kind": "callout-depth-jump",
                                   "line": i + 1, "text": stripped[:80], "type": ctype})
                    parent = open_stack[-1]
            nodes.append({"type": ctype, "folded": folded, "title": title, "depth": depth,
                          "line": i + 1, "body": [], "parent": parent, "children": []})
            if parent is not None:
                nodes[parent]["children"].append(len(nodes) - 1)
            open_stack.append(len(nodes) - 1)
        elif stripped:
            for j in reversed(open_stack):  # 正文行归属栈内 depth ≤ 行深的最内层节点
                if nodes[j]["depth"] <= depth:
                    nodes[j]["body"].append((depth, stripped))
                    break
    for n in nodes:
        if n["type"] == "question" and not _node_stem(n):
            errors.append({"kind": "empty-question-stem", "line": n["line"],
                           "text": n["title"][:80], "type": "question"})
    return nodes, errors


def _node_stem(node: dict) -> str:
    """题干 = 节点自身深度上的首个正文行；无正文行时回退标题文本。"""
    return next((t for d, t in node["body"] if d == node["depth"]), "") or node["title"]


def _question_nodes(body: str) -> list[dict]:
    return [n for n in parse_callouts(body)[0] if n["type"] == "question"]


def extract_question_stems(body: str) -> list[str]:
    """返回正文中每个 [!question] callout 的题干（quiz 索引原语；空题干跳过——它由
    empty-question-stem 结构错误另行处理，不进复习索引）。纯函数、无 I/O。"""
    return [s for n in _question_nodes(body) if (s := _node_stem(n))]


# 具名命题：库内承重结论的稳定锚点（`**命题（先发优势）**：一句话结论`）。名字即锚点、域内唯一，
# v1 不做数字编号（编号需持久注册表，重建即重排会断引用）；收尾收割成 propositions.generated.md。
_PROPOSITION = re.compile(r"\*\*命题（([^）\n]{1,24})）\*\*[：:]\s*(.+)")


def extract_propositions(body: str) -> list[tuple[str, str]]:
    """返回正文中的具名命题 [(名, 结论句)]（命题总表原语）。纯函数、无 I/O。"""
    return [(m.group(1).strip(), m.group(2).strip()) for m in _PROPOSITION.finditer(body)]


def malformed_nested_callouts(body: str) -> list[str]:
    """块内同级 callout 头（渲染安全硬伤原语）：Obsidian 会把它渲染成字面量文本而非嵌套
    callout——折叠答案因此明文可见。嵌套必须写 `> > [!type]`，或用真空行结束上一个块。
    消费统一解析器的 same-depth 结构错误。返回命中行文本（截 80 字）。纯函数、无 I/O。"""
    return [e["text"] for e in parse_callouts(body)[1]
            if e["kind"] == "same-depth-callout-inside-active-block"]


# 旧强制小节骨架（2026-07-01 realignment 已废除）。模板/回退常量已散文化，但模型仍可能
# 凭训练记忆整体重写出这套骨架——窄规则只拦"成套复活"（≥3 个），单个自然标题合法（D-4）。
_LEGACY_HEADINGS = frozenset({"一句话", "直觉", "形式化", "各章如何处理", "与其他概念的关系"})
_HEADING_LINE = re.compile(r"^#{2,3}\s*(\S[^\n]*?)\s*$", re.MULTILINE)


def legacy_scaffold_headings(body: str) -> list[str]:
    """检测已废除的概念页模板骨架成套复活：标题精确归一后命中旧骨架标题集 ≥3 个（去重）
    才返回命中列表，否则空。调用方先剥代码块。纯函数、无 I/O。"""
    found = {m.group(1).strip() for m in _HEADING_LINE.finditer(body)} & _LEGACY_HEADINGS
    return sorted(found) if len(found) >= 3 else []


_DERIVATION_FOLD = re.compile(r"^>\s*\[!abstract\]-", re.IGNORECASE | re.MULTILINE)


def device_usage(body: str) -> dict:
    """页内写作装置计数（复盘 proxy 指标原语，不进门禁）：具名命题/推导折叠/自测题。
    单页各项为零都合法；整本书全部归零是"写作偏好未被执行"的强信号。纯函数、无 I/O。"""
    return {"propositions": len(extract_propositions(body)),
            "derivation_folds": len(_DERIVATION_FOLD.findall(body)),
            "questions": len(_question_nodes(body))}


def misplaced_question_stems(body: str) -> list[str]:
    """题干疑似写进 callout 标题的 [!question]（软警告原语）：标题以问号结尾、块内又有
    正文行——quiz 收割取块内首行当题干，会把答案收进索引。标准写法：标题只放「自测」类
    短语，题干做块内首行，答案进嵌套折叠 `> > [!success]-`。纯函数、无 I/O。"""
    return [n["title"] for n in _question_nodes(body)
            if n["title"].rstrip().endswith(("？", "?")) and _node_stem(n) != n["title"]]


def question_resolution(nodes: list[dict], q_index: int) -> str:
    """一道 question 的解答形态（软判断的确定性前置）：
    `nested_success`（success 后代——含跳级）> `linked_resolution_candidate`（题干区 wikilink
    指向解答）> `sibling_success`（紧随其后的同级 success 块，既有惯例）> `none`。
    嵌套 [!tip]/[!info] 等提示不是解答。纯函数、无 I/O。"""
    q = nodes[q_index]
    stack = list(q["children"])
    while stack:
        c = nodes[stack.pop()]
        if c["type"] == "success":
            return "nested_success"
        stack.extend(c["children"])
    if any("[[" in t for _d, t in q["body"]):
        return "linked_resolution_candidate"
    for j in range(q_index + 1, len(nodes)):
        if nodes[j]["parent"] is None:
            if nodes[j]["type"] == "success":
                return "sibling_success"
            break  # 下一个顶层块不是 success → 不算紧随解答
    return "none"


def unanswered_question_stems(body: str) -> list[str]:
    """有题无解的 [!question] 题干（软警告原语，"never questions with no resolution"）。
    解答形态判定见 question_resolution——嵌套 [!tip] 不再被误认成答案。纯函数、无 I/O。"""
    nodes, _errors = parse_callouts(body)
    return [_node_stem(nodes[i]) for i, n in enumerate(nodes)
            if n["type"] == "question" and question_resolution(nodes, i) == "none"
            and _node_stem(nodes[i])]
