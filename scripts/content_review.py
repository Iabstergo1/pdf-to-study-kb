"""Versioned content reviews and human acceptance, exposed only through pipeline.py.

The read receipt contains the exact bytes returned to the reviewer. Registration
never substitutes the current file for those bytes. SQLite commits the manifest,
readable report and findings together; report files are reproducible copies.
"""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import mdpage
import reader_data
import site_exporter
import state_store
import wiki_gate


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("A review run requires a UUID") from None


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing " + name)
    return value


def rows(root, table, where="", params=()):
    if not reader_data._rows(root, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)):
        return []
    return reader_data._rows(root, "SELECT * FROM " + table + " " + where, params)


def _pages(root, source):
    return {p["rel"]: p for p in site_exporter._vault_pages(Path(root) / "wiki")
            if source in p["source_refs"] and p["type"] in ("concept", "lesson", "topic", "comparison", "synthesis")}


def _run(con, run_id):
    row = con.execute("SELECT * FROM content_review_runs WHERE id=?", (_id(run_id),)).fetchone()
    if not row:
        raise ValueError("Unknown review run")
    return dict(row)


def _begin(con, root, value):
    run_id, source = _id(value.get("run_id")), value.get("source")
    _text(source, "source")
    info = reader_data._rows(root, "SELECT current_status FROM sources WHERE source_id=?", (source,))
    if not info or info[0]["current_status"] != "published":
        raise ValueError("Content review requires a published source")
    method = value.get("method")
    if method not in ("full-page", "sampled-claims"):
        raise ValueError("Choose full-page or sampled-claims")
    parent_id = value.get("parent_id")
    old = con.execute("SELECT * FROM content_review_runs WHERE id=?", (run_id,)).fetchone()
    if old:
        if (old["source_id"], old["parent_id"], old["method"]) != (source, parent_id, method) or "pages" in value and _json(sorted(value["pages"])) != old["scope_json"]:
            raise ValueError("This review ID already has a different scope")
        return {"run_id": run_id, "source": source, "method": method, "pages": json.loads(old["scope_json"]), "source_total": old["source_total"]}
    pages = _pages(root, source)
    if parent_id:
        parent = _run(con, parent_id)
        if parent["parent_id"] or not parent["completed_at"] or parent["source_id"] != source or parent["method"] != method:
            raise ValueError("Recheck must reference a completed original batch with the same source and method")
        selected = value.get("pages", json.loads(parent["scope_json"]))
        if not isinstance(selected, list) or any(not isinstance(p, str) for p in selected) or not set(selected) <= set(json.loads(parent["scope_json"])):
            raise ValueError("Recheck pages must belong to the original batch")
    else:
        selected = value.get("pages", sorted(pages) if method == "full-page" else [])
    if not isinstance(selected, list) or not selected or any(not isinstance(p, str) for p in selected):
        raise ValueError("Select the knowledge pages to review; sampling requires an explicit page list")
    if len(selected) != len(set(selected)) or not set(selected) <= pages.keys():
        raise ValueError("Review scope must contain unique published pages attributed to this source")
    scope = _json(sorted(selected))
    con.execute("INSERT INTO content_review_runs VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (run_id, source, parent_id, method, scope, len(pages), _now()))
    return {"run_id": run_id, "source": source, "method": method, "pages": sorted(selected), "source_total": len(pages)}


def _read(con, root, value):
    run = _run(con, value.get("run_id"))
    if run["completed_at"]:
        raise ValueError("This review is registered; start a recheck to read another version")
    rel = value.get("path")
    if rel not in json.loads(run["scope_json"]):
        raise ValueError("Page is outside the frozen review scope")
    file, _, _ = reader_data.published_page(root, rel)
    data = file.read_bytes()
    if len(data) > 1_000_000:
        raise ValueError("Page exceeds the review tool's size limit")
    receipt = {"read_id": str(uuid4()), "run_id": run["id"], "path": rel,
               "revision": _hash(data), "read_at": _now(), "content": data.decode("utf-8")}
    con.execute("INSERT INTO content_review_reads VALUES (?,?,?,?,?,?)", tuple(receipt.values()))
    return receipt


def _manifest(con, root, run, value):
    if value.get("format") != 1 or value.get("run_id") != run["id"]:
        raise ValueError("Expected review manifest format=1 and this run_id")
    _text(value.get("summary"), "review summary")
    entries = value.get("pages")
    if not isinstance(entries, list) or any(not isinstance(p, dict) for p in entries):
        raise ValueError("Manifest requires page results")
    paths = [p.get("path") for p in entries]
    if any(not isinstance(p, str) for p in paths) or sorted(paths) != json.loads(run["scope_json"]):
        raise ValueError("Register exactly the frozen scope once each; partial work remains incomplete")
    normalized, finding_ids = [], set()
    for entry in entries:
        receipt = con.execute("SELECT * FROM content_review_reads WHERE id=? AND run_id=? AND path=?",
                              (entry.get("read_id"), run["id"], entry["path"])).fetchone()
        if not receipt:
            raise ValueError("Every result must reference its own review-read receipt")
        checks = entry.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("Every page requires explicit claims, evidence and judgements")
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError("Invalid check")
            _text(check.get("claim"), "checked claim/scope")
            _text(check.get("judgment"), "judgment")
            evidence = check.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("Every check requires evidence references")
            for ref in evidence:
                if not isinstance(ref, dict):
                    raise ValueError("Invalid evidence reference")
                location = _text(ref.get("path"), "evidence path or URL")
                _text(ref.get("locator"), "evidence locator")
                if location.startswith("https://"):
                    continue
                file = (Path(root) / location).resolve()
                allowed = [Path(root) / p for p in ("books", "wiki", "pipeline-workspace/staging")]
                if not any(file.is_relative_to(p.resolve()) for p in allowed) or not file.is_file():
                    raise ValueError("Evidence must be an accessible source/knowledge file or an HTTPS citation")
        findings = entry.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("findings must be a list")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("Invalid finding")
            fid = _text(finding.get("id"), "finding id")
            _text(finding.get("reason"), "finding reason")
            if fid in finding_ids:
                raise ValueError("Finding IDs must be unique within the batch")
            finding_ids.add(fid)
        outcome = entry.get("outcome")
        if outcome not in ("no-findings", "findings", "inconclusive") or (outcome == "findings") != bool(findings):
            raise ValueError("Page outcome must agree with its findings")
        normalized.append({"path": entry["path"], "read_id": receipt["id"], "revision": receipt["revision"],
                           "read_at": receipt["read_at"], "outcome": outcome, "checks": checks, "findings": findings})
    return {"format": 1, "run_id": run["id"], "source_id": run["source_id"], "parent_id": run["parent_id"],
            "method": run["method"], "source_total_at_start": run["source_total"], "summary": value["summary"],
            "pages": sorted(normalized, key=lambda p: p["path"])}


def report_paths(run_id):
    base = "pipeline-workspace/reports/kb-qa/" + run_id
    return [base + ".md", base + ".json"]


def _report(manifest):
    lines = ["---", "source_id: " + manifest["source_id"], "---", "# 内容审查 / Content review",
             "", manifest["summary"], "", f"Method: {manifest['method']}",
             f"Knowledge pages: {len(manifest['pages'])} / {manifest['source_total_at_start']} (at start)",
             "Scope counts knowledge pages, not original PDF pages. Sampling does not certify a whole page.",
             "Model findings remain historical; dispositions and human acceptance are separate records."]
    for page in manifest["pages"]:
        lines += ["", "## " + page["path"], f"Read: {page['read_at']} · {page['revision']}", "Outcome: " + page["outcome"]]
        for check in page["checks"]:
            lines += ["", "- " + check["claim"], "  " + check["judgment"]]
            lines += ["  Evidence: " + ref["path"] + " · " + ref["locator"] for ref in check["evidence"]]
        lines += ["- Finding " + f["id"] + ": " + f["reason"] for f in page["findings"]]
    return "\n".join(lines) + "\n"


def _register(con, root, value):
    run = _run(con, value.get("run_id"))
    manifest = _manifest(con, root, run, value)
    payload = _json(manifest)
    if run["completed_at"]:
        if run["manifest_json"] != payload:
            raise ValueError("Registered results are immutable; create a recheck")
        return {"run_id": run["id"], "duplicate": True}
    at = _now()
    for page in manifest["pages"]:
        for finding in page["findings"]:
            cur = con.execute("INSERT INTO review_proposals(source_id,target_path,kind,diff_path,reason,created_at,status) VALUES (?,?,?,?,?,?,?)",
                              (run["source_id"], page["path"], "content-qa", report_paths(run["id"])[0], finding["reason"], at, "open"))
            con.execute("INSERT INTO content_review_findings VALUES (?,?,?,?)",
                        (run["id"], finding["id"], cur.lastrowid, page["path"]))
    con.execute("UPDATE content_review_runs SET completed_at=?,manifest_json=?,report_md=? WHERE id=?",
                (at, payload, _report(manifest), run["id"]))
    return {"run_id": run["id"], "duplicate": False}


def materialize(root, run):
    paths = report_paths(run["id"])
    for rel, text in zip(paths, (run["report_md"], run["manifest_json"])):
        file = (Path(root) / rel).resolve()
        if not file.is_relative_to(Path(root).resolve()):
            raise ValueError("Report path escapes workspace")
        file.parent.mkdir(parents=True, exist_ok=True)
        temp = file.with_suffix(file.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(file)
    return paths


def batches(root, source=None, con=None):
    read = (lambda table: [dict(r) for r in con.execute("SELECT * FROM " + table)]) if con else (lambda table: rows(root, table))
    runs = read("content_review_runs")
    findings = read("content_review_findings")
    proposals = {p["id"]: p for p in read("review_proposals")}
    accepted = read("content_review_acceptances")
    current = {p["rel"]: p["revision"] for p in site_exporter._vault_pages(Path(root) / "wiki")}
    result = []
    for run in runs:
        if run["parent_id"] or source and run["source_id"] != source:
            continue
        scope = json.loads(run["scope_json"])
        chain = sorted([r for r in runs if r["id"] == run["id"] or r["parent_id"] == run["id"]],
                       key=lambda r: (r["completed_at"] or "", r["created_at"], r["id"]))
        latest = {}
        for child in chain:
            if child["completed_at"]:
                for page in json.loads(child["manifest_json"])["pages"]:
                    latest[page["path"]] = {**page, "run_id": child["id"], "date": child["completed_at"]}
        pages = [{**latest.get(p, {}), "path": p,
                  "version": "unreviewed" if p not in latest else "current" if latest[p]["revision"] == current.get(p) else "changed"}
                 for p in scope]
        affected = [p["path"] for p in pages if p["version"] != "current" or p.get("outcome") != "no-findings"]
        relevant = [p for p in proposals.values() if p["target_path"] in scope or not p["target_path"] and p["source_id"] == run["source_id"]]
        open_ids = sorted(p["id"] for p in relevant if p["status"] == "open")
        snapshot = {"run_id": run["id"], "pages": [{k: p.get(k) for k in ("path", "run_id", "revision", "version", "outcome")} for p in pages],
                    "proposals": [{"id": p["id"], "status": p["status"]} for p in sorted(relevant, key=lambda p: p["id"])]}
        token = _hash(snapshot)
        history = [dict(a, snapshot=json.loads(a["snapshot_json"])) for a in accepted if a["run_id"] == run["id"]]
        for a in history:
            del a["snapshot_json"]
            a["current"] = a["token"] == token
        result.append({"id": run["id"], "source": run["source_id"], "method": run["method"], "created_at": run["created_at"],
                       "completed_at": run["completed_at"], "source_total": run["source_total"], "scope_count": len(scope),
                       "pages": pages, "affected": affected, "open_ids": open_ids, "token": token,
                       "can_accept": bool(run["completed_at"]) and not affected and not open_ids,
                       "accepted": any(a["current"] for a in history), "acceptances": history, "snapshot": snapshot,
                       "runs": [{"id": c["id"], "completed_at": c["completed_at"], "report": report_paths(c["id"])[0]} for c in chain],
                       "finding_ids": [f["proposal_id"] for f in findings if f["run_id"] in {c["id"] for c in chain}]})
    return sorted(result, key=lambda r: r["created_at"], reverse=True)


def _accept(con, root, value):
    if value.get("confirmed") is not True:
        raise ValueError("Human batch confirmation is required")
    run_id = _id(value.get("run_id"))
    batch = next((b for b in batches(root, con=con) if b["id"] == run_id), None)
    if not batch or batch["token"] != value.get("token") or not batch["can_accept"]:
        raise ValueError("Review changed or has unresolved findings/pages; refresh and recheck before acceptance")
    if not batch["accepted"]:
        con.execute("INSERT INTO content_review_acceptances VALUES (?,?,?,?,?,?)",
                    (str(uuid4()), run_id, batch["token"], _json(batch["snapshot"]), _now(), str(value.get("note", ""))))
    return {"accepted": True, "run_id": run_id, "duplicate": batch["accepted"]}


def record_decision(con, proposal_id, decision, reason, at, evidence=None):
    con.execute("INSERT INTO proposal_decisions(proposal_id,decision,reason,decided_at,evidence_json) VALUES (?,?,?,?,?)",
                (proposal_id, decision, reason, at, _json(evidence or {})))


def proposal_checks(root, source=None, ids=None, con=None):
    proposals = [dict(p) for p in con.execute("SELECT * FROM review_proposals")] if con else reader_data._rows(root, "SELECT * FROM review_proposals")
    result, membership = [], None
    for item in proposals:
        if item["status"] != "open" or source and source != item["source_id"] or ids is not None and item["id"] not in ids:
            continue
        evidence, passed, reason = {}, False, "此类问题需人工或模型复查，不能由结构检查确认。"
        rel, kind = item["target_path"], item["kind"]
        if kind in ("placeholder-unfilled", "concepts-uncovered", "unaccounted-write"):
            try:
                file, meta, body = reader_data.published_page(root, rel)
                evidence = {"path": rel, "revision": _hash(file.read_bytes())}
                if kind == "placeholder-unfilled":
                    passed = meta.get("type") in wiki_gate._PLACEHOLDER_TYPES and not wiki_gate._PLACEHOLDER.search(body)
                    reason = "已按原占位符规则复检：" + ("当前发布页未检出未填写占位符。" if passed else "仍存在占位符或页面类型不符。")
                elif kind == "concepts-uncovered":
                    from graph_model import topic_membership
                    if membership is None:
                        nodes = {p: n for p, n in wiki_gate._membership_nodes(Path(root) / "wiki").items()
                                 if mdpage.read_page(Path(root) / "wiki" / p)[0].get("status") == "published"}
                        membership, _ = topic_membership(nodes)
                    topics = sorted(t for t, members in membership.items() if rel in members)
                    evidence["topics"] = [{"path": t, "revision": _hash((Path(root) / "wiki" / t).read_bytes())} for t in topics]
                    passed = meta.get("type") == "concept" and bool(topics)
                    reason = "已按主题收录规则复检：" + ("；".join(topics) if passed else "当前概念仍未被主题页收录。")
                else:
                    orders = reader_data._rows(root, "SELECT round FROM work_orders WHERE source_id=?", (item["source_id"],))
                    round_id = orders[0]["round"] if orders else None
                    windows = reader_data._rows(root, "SELECT window_id,round,status,write_set_json FROM ingest_progress WHERE source_id=? AND round=? AND status='finished'", (item["source_id"], round_id))
                    evidence.update(round=round_id, windows=[w["window_id"] for w in windows if rel.replace("\\", "/") in
                                    [p.replace("\\", "/") for p in json.loads(w["write_set_json"] or "[]")]])
                    passed = round_id is not None and bool(evidence["windows"])
                    reason = f"当前轮次 {round_id} 的已完成窗口写集复核：" + ("、".join(evidence["windows"]) + " 已记录该页。" if passed else "未找到该页记账。")
            except ValueError as error:
                reason = str(error)
        proof = {"id": item["id"], "kind": kind, "status": item["status"], "passed": bool(passed), "evidence": evidence, "reason": reason}
        result.append({**proof, "token": _hash(proof)})
    return result


def _close(con, root, value):
    items = value.get("items")
    if value.get("confirmed") is not True or not isinstance(items, list) or not items:
        raise ValueError("Select findings and confirm that you checked their evidence")
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate finding selection")
    checks = {p["id"]: p for p in proposal_checks(root, ids=ids, con=con)}
    for selected in items:
        proof = checks.get(selected["id"])
        if not proof or not proof["passed"] or proof["token"] != selected.get("token"):
            raise ValueError("Finding or evidence changed; run the checks again")
    at = _now()
    for selected in items:
        proof = checks[selected["id"]]
        reason = str(selected.get("reason") or proof["reason"]).strip()
        con.execute("UPDATE review_proposals SET status='resolved' WHERE id=?", (selected["id"],))
        record_decision(con, selected["id"], "resolved", reason, at, proof["evidence"])
        con.execute("INSERT OR REPLACE INTO study_state VALUES (?,?,?)",
                    ("proposal:" + str(selected["id"]), _json({"decision": "resolved", "reason": reason, "at": at}), at))
    return {"closed": ids}


def execute(root, action, value):
    root = Path(root)
    if action == "status":
        return batches(root, value.get("source"))
    if action == "checks":
        return proposal_checks(root, value.get("source"), value.get("ids"))
    if action not in ("begin", "read", "register", "close", "accept"):
        raise ValueError("Unknown content review action")
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    state_store.init_db(db)
    with closing(state_store.connect(db)) as con:
        with con:
            con.execute("BEGIN IMMEDIATE")
            result = {"begin": _begin, "read": _read, "register": _register, "close": _close, "accept": _accept}[action](con, root, value)
        if action == "register":
            run = _run(con, result["run_id"])
            result["outputs"] = report_paths(run["id"])
            try:
                materialize(root, run)
                result["exports_pending"] = False
            except OSError:
                result["exports_pending"] = True
    return result
