#!/usr/bin/env python3
"""PDF 结构分析脚本"""
import sys
import json
from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    print("pymupdf not installed")
    sys.exit(1)

def analyze_pdf(pdf_path: str) -> dict:
    """分析 PDF 结构和内容质量"""
    doc = fitz.open(pdf_path)

    result = {
        "file": pdf_path,
        "total_pages": len(doc),
        "metadata": doc.metadata,
        "toc": [],
        "page_samples": [],
        "extraction_quality": {
            "pages_with_text": 0,
            "pages_with_images": 0,
            "pages_with_formulas": 0,
            "pages_with_tables": 0,
            "empty_pages": 0,
            "avg_text_length": 0,
        },
        "risks": []
    }

    # 获取目录
    toc = doc.get_toc()
    if toc:
        result["toc"] = [{"level": t[0], "title": t[1], "page": t[2]} for t in toc]
    else:
        result["risks"].append("NO_TOC: PDF 没有内置目录结构")

    # 分析每页
    total_text_len = 0
    formula_pages = []
    table_pages = []
    image_pages = []
    empty_pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        text_len = len(text.strip())
        total_text_len += text_len

        # 检查是否有文本
        if text_len < 50:
            empty_pages.append(page_num + 1)
            result["extraction_quality"]["empty_pages"] += 1
        else:
            result["extraction_quality"]["pages_with_text"] += 1

        # 检查图像
        images = page.get_images()
        if images:
            image_pages.append(page_num + 1)
            result["extraction_quality"]["pages_with_images"] += 1

        # 简单检测公式（LaTeX 符号）
        formula_indicators = ['\\', '∑', '∫', '∂', 'α', 'β', 'γ', 'δ', 'θ', 'λ', 'σ', 'π', '∞', '≤', '≥', '∈', '∀', '∃']
        if any(ind in text for ind in formula_indicators):
            formula_pages.append(page_num + 1)
            result["extraction_quality"]["pages_with_formulas"] += 1

        # 简单检测表格（多行多列结构）
        lines = text.strip().split('\n')
        if len(lines) > 3 and any('\t' in line or '  ' in line for line in lines[:5]):
            table_pages.append(page_num + 1)
            result["extraction_quality"]["pages_with_tables"] += 1

    result["extraction_quality"]["avg_text_length"] = total_text_len / len(doc) if len(doc) > 0 else 0

    # 风险评估
    if formula_pages:
        result["risks"].append(f"FORMULA_RISK: {len(formula_pages)} 页含公式符号，需人工校验")
        result["formula_pages_sample"] = formula_pages[:10]

    if table_pages:
        result["risks"].append(f"TABLE_RISK: {len(table_pages)} 页含表格，提取可能丢失格式")
        result["table_pages_sample"] = table_pages[:10]

    if empty_pages:
        result["risks"].append(f"EMPTY_PAGES: {len(empty_pages)} 页文本极少，可能是扫描页或纯图页")
        result["empty_pages_sample"] = empty_pages[:10]

    if result["extraction_quality"]["avg_text_length"] < 200:
        result["risks"].append("LOW_TEXT_DENSITY: 平均每页文本量偏低，可能有大量图表")

    # 样本页面内容
    sample_pages = [0, 1, 2, len(doc)//4, len(doc)//2, 3*len(doc)//4, len(doc)-1]
    for p in sample_pages:
        if p < len(doc):
            page = doc[p]
            text = page.get_text()[:500]
            result["page_samples"].append({
                "page": p + 1,
                "text_preview": text
            })

    doc.close()
    return result

if __name__ == "__main__":
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf"
    result = analyze_pdf(pdf_path)
    output_path = Path(pdf_path).parent.parent / "pipeline-workspace" / "reports" / "pdf-structure-raw.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Report saved to: {output_path}")
