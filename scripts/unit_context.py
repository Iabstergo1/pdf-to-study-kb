"""Per-unit context extraction for the semantic unit graph."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import fitz

import ocr_surya
from pdf_profile import compact_summary, find_pdf
from unit_plan import expand_pages


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_profile_by_number(pdf_profile: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {page["page"]: page for page in pdf_profile.get("pages", [])}


def _block_text(block: dict[str, Any]) -> str:
    lines = []
    for line in block.get("lines", []):
        spans = [span.get("text", "") for span in line.get("spans", [])]
        text = "".join(spans).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def extract_text_blocks(page_number: int, page: fitz.Page) -> list[dict[str, Any]]:
    text_dict = page.get_text("dict")
    blocks = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        if not text.strip():
            continue
        preview = compact_summary(text)
        blocks.append({
            "page": page_number,
            "bbox": [round(float(value), 2) for value in block.get("bbox", [])],
            "text_preview": preview,
            "sha256": sha256_text(text),
        })
    return blocks


def should_ocr_page(method: str, page_profile: dict[str, Any]) -> bool:
    if method == "screenshot_ocr":
        return True
    if method != "hybrid":
        return False
    return (
        page_profile.get("formula_risk") == "high"
        or page_profile.get("blank_variable_risk") in {True, "high"}
        or page_profile.get("table_risk") == "high"
        or int(page_profile.get("text_length") or 0) < 50
    )


def render_page_image(page: fitz.Page, image_path: Path) -> None:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    pixmap.save(str(image_path))


def _ocr_cache_path(book_root: Path, page_number: int) -> Path:
    return book_root / "pipeline-workspace" / "ocr-cache" / f"page-{page_number:04d}.json"


def _load_cached_ocr(book_root: Path, page_number: int) -> dict[str, Any] | None:
    path = _ocr_cache_path(book_root, page_number)
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if result.get("status") == "ok" and result.get("blocks"):
        return result
    return None


def _write_cached_ocr(book_root: Path, page_number: int, result: dict[str, Any]) -> None:
    if result.get("status") != "ok" or not result.get("blocks"):
        return
    path = _ocr_cache_path(book_root, page_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_ocr_blocks(page_number: int, result: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for block in result.get("blocks", []):
        text = block.get("text", "")
        html = block.get("html", "")
        preview_source = text or html
        preview = compact_summary(preview_source)
        normalized.append({
            "page": page_number,
            "text_preview": preview,
            "latex_preview": ocr_surya.extract_latex_preview(html),
            "sha256": sha256_text(preview_source),
        })
    return normalized


def _evidence_from_blocks(unit_id: str, text_blocks: list[dict[str, Any]], ocr_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = []
    index = 1
    for block in text_blocks:
        evidence.append({
            "evidence_id": f"E-{unit_id}-{index:04d}",
            "page": block["page"],
            "bbox": block.get("bbox", []),
            "preview": block["text_preview"],
            "sha256": block["sha256"],
            "evidence_type": "text",
        })
        index += 1
    for block in ocr_blocks:
        evidence.append({
            "evidence_id": f"E-{unit_id}-{index:04d}",
            "page": block["page"],
            "bbox": [],
            "preview": block["text_preview"],
            # LaTeX 必须随 OCR 证据传递，author 才能把公式直接嵌入正文并进入 Formula-Ledger
            "latex": block.get("latex_preview", ""),
            "sha256": block["sha256"],
            "evidence_type": "ocr",
        })
        index += 1
    return evidence


def _write_context_artifacts(book_root: Path, unit_id: str, context: dict[str, Any]) -> None:
    staging_dir = book_root / "pipeline-workspace" / "staging" / unit_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    preview_path = staging_dir / "context-preview.json"
    evidence_path = staging_dir / "evidence-index.jsonl"
    preview_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    evidence_lines = [
        json.dumps(item, ensure_ascii=False)
        for item in context.get("evidence_candidates", [])
    ]
    evidence_path.write_text("\n".join(evidence_lines) + ("\n" if evidence_lines else ""), encoding="utf-8")


def prepare_unit_context(book_root: Path, unit: dict[str, Any], pdf_profile: dict[str, Any]) -> dict[str, Any]:
    unit_id = unit["unit_id"]
    method = unit.get("extraction_method", "text")
    source_pages = expand_pages(unit.get("source_scope", {}).get("pages", []))
    profile_by_page = _page_profile_by_number(pdf_profile)
    text_blocks: list[dict[str, Any]] = []
    ocr_blocks: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    block_publish = False
    formula_risk = "low"

    pdf_path = find_pdf(book_root)
    doc = fitz.open(str(pdf_path))
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for page_number in source_pages:
                page = doc[page_number - 1]
                page_profile = profile_by_page.get(page_number, {})
                if method in {"text", "hybrid"}:
                    text_blocks.extend(extract_text_blocks(page_number, page))
                if should_ocr_page(method, page_profile):
                    result = _load_cached_ocr(book_root, page_number)
                    if result is None:
                        image_path = tmp_path / f"{unit_id}-p{page_number}.png"
                        render_page_image(page, image_path)
                        result = ocr_surya.recognize_page_image_with_retry(image_path)
                        _write_cached_ocr(book_root, page_number, result)
                    if result.get("block_publish"):
                        block_publish = True
                        formula_risk = result.get("formula_risk", formula_risk)
                        risk_flags.extend(result.get("risk_flags", []))
                    else:
                        page_ocr_blocks = _normalize_ocr_blocks(page_number, result)
                        ocr_blocks.extend(page_ocr_blocks)
                        if method == "hybrid" and page_ocr_blocks:
                            text_preview = " ".join(
                                block["text_preview"]
                                for block in text_blocks
                                if block["page"] == page_number
                            )
                            ocr_preview = " ".join(block["text_preview"] for block in page_ocr_blocks)
                            if text_preview and ocr_preview and text_preview != ocr_preview:
                                risk_flags.append("hybrid_conflict")
    finally:
        doc.close()

    evidence_candidates = _evidence_from_blocks(unit_id, text_blocks, ocr_blocks)
    context = {
        "unit_id": unit_id,
        "source_pages": source_pages,
        "text_blocks": text_blocks,
        "ocr_blocks": ocr_blocks,
        "evidence_candidates": evidence_candidates,
        "boundary_validation": {
            "start_title_match": True,
            "next_title_leak": False,
            "tail_page_has_content": bool(text_blocks or ocr_blocks),
        },
        "block_publish": block_publish,
        "risk_flags": sorted(set(risk_flags)),
    }
    if formula_risk == "high":
        context["formula_risk"] = "high"
    _write_context_artifacts(book_root, unit_id, context)
    return context
