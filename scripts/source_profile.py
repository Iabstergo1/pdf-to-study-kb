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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thresholds  # 检测阈值单一真值（env 可覆盖；调用点用 thresholds.X 以便覆盖/测试生效）

# 确定性 profiler 版本：needs_vision/公式信号启发式每次实质改动就 +1。
# 折进 profile/convert 阶段的 input_hash，使启发式升级自动失效缓存、强制对任意来源重算
# （否则 should_run_stage 只看 PDF sha，改了启发式也会 [skip]）。v2: 强/弱信号分层 + 代码页抑制。
PROFILER_VERSION = "5"  # v5: 矩阵/matrix(通用结构词)+结构证据补符号化矩阵 + 整本扫描件 fail-closed(is_scanned_source)。

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

# 图/表标题：仅当"图/表"位于词边界（前非 CJK/字母）才算图题——排除"地图 57"(目录)、"代表10"(普通句)、
# "讲图1"(散文提及)这类复合词/行内提及误报；真图题"图4.1 …"多在行首或空白后。英文 Figure/Table 同理。
_CAPTION = re.compile(
    r"(?<![一-鿿A-Za-z])(?:图|表)\s*\d+(?:[.\-]\d+)?"
    r"|(?:Figure|Fig\.|Table)\s*\d+", re.IGNORECASE)

# 域无关方程行：一行内含等号且其后邻接数字/运算符（数学/物理/CS 通用，不绑定任何领域词）。
# 用于补回 PyMuPDF 拍平后残留符号低、但确有多行方程的真公式页（formula-borderline）。
_EQ_LINE = re.compile(r"(?m)^[^\n]*[=＝][^\n]*[\d+\-*/^()<>≤≥−]")

# 通用数学结构词"矩阵/matrix"（与导数/方程/函数同级，跨线代/CS/物理/统计通用——非领域专名，
# 故不属"领域关键词"）。补符号化支付矩阵（用变量非数字、find_tables 与数字网格都抓不到）。
# 只用单个通用词，刻意不含"支付矩阵/贝叶斯/古诺"等领域专名。
_MATRIX = re.compile(r"矩阵|matrix", re.IGNORECASE)

# 代码页指纹：REPL 提示符、转义序列、十六进制字面量、Python 关键字/内建常量。
_CODE_HINT = re.compile(
    r">>>|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\[ntr]|0x[0-9a-fA-F]+|"
    r"\b(?:def|class|import|from|return|lambda|self|None|True|False|print|yield|"
    r"except|finally|async|await|elif|assert)\b")


def looks_like_code(text: str) -> bool:
    """代码页判定：REPL/转义/关键字等代码指纹命中≥阈值，则页面以代码为主而非公式。"""
    return len(_CODE_HINT.findall(text)) >= thresholds.CODE_HINT_MIN


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


def has_matrix_word(text: str) -> bool:
    return bool(_MATRIX.search(text))


def is_scanned_source(pages: list, *, zero_text_ratio: float | None = None,
                      image_ratio: float | None = None, text_floor: int | None = None) -> bool:
    """整本扫描件判定（fail-closed gate）：≥比例阈值页几乎零文本层 且 ≥比例阈值页含图像。
    route B 适合 born-digital PDF 的少数难页，不适合让 LLM 临场 OCR 上千整页图——
    命中即应停在 profile、明确提示需 OCR route，不再生成可 ingest 的 workorder。
    少数扫描页混在普通 PDF（比值低于阈值）不触发，仍按 route B 逐页处理，不误伤。
    阈值缺省取 thresholds（env 可覆盖）；显式传参仍优先。"""
    zero_text_ratio = thresholds.SCANNED_ZERO_TEXT_RATIO if zero_text_ratio is None else zero_text_ratio
    image_ratio = thresholds.SCANNED_IMAGE_RATIO if image_ratio is None else image_ratio
    text_floor = thresholds.SCANNED_TEXT_FLOOR if text_floor is None else text_floor
    n = len(pages)
    if not n:
        return False
    zero = sum(1 for p in pages if p.get("text_len", 0) < text_floor)
    img = sum(1 for p in pages if p.get("image_count", 0) >= 1)
    return zero / n >= zero_text_ratio and img / n >= image_ratio


