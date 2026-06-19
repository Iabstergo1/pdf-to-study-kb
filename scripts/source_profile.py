r"""逐页 profile：文本长度、公式符号密度、needs_vision 判定（确定性，零 LLM）。

公式信号分两层，对"任意来源"（公式书 / 代码书 / 散文书）都稳健：
- **强信号**：PyMuPDF 把数学 PDF 拍平后仍保留、而代码/散文里几乎不出现的非 ASCII 数学字符
  （∑∫∂√≤≥≠、希腊字母、上/下标 ²₁、真减号 −）。这些是判公式页的可靠依据。
- **ASCII 弱信号**：行内 `$…$`、裸 `^`、LaTeX 风格 `\cmd`、字母+数字下标 `q1`/`R1`。它们在
  **代码**里同样高频（正则锚点 `^`/`$`、转义 `\x`/`\n`、变量名 `s1`/`t2`），易误判。故弱信号
  **仅在"非代码页"计入**；代码页指纹（REPL `>>>`、转义序列、Python 关键字）命中≥3 即抑制弱信号，
  使代码密集的书（如 Python Cookbook）不再把成片代码页误渲为公式 PNG（route B）。
"""
from __future__ import annotations

import re

# 确定性 profiler 版本：needs_vision/公式信号启发式每次实质改动就 +1。
# 折进 profile/convert 阶段的 input_hash，使启发式升级自动失效缓存、强制对任意来源重算
# （否则 should_run_stage 只看 PDF sha，改了启发式也会 [skip]）。v2: 强/弱信号分层 + 代码页抑制。
PROFILER_VERSION = "3"  # v3: 高召回视觉信号(get_drawings/find_tables/caption) + reason 审计;丢弃面积信号。

# 强信号：非 ASCII 数学符号 + 希腊字母 + Unicode 上/下标 + 真减号（U+2212）。
_MATH_STRONG = re.compile(
    r"[∑∏∫∬∮∂∇√∛∜±∓×÷⋅≤≥≠≈≡≅≜∝∞∈∉∋∌⊂⊆⊃⊇⊄⊊∪∩∧∨¬∀∃∄∅≪≫⌊⌋⌈⌉⟨⟩↦⇒⇔→←↔"
    r"αβγδεζηθικλμνξοπρςστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    r"⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉−]")
# 词级强信号：arg max / arg min / F.O.C.（一阶条件），权重×2。
_MATH_OPS = re.compile(r"arg\s*max|arg\s*min|\bF\.?\s*O\.?\s*C\.?\b")

# ASCII 弱信号（各自独立计数后求和；与强信号叠加）。
_W_DOLLAR = re.compile(r"\$[^$\n]{1,60}\$")     # 行内 $…$
_W_CARET = re.compile(r"\^")                    # 裸上标符
_W_LATEX = re.compile(r"\\[A-Za-z]{2,}")        # \alpha \frac \sum
_W_SUBVAR = re.compile(r"[A-Za-zα-ωΑ-Ω][0-9](?![0-9A-Za-z])")  # 下标变量 q1 R1 π1

# 图/表标题：get_drawings 漏的小图靠标题兜底（图4.1 / 表2 / Figure 3 / Table 1）。
_CAPTION = re.compile(r"(?:图|表)\s*\d+(?:[.\-]\d+)?|(?:Figure|Fig\.|Table)\s*\d+", re.IGNORECASE)

# 代码页指纹：REPL 提示符、转义序列、十六进制字面量、Python 关键字/内建常量。
_CODE_HINT = re.compile(
    r">>>|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\[ntr]|0x[0-9a-fA-F]+|"
    r"\b(?:def|class|import|from|return|lambda|self|None|True|False|print|yield|"
    r"except|finally|async|await|elif|assert)\b")


def looks_like_code(text: str) -> bool:
    """代码页判定：REPL/转义/关键字等代码指纹命中≥3，则页面以代码为主而非公式。"""
    return len(_CODE_HINT.findall(text)) >= 3


def count_formula_symbols(text: str) -> int:
    """公式信号加权计数（越高越像公式页）：强信号恒计；ASCII 弱信号仅在非代码页计入。"""
    strong = len(_MATH_STRONG.findall(text)) + 2 * len(_MATH_OPS.findall(text))
    weak = (len(_W_DOLLAR.findall(text)) + len(_W_CARET.findall(text))
            + len(_W_LATEX.findall(text)) + len(_W_SUBVAR.findall(text)))
    if looks_like_code(text):
        weak = 0
    return strong + weak


def has_caption(text: str) -> bool:
    return bool(_CAPTION.search(text))


def visual_signals(page) -> dict:
    """从 fitz page 取确定性视觉信号（零 ML）：矢量路径数 / 表格数 / 内嵌栅格图数。
    全部 try/except 兜底：任一信号失败按 0 计，绝不让 profile 崩。"""
    try:
        n_draw = len(page.get_drawings())
    except Exception:
        n_draw = 0
    try:
        n_tables = len(page.find_tables().tables)
    except Exception:
        n_tables = 0
    try:
        image_count = len(page.get_images())
    except Exception:
        image_count = 0
    return {"n_draw": n_draw, "n_tables": n_tables, "image_count": image_count}


def needs_vision_reasons(page: dict) -> list:
    """高召回：任一视觉/公式信号命中即返回原因（可审计）。
    代价不对称——漏页不可恢复，多截近乎零成本，故偏召回。"""
    reasons = []
    f = page.get("formula_symbols", 0)
    if f >= 12:
        reasons.append("formula")
    elif f >= 6 and not page.get("is_code", False):
        reasons.append("formula-borderline")
    if page.get("text_len", 0) < 50 and page.get("image_count", 0) >= 1:
        reasons.append("scanned-or-image")
    if page.get("n_draw", 0) >= 12:          # 阈值据实测：真图 13-37，纯文字页页眉线 2-6
        reasons.append("vector-figure")
    if page.get("n_tables", 0) >= 1:
        reasons.append("table")
    if page.get("has_caption", False):
        reasons.append("caption")
    return reasons


def needs_vision(page: dict) -> bool:
    """难页：任一信号命中即需视觉（route B 读图）。"""
    return bool(needs_vision_reasons(page))


def profile_page(page_number: int, text: str, image_count: int,
                 *, n_draw: int = 0, n_tables: int = 0) -> dict:
    text_len = len(text.strip())
    formula = count_formula_symbols(text)
    p = {"page": page_number, "text_len": text_len, "formula_symbols": formula,
         "image_count": image_count, "n_draw": n_draw, "n_tables": n_tables,
         "is_code": looks_like_code(text), "has_caption": has_caption(text)}
    p["needs_vision_reason"] = needs_vision_reasons(p)
    p["needs_vision"] = bool(p["needs_vision_reason"])
    return p


def profile_source(src_path, *, fmt: str) -> list[dict]:
    """逐页 profile 整个来源（CLI profile 阶段的真实产出；pdf 用 PyMuPDF，md 视为单页）。"""
    from pathlib import Path
    src = Path(src_path)
    if fmt == "md":
        return [profile_page(1, src.read_text(encoding="utf-8"), image_count=0)]
    if fmt == "pdf":
        import fitz  # PyMuPDF（已装）
        doc = fitz.open(str(src))
        pages = []
        for i in range(len(doc)):
            page = doc[i]
            sig = visual_signals(page)
            pages.append(profile_page(i + 1, page.get_text(), image_count=sig["image_count"],
                                      n_draw=sig["n_draw"], n_tables=sig["n_tables"]))
        doc.close()
        return pages
    raise ValueError(f"no P1 profile backend for fmt={fmt}")
