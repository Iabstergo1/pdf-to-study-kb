"""PDF profile generation for semantic unit planning."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz
import yaml


FORMULA_INDICATORS = set("\\∑∫∂αβγδθλσπ∞≤≥∈∀∃+-=*/")
GREEK_NAMES = {
    "alpha",
    "beta",
    "gamma",
    "delta",
    "theta",
    "lambda",
    "sigma",
    "pi",
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def find_pdf(book_root: Path) -> Path:
    input_dir = book_root / "input"
    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"input 目录中没有 PDF 文件: {input_dir}")
    return pdf_files[0]


def compact_summary(text: str, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _risk_max(*risks: str) -> str:
    return max(risks, key=lambda item: RISK_ORDER[item])


def detect_formula_risk(plain_text: str) -> str:
    text = plain_text.strip()
    if not text:
        return "low"

    indicator_hits = sum(1 for char in text if char in FORMULA_INDICATORS)
    lower_text = text.lower()
    greek_hits = sum(1 for name in GREEK_NAMES if name in lower_text)
    dense_variable_lines = 0
    for line in text.splitlines():
        tokens = re.findall(r"\b[A-Za-z]\b", line)
        if len(tokens) >= 4:
            dense_variable_lines += 1

    if indicator_hits >= 2 or greek_hits >= 2 or dense_variable_lines >= 1:
        return "high"
    if indicator_hits or greek_hits:
        return "medium"
    return "low"


def detect_table_risk(plain_text: str, text_dict: dict[str, Any]) -> str:
    lines = [line for line in plain_text.splitlines() if line.strip()]
    tabular_lines = [
        line for line in lines
        if "\t" in line or re.search(r"\S\s{2,}\S", line)
    ]
    if len(tabular_lines) >= 2:
        return "high"
    if tabular_lines:
        return "medium"

    aligned_blocks = 0
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if len(spans) >= 3:
                aligned_blocks += 1
    return "medium" if aligned_blocks else "low"


def detect_blank_variable_risk(
    plain_text: str,
    text_dict: dict[str, Any],
    image_count: int,
    formula_risk: str,
) -> str:
    text_length = len(plain_text.strip())
    block_count = len(text_dict.get("blocks", []))
    if text_length < 40 and image_count > 0 and formula_risk == "low":
        return "high"
    if text_length < 80 and image_count > 0:
        return "medium"
    if text_length < 40 and block_count > 3:
        return "medium"
    return "low"


def choose_extraction_method(
    formula_risk: str,
    table_risk: str,
    blank_variable_risk: str,
    text_length: int,
) -> str:
    if blank_variable_risk == "high" or (formula_risk == "high" and text_length < 80):
        return "screenshot_ocr"
    if formula_risk == "high" or table_risk == "high" or blank_variable_risk == "medium":
        return "hybrid"
    return "text"


def profile_page(
    page_number: int,
    page: fitz.Page,
    text_dict: dict[str, Any],
    plain_text: str,
) -> dict[str, Any]:
    text_length = len(plain_text.strip())
    image_count = len(page.get_images())
    block_count = len(text_dict.get("blocks", []))
    formula_risk = detect_formula_risk(plain_text)
    table_risk = detect_table_risk(plain_text, text_dict)
    blank_variable_risk = detect_blank_variable_risk(
        plain_text,
        text_dict,
        image_count,
        formula_risk,
    )
    return {
        "page": page_number,
        "text_length": text_length,
        "summary_200": compact_summary(plain_text),
        "image_count": image_count,
        "block_count": block_count,
        "formula_risk": formula_risk,
        "table_risk": table_risk,
        "blank_variable_risk": blank_variable_risk,
        "recommended_extraction_method": choose_extraction_method(
            formula_risk,
            table_risk,
            blank_variable_risk,
            text_length,
        ),
    }


def profile_pdf(book_root: Path) -> dict[str, Any]:
    pdf_path = find_pdf(book_root)
    doc = fitz.open(str(pdf_path))
    try:
        pages = []
        for index in range(len(doc)):
            page = doc[index]
            text_dict = page.get_text("dict")
            plain_text = page.get_text()
            pages.append(profile_page(index + 1, page, text_dict, plain_text))
        return {
            "book_id": book_root.name,
            "source_pdf": pdf_path.name,
            "total_pages": len(doc),
            "toc": [
                {"level": level, "title": title, "page": page}
                for level, title, page in doc.get_toc()
            ],
            "pages": pages,
        }
    finally:
        doc.close()


def write_profile_yaml(profile: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(profile, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def risk_counts(profile: dict[str, Any]) -> dict[str, int]:
    counts = {
        "formula_high": 0,
        "table_high": 0,
        "blank_variable_high": 0,
        "hybrid_or_ocr": 0,
    }
    for page in profile.get("pages", []):
        if page.get("formula_risk") == "high":
            counts["formula_high"] += 1
        if page.get("table_risk") == "high":
            counts["table_high"] += 1
        if page.get("blank_variable_risk") == "high":
            counts["blank_variable_high"] += 1
        if page.get("recommended_extraction_method") in {"hybrid", "screenshot_ocr"}:
            counts["hybrid_or_ocr"] += 1
    return counts


def render_profile_report(profile: dict[str, Any]) -> str:
    counts = risk_counts(profile)
    lines = [
        "# PDF Profile",
        "",
        f"- book_id: {profile['book_id']}",
        f"- source_pdf: {profile['source_pdf']}",
        f"- 总页数: {profile['total_pages']}",
        "",
        "## 风险页统计",
        "",
        f"- 高公式风险页: {counts['formula_high']}",
        f"- 高表格风险页: {counts['table_high']}",
        f"- 高空白变量风险页: {counts['blank_variable_high']}",
        f"- 建议 hybrid/OCR 页: {counts['hybrid_or_ocr']}",
        "",
        "## 每页短摘要",
        "",
    ]
    for page in profile.get("pages", []):
        lines.append(
            "- p.{page}: method={method}, formula={formula}, table={table}, "
            "blank_variable={blank}, text_length={length} - {summary}".format(
                page=page["page"],
                method=page["recommended_extraction_method"],
                formula=page["formula_risk"],
                table=page["table_risk"],
                blank=page["blank_variable_risk"],
                length=page["text_length"],
                summary=page["summary_200"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_profile_report(profile: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_profile_report(profile), encoding="utf-8")


def profile_pdf_command(book_root: Path, force: bool = False) -> dict[str, Any]:
    profile_path = book_root / "config" / "pdf-profile.yaml"
    report_path = book_root / "pipeline-workspace" / "reports" / "pdf-profile.md"
    if (profile_path.exists() or report_path.exists()) and not force:
        raise SystemExit("pdf profile already exists; use --force to overwrite")

    profile = profile_pdf(book_root)
    write_profile_yaml(profile, profile_path)
    write_profile_report(profile, report_path)
    print(f"[OK] 已生成 {profile_path}")
    print(f"[OK] 已生成 {report_path}")
    return profile
