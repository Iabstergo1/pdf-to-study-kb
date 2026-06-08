"""Build semantic-unit Obsidian vault indexes and generated artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def can_overwrite(path: Path) -> bool:
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    return "managed_by: pipeline" in text[:1000]


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", value).strip() or "untitled"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_managed_markdown(path: Path, content: str) -> bool:
    if not can_overwrite(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return True


def frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + yaml.dump(data, allow_unicode=True, sort_keys=False).strip() + "\n---\n\n"


def expand_pages(raw_pages: list[int]) -> list[int]:
    if len(raw_pages) == 2 and raw_pages[0] <= raw_pages[1]:
        return list(range(int(raw_pages[0]), int(raw_pages[1]) + 1))
    return [int(page) for page in raw_pages]


def _unit_lesson_frontmatter(unit: dict[str, Any], source_pdf: str, memory: dict[str, Any]) -> dict[str, Any]:
    unit_id = unit["unit_id"]
    concepts = [
        term for term, entry in memory.get("concept_index", {}).items()
        if unit_id in entry.get("units", [])
    ]
    symbols = [
        symbol for symbol, entry in memory.get("symbol_index", {}).items()
        if unit_id in entry.get("units", [])
    ]
    return {
        "type": "section-lesson",
        "unit_id": unit_id,
        "chapter": str(unit_id.split("-")[1]) if "-" in unit_id else "",
        "difficulty": unit.get("difficulty", 3),
        "formula_risk": unit.get("formula_risk", "unknown"),
        "status": "published",
        "concepts": concepts,
        "symbols": symbols,
        "depends_on": unit.get("depends_on", []),
        "source_pdf": source_pdf,
        "source_pages": expand_pages(unit.get("source_scope", {}).get("pages", [])),
        "risk_flags": unit.get("risk_flags", []),
        "managed_by": "pipeline",
    }


def _strip_frontmatter(body: str) -> str:
    if body.startswith("---\n"):
        body = body.split("---\n", 2)[-1]
        if "\n---" in body:
            body = body.split("\n---", 1)[-1].lstrip()
    return body


def render_lesson(unit: dict[str, Any], source_pdf: str, memory: dict[str, Any], draft: str) -> str:
    """Render a published Section-Lesson: Dataview frontmatter + evidence-cited body.

    Shared by the unit graph's publish_note and the end-of-run index rebuild so
    both emit the same managed file (with ``managed_by: pipeline``)."""
    body = _strip_frontmatter(draft)
    return frontmatter(_unit_lesson_frontmatter(unit, source_pdf, memory or {})) + body


def build_obsidian_indexes(
    book_root: Path,
    plan: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
) -> None:
    book_root = Path(book_root)
    plan = plan or load_yaml(book_root / "config" / "semantic-unit-plan.yaml")
    memory = memory or {}
    pdf_profile = load_yaml(book_root / "config" / "pdf-profile.yaml")
    source_pdf = pdf_profile.get("source_pdf", "")
    study_kb = book_root / "study-kb"
    units = plan.get("units", [])

    for rel in [
        "Section-Lessons",
        "Concept-Cards",
        "Glossary",
        "Symbols",
        "Formula-Ledger",
        "Claims",
        "Questions",
        "Review-Queue",
        "Learning-Maps",
        "Source-QA",
        "Dashboards",
    ]:
        (study_kb / rel).mkdir(parents=True, exist_ok=True)

    _write_lessons(book_root, study_kb, units, source_pdf, memory)
    _write_concepts(study_kb, memory)
    _write_glossary(study_kb, memory)
    _write_symbols(study_kb, memory)
    _write_per_unit_artifacts(book_root, study_kb, units, memory)
    _write_home(study_kb, plan)
    _write_maps(study_kb, units)
    _write_source_qa(study_kb, units)
    _write_dashboard(study_kb, units, memory)
    _cleanup_stale_review_queue(study_kb, units)
    _cleanup_stale_outputs(study_kb, units, memory)


def _cleanup_stale_managed(directory: Path, valid_filenames: set[str]) -> None:
    """删除目录下不再属于当前 memory/plan 的「管线托管」.md 文件。

    ``write_managed_markdown`` 只写不删；多次重跑后（概念/符号命名随 LLM 输出变化）会在
    Concept-Cards/Glossary/Symbols 等目录残留旧文件。这里按当前有效文件名集合清理，只删
    带 ``managed_by: pipeline`` 的文件，保留用户手写笔记。"""
    if not directory.exists():
        return
    for path in directory.glob("*.md"):
        if path.name not in valid_filenames and can_overwrite(path):
            path.unlink()


def _cleanup_stale_outputs(study_kb: Path, units: list[dict[str, Any]], memory: dict[str, Any]) -> None:
    concept_files = {f"{safe_name(term)}.md" for term in memory.get("concept_index", {})}
    symbol_files = {f"{safe_name(symbol)}.md" for symbol in memory.get("symbol_index", {})}
    all_unit_files = {f"{unit['unit_id']}.md" for unit in units}
    included_unit_files = {f"{unit['unit_id']}.md" for unit in units if unit.get("include", True)}
    _cleanup_stale_managed(study_kb / "Concept-Cards", concept_files)
    _cleanup_stale_managed(study_kb / "Glossary", concept_files)
    _cleanup_stale_managed(study_kb / "Symbols", symbol_files)
    _cleanup_stale_managed(study_kb / "Section-Lessons", included_unit_files)
    for rel in ("Claims", "Formula-Ledger", "Questions"):
        _cleanup_stale_managed(study_kb / rel, all_unit_files)


def _write_lessons(book_root: Path, study_kb: Path, units: list[dict[str, Any]], source_pdf: str, memory: dict[str, Any]) -> None:
    for unit in units:
        if not unit.get("include", True):
            continue
        unit_id = unit["unit_id"]
        draft_path = book_root / "pipeline-workspace" / "staging" / unit_id / "section-lesson-draft.md"
        lesson_path = study_kb / "Section-Lessons" / f"{unit_id}.md"
        if not draft_path.exists() or not _review_accepts_publish(book_root, unit_id):
            if can_overwrite(lesson_path):
                lesson_path.unlink(missing_ok=True)
            continue
        body = draft_path.read_text(encoding="utf-8", errors="replace")
        content = render_lesson(unit, source_pdf, memory, body)
        write_managed_markdown(lesson_path, content)


def _review_accepts_publish(book_root: Path, unit_id: str) -> bool:
    decision_path = book_root / "pipeline-workspace" / "reviews" / unit_id / "review-decision.yaml"
    decision = load_yaml(decision_path)
    return decision.get("decision") == "accept" and decision.get("confidence") != "low"


def _write_concepts(study_kb: Path, memory: dict[str, Any]) -> None:
    for term, entry in memory.get("concept_index", {}).items():
        content = frontmatter({"type": "concept-card", "term": term, "managed_by": "pipeline"})
        content += f"# {term}\n\n{entry.get('definition', '')}\n\nUnits: {', '.join(entry.get('units', []))}\n"
        write_managed_markdown(study_kb / "Concept-Cards" / f"{safe_name(term)}.md", content)


def _write_glossary(study_kb: Path, memory: dict[str, Any]) -> None:
    for term, entry in memory.get("concept_index", {}).items():
        content = frontmatter({"type": "glossary-entry", "term": term, "managed_by": "pipeline"})
        content += f"# {term}\n\n{entry.get('definition', '')}\n\nFirst unit: {entry.get('first_unit', '')}\n"
        write_managed_markdown(study_kb / "Glossary" / f"{safe_name(term)}.md", content)


def _write_symbols(study_kb: Path, memory: dict[str, Any]) -> None:
    for symbol, entry in memory.get("symbol_index", {}).items():
        content = frontmatter({"type": "symbol", "symbol": symbol, "managed_by": "pipeline"})
        content += f"# {symbol}\n\n{entry.get('meaning', '')}\n\nFirst unit: {entry.get('first_unit', '')}\n"
        write_managed_markdown(study_kb / "Symbols" / f"{safe_name(symbol)}.md", content)


def _load_unit_questions(book_root: Path, unit_id: str) -> list[str]:
    path = book_root / "pipeline-workspace" / "staging" / unit_id / "questions.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [q for q in data if isinstance(q, str) and q.strip()] if isinstance(data, list) else []


def _load_unit_claims(book_root: Path, unit_id: str) -> list[dict[str, Any]]:
    path = book_root / "pipeline-workspace" / "staging" / unit_id / "claims.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("statement")] if isinstance(data, list) else []


_CLAIM_TYPE_LABEL = {"source": "原文压缩", "explanation": "学习解释", "bridge": "个人桥接"}


def _render_claims(book_root: Path, unit_id: str, unit_evidence: list[dict[str, Any]]) -> list[str]:
    """优先用 author 声明的结构化 claims（区分忠实度类型）；缺失时回退 evidence_ledger。"""
    claims = _load_unit_claims(book_root, unit_id)
    lines = [f"# Claims: {unit_id}", ""]
    if claims:
        for claim in claims:
            ctype = claim.get("type", "source")
            label = _CLAIM_TYPE_LABEL.get(ctype, ctype)
            refs = " ".join(f"[{eid}]" for eid in claim.get("evidence_ids", []))
            lines.append(f"- ({label}) {claim.get('statement', '')}{(' → ' + refs) if refs else ''}")
        return lines
    for item in unit_evidence:
        lines.append(f"- {item.get('claim', '')} -> {item.get('evidence_id', '')} (p.{item.get('page', '')})")
    if not unit_evidence:
        lines.append("- 无证据账本条目")
    return lines


def _write_per_unit_artifacts(book_root: Path, study_kb: Path, units: list[dict[str, Any]], memory: dict[str, Any]) -> None:
    evidence_by_unit: dict[str, list[dict[str, Any]]] = {}
    for item in memory.get("evidence_ledger", []):
        evidence_by_unit.setdefault(item.get("unit_id", ""), []).append(item)
    for unit in units:
        unit_id = unit["unit_id"]
        unit_evidence = evidence_by_unit.get(unit_id, [])

        formula_lines = [
            f"# Formula Ledger: {unit_id}",
            "",
            f"formula_risk: {unit.get('formula_risk', 'unknown')}",
            "",
        ]
        formula_evidence = [
            item for item in unit_evidence
            if item.get("evidence_type") == "ocr" or (item.get("payload") or {}).get("latex_preview")
        ]
        if formula_evidence:
            formula_lines.append("## 公式来源（OCR/LaTeX 证据）")
            for item in formula_evidence:
                latex = (item.get("payload") or {}).get("latex_preview", "")
                preview = latex or item.get("claim", "")
                formula_lines.append(f"- p.{item.get('page', '')} [{item.get('evidence_id', '')}] {preview}")
        else:
            formula_lines.append("- 无 OCR/LaTeX 公式证据（纯文本抽取或无公式）")
        write_managed_markdown(
            study_kb / "Formula-Ledger" / f"{unit_id}.md",
            frontmatter({"type": "formula-ledger", "unit_id": unit_id, "managed_by": "pipeline"})
            + "\n".join(formula_lines),
        )

        claim_lines = _render_claims(book_root, unit_id, unit_evidence)
        write_managed_markdown(
            study_kb / "Claims" / f"{unit_id}.md",
            frontmatter({"type": "claims", "unit_id": unit_id, "managed_by": "pipeline"}) + "\n".join(claim_lines),
        )

        questions = _load_unit_questions(book_root, unit_id)
        question_lines = [f"# Questions: {unit_id}", ""]
        question_lines.extend(f"- {q}" for q in questions) if questions else question_lines.append("- 待生成")
        write_managed_markdown(
            study_kb / "Questions" / f"{unit_id}.md",
            frontmatter({"type": "questions", "unit_id": unit_id, "managed_by": "pipeline"}) + "\n".join(question_lines),
        )


def _write_home(study_kb: Path, plan: dict[str, Any]) -> None:
    lines = [
        frontmatter({"type": "home", "managed_by": "pipeline"}).rstrip(),
        f"# {plan.get('book_id', 'Study KB')}",
        "",
        "- [[Learning-Maps/MOC-全书学习地图|全书学习地图]]",
        "- [[Source-QA/覆盖率报告|覆盖率报告]]",
        "- [[Dashboards/质量看板|质量看板]]",
    ]
    write_managed_markdown(study_kb / "Home.md", "\n".join(lines))


def _write_maps(study_kb: Path, units: list[dict[str, Any]]) -> None:
    lines = [frontmatter({"type": "learning-map", "managed_by": "pipeline"}).rstrip(), "# 全书学习地图", ""]
    for unit in units:
        lines.append(f"- [[Section-Lessons/{unit['unit_id']}|{unit['unit_id']}]] - {unit.get('title', '')}")
    write_managed_markdown(study_kb / "Learning-Maps" / "MOC-全书学习地图.md", "\n".join(lines))


def _write_source_qa(study_kb: Path, units: list[dict[str, Any]]) -> None:
    total = len(units)
    included = sum(1 for unit in units if unit.get("include", True))
    coverage = frontmatter({"type": "coverage-report", "managed_by": "pipeline"})
    coverage += f"# 覆盖率报告\n\n- total_units: {total}\n- included_units: {included}\n"
    write_managed_markdown(study_kb / "Source-QA" / "覆盖率报告.md", coverage)

    risky = [unit for unit in units if unit.get("formula_risk") in {"medium", "high"} or unit.get("risk_flags")]
    lines = [frontmatter({"type": "risk-list", "managed_by": "pipeline"}).rstrip(), "# 高风险清单", ""]
    for unit in risky:
        lines.append(f"- {unit['unit_id']}: formula={unit.get('formula_risk')} risk_flags={unit.get('risk_flags', [])}")
    write_managed_markdown(study_kb / "Source-QA" / "高风险清单.md", "\n".join(lines))


def _write_dashboard(study_kb: Path, units: list[dict[str, Any]], memory: dict[str, Any]) -> None:
    content = frontmatter({"type": "dashboard", "managed_by": "pipeline"})
    content += "# 质量看板\n\n"
    content += f"- units: {len(units)}\n"
    content += f"- concepts: {len(memory.get('concept_index', {}))}\n"
    content += f"- symbols: {len(memory.get('symbol_index', {}))}\n"
    write_managed_markdown(study_kb / "Dashboards" / "质量看板.md", content)


def _cleanup_stale_review_queue(study_kb: Path, units: list[dict[str, Any]]) -> None:
    valid_unit_ids = {unit["unit_id"] for unit in units if unit.get("include", True)}
    review_queue = study_kb / "Review-Queue"
    if not review_queue.exists():
        return
    for path in review_queue.glob("*.md"):
        if path.stem not in valid_unit_ids and can_overwrite(path):
            path.unlink()
