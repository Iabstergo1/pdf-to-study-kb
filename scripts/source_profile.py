"""逐页 profile：文本长度、公式符号密度、needs_vision 判定（确定性，零 LLM；spec §5）。"""
from __future__ import annotations

import re

_FORMULA = re.compile(r"[\\∑∫∂∇√±×÷≤≥≠≈→←↔∈∉⊂⊆∀∃αβγδεθλμπσφψωΩ]|\$[^$]+\$|\^|_\{")


def count_formula_symbols(text: str) -> int:
    return len(_FORMULA.findall(text))


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