def count_equation_lines(text: str) -> int:
    """域无关方程行计数：含等号且其后有数字/运算符的行（拍平公式页常残留多行）。"""
    return len(_EQ_LINE.findall(text))


_NUM = re.compile(r"\d+")
_SECTION_NUM = re.compile(r"^\d+(?:\.\d+)*$")          # 纯编号/页码行：3.3.2 / 35（TOC 抽取产物）
_DOTLEADER = re.compile(r"\.\s*\.\s*\.|…|·{3,}")        # 点导线行（TOC 标题↔页码连接）


def looks_like_numeric_grid(text: str) -> bool:
    """域无关数字网格信号：≥2 个"短行 + ≥2 个数字 token + 数字占比高"的行——像支付矩阵/数值表
    （find_tables 常漏的无框线/数学矩阵）。不靠"支付矩阵"等领域词，换书不失效。
    刻意排除真书回归暴露的过召回：目录的层级编号行（3.3.2）、页码行（35）、点导线行。"""
    rows = 0
    for ln in text.splitlines():
        s = ln.strip()
        if not (3 <= len(s) <= 30):                    # 长行=TOC 标题/散文，短行才像矩阵单元行
            continue
        if _SECTION_NUM.match(s) or _DOTLEADER.search(s):
            continue                                   # 目录编号/页码/点导线≠数据网格
        nums = _NUM.findall(s)
        if len(nums) >= thresholds.GRID_MIN_NUMS and \
                (sum(c.isdigit() for c in s) / len(s)) >= thresholds.GRID_DIGIT_DENSITY:
            rows += 1                                  # 多数字 token + 数字密度高（CJK 散文密度低被排除）
    return rows >= thresholds.GRID_MIN_ROWS            # 2×2 数值矩阵=2 数据行；漏页不可恢复故取低阈


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


def render_page_png(doc, page_number: int, out_path, *, zoom: int = 3) -> str:
    """渲染开着的 fitz doc 的 1-based 第 page_number 页为整页 PNG（route B）。
    convert / source-audit 难页补图 / arbitration 物化共用同一渲染口径（zoom=3）。"""
    import fitz  # noqa: F401（确保依赖存在；matrix 用到）
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(str(out_path))
    return str(out_path)


def render_pages_png(raw_path, pages, out_dir, *, prefix: str = "p", zoom: int = 3) -> dict:
    """打开 raw PDF 一次，把 `pages` 渲成 out_dir/<prefix><NNNN>.png；返回 {page: rel_path}。"""
    import fitz
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = {}
    doc = fitz.open(str(raw_path))
    try:
        for pg in pages:
            name = f"{prefix}{int(pg):04d}.png"
            render_page_png(doc, int(pg), out_dir / name, zoom=zoom)
            rel[int(pg)] = name
    finally:
        doc.close()
    return rel


def needs_vision_reasons(page: dict) -> list:
    """高召回：任一视觉/公式信号命中即返回原因（可审计）。
    代价不对称——漏页不可恢复，多截近乎零成本，故偏召回。
    补漏报用域无关结构信号（方程行 / 数字网格），不靠领域关键词；caption 须与视觉信号共现以杀误报。"""
    reasons = []
    f = page.get("formula_symbols", 0)
    is_code = page.get("is_code", False)
    if f >= thresholds.FORMULA_STRONG:
        reasons.append("formula")
    elif (f >= thresholds.FORMULA_BORDERLINE
          or page.get("eq_lines", 0) >= thresholds.EQ_LINES_MIN) and not is_code:
        reasons.append("formula-borderline")   # 方程行补回拍平后低符号的真公式页（p0042/p0086/p0095）
    if page.get("text_len", 0) < thresholds.SCANNED_TEXT_FLOOR and page.get("image_count", 0) >= 1:
        reasons.append("scanned-or-image")
    if page.get("n_draw", 0) >= thresholds.VECTOR_FIGURE_DRAW:   # 阈值据实测：真图 13-37，纯文字页页眉线 2-6
        reasons.append("vector-figure")
    # 表：find_tables 命中 / 数字网格（数值矩阵/无框线数值表）/ "矩阵|matrix"通用词 + 结构证据
    # （n_draw≥阈值 或公式符号足够），后者补符号化矩阵（如用变量的支付矩阵），且避免"讨论矩阵思想"纯文本误报。
    matrix_struct = page.get("has_matrix_word", False) and (
        page.get("n_draw", 0) >= thresholds.MATRIX_STRUCT_DRAW or f >= thresholds.FORMULA_BORDERLINE)
    if page.get("n_tables", 0) >= 1 or page.get("numeric_grid", False) or matrix_struct:
        reasons.append("table")
    has_visual = (page.get("n_draw", 0) >= thresholds.CAPTION_VISUAL_DRAW
                  or page.get("n_tables", 0) >= 1 or page.get("image_count", 0) >= 1)
    if page.get("has_caption", False) and has_visual:
        reasons.append("caption")            # 仅"提到图表的文字"（目录/散文）不截；须页上确有图/表/图像
    return reasons


