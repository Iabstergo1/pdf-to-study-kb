"""In-process operations backing the local web UI.

serve.py 负责 HTTP 管道；这里是可单测的纯业务函数：书目状态、plan 卡片审批、定稿、
Review-Queue 页内处理、study-kb 浏览。长命令（profile-pdf/plan-units/run-book）由
serve.py 以子进程作业方式运行，不在此处。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

import unit_plan


class WebError(Exception):
    """面向前端的可读错误（HTTP 400）。"""


_BOOK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def _safe_book_id(book_id: str) -> str:
    if not isinstance(book_id, str) or not _BOOK_ID_RE.match(book_id):
        raise WebError(f"非法 book id: {book_id!r}（只能用字母/数字/连字符/下划线）")
    return book_id


def books_root() -> Path:
    return Path("books")


def book_root(book_id: str) -> Path:
    root = books_root() / _safe_book_id(book_id)
    if not root.exists():
        raise WebError(f"书籍不存在: {book_id}")
    return root


def _config(book_root_path: Path, name: str) -> Path:
    return book_root_path / "config" / name


def slugify(text: str) -> str:
    # book_id 必须是 ASCII（目录名跨平台 + 避免 Git Bash 中文路径崩溃），故剥离非 ASCII。
    # 全中文标题会得到空串 → 回退 "book"，此时应由前端让用户显式填 book_id。
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text or "book"


# --------------------------------------------------------------------------- #
# 书目列表与状态
# --------------------------------------------------------------------------- #

_STAGE_ORDER = ["imported", "profiled", "planned", "reviewed", "run"]
_STAGE_LABEL = {
    "imported": "已导入 PDF",
    "profiled": "已分析 PDF",
    "planned": "已生成切分候选",
    "reviewed": "已定稿规划",
    "run": "已编译",
}


def _book_stage(root: Path) -> str:
    if (root / "config" / "pdf-profile.yaml").exists():
        if _last_run_summary(root):
            return "run"
        if (root / "config" / "semantic-unit-plan.yaml").exists():
            return "reviewed"
        if (root / "config" / "semantic-unit-plan.candidates.yaml").exists():
            return "planned"
        return "profiled"
    return "imported"


def _input_pdf(root: Path) -> Path | None:
    input_dir = root / "input"
    if not input_dir.exists():
        return None
    pdfs = sorted(input_dir.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def _last_run_summary(root: Path) -> dict[str, Any] | None:
    runs = root / "pipeline-workspace" / "runs"
    if not runs.exists():
        return None
    summaries = sorted(runs.glob("*/semantic-run-summary.json"))
    if not summaries:
        return None
    import json

    try:
        return json.loads(summaries[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_books() -> list[dict[str, Any]]:
    root = books_root()
    if not root.exists():
        return []
    books = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        profile = unit_plan.load_yaml(child / "config" / "book-profile.yaml")
        stage = _book_stage(child)
        books.append({
            "book_id": child.name,
            "title": profile.get("title", child.name),
            "stage": stage,
            "stage_label": _STAGE_LABEL.get(stage, stage),
            "stage_index": _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0,
        })
    return books


def _count_md(directory: Path) -> int:
    return len([p for p in directory.glob("*.md")]) if directory.exists() else 0


def book_status(book_id: str) -> dict[str, Any]:
    root = book_root(book_id)
    profile = unit_plan.load_yaml(root / "config" / "book-profile.yaml")
    pdf = _input_pdf(root)
    stage = _book_stage(root)

    candidates = unit_plan.load_yaml(root / "config" / "semantic-unit-plan.candidates.yaml")
    cand_units = candidates.get("units", []) if candidates else []
    review_breakdown: dict[str, int] = {}
    include_count = 0
    for unit in cand_units:
        status = unit.get("review_status", "pending")
        review_breakdown[status] = review_breakdown.get(status, 0) + 1
        if unit.get("include", True):
            include_count += 1

    kb = root / "study-kb"
    summary = _last_run_summary(root) or {}
    results = summary.get("results", [])

    return {
        "book_id": book_id,
        "title": profile.get("title", book_id),
        "stage": stage,
        "stage_label": _STAGE_LABEL.get(stage, stage),
        "steps": _step_states(root, stage),
        "pdf": pdf.name if pdf else None,
        "has": {
            "pdf_profile": (root / "config" / "pdf-profile.yaml").exists(),
            "candidates": bool(cand_units),
            "final_plan": (root / "config" / "semantic-unit-plan.yaml").exists(),
            "run": bool(summary),
        },
        "candidates": {
            "total": len(cand_units),
            "include": include_count,
            "review_breakdown": review_breakdown,
            "unreviewed_include": sum(
                1 for u in cand_units
                if u.get("include", True) and u.get("review_status") not in {"accepted", "edited"}
            ),
        },
        "run": {
            "published": sum(1 for r in results if r.get("status") == "published"),
            "needs_review": sum(1 for r in results if r.get("status") == "needs_human_review"),
            "failed": sum(1 for r in results if r.get("status") == "failed"),
        },
        "kb": {
            "lessons": _count_md(kb / "Section-Lessons"),
            "review_queue": _count_md(kb / "Review-Queue"),
            "concepts": _count_md(kb / "Concept-Cards"),
            "symbols": _count_md(kb / "Symbols"),
        },
    }


def _step_states(root: Path, stage: str) -> list[dict[str, Any]]:
    """流水线 6 步的完成状态，驱动前端的步骤条。"""
    done_through = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0
    steps = [
        ("import", "导入 PDF", 0),
        ("profile-pdf", "分析 PDF", 0),
        ("plan-units", "生成切分", 1),
        ("review", "人工审批切分", 2),
        ("run-book", "编译全书", 3),
        ("review-queue", "处理待复核", 4),
    ]
    out = []
    for key, label, min_stage in steps:
        out.append({"key": key, "label": label, "done": done_through >= min_stage + 1 or (key == "import")})
    # import done if pdf exists
    out[0]["done"] = _input_pdf(root) is not None
    out[1]["done"] = (root / "config" / "pdf-profile.yaml").exists()
    out[2]["done"] = (root / "config" / "semantic-unit-plan.candidates.yaml").exists()
    out[3]["done"] = (root / "config" / "semantic-unit-plan.yaml").exists()
    out[4]["done"] = bool(_last_run_summary(root))
    out[5]["done"] = bool(_last_run_summary(root)) and _count_md(root / "study-kb" / "Review-Queue") == 0
    return out


# --------------------------------------------------------------------------- #
# 导入 PDF（init-book）
# --------------------------------------------------------------------------- #

def import_book(book_id: str, title: str, pdf_bytes: bytes, filename: str, force: bool = False) -> dict[str, Any]:
    book_id = _safe_book_id(book_id)
    if not pdf_bytes:
        raise WebError("PDF 内容为空")
    if not (filename or "").lower().endswith(".pdf"):
        filename = (filename or "source") + ".pdf"
    import pipeline

    tmp = Path(tempfile.gettempdir()) / f"webupload-{book_id}-{Path(filename).name}"
    tmp.write_bytes(pdf_bytes)
    args = SimpleNamespace(book=book_id, pdf=str(tmp), title=(title or book_id), force=force)
    try:
        pipeline.cmd_init_book(args)
    except SystemExit as exc:
        raise WebError(str(exc) or "init-book 失败（书籍可能已存在，可勾选覆盖）")
    finally:
        tmp.unlink(missing_ok=True)
    return book_status(book_id)


# --------------------------------------------------------------------------- #
# 切分候选的卡片式人工审批
# --------------------------------------------------------------------------- #

def _candidates_path(root: Path) -> Path:
    path = root / "config" / "semantic-unit-plan.candidates.yaml"
    if not path.exists():
        raise WebError("尚无切分候选，请先运行 plan-units")
    return path


def _page_summaries(root: Path) -> dict[int, str]:
    pdf_profile = unit_plan.load_yaml(root / "config" / "pdf-profile.yaml")
    return unit_plan._page_summaries_by_number(pdf_profile)


def get_candidates(book_id: str) -> dict[str, Any]:
    root = book_root(book_id)
    plan = unit_plan.load_yaml(_candidates_path(root))
    summaries = _page_summaries(root)
    total_pages = int(plan.get("total_pages") or 0)
    units = []
    for idx, unit in enumerate(plan.get("units", [])):
        units.append(_unit_card(unit, idx, summaries))
    validation = unit_plan.validate_unit_plan(plan, total_pages) if total_pages else {}
    return {
        "book_id": book_id,
        "total_pages": total_pages,
        "units": units,
        "validation": validation,
        "unreviewed_include": unit_plan._has_unreviewed_include_units(plan),
    }


def _unit_card(unit: dict[str, Any], index: int, summaries: dict[int, str]) -> dict[str, Any]:
    pages = unit.get("source_scope", {}).get("pages", [])
    return {
        "index": index,
        "unit_id": unit.get("unit_id"),
        "title": unit.get("title", ""),
        "unit_type": unit.get("unit_type", ""),
        "pages": pages,
        "pages_label": _pages_label(pages),
        "include": bool(unit.get("include", True)),
        "extraction_method": unit.get("extraction_method", ""),
        "formula_risk": unit.get("formula_risk", ""),
        "planner_confidence": unit.get("planner_confidence", ""),
        "review_status": unit.get("review_status", "pending"),
        "risk_flags": unit.get("risk_flags", []),
        "skip_reason": unit.get("skip_reason", ""),
        "summary": unit_plan._unit_one_line_summary(unit, summaries),
        "auto_acceptable": unit_plan.is_low_risk_text_unit(unit),
    }


def _pages_label(pages: list[int]) -> str:
    if len(pages) == 2 and pages[0] <= pages[1]:
        return f"{pages[0]}–{pages[1]}"
    return ",".join(str(p) for p in pages)


_PLAN_OPS = {"accept", "skip", "edit_title", "edit_pages", "merge_prev", "reset"}


def apply_plan_op(book_id: str, op: str, unit_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    root = book_root(book_id)
    path = _candidates_path(root)
    plan = unit_plan.load_yaml(path)
    units = plan.get("units", [])
    payload = payload or {}
    index = next((i for i, u in enumerate(units) if u.get("unit_id") == unit_id), None)
    if index is None:
        raise WebError(f"未找到 unit: {unit_id}")
    unit = units[index]

    if op == "accept":
        unit["review_status"] = "accepted"
        if not unit.get("include"):
            unit["include"] = True
            unit.pop("skip_reason", None)
            # 复原 output_targets：skip 时会清空它，而 include=true 的 unit 必须含
            # section-lesson，否则 finalize 校验报错。
            if "section-lesson" not in (unit.get("output_targets") or []):
                unit["output_targets"] = ["section-lesson"]
    elif op == "skip":
        unit["include"] = False
        unit["skip_reason"] = (payload.get("reason") or "人工跳过").strip()
        unit["review_status"] = "skipped"
        unit["output_targets"] = []
    elif op == "edit_title":
        title = (payload.get("title") or "").strip()
        if not title:
            raise WebError("标题不能为空")
        unit["title"] = title
        unit["review_status"] = "edited"
    elif op == "edit_pages":
        try:
            pages = unit_plan.parse_pages_input(payload.get("pages", ""))
        except ValueError as exc:
            raise WebError(f"页码格式错误: {exc}")
        if not pages:
            raise WebError("页码不能为空")
        unit.setdefault("source_scope", {})["pages"] = pages
        unit["review_status"] = "edited"
    elif op == "merge_prev":
        if index == 0:
            raise WebError("第一个 unit 不能并入前项")
        unit_plan.merge_unit_into_previous(units, index)
    elif op == "reset":
        unit["review_status"] = "pending"
    else:
        raise WebError(f"不支持的操作: {op}")

    unit_plan.write_yaml(path, plan)
    return get_candidates(book_id)


def auto_resolve_candidates(book_id: str) -> dict[str, Any]:
    """对候选执行与 CLI 一致的自动判定：include=false+skip_reason→skipped；
    纯文字低风险高置信→accepted。把真正需要人判断的留给前端逐个处理。"""
    root = book_root(book_id)
    path = _candidates_path(root)
    plan = unit_plan.load_yaml(path)
    auto_skipped = auto_accepted = 0
    for unit in plan.get("units", []):
        if unit.get("review_status") in {"accepted", "edited", "skipped"}:
            continue
        if not unit.get("include") and unit.get("skip_reason"):
            unit["review_status"] = "skipped"
            auto_skipped += 1
        elif unit_plan.is_low_risk_text_unit(unit):
            unit["review_status"] = "accepted"
            auto_accepted += 1
    unit_plan.write_yaml(path, plan)
    result = get_candidates(book_id)
    result["auto_skipped"] = auto_skipped
    result["auto_accepted"] = auto_accepted
    return result


def finalize_plan(book_id: str) -> dict[str, Any]:
    """定稿：校验覆盖 + 全部 include 已审 → 写 config/semantic-unit-plan.yaml。"""
    root = book_root(book_id)
    path = _candidates_path(root)
    plan = unit_plan.load_yaml(path)
    total_pages = int(plan.get("total_pages") or 0)
    validation = unit_plan.validate_unit_plan(plan, total_pages)

    report_path = root / "pipeline-workspace" / "reports" / "unit-plan-validation.md"
    unit_plan.write_validation_report(report_path, "Unit Plan Validation", validation)

    if unit_plan._has_unreviewed_include_units(plan):
        return {"ok": False, "reason": "unreviewed", "message": "仍有未审批的 include unit",
                "validation": validation}
    if not validation["passed"]:
        return {"ok": False, "reason": "validation", "message": "覆盖校验未通过",
                "validation": validation}

    final = unit_plan.mark_plan_reviewed(plan, validation)
    unit_plan.write_yaml(root / "config" / "semantic-unit-plan.yaml", final)
    return {"ok": True, "validation": validation, "message": "已生成正式规划 semantic-unit-plan.yaml"}


# --------------------------------------------------------------------------- #
# Review-Queue 页内处理
# --------------------------------------------------------------------------- #

def _kb(root: Path) -> Path:
    return root / "study-kb"


def list_review_queue(book_id: str) -> list[dict[str, Any]]:
    root = book_root(book_id)
    queue_dir = _kb(root) / "Review-Queue"
    items = []
    for path in sorted(queue_dir.glob("*.md")) if queue_dir.exists() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        reason = ""
        for line in text.splitlines():
            m = re.match(r"^-?\s*reason:\s*(.+)$", line.strip())
            if m:
                reason = m.group(1).strip()
                break
        unit_id = path.stem
        draft = root / "pipeline-workspace" / "staging" / unit_id / "section-lesson-draft.md"
        items.append({
            "unit_id": unit_id,
            "reason": reason,
            "has_draft": draft.exists(),
        })
    return items


def get_unit_draft(book_id: str, unit_id: str) -> dict[str, Any]:
    root = book_root(book_id)
    unit_id = _safe_unit_id(unit_id)
    draft = root / "pipeline-workspace" / "staging" / unit_id / "section-lesson-draft.md"
    review = root / "pipeline-workspace" / "reviews" / unit_id / "review-report.md"
    return {
        "unit_id": unit_id,
        "draft": draft.read_text(encoding="utf-8", errors="replace") if draft.exists() else "",
        "review_report": review.read_text(encoding="utf-8", errors="replace") if review.exists() else "",
        "has_draft": draft.exists(),
    }


def save_unit_draft(book_id: str, unit_id: str, content: str) -> dict[str, Any]:
    root = book_root(book_id)
    unit_id = _safe_unit_id(unit_id)
    if not isinstance(content, str) or not content.strip():
        raise WebError("草稿内容为空")
    staging = root / "pipeline-workspace" / "staging" / unit_id
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "section-lesson-draft.md").write_text(content, encoding="utf-8")
    return {"ok": True, "unit_id": unit_id}


def publish_unit(book_id: str, unit_id: str) -> dict[str, Any]:
    """人工接受 Review-Queue 中的 unit：写 accept 决策 + 渲染讲义 + 移除队列提示 + 重建索引。"""
    root = book_root(book_id)
    unit_id = _safe_unit_id(unit_id)
    draft_path = root / "pipeline-workspace" / "staging" / unit_id / "section-lesson-draft.md"
    if not draft_path.exists():
        raise WebError(f"{unit_id} 无草稿可发布；请先编辑保存或重跑该 unit")

    plan = unit_plan.load_yaml(root / "config" / "semantic-unit-plan.yaml")
    unit = unit_plan.unit_by_id(plan, unit_id)
    if not unit:
        raise WebError(f"正式规划中找不到 {unit_id}")

    # 写人工 accept 决策，使索引重建时 _write_lessons 会保留这篇讲义
    review_dir = root / "pipeline-workspace" / "reviews" / unit_id
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "review-decision.yaml").write_text(
        yaml.dump({
            "unit_id": unit_id,
            "decision": "accept",
            "confidence": "medium",
            "reviewer": "human-web",
            "notes": "人工在前端接受",
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # 移除托管的 Review-Queue 提示
    queue_note = _kb(root) / "Review-Queue" / f"{unit_id}.md"
    if queue_note.exists():
        head = queue_note.read_text(encoding="utf-8", errors="replace")[:1000]
        if "managed_by: pipeline" in head:
            queue_note.unlink()

    rebuild_indexes(book_id)
    return {"ok": True, "unit_id": unit_id}


def rebuild_indexes(book_id: str) -> dict[str, Any]:
    root = book_root(book_id)
    import memory_store
    from obsidian_indexes import build_obsidian_indexes

    plan = unit_plan.load_yaml(root / "config" / "semantic-unit-plan.yaml")
    memory = memory_store.reconstruct_memory_from_db(root)
    build_obsidian_indexes(root, plan=plan, memory=memory)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# study-kb 浏览
# --------------------------------------------------------------------------- #

_KB_CATEGORIES = [
    "Section-Lessons", "Review-Queue", "Concept-Cards", "Glossary", "Symbols",
    "Formula-Ledger", "Claims", "Questions", "Learning-Maps", "Source-QA", "Dashboards",
]
_UNIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _safe_unit_id(unit_id: str) -> str:
    if not isinstance(unit_id, str) or not _UNIT_ID_RE.match(unit_id):
        raise WebError(f"非法 unit id: {unit_id!r}")
    return unit_id


def kb_tree(book_id: str) -> dict[str, Any]:
    root = book_root(book_id)
    kb = _kb(root)
    tree = []
    for category in _KB_CATEGORIES:
        directory = kb / category
        files = sorted(p.name for p in directory.glob("*.md")) if directory.exists() else []
        tree.append({"category": category, "count": len(files), "files": files})
    home = kb / "Home.md"
    return {"book_id": book_id, "categories": tree, "has_home": home.exists()}


def kb_file(book_id: str, relpath: str) -> dict[str, Any]:
    root = book_root(book_id)
    kb = _kb(root).resolve()
    target = (kb / relpath).resolve()
    # 路径逃逸防护：必须落在 study-kb 内。用 is_relative_to 按路径分量判断，
    # 避免 startswith 的兄弟目录前缀绕过（如 study-kb-evil）。
    if not target.is_relative_to(kb) or target.suffix != ".md":
        raise WebError("非法路径")
    if not target.exists():
        raise WebError(f"文件不存在: {relpath}")
    return {"path": relpath, "markdown": target.read_text(encoding="utf-8", errors="replace")}
