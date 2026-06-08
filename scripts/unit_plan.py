"""Semantic unit planning and validation."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


VALID_UNIT_TYPES = {"concept", "derivation", "application", "intro", "transition", "appendix"}
VALID_EXTRACTION_METHODS = {"text", "screenshot_ocr", "hybrid"}
VALID_FORMULA_RISKS = {"unknown", "low", "medium", "high"}
VALID_PLANNER_CONFIDENCE = {"low", "medium", "high"}
VALID_REVIEW_STATUSES = {"pending", "accepted", "edited", "skipped"}


REQUIRED_UNIT_FIELDS = [
    "unit_id",
    "title",
    "unit_type",
    "include",
    "source_scope",
    "extraction_method",
    "formula_risk",
    "planner_confidence",
    "review_status",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def expand_pages(raw_pages: list[int]) -> list[int]:
    if len(raw_pages) == 2 and raw_pages[0] <= raw_pages[1]:
        return list(range(int(raw_pages[0]), int(raw_pages[1]) + 1))
    return [int(page) for page in raw_pages]


def unit_by_id(plan: dict[str, Any], unit_id: str) -> dict[str, Any]:
    for unit in plan.get("units", []):
        if unit.get("unit_id") == unit_id:
            return unit
    return {}


def validate_unit_fields(unit: dict[str, Any], all_unit_ids: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    unit_label = unit.get("unit_id", "<missing unit_id>")

    for field in REQUIRED_UNIT_FIELDS:
        if field not in unit:
            errors.append(f"{unit_label}: missing required field {field}")

    source_scope = unit.get("source_scope")
    if not isinstance(source_scope, dict) or not source_scope.get("pages"):
        errors.append(f"{unit_label}: missing source_scope.pages")

    if unit.get("unit_type") not in VALID_UNIT_TYPES:
        errors.append(f"{unit_label}: invalid unit_type {unit.get('unit_type')!r}")

    if unit.get("extraction_method") not in VALID_EXTRACTION_METHODS:
        errors.append(f"{unit_label}: invalid extraction_method {unit.get('extraction_method')!r}")

    if unit.get("formula_risk") not in VALID_FORMULA_RISKS:
        errors.append(f"{unit_label}: invalid formula_risk {unit.get('formula_risk')!r}")

    if unit.get("planner_confidence") not in VALID_PLANNER_CONFIDENCE:
        errors.append(f"{unit_label}: invalid planner_confidence {unit.get('planner_confidence')!r}")

    if unit.get("review_status") not in VALID_REVIEW_STATUSES:
        errors.append(f"{unit_label}: invalid review_status {unit.get('review_status')!r}")

    include = unit.get("include")
    if not isinstance(include, bool):
        errors.append(f"{unit_label}: include must be boolean")
    elif include:
        output_targets = unit.get("output_targets") or []
        if "section-lesson" not in output_targets:
            errors.append(f"{unit_label}: include=true requires output_targets containing section-lesson")
    elif not str(unit.get("skip_reason", "")).strip():
        errors.append(f"{unit_label}: include=false requires skip_reason")

    if all_unit_ids is not None:
        for dep in unit.get("depends_on") or []:
            if dep not in all_unit_ids:
                errors.append(f"{unit_label}: unknown depends_on {dep}")

    return errors


def validate_unit_plan(plan: dict[str, Any], total_pages: int) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    covered: dict[int, list[str]] = {}
    units = plan.get("units", [])
    all_unit_ids = {unit.get("unit_id") for unit in units if unit.get("unit_id")}

    for unit in units:
        unit_errors = validate_unit_fields(unit, all_unit_ids)
        errors.extend(unit_errors)
        pages = unit.get("source_scope", {}).get("pages", [])
        try:
            expanded_pages = expand_pages(pages)
        except (TypeError, ValueError):
            errors.append(f"{unit.get('unit_id', '<missing unit_id>')}: source_scope.pages must be integers")
            continue
        for page in expanded_pages:
            if page < 1 or page > total_pages:
                errors.append(
                    f"{unit.get('unit_id', '<missing unit_id>')}: page {page} out of range 1..{total_pages}"
                )
            covered.setdefault(page, []).append(unit.get("unit_id", "<missing unit_id>"))

    missing_pages = [page for page in range(1, total_pages + 1) if page not in covered]
    overlaps = [
        {"page": page, "units": ids}
        for page, ids in sorted(covered.items())
        if len(ids) > 1
    ]
    unexplained = [
        item for item in overlaps
        if any(not unit_by_id(plan, uid).get("overlap_reason") for uid in item["units"])
    ]
    if missing_pages:
        errors.append(f"missing pages: {missing_pages}")
    if unexplained:
        errors.append(f"unexplained overlaps: {unexplained}")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "missing_pages": missing_pages,
        "overlaps": overlaps,
    }


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


PLANNER_SYSTEM_PROMPT = (
    "你是 PDF-to-Obsidian 语义规划器。只输出 JSON 对象，不输出 Markdown。"
    "输出必须能直接转换为 semantic-unit-plan.candidates.yaml。\n"
    "\n"
    "【切分原则】\n"
    "每个 include=true 的 unit 最终会成为本地知识库中一个完整、独立的学习小单元。"
    "切分时以「读者只读这一个 unit 就能获得逻辑完整、阅读友好的认知」为标准：\n"
    "- 优先以 TOC 二级标题作为切分边界；只有当一个二级标题内部包含多个主题差异显著的三级小节时，"
    "才在三级标题边界进一步拆分。\n"
    "- 禁止拆开内在逻辑连贯的内容：同一个定理的陈述与证明、同一个均衡/最优化问题的完整推导、"
    "贯穿多页的同一个数值案例或例题，必须保留在同一个 unit 内。\n"
    "- 当内容存在「先理解 A 才能理解 B」的强依赖时，A 与 B 应放在同一 unit，或用 depends_on 显式声明依赖。\n"
    "- 过于零散、脱离上下文读者无法独立理解的片段，应合并到相邻的同级 unit，而不是单独成 unit。\n"
    "- 不要机械按固定页数切分；页数只是参考，逻辑一致性和阅读友好性优先。\n"
    "\n"
    "【extraction_method 判断】\n"
    "- 任意页 formula_risk=high 或 blank_variable_risk=high → hybrid\n"
    "- 多数页 formula_risk=high 且 text_length 很短（疑似扫描件）→ screenshot_ocr\n"
    "- 有图表（image_count>0）但公式很少 → text\n"
    "- 其余 → text\n"
    "\n"
    "【include 判断】\n"
    "- include=false 仅限：封面、版权页、空白页、纯目录页、仅含章节标题的过渡页；必须给 skip_reason。\n"
    "- 前言/序言/引言只要有实质内容就必须 include=true。\n"
    "- 不确定时倾向 include=true。\n"
    "\n"
    "【输出格式——严格遵守，节省 token】\n"
    "每个 unit 对象只输出以下字段，不要输出任何其他字段（不要 learning_targets、不要 notes、不要长描述）：\n"
    "  unit_id（字符串）、title（小节标题）、unit_type、include（布尔）、"
    "pages（整数数组，连续区间用 [起,止] 两元素表示）、extraction_method、formula_risk、"
    "depends_on（数组）、risk_flags（数组，没有就空数组）、planner_confidence、"
    "summary（一句话，不超过 40 字，概括这个 unit 讲什么，供人工 review 快速判断是否合并/拆分）。"
    "include=false 的 unit 额外带 skip_reason。\n"
    "unit_type 只能取：concept（概念阐述）、derivation（推导/证明）、application（应用/案例）、"
    "intro（引言/前言）、transition（过渡/分部标题）、appendix（封面/版权/目录/空白等附属页）。"
    "不要自创 cover、blank、section、chapter 等类型名。"
    "extraction_method 只能取 text、hybrid、screenshot_ocr；formula_risk 只能取 low、medium、high；"
    "planner_confidence 只能取 low、medium、high，把握不足时设 medium 或 low 以便人工重点复核。"
    "页码必须连续覆盖 1..total_pages，无缺漏、无未解释重叠。"
    "参考 few_shot_example 字段给出的样例，严格照其格式输出。"
)


# 给规划器的 few-shot 样例：用极简正例锁死输出格式，比否定式禁令更有效，
# 同时演示「每个 unit 一句话 summary」「连续页用 [起,止]」「risk_flags 用真实风险码」。
PLANNER_FEW_SHOT_EXAMPLE = {
    "units": [
        {
            "unit_id": "U-001-01",
            "title": "封面",
            "unit_type": "appendix",
            "include": False,
            "pages": [1],
            "extraction_method": "text",
            "formula_risk": "low",
            "depends_on": [],
            "risk_flags": [],
            "planner_confidence": "high",
            "skip_reason": "封面页，无学习内容",
        },
        {
            "unit_id": "U-002-01",
            "title": "2.1 纳什均衡的定义",
            "unit_type": "concept",
            "include": True,
            "pages": [12, 14],
            "extraction_method": "hybrid",
            "formula_risk": "high",
            "depends_on": [],
            "risk_flags": [],
            "planner_confidence": "high",
            "summary": "给出纳什均衡的形式定义与直觉解释。",
        },
    ]
}


def build_planner_payload(
    pdf_profile: dict[str, Any],
    book_profile: dict[str, Any],
    study_profile: dict[str, Any],
) -> dict[str, Any]:
    pages = [
        {
            "page": page["page"],
            "summary_200": page["summary_200"],
            "text_length": page["text_length"],
            "formula_risk": page["formula_risk"],
            "table_risk": page["table_risk"],
            "image_count": page["image_count"],
            "blank_variable_risk": page["blank_variable_risk"],
        }
        for page in pdf_profile["pages"]
    ]
    return {
        "task": "generate_semantic_unit_plan",
        "book": {
            "book_id": book_profile["book_id"],
            "title": book_profile.get("title", ""),
            "language": book_profile.get("language", "zh"),
            "study_goal": "生成 Obsidian 本地学习知识库",
        },
        "toc_hint": "level<=2 的 TOC 条目是首选切分边界；level=3 仅作为可选的内部细分点",
        "toc": pdf_profile.get("toc", []),
        "total_pages": pdf_profile["total_pages"],
        "pages": pages,
        "study_profile": study_profile,
        "constraints": [
            "必须覆盖 1..total_pages 的全部页码，包括 include=false 的页",
            "缺页、越界、未解释重叠均不可接受",
            "引言、目录、过渡、重复内容可 include=false，但必须给 skip_reason",
            "高公式页推荐 hybrid 或 screenshot_ocr",
            "每个 include=true unit 必须包含 depends_on, risk_flags, output_targets",
            "每个 include=true unit 的内容须具备独立阅读的逻辑一致性，读者无需外部上下文即可理解",
            "同一个定理证明、推导过程或数值案例不得跨 unit 拆开",
            "优先以 TOC 二级标题切分，谨慎使用三级标题；不要机械按固定页数切分",
            "formula_risk=high 或 blank_variable_risk=high 的 unit 必须设 extraction_method 为 hybrid 或 screenshot_ocr",
        "每个 include=true unit 必须给一句话 summary（<=40字），供人工 review 判断合并/拆分",
        "只输出规定字段，不要附带任何额外字段，以节省 token",
        ],
        "few_shot_example": PLANNER_FEW_SHOT_EXAMPLE,
        "output_schema": {
            "units": "array of unit objects, each EXACTLY like few_shot_example items",
        },
    }


def normalize_planner_response(
    response: dict[str, Any],
    book_id: str,
    total_pages: int,
    planner_model: str,
) -> dict[str, Any]:
    units = response.get("units")
    if not isinstance(units, list):
        raise ValueError("planner response must contain units array")
    normalized_units = _explain_same_page_overlaps([_normalize_unit(unit) for unit in units])
    return {
        "book_id": response.get("book_id") or book_id,
        "generated_at": response.get("generated_at") or utc_now(),
        "planner_model": response.get("planner_model") or planner_model,
        "total_pages": int(response.get("total_pages") or total_pages),
        "units": normalized_units,
    }


def _normalize_unit(unit: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(unit)

    if "unit_id" not in item and "id" in item:
        item["unit_id"] = str(item["id"])

    if "source_scope" not in item:
        if "pages" in item:
            item["source_scope"] = {"pages": item["pages"]}
        elif "page_range" in item:
            item["source_scope"] = {"pages": item.pop("page_range")}
        else:
            start_page = item.pop("start_page", None)
            end_page = item.pop("end_page", start_page)
            if start_page is not None:
                item["source_scope"] = {"pages": _page_range_pair(start_page, end_page)}
    elif isinstance(item.get("source_scope"), dict) and "pages" not in item["source_scope"]:
        if "pages" in item:
            item["source_scope"]["pages"] = item["pages"]
        else:
            start_page = item["source_scope"].get("start_page")
            end_page = item["source_scope"].get("end_page", start_page)
            if start_page is not None:
                item["source_scope"]["pages"] = _page_range_pair(start_page, end_page)

    if "source_scope" in item and isinstance(item["source_scope"].get("pages"), list):
        raw_pages = item["source_scope"]["pages"]
        if len(raw_pages) == 2 and int(raw_pages[0]) == int(raw_pages[1]):
            item["source_scope"]["pages"] = [int(raw_pages[0])]
        else:
            item["source_scope"]["pages"] = [int(page) for page in raw_pages]

    include = item.get("include")
    if not isinstance(include, bool):
        item["include"] = bool(include) if include is not None else True

    # 规划器有时自创 unit_type（cover/section/chapter 等）；非法值一律重新推断为合法类型
    if item.get("unit_type") not in VALID_UNIT_TYPES:
        item["unit_type"] = _infer_unit_type(item)

    formula_risk = _extract_formula_risk(item.get("risk_flags"))
    item.setdefault("formula_risk", formula_risk)
    item.setdefault("extraction_method", _infer_extraction_method(item["formula_risk"], item.get("risk_flags")))
    item.setdefault("planner_confidence", "high")
    item.setdefault("review_status", "pending")
    item.setdefault("depends_on", [])
    item.setdefault("summary", "")

    output_targets = item.get("output_targets")
    if item["include"]:
        if isinstance(output_targets, dict):
            item["learning_targets"] = output_targets
            item["output_targets"] = ["section-lesson"]
        elif isinstance(output_targets, list):
            item["output_targets"] = _normalize_output_targets(output_targets)
        else:
            item["output_targets"] = ["section-lesson"]
    elif "output_targets" not in item or not isinstance(output_targets, list):
        item["output_targets"] = []

    if isinstance(item.get("risk_flags"), dict):
        item["risk_flags"] = _risk_flags_to_list(item["risk_flags"])

    return item


def _normalize_output_targets(output_targets: list[Any]) -> list[str]:
    aliases = {"lesson_note", "lesson", "note", "section_lesson", "section-lesson"}
    normalized = ["section-lesson" if str(target) in aliases else str(target) for target in output_targets]
    if "section-lesson" not in normalized:
        normalized.insert(0, "section-lesson")
    return normalized


def _explain_same_page_overlaps(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    page_to_units: dict[int, list[dict[str, Any]]] = {}
    for unit in units:
        for page in expand_pages(unit.get("source_scope", {}).get("pages", [])):
            page_to_units.setdefault(page, []).append(unit)
    for shared_units in page_to_units.values():
        if len(shared_units) > 1:
            for unit in shared_units:
                unit.setdefault("overlap_reason", "同一 PDF 页包含多个语义小节")
    return units


def _infer_unit_type(unit: dict[str, Any]) -> str:
    if not unit.get("include"):
        return "appendix"
    title = str(unit.get("title", ""))
    if any(word in title for word in ["推导", "求解", "均衡", "最优化", "判别"]):
        return "derivation"
    if any(word in title for word in ["应用", "案例", "数值模拟"]):
        return "application"
    if any(word in title for word in ["引言", "概述", "开头"]):
        return "intro"
    return "concept"


def _page_range_pair(start_page: Any, end_page: Any) -> list[int]:
    start = int(start_page)
    end = int(end_page)
    if start == end:
        return [start]
    return [start, end]


def _extract_formula_risk(risk_flags: Any) -> str:
    if isinstance(risk_flags, dict):
        value = str(risk_flags.get("formula", "low")).lower()
        if value in VALID_FORMULA_RISKS:
            return value
    if isinstance(risk_flags, list):
        text = " ".join(str(flag).lower() for flag in risk_flags)
        if "formula_heavy" in text or "formula_high" in text or "formula_risk_high" in text:
            return "high"
        if "formula_medium" in text or "formula_risk_medium" in text:
            return "medium"
    return "low"


def _infer_extraction_method(formula_risk: str, risk_flags: Any) -> str:
    if formula_risk == "high":
        return "hybrid"
    if isinstance(risk_flags, dict) and str(risk_flags.get("table", "")).lower() == "high":
        return "hybrid"
    return "text"


_METHOD_STRENGTH = {"text": 0, "hybrid": 1, "screenshot_ocr": 2}


def apply_profile_extraction_method(plan: dict[str, Any], pdf_profile: dict[str, Any]) -> dict[str, Any]:
    """Upgrade each unit's extraction_method from the per-page PDF profile.

    The planner LLM tends to under-detect formula pages and label everything
    ``text``. The deterministic page profile already knows which pages need
    OCR (``recommended_extraction_method``), so promote a unit to ``hybrid`` /
    ``screenshot_ocr`` when any of its source pages requires it. Never downgrade
    a method the planner chose; only strengthen. Also raise ``formula_risk`` to
    high when any covered page is high-formula."""
    page_method = {
        int(page["page"]): page.get("recommended_extraction_method", "text")
        for page in pdf_profile.get("pages", [])
        if page.get("page") is not None
    }
    page_formula = {
        int(page["page"]): page.get("formula_risk", "low")
        for page in pdf_profile.get("pages", [])
        if page.get("page") is not None
    }
    for unit in plan.get("units", []):
        pages = expand_pages(unit.get("source_scope", {}).get("pages", []))
        if not pages:
            continue
        best = max((page_method.get(p, "text") for p in pages), key=lambda m: _METHOD_STRENGTH.get(m, 0))
        current = unit.get("extraction_method", "text")
        if _METHOD_STRENGTH.get(best, 0) > _METHOD_STRENGTH.get(current, 0):
            unit["extraction_method"] = best
        if any(page_formula.get(p) == "high" for p in pages) and unit.get("formula_risk") != "high":
            unit["formula_risk"] = "high"
    return plan


def _compress_pages(pages: list[int]) -> list[int]:
    """连续页压成 [起, 止] 二元区间；不连续则保留显式列表。"""
    pages = sorted(set(int(p) for p in pages))
    if not pages:
        return []
    if pages == list(range(pages[0], pages[-1] + 1)):
        return [pages[0]] if pages[0] == pages[-1] else [pages[0], pages[-1]]
    return pages


def fill_coverage_gaps(plan: dict[str, Any], total_pages: int) -> dict[str, Any]:
    """把规划器漏掉的页码补进相邻 unit，消除覆盖缺口。

    规划器有时会漏掉章节尾页（夹在某 unit 末页和下一章之间）。每个未覆盖页归给
    「最近的前驱 unit」（页码最大且 < 缺页者，通常正是该章节的尾页所属 unit）；
    若无前驱则归给最近后继。这是对规划器随机漏页的确定性兜底，使覆盖率自愈。"""
    units = plan.get("units", [])
    if not units:
        return plan

    def max_page(u):
        pgs = expand_pages(u.get("source_scope", {}).get("pages", []))
        return max(pgs) if pgs else -1

    def min_page(u):
        pgs = expand_pages(u.get("source_scope", {}).get("pages", []))
        return min(pgs) if pgs else 10 ** 9

    covered = set()
    for u in units:
        covered.update(expand_pages(u.get("source_scope", {}).get("pages", [])))
    missing = [p for p in range(1, total_pages + 1) if p not in covered]

    for p in missing:
        prev_idx, prev_max = None, -1
        for i, u in enumerate(units):
            m = max_page(u)
            if m < p and m > prev_max:
                prev_idx, prev_max = i, m
        target = prev_idx
        if target is None:
            nxt_idx, nxt_min = None, 10 ** 9
            for i, u in enumerate(units):
                m = min_page(u)
                if m > p and m < nxt_min:
                    nxt_idx, nxt_min = i, m
            target = nxt_idx
        if target is None:
            continue
        u = units[target]
        merged = expand_pages(u.get("source_scope", {}).get("pages", [])) + [p]
        u.setdefault("source_scope", {})["pages"] = _compress_pages(merged)
        if u.get("review_status") == "accepted":
            u["review_status"] = "edited"  # 自动补页后需人工再确认
    return plan


def _risk_flags_to_list(risk_flags: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for key, value in risk_flags.items():
        if value in (None, "", False, 0, "0", "none", "None"):
            continue
        flags.append(f"{key}_{str(value).lower()}")
    return flags


def render_validation_report(title: str, validation: dict[str, Any]) -> str:
    lines = [
        f"# {title}",
        "",
        f"passed: {str(validation['passed']).lower()}",
        "",
        "## Errors",
        "",
    ]
    if validation.get("errors"):
        for error in validation["errors"]:
            lines.append(f"- {error}")
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Missing Pages",
        "",
        str(validation.get("missing_pages", [])),
        "",
        "## Overlaps",
        "",
        yaml.dump(validation.get("overlaps", []), allow_unicode=True, sort_keys=False).strip(),
        "",
    ])
    return "\n".join(lines)


def write_validation_report(path: Path, title: str, validation: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_validation_report(title, validation), encoding="utf-8")


def render_planning_report(plan: dict[str, Any], validation: dict[str, Any]) -> str:
    lines = [
        "# Unit Planning Report",
        "",
        f"book_id: {plan.get('book_id')}",
        f"total_pages: {plan.get('total_pages')}",
        f"units: {len(plan.get('units', []))}",
        f"validation: {'passed' if validation['passed'] else 'failed'}",
        "",
        "## Validation",
        "",
    ]
    if validation.get("errors"):
        for error in validation["errors"]:
            lines.append(f"- {error}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def plan_units_command(book_root: Path, force: bool = False) -> dict[str, Any]:
    candidates_path = book_root / "config" / "semantic-unit-plan.candidates.yaml"
    report_path = book_root / "pipeline-workspace" / "reports" / "unit-planning-report.md"
    if candidates_path.exists() and not force:
        raise SystemExit("semantic-unit-plan.candidates.yaml already exists; use --force to overwrite")

    pdf_profile = load_yaml(book_root / "config" / "pdf-profile.yaml")
    book_profile = load_yaml(book_root / "config" / "book-profile.yaml")
    study_profile = load_yaml(book_root / "config" / "study-profile.yaml")
    if not pdf_profile:
        raise SystemExit("missing config/pdf-profile.yaml; run profile-pdf first")
    if not book_profile:
        raise SystemExit("missing config/book-profile.yaml")

    from llm_provider import create_provider, load_provider_config

    provider_config = load_provider_config()
    provider = create_provider(provider_config)
    payload = build_planner_payload(pdf_profile, book_profile, study_profile)
    response = provider.chat_json(
        system=PLANNER_SYSTEM_PROMPT,
        user=yaml.dump(payload, allow_unicode=True, sort_keys=False),
        model=provider_config.planner_model,
        temperature=0.1,
    )
    plan = normalize_planner_response(
        response,
        book_root.name,
        int(pdf_profile["total_pages"]),
        provider_config.planner_model,
    )
    plan = apply_profile_extraction_method(plan, pdf_profile)
    plan = fill_coverage_gaps(plan, int(pdf_profile["total_pages"]))
    validation = validate_unit_plan(plan, int(pdf_profile["total_pages"]))
    plan["coverage_validation"] = {
        "passed": validation["passed"],
        "missing_pages": validation["missing_pages"],
        "overlaps": validation["overlaps"],
        "validated_at": utc_now(),
    }

    write_yaml(candidates_path, plan)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_planning_report(plan, validation), encoding="utf-8")
    print(f"[OK] 已生成候选规划: {candidates_path}")
    print(f"[OK] 已生成规划报告: {report_path}")
    if not validation["passed"]:
        raise SystemExit("unit plan validation failed; see unit-planning-report.md")
    return plan


def _find_plan_for_validation(book_root: Path) -> Path:
    plan_path = book_root / "config" / "semantic-unit-plan.yaml"
    candidates_path = book_root / "config" / "semantic-unit-plan.candidates.yaml"
    if plan_path.exists():
        return plan_path
    if candidates_path.exists():
        return candidates_path
    raise FileNotFoundError("missing semantic-unit-plan.yaml or semantic-unit-plan.candidates.yaml")


def validate_unit_plan_command(book_root: Path) -> dict[str, Any]:
    plan_path = _find_plan_for_validation(book_root)
    plan = load_yaml(plan_path)
    total_pages = int(plan.get("total_pages") or 0)
    if not total_pages:
        pdf_profile = load_yaml(book_root / "config" / "pdf-profile.yaml")
        total_pages = int(pdf_profile.get("total_pages") or 0)
    if not total_pages:
        raise SystemExit("missing total_pages in plan or pdf-profile.yaml")

    validation = validate_unit_plan(plan, total_pages)
    report_path = book_root / "pipeline-workspace" / "reports" / "unit-plan-validation.md"
    write_validation_report(report_path, "Unit Plan Validation", validation)
    print(f"[OK] 已生成校验报告: {report_path}")
    if not validation["passed"]:
        raise SystemExit("unit plan validation failed; see unit-plan-validation.md")
    return validation


def parse_pages_input(raw: str) -> list[int]:
    pages: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = [item.strip() for item in part.split("-", 1)]
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"invalid page range: {part}")
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def print_unit_table(plan: dict[str, Any]) -> None:
    for unit in plan.get("units", []):
        pages = unit.get("source_scope", {}).get("pages", [])
        print(
            "{unit_id} pages={pages} include={include} method={method} "
            "formula={formula} confidence={confidence} status={status}".format(
                unit_id=unit.get("unit_id"),
                pages=pages,
                include=unit.get("include"),
                method=unit.get("extraction_method"),
                formula=unit.get("formula_risk"),
                confidence=unit.get("planner_confidence"),
                status=unit.get("review_status"),
            )
        )


def _page_summaries_by_number(pdf_profile: dict[str, Any]) -> dict[int, str]:
    summaries: dict[int, str] = {}
    for page in pdf_profile.get("pages", []):
        number = page.get("page")
        if number is None:
            continue
        summaries[int(number)] = str(page.get("summary_200", "")).strip()
    return summaries


def _unit_one_line_summary(unit: dict[str, Any], page_summaries: dict[int, str] | None) -> str:
    """优先用规划器给的一句话 summary；缺失时回退到首页摘要截断。"""
    summary = str(unit.get("summary", "")).strip()
    if summary:
        return summary
    if page_summaries:
        pages = expand_pages(unit.get("source_scope", {}).get("pages", []))
        if pages:
            fallback = page_summaries.get(pages[0], "")
            return (fallback[:40] + "…") if len(fallback) > 40 else fallback
    return "（无摘要）"


def print_unit_for_review(
    unit: dict[str, Any],
    units: list[dict[str, Any]] | None = None,
    index: int | None = None,
    page_summaries: dict[int, str] | None = None,
) -> None:
    pages = unit.get("source_scope", {}).get("pages", [])
    page_count = len(expand_pages(pages))
    print("")
    print(f"{unit.get('unit_id')}  [{unit.get('unit_type')}]  {unit.get('title')}")
    print(f"  摘要: {_unit_one_line_summary(unit, page_summaries)}")
    print(f"  页码: {pages}（{page_count}页）  方法: {unit.get('extraction_method')}  "
          f"公式: {unit.get('formula_risk')}  置信: {unit.get('planner_confidence')}  "
          f"风险: {unit.get('risk_flags', [])}")

    if units is not None and index is not None:
        prev_unit = units[index - 1] if index > 0 else None
        next_unit = units[index + 1] if index + 1 < len(units) else None
        if prev_unit is not None:
            print(f"  ↑前: {prev_unit.get('unit_id')} {prev_unit.get('title')} "
                  f"[{_unit_one_line_summary(prev_unit, page_summaries)}]")
        if next_unit is not None:
            print(f"  ↓后: {next_unit.get('unit_id')} {next_unit.get('title')} "
                  f"[{_unit_one_line_summary(next_unit, page_summaries)}]")


def print_validation_summary(validation: dict[str, Any]) -> None:
    if validation["passed"]:
        print("[OK] 当前规划校验通过")
        return
    print("[WARN] 当前规划校验失败:")
    for error in validation.get("errors", []):
        print(f"  - {error}")


def merge_unit_into_previous(units: list[dict[str, Any]], index: int) -> None:
    if index == 0:
        raise ValueError("第一个 unit 不能并入前项")
    previous = units[index - 1]
    current = units.pop(index)
    # expand_pages 必须在合并前调用：pages 以二元区间格式存储（[start, end]），
    # 直接相加会产生 4 元素列表，expand_pages 会误判为枚举列表而丢失中间页。
    previous_pages = expand_pages(previous.get("source_scope", {}).get("pages", []))
    current_pages = expand_pages(current.get("source_scope", {}).get("pages", []))
    previous.setdefault("source_scope", {})["pages"] = sorted(set(previous_pages + current_pages))
    previous["source_scope"].setdefault("headings", [])
    previous["source_scope"]["headings"].extend(current.get("source_scope", {}).get("headings", []))
    previous.setdefault("merge_from", []).append(current["unit_id"])
    previous["overlap_reason"] = previous.get("overlap_reason") or "人工合并连续语义单元"
    previous["review_status"] = "edited"


def next_available_unit_id(units: list[dict[str, Any]], base_unit_id: str) -> str:
    match = re.match(r"^(?P<prefix>[A-Z]+-\d{3})-\d{2}$", base_unit_id)
    if not match:
        raise ValueError(f"unit_id 不符合 schema: {base_unit_id}")
    prefix = match.group("prefix")
    used = {unit["unit_id"] for unit in units}
    for number in range(1, 100):
        candidate = f"{prefix}-{number:02d}"
        if candidate not in used:
            return candidate
    raise ValueError(f"无法为 {base_unit_id} 分配新的 unit_id")


def split_unit_interactively(units: list[dict[str, Any]], index: int) -> None:
    current = units[index]
    left_pages = parse_pages_input(input("前半 unit 页码 > "))
    right_pages = parse_pages_input(input("后半 unit 页码 > "))
    left = copy.deepcopy(current)
    right = copy.deepcopy(current)
    left["source_scope"]["pages"] = left_pages
    right["source_scope"]["pages"] = right_pages
    left["title"] = input("前半标题 > ").strip() or current["title"]
    right["title"] = input("后半标题 > ").strip() or current["title"] + "（续）"
    left["unit_id"] = current["unit_id"]
    right["unit_id"] = next_available_unit_id(units, current["unit_id"])
    left["review_status"] = "edited"
    right["review_status"] = "edited"
    units[index:index + 1] = [left, right]


def mark_plan_reviewed(plan: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    reviewed = copy.deepcopy(plan)
    reviewed["coverage_validation"] = {
        "passed": validation["passed"],
        "missing_pages": validation["missing_pages"],
        "overlaps": validation["overlaps"],
        "validated_at": utc_now(),
    }
    return reviewed


def _has_unreviewed_include_units(plan: dict[str, Any]) -> bool:
    for unit in plan.get("units", []):
        if unit.get("include") and unit.get("review_status") not in {"accepted", "edited"}:
            return True
    return False


# 真正需要人工确认的管线风险码（公式丢失、OCR 失败/不可用、证据缺失等）。
# 规划器常把 key-concept、mathematical-proof 之类描述性标签塞进 risk_flags，
# 这些不是管线风险，不应阻挡纯文字 unit 的自动接受。
BLOCKING_RISK_FLAGS = {
    "formula_loss_risk",
    "screenshot_ocr_failed",
    "ocr_unavailable",
    "evidence_missing",
    "text_ocr_conflict",
}


def is_low_risk_text_unit(unit: dict[str, Any]) -> bool:
    """纯文字、低风险、规划器高置信的 include unit 可自动接受，无需人工确认。

    判定条件（全部满足）：extraction_method=text（不走 OCR）、formula_risk=low、
    planner_confidence=high、且 risk_flags 不含真正的管线风险码（描述性标签忽略）。
    任一不满足都需要人工审核——公式页要确认 OCR、低置信要确认切分。"""
    flags = unit.get("risk_flags") or []
    has_blocking = bool(set(flags) & BLOCKING_RISK_FLAGS)
    return (
        bool(unit.get("include"))
        and unit.get("extraction_method") == "text"
        and unit.get("formula_risk") == "low"
        and not has_blocking
        and unit.get("planner_confidence") == "high"
    )


def review_unit_plan_command(book_root: Path, list_only: bool = False) -> None:
    path = book_root / "config" / "semantic-unit-plan.candidates.yaml"
    if not path.exists():
        raise SystemExit(f"候选规划不存在: {path}")
    plan = load_yaml(path)
    if list_only:
        print_unit_table(plan)
        return

    pdf_profile = load_yaml(book_root / "config" / "pdf-profile.yaml")
    page_summaries = _page_summaries_by_number(pdf_profile)

    units = plan["units"]
    auto_count = 0
    index = 0
    while index < len(units):
        unit = units[index]
        if (
            unit.get("review_status") in {"accepted", "edited", "skipped"}
            and unit.get("planner_confidence") != "low"
        ):
            index += 1
            continue
        # include=False 且规划器已标注 skip_reason（封面/空白/目录等）自动接受，无需人工确认
        if not unit.get("include") and unit.get("skip_reason"):
            unit["review_status"] = "skipped"
            index += 1
            continue
        # 纯文字、低风险、高置信的 include unit 自动接受，只把真正需要判断的留给人工
        if is_low_risk_text_unit(unit):
            unit["review_status"] = "accepted"
            auto_count += 1
            print(f"[自动接受] {unit.get('unit_id')} {unit.get('title')} "
                  f"pages={unit.get('source_scope', {}).get('pages', [])}（纯文字低风险）")
            index += 1
            continue
        print_unit_for_review(unit, units=units, index=index, page_summaries=page_summaries)
        choice = input("[a]接受 [t]改标题 [p]改页码 [m]并入前项 [s]拆分 [x]跳过 [q]退出 > ")
        choice = choice.strip().lower()
        try:
            if choice == "a":
                unit["review_status"] = "accepted"
                index += 1
            elif choice == "t":
                unit["title"] = input("新标题 > ").strip()
                unit["review_status"] = "edited"
            elif choice == "p":
                unit["source_scope"]["pages"] = parse_pages_input(input("页码，如 1-3,5 > "))
                unit["review_status"] = "edited"
            elif choice == "m":
                merge_unit_into_previous(units, index)
                index = max(0, index - 1)
            elif choice == "s":
                split_unit_interactively(units, index)
            elif choice == "x":
                unit["include"] = False
                unit["skip_reason"] = input("跳过原因 > ").strip()
                unit["review_status"] = "skipped"
                unit["output_targets"] = []
                index += 1
            elif choice == "q":
                write_yaml(path, plan)
                return
            else:
                print("无效选择")
                continue
        except ValueError as exc:
            print(f"操作失败: {exc}")
            continue

        write_yaml(path, plan)
        print_validation_summary(validate_unit_plan(plan, int(plan["total_pages"])))

    write_yaml(path, plan)
    if auto_count:
        print(f"\n[汇总] 自动接受 {auto_count} 个纯文字低风险 unit；其余需人工判断的已逐个处理。")
    validation = validate_unit_plan(plan, int(plan["total_pages"]))
    report_path = book_root / "pipeline-workspace" / "reports" / "unit-plan-validation.md"
    write_validation_report(report_path, "Unit Plan Validation", validation)
    if _has_unreviewed_include_units(plan):
        raise SystemExit("unit plan has unreviewed include units")
    if not validation["passed"]:
        raise SystemExit("unit plan validation failed; see unit-plan-validation.md")

    write_yaml(book_root / "config" / "semantic-unit-plan.yaml", mark_plan_reviewed(plan, validation))
    print(f"[OK] 已生成正式规划: {book_root / 'config' / 'semantic-unit-plan.yaml'}")