def vision_tier(page: dict, reasons: list | None = None) -> str:
    """难页分层（成本控制，毛病 #10）：
    must = 强证据必读图（强公式 / 扫描页 / find_tables 命中 / 强矢量图）；
    nice = 边界页（边界公式 / 数字网格 / 中矢量图 / caption），ingest 可优先级低；
    none = 无信号不截。`needs_vision = vision_tier != "none"`。"""
    reasons = reasons if reasons is not None else needs_vision_reasons(page)
    if not reasons:
        return "none"
    if ("formula" in reasons or "scanned-or-image" in reasons
            or page.get("n_tables", 0) >= 1
            or page.get("n_draw", 0) >= thresholds.VECTOR_FIGURE_MUST_DRAW):
        return "must"
    return "nice"


def needs_vision(page: dict) -> bool:
    """难页：任一信号命中即需视觉（route B 读图）。"""
    return bool(needs_vision_reasons(page))


def profile_page(page_number: int, text: str, image_count: int,
                 *, n_draw: int = 0, n_tables: int = 0) -> dict:
    text_len = len(text.strip())
    formula = count_formula_symbols(text)
    is_code = looks_like_code(text)
    p = {"page": page_number, "text_len": text_len, "formula_symbols": formula,
         "image_count": image_count, "n_draw": n_draw, "n_tables": n_tables,
         "is_code": is_code, "has_caption": has_caption(text),
         "has_matrix_word": has_matrix_word(text),
         "eq_lines": 0 if is_code else count_equation_lines(text),
         "numeric_grid": looks_like_numeric_grid(text)}
    p["needs_vision_reason"] = needs_vision_reasons(p)
    p["vision_tier"] = vision_tier(p, p["needs_vision_reason"])
    p["needs_vision"] = p["vision_tier"] != "none"
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
        n = len(doc)
        # 廉价预扫（纯文本层 + 图像计数，不跑 get_drawings/find_tables）：整本扫描件直接早退，
        # 不为上千整页图白跑逐页重信号；scanned-or-image/must 由 needs_vision_reasons 给出。
        cheap = [(len(doc[i].get_text().strip()), len(doc[i].get_images())) for i in range(n)]
        if n and sum(1 for t, _ in cheap if t < 50) / n >= 0.8 \
                and sum(1 for _, im in cheap if im >= 1) / n >= 0.8:
            pages = [profile_page(i + 1, doc[i].get_text(), image_count=cheap[i][1]) for i in range(n)]
            doc.close()
            return pages
        pages = []
        for i in range(n):
            page = doc[i]
            sig = visual_signals(page)
            pages.append(profile_page(i + 1, page.get_text(), image_count=sig["image_count"],
                                      n_draw=sig["n_draw"], n_tables=sig["n_tables"]))
        doc.close()
        return pages
    if fmt in ("docx", "pptx"):
        # Spec 2：docx/pptx 由 MinerU 结构化解析；profile 不逐页扫（无等价轻量后端）。
        # 返回空 pages，使 profile 阶段不崩；auto 路由据 fmt 直接选 mineru。
        return []
    raise ValueError(f"no P1 profile backend for fmt={fmt}")
