"""Section planning helpers for manifest and source-boundary candidates."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def plan_sections(book_root: Path, force: bool = False,
                  auto_accept_high: bool = True,
                  planner: str = "deterministic",
                  provider=None,
                  planner_model: str | None = None) -> dict[str, Any]:
    book_root = Path(book_root)
    manifest = _load_yaml(book_root / "config" / "section-manifest.yaml")
    pdf_path = _find_pdf(book_root)

    manifest_candidate = book_root / "config" / "section-manifest.candidates.yaml"
    hints_candidate = book_root / "config" / "source-boundary-hints.candidates.yaml"
    report_path = book_root / "pipeline-workspace" / "reports" / "section-planning-report.md"

    for path in (manifest_candidate, hints_candidate):
        if path.exists() and not force:
            raise SystemExit(f"候选文件已存在: {path}，使用 --force 覆盖")

    sections = manifest.get("sections", [])
    candidates = {}
    for idx, section in enumerate(sections):
        next_section = sections[idx + 1] if idx + 1 < len(sections) else None
        candidate = _build_boundary_candidate(pdf_path, section, next_section)
        if auto_accept_high and candidate["confidence"] == "high":
            candidate["review_status"] = "accepted"
        candidates[section["id"]] = candidate

    manifest_candidate.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(manifest_candidate, manifest)

    hints_payload = {
        "book_id": manifest.get("book_id", book_root.name),
        "generated_at": _now(),
        "source_file": pdf_path.name,
        "planner": planner,
        "candidate_policy": {
            "high": "auto-accepted when start/end boundaries are found",
            "medium": "requires review",
            "low": "requires review",
        },
        "sections": candidates,
    }
    if planner == "hybrid-llm":
        if provider is None or not planner_model:
            raise SystemExit("hybrid-llm planner 需要可用 LLM provider 和 planner_model")
        from llm_section_planner import enhance_boundary_candidates
        hints_payload = enhance_boundary_candidates(
            book_root=book_root,
            hints_payload=hints_payload,
            provider=provider,
            planner_model=planner_model,
        )
        candidates = hints_payload.get("sections") or {}

    _write_yaml(hints_candidate, hints_payload)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(candidates), encoding="utf-8")

    return {
        "manifest_candidate": manifest_candidate,
        "hints_candidate": hints_candidate,
        "report": report_path,
        "counts": _count_confidence(candidates),
    }


def review_sections(book_root: Path, list_only: bool = False,
                    section_id: str | None = None) -> dict[str, Any]:
    book_root = Path(book_root)
    path = book_root / "config" / "source-boundary-hints.candidates.yaml"
    data = _load_yaml(path)
    sections = data.get("sections") or {}

    selected = [
        (sid, item) for sid, item in sections.items()
        if section_id in {None, sid}
    ]
    if not selected:
        raise SystemExit("没有找到需要审核的候选边界")

    if list_only:
        for sid, item in selected:
            print(
                f"{sid}: confidence={item.get('confidence')} "
                f"review_status={item.get('review_status')} "
                f"start_found={item.get('start_found')} end_found={item.get('end_found')}"
            )
        return {"updated": 0, "path": path}

    updated = 0
    for sid, item in selected:
        if item.get("review_status") == "accepted":
            continue
        while True:
            _print_candidate(sid, item)
            choice = input("[a]接受 [s]跳过 [b]改起点 [e]改终点 [h]人工复核 [q]退出 > ").strip().lower()
            if choice == "a":
                item["review_status"] = "accepted"
                updated += 1
                break
            if choice == "s":
                break
            if choice == "b":
                item["start_regex"] = input("新的 start_regex > ").strip()
                item["review_status"] = "edited"
                updated += 1
                continue
            if choice == "e":
                item["end_regex"] = input("新的 end_regex > ").strip()
                item["review_status"] = "edited"
                updated += 1
                continue
            if choice == "h":
                item["review_status"] = "needs_human_review"
                updated += 1
                break
            if choice == "q":
                _write_yaml(path, data)
                return {"updated": updated, "path": path}
            print("无效选择")

    _write_yaml(path, data)
    return {"updated": updated, "path": path}


def apply_section_plan(book_root: Path, allow_pending: bool = False,
                       force: bool = False) -> dict[str, Any]:
    book_root = Path(book_root)
    manifest_candidate = book_root / "config" / "section-manifest.candidates.yaml"
    hints_candidate = book_root / "config" / "source-boundary-hints.candidates.yaml"
    manifest_target = book_root / "config" / "section-manifest.yaml"
    hints_target = book_root / "config" / "source-boundary-hints.yaml"

    manifest_data = _load_yaml(manifest_candidate)
    hints_data = _load_yaml(hints_candidate)
    sections = hints_data.get("sections") or {}

    pending = [
        sid for sid, item in sections.items()
        if item.get("review_status") not in {"accepted", "edited"}
        and item.get("confidence") != "high"
    ]
    if pending and not allow_pending:
        raise SystemExit(
            "仍有未审核的中低置信度边界: "
            + ", ".join(pending[:20])
            + (" ..." if len(pending) > 20 else "")
        )

    if hints_target.exists() and not force:
        raise SystemExit(f"目标文件已存在: {hints_target}，使用 --force 覆盖")

    accepted_sections = {
        sid: _strip_candidate_fields(item)
        for sid, item in sections.items()
        if item.get("review_status") in {"accepted", "edited"}
    }
    hints_payload = {
        "book_id": hints_data.get("book_id", manifest_data.get("book_id", book_root.name)),
        "source_file": hints_data.get("source_file", ""),
        "generated_at": _now(),
        "sections": accepted_sections,
    }

    shutil.copy2(manifest_candidate, manifest_target)
    _write_yaml(hints_target, hints_payload)

    return {
        "manifest": manifest_target,
        "hints": hints_target,
        "accepted_count": len(accepted_sections),
        "pending_count": len(pending),
    }


def _build_boundary_candidate(pdf_path: Path, section: dict[str, Any],
                              next_section: dict[str, Any] | None) -> dict[str, Any]:
    pages = _expand_page_locator(section.get("source_locator", {}).get("pages", []))
    raw_text = _extract_pages(pdf_path, pages) if pages else ""
    start_regex = _title_regex(section.get("title", ""))
    end_regex = _title_regex(next_section.get("title", "")) if next_section else r"\Z"

    start_found = bool(start_regex and re.search(start_regex, raw_text, flags=re.MULTILINE))
    end_found = bool(end_regex and re.search(end_regex, raw_text, flags=re.MULTILINE))
    if start_found and end_found:
        confidence = "high"
    elif start_found or end_found:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "source_order": section.get("source_order", ""),
        "title": section.get("title", ""),
        "rough_pages": pages,
        "boundary_mode": "title-range",
        "confidence": confidence,
        "review_status": "pending" if confidence != "high" else "candidate",
        "start_regex": start_regex,
        "end_regex": end_regex,
        "start_found": start_found,
        "end_found": end_found,
        "notes": _candidate_note(confidence, start_found, end_found),
    }


def _title_regex(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    title = re.sub(r"\s+", " ", title)
    escaped = re.escape(title)
    escaped = escaped.replace(r"\ ", r"\s*")
    escaped = escaped.replace(r"\：", r"[:：]")
    escaped = escaped.replace(r"\:", r"[:：]")
    escaped = escaped.replace(r"'", r"['‘’]")
    escaped = escaped.replace(r"\"", r'["“”]')
    escaped = escaped.replace(r"\(", r"\s*\(")
    return escaped


def _extract_pages(pdf_path: Path, pages: list[int]) -> str:
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        raw = ""
        for page_num in pages:
            if 1 <= page_num <= len(doc):
                raw += doc[page_num - 1].get_text() + "\n"
        return raw
    finally:
        doc.close()


def _find_pdf(book_root: Path) -> Path:
    pdfs = sorted((book_root / "input").glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"input 目录中没有 PDF 文件: {book_root / 'input'}")
    return pdfs[0]


def _expand_page_locator(pages: list) -> list[int]:
    if not pages:
        return []
    pages = [int(p) for p in pages]
    if len(pages) == 2 and pages[0] <= pages[1]:
        return list(range(pages[0], pages[1] + 1))
    return pages


def _candidate_note(confidence: str, start_found: bool, end_found: bool) -> str:
    if confidence == "high":
        return "起点和终点标题均命中，可自动接受"
    if start_found:
        return "起点标题命中，终点标题未命中，需要人工确认结束边界"
    if end_found:
        return "终点标题命中，起点标题未命中，需要人工确认开始边界"
    return "起点和终点标题均未命中，需要人工确认或改写正则"


def _strip_candidate_fields(item: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "source_order", "title", "rough_pages", "boundary_mode",
        "start_regex", "end_regex", "confidence", "review_status", "notes",
    }
    return {k: v for k, v in item.items() if k in keep}


def _render_report(candidates: dict[str, dict[str, Any]]) -> str:
    counts = _count_confidence(candidates)
    lines = [
        "# Section Planning Report",
        "",
        f"- generated_at: {_now()}",
        f"- high: {counts['high']}",
        f"- medium: {counts['medium']}",
        f"- low: {counts['low']}",
        "",
        "| Section ID | confidence | review_status | start | end | note |",
        "|------------|------------|---------------|-------|-----|------|",
    ]
    for sid, item in candidates.items():
        lines.append(
            f"| {sid} | {item['confidence']} | {item['review_status']} | "
            f"{item['start_found']} | {item['end_found']} | {item['notes']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _count_confidence(candidates: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for item in candidates.values():
        confidence = item.get("confidence")
        if confidence in counts:
            counts[confidence] += 1
    return counts


def _print_candidate(section_id: str, item: dict[str, Any]):
    print("")
    print(f"[{section_id}] {item.get('title', '')}")
    print(f"confidence: {item.get('confidence')}")
    print(f"start_found: {item.get('start_found')}  end_found: {item.get('end_found')}")
    print(f"start_regex: {item.get('start_regex')}")
    print(f"end_regex: {item.get('end_regex')}")
    print(f"notes: {item.get('notes')}")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
