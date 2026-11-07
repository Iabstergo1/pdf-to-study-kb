"""Read-only reader evidence, report associations and review history.

Section labels never stand in for PDF page numbers. Window write records provide
related reading context, not sentence-level evidence. Historical QA without a
content revision remains historical; this module does not rewrite the ledger.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import yaml
import mdpage
import site_exporter
import site_render
import source_artifacts
import wiki_gate


REPORT_ROOTS = ("wiki/Review-Queue", "pipeline-workspace/reports", "pipeline-workspace/query-sessions",
                "pipeline-workspace/skill-evolution/candidates")


def published_page(root, rel):
    vault = (Path(root) / "wiki").resolve()
    file = (vault / rel).resolve()
    if not file.is_relative_to(vault) or file.suffix != ".md" or rel.split("/")[0] in wiki_gate._EXCLUDE_TOP:
        raise ValueError("Unknown knowledge page")
    if not file.is_file():
        raise ValueError("Knowledge page not found")
    meta, body = mdpage.read_page(file)
    if meta.get("status") != "published":
        raise ValueError("Only published pages are available in the reader")
    return file, meta, body


def _rows(root, sql, params=()):
    db = Path(root) / "pipeline-workspace/state/study-kb.sqlite"
    if not db.is_file():
        return []
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def _ledger(root):
    import content_review
    file = Path(root) / "wiki/Review-Queue/content-review-ledger.yaml"
    value = (yaml.safe_load(file.read_text(encoding="utf-8")) or {}) if file.is_file() else {}
    if not isinstance(value, dict) or not isinstance(value.get("pages", {}), dict):
        raise ValueError("Content review ledger must contain a pages mapping")
    pages = value.get("pages", {})
    for run in content_review.rows(root, "content_review_runs", "WHERE completed_at IS NOT NULL ORDER BY completed_at,id"):
        manifest = json.loads(run["manifest_json"])
        for page in manifest["pages"]:
            pages.setdefault(page["path"], []).append({"date": run["completed_at"], "source_id": run["source_id"],
                "revision": page["revision"], "report": content_review.report_paths(run["id"])[0],
                "method": run["method"], "scope": "whole-source" if run["method"] == "full-page" and len(manifest["pages"]) == run["source_total"] else run["method"],
                "outcome": "findings-open" if page["outcome"] == "findings" else page["outcome"]})
    return pages


def _report_path(value):
    value = str(value or "").replace("\\", "/")
    return value if value.startswith(REPORT_ROOTS) else "pipeline-workspace/reports/" + value


def report_file(root, rel):
    root = Path(root).resolve()
    file = (root / rel).resolve()
    if not any(file.is_relative_to(root / directory) for directory in REPORT_ROOTS):
        raise ValueError("Report not accessible")
    if not file.is_file() or file.suffix not in (".md", ".json", ".txt") or file.stat().st_size > 2_000_000:
        raise ValueError("Report is missing or too large to preview")
    return file


def _kind(path):
    for part in ("kb-qa", "postmortem", "source-xray", "query-sessions", "skill-evolve", "Review-Queue"):
        if part in path.split("/"):
            return part
    return "report"


def _declared_sources(value):
    sources = [value.get("source_id"), value.get("source")]
    sources += value.get("sources", []) if isinstance(value.get("sources"), list) else []
    return sorted({s for s in sources if isinstance(s, str) and s})


def _legacy_report_sources(rel, known_sources):
    matches = [s for s in known_sources if re.search(r"(?:^|[/_.-])" + re.escape(s) + r"(?:$|[/_.-])", rel)]
    return sorted(s for s in matches if not any(s != other and s in other for other in matches))


def reports(root, source=None):
    root = Path(root)
    pages = site_exporter._vault_pages(root / "wiki")
    return _reports(root, pages, _ledger(root), source)


def _reports(root, pages, ledger, source=None):
    known_sources = {s for p in pages for s in p["source_refs"]}
    known_sources.update(r["source_id"] for r in _rows(root, "SELECT source_id FROM sources"))
    records = {}
    for directory in REPORT_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for file in base.rglob("*"):
            if not file.is_file() or not file.resolve().is_relative_to(root.resolve()) or file.suffix not in (".md", ".json", ".txt"):
                continue
            rel = file.relative_to(root).as_posix()
            record = {"path": rel, "title": file.stem, "kind": _kind(rel), "sources": [],
                      "associations": [], "exists": True, "pages": []}
            if file.stat().st_size < 2_000_000:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
                heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                if heading:
                    record["title"] = heading[1]
                meta = {}
                if text.startswith("---\n"):
                    end = text.find("\n---", 4)
                    if end != -1:
                        try:
                            candidate = yaml.safe_load(text[4:end])
                            meta = candidate if isinstance(candidate, dict) else {}
                        except yaml.YAMLError:
                            record["metadata_error"] = True
                elif file.suffix == ".json":
                    try:
                        candidate = json.loads(text)
                        meta = candidate if isinstance(candidate, dict) else {}
                    except ValueError:
                        pass
                record["sources"] = _declared_sources(meta)
                if record["sources"]:
                    record["associations"].append("metadata")
            # Legacy names are matched as delimited IDs, longest first. Report
            # body mentions do not establish which source was actually checked.
            matches = _legacy_report_sources(rel, known_sources)
            if matches and not record["sources"]:
                record["sources"] = matches
                record["associations"].append("legacy-name")
            records[rel] = record
    for page, events in ledger.items():
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict) or not event.get("report"):
                continue
            rel = _report_path(event["report"])
            record = records.setdefault(rel, {"path": rel, "title": Path(rel).stem, "kind": _kind(rel),
                                              "sources": _legacy_report_sources(rel, known_sources),
                                              "associations": [], "exists": False, "pages": []})
            record["pages"].append(page)
            # Page ownership can grow after a review. Only the recorded review
            # scope may associate that historical report with another source.
            declared = _declared_sources(event)
            if declared:
                record["sources"] = sorted(set(record["sources"]) | set(declared))
                if "review-scope" not in record["associations"]:
                    record["associations"].append("review-scope")
            if "page-history" not in record["associations"]:
                record["associations"].append("page-history")
    import content_review
    for run in content_review.rows(root, "content_review_runs", "WHERE completed_at IS NOT NULL"):
        md, manifest = content_review.report_paths(run["id"])
        records.pop(manifest, None)
        records[md] = {"path": md, "title": run["source_id"] + " · " + run["completed_at"] + " · " + run["method"],
                       "kind": "kb-qa", "sources": [run["source_id"]], "associations": ["registered-review"],
                       "exists": True, "manifest": manifest, "pages": json.loads(run["scope_json"])}
    return sorted((r for r in records.values() if not source or source in r["sources"]),
                  key=lambda r: r["path"], reverse=True)


def review_status(root, source):
    root = Path(root)
    all_pages = site_exporter._vault_pages(root / "wiki")
    pages = [p for p in all_pages
             if source in p["source_refs"] and p["type"] in ("concept", "lesson", "topic", "comparison", "synthesis")]
    ledger = _ledger(root)
    report_index = {r["path"]: r for r in _reports(root, all_pages, ledger)}
    result = {"total": len(pages), "historical": 0, "current": 0, "changed": 0,
              "unconfirmed": 0, "missing_reports": 0, "findings_open": 0, "sampled": 0, "whole": 0,
              "pages": [], "related_pages": []}
    for page in pages:
        events = [e for e in ledger.get(page["rel"], []) if isinstance(e, dict)]
        scoped = []
        for event in events:
            sources = _declared_sources(event) or report_index.get(_report_path(event.get("report")), {}).get("sources", [])
            scoped.append((event, sources))
        for own in (True, False):
            candidates = [(e, sources) for e, sources in scoped if (source in sources) == own]
            if not candidates:
                continue
            event, sources = max(enumerate(candidates), key=lambda pair: (str(pair[1][0].get("date", "")), pair[0]))[1]
            rel = _report_path(event.get("report"))
            available = bool(event.get("report")) and report_index.get(rel, {}).get("exists", False)
            reviewed_revision = event.get("revision") or event.get("content_hash") or event.get("sha256")
            version = "unconfirmed" if not reviewed_revision else "current" if reviewed_revision == page["revision"] else "changed"
            if own:
                result["historical"] += 1
                result[version] += 1
                result["missing_reports"] += int(not available)
                result["findings_open"] += int(event.get("outcome") == "findings-open")
                result["whole" if event.get("scope") == "whole-source" else "sampled"] += 1
            result["pages" if own else "related_pages"].append({
                "path": page["rel"], "date": str(event.get("date", "")), "scope": event.get("scope"),
                "outcome": event.get("outcome"), "version": version, "report": rel, "available": available, "sources": sources})
    result["proposals_open"] = len(_rows(root, "SELECT id FROM review_proposals WHERE source_id=? AND status='open'", (source,)))
    import content_review
    result["batches"] = content_review.batches(root, source)
    result["source"] = source
    return result


def source_info(root, source):
    if not re.fullmatch(r"[\w][\w.-]{0,127}", source):
        raise ValueError("Invalid source ID")
    row = _rows(root, "SELECT * FROM sources WHERE source_id=?", (source,))
    if not row:
        raise ValueError("Unknown source")
    raw = _rows(root, "SELECT path FROM artifacts WHERE source_id=? AND kind='raw_source' ORDER BY id DESC LIMIT 1", (source,))
    raw_path = (Path(root) / raw[0]["path"]).resolve() if raw else None
    stage = Path(root) / "pipeline-workspace/staging" / source
    blocks = source_artifacts.read_jsonl(stage / "blocks.jsonl") if (stage / "blocks.jsonl").is_file() else []
    count = max((b.get("page", 0) or 0 for b in blocks), default=0)
    return {**row[0], "raw_path": raw_path, "stage": stage, "blocks": blocks, "pages": count}


def evidence(root, rel):
    _file, meta, _body = published_page(root, rel)
    result = []
    refs = site_exporter._source_refs(meta.get("source_refs"))
    if meta.get("type") == "source" and meta.get("source_id") not in refs:
        refs += [meta["source_id"]] if meta.get("source_id") else []
    for source in refs:
        if not _rows(root, "SELECT source_id FROM sources WHERE source_id=?", (source,)):
            result.append({"source": source, "pages": 0, "windows": [], "precision": "source", "unavailable": True})
            continue
        info = source_info(root, source)
        rows = _rows(root, "SELECT p.* FROM ingest_progress p JOIN work_orders w ON p.source_id=w.source_id "
                     "WHERE p.source_id=? AND p.status='finished' AND p.round=w.round", (source,))
        ids = {r["window_id"] for r in rows if rel.replace("\\", "/") in
               [p.replace("\\", "/") for p in json.loads(r["write_set_json"] or "[]")]}
        file = info["stage"] / "windows.jsonl"
        windows = [{"id": w["window_id"], "start": w.get("page_start"), "end": w.get("page_end")}
                   for w in source_artifacts.read_jsonl(file) if w["window_id"] in ids] if file.is_file() else []
        result.append({"source": source, "format": info["format"], "pages": info["pages"], "windows": windows,
                       "precision": "window" if windows else "source", "unavailable": not info["blocks"]})
    return {"page": rel, "sources": result}


def source_page(root, source, page, image=False):
    info = source_info(root, source)
    page = int(page)
    if page < 1 or page > info["pages"]:
        raise ValueError("Source page is outside the available range")
    if not image:
        blocks = [b for b in info["blocks"] if b.get("page") == page]
        return {"source": source, "page": page, "pages": info["pages"],
                "text": "\n\n".join(b.get("text", "") for b in blocks),
                "blocks": [{"id": b.get("block_id"), "type": b.get("type"), "text": b.get("text", "")} for b in blocks]}
    import fitz
    directories = [info["stage"] / "assets", Path(root) / "wiki/assets" / source]
    existing = next((directory / f"p{page:04d}.png" for directory in directories
                     if (directory / f"p{page:04d}.png").is_file()
                     and (directory / f"p{page:04d}.png").resolve().is_relative_to(directory.resolve())
                     and directory.resolve().is_relative_to(Path(root).resolve())), None)
    if existing:
        data = existing.read_bytes()
    elif info["format"] == "pdf" and info["raw_path"] and info["raw_path"].is_file():
        with fitz.open(info["raw_path"]) as document:
            sheet = document.load_page(page - 1)
            scale = min(2, 1600 / sheet.rect.width)
            data = sheet.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
    else:
        return {"available": False}
    return {"available": True, "data": base64.b64encode(data).decode("ascii"), "mime": "image/png"}


def read(root, action, value=None):
    value = value or {}
    if action == "reports":
        return reports(root, value.get("source"))
    if action == "review":
        return review_status(root, value["source"])
    if action == "evidence":
        return evidence(root, value["page"])
    if action in ("source-page", "source-image"):
        return source_page(root, value["source"], value["page"], action == "source-image")
    if action in ("report", "render"):
        from markdown_it import MarkdownIt
        import content_review
        registered = [(r, p) for r in content_review.rows(root, "content_review_runs", "WHERE completed_at IS NOT NULL")
                      for p in content_review.report_paths(r["id"]) if p == value.get("path")] if action == "report" else []
        file = report_file(root, value["path"]) if action == "report" and not registered else None
        text = (registered[0][0]["manifest_json"] if value["path"].endswith(".json") else registered[0][0]["report_md"]) if registered else file.read_text(encoding="utf-8-sig") if file else str(value.get("text", ""))
        if action == "report" and not value["path"].endswith(".md"):
            from html import escape
            return {"text": text, "html": "<pre>" + escape(text) + "</pre>"}
        protected, math = site_render._protect_math(text)
        html = MarkdownIt("commonmark", {"html": False}).enable("table").render(protected)
        return {"text": text, "html": site_render._restore_math(html, math)}
    if action == "proposals":
        rows = _rows(root, "SELECT * FROM review_proposals ORDER BY id DESC")
        for row in rows:
            if row["kind"] == "reader-question":
                row["question"] = json.loads(row["reason"])
        state = _rows(root, "SELECT name FROM sqlite_master WHERE type='table' AND name='study_state'")
        decisions = {r["key"]: json.loads(r["value_json"]) for r in _rows(root, "SELECT * FROM study_state WHERE key LIKE 'proposal:%'")} if state else {}
        import content_review
        history = content_review.rows(root, "proposal_decisions", "ORDER BY id")
        return [{**r, "decision": decisions.get("proposal:" + str(r["id"])),
                 "history": [d for d in history if d["proposal_id"] == r["id"]]} for r in rows
                if not value.get("source") or value["source"] == r["source_id"]]
    if action == "freshness":
        file = Path(root) / "pipeline-workspace/exports/site-data/site.json"
        manifest = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
        before = {p["path"]: p.get("revision") for p in manifest.get("pages", [])}
        after = {p["rel"]: p["revision"] for p in site_exporter._vault_pages(Path(root) / "wiki")}
        return {"stale": before != after or manifest.get("format") != 2,
                "changed": sum(before.get(p) != after.get(p) for p in before.keys() | after.keys())}
    if action == "preferences":
        file = Path(root) / "wiki/_meta/purpose.md"
        return {"global": file.read_text(encoding="utf-8") if file.is_file() else "",
                "sources": [{"source": p.parent.name, "goal": p.read_text(encoding="utf-8")}
                            for p in sorted((Path(root) / "pipeline-workspace/staging").glob("*/learning-goal.md"))]}
    raise ValueError("Unknown reader action")
