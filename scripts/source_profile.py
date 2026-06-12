"""逐页 profile：文本长度、公式符号密度、needs_vision 判定（确定性，零 LLM；spec §5）。"""
from __future__ import annotations

import re

_FORMULA = re.compile(r"[\\∑∫∂∇√±×÷≤≥≠≈→←↔∈∉⊂⊆∀∃αβγδεθλμπσφψωΩ]|\$[^$]+\$|\^|_\{")
# 文本层被拍平的公式信号：pymupdf 抽取会把上标/下标/分数拍成纯文本，结构符号消失，
# 但留下普通中文散文极少出现的特征——下标变量(q1/R1/π1)、真减号 U+2212、arg max / F.O.C.。
# 这些信号让 marker 缺席时的文本兜底路径仍能把公式页判为 needs_vision（spec §5 难页读图）。
_FLAT_SUBVAR = re.compile(r"[A-Za-zα-ωΑ-Ω][₀-₉0-9](?![0-9A-Za-z])")
_FLAT_MINUS = re.compile("−")  # 数学减号 −（区别于 hyphen-minus '-'）
_FLAT_OPS = re.compile(r"arg\s*max|arg\s*min|\bF\.?\s*O\.?\s*C\.?\b|≜|↦")


def count_formula_symbols(text: str) -> int:
    """结构化数学符号 + 文本层被拍平的公式信号的加权计数（越高越像公式页）。"""
    base = len(_FORMULA.findall(text))
    flat = (len(_FLAT_SUBVAR.findall(text))
            + len(_FLAT_MINUS.findall(text))
            + 2 * len(_FLAT_OPS.findall(text)))
    return base + flat


def needs_vision(page: dict) -> bool:
    """难页：公式符号密集 / 文本过短且有图（疑似扫描或图密集）。"""
    text_len = page.get("text_len", 0)
    formula = page.get("formula_symbols", 0)
    images = page.get("image_count", 0)
    if formula >= 12:
        return True
    if text_len < 50 and images >= 1:
        return True
    return False


def profile_page(page_number: int, text: str, image_count: int) -> dict:
    text_len = len(text.strip())
    formula = count_formula_symbols(text)
    p = {"page": page_number, "text_len": text_len, "formula_symbols": formula,
         "image_count": image_count}
    p["needs_vision"] = needs_vision(p)
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
        pages = [profile_page(i + 1, doc[i].get_text(), image_count=len(doc[i].get_images()))
                 for i in range(len(doc))]
        doc.close()
        return pages
    raise ValueError(f"no P1 profile backend for fmt={fmt}")
