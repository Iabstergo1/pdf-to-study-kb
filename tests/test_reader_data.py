import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mdpage
import reader_data
import state_store
import content_review
from uuid import uuid4


def fixture(root):
    file = root / "wiki/domains/demo/concepts/a.md"
    mdpage.write_page(file, {"type": "concept", "status": "published", "source_refs": ["mysql"]}, "正文")
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "mysql", domain="demo", fmt="pdf")
    return file


def test_date_prefixed_report_and_ledger_survive_missing_report(tmp_path):
    file = fixture(tmp_path)
    report = tmp_path / "pipeline-workspace/reports/kb-qa/2026-07-20-mysql-final.md"
    report.parent.mkdir(parents=True)
    report.write_text("# 整书检查\n\n正文", encoding="utf-8")
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(yaml.safe_dump({"pages": {file.relative_to(tmp_path / "wiki").as_posix(): [
        {"date": "2026-07-20", "outcome": "no-findings", "scope": "whole-source", "report": "kb-qa/" + report.name}]}}), encoding="utf-8")
    assert len(reader_data.reports(tmp_path, "mysql")) == 1
    review = reader_data.review_status(tmp_path, "mysql")
    assert review["historical"] == 1 and review["current"] == 0 and review["unconfirmed"] == 1
    report.unlink()
    review = reader_data.review_status(tmp_path, "mysql")
    assert review["historical"] == 1 and review["missing_reports"] == 1


def test_report_body_mention_does_not_establish_source_association(tmp_path):
    fixture(tmp_path)
    report = tmp_path / "pipeline-workspace/reports/unrelated.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Other report\n\nmysql <script>alert(1)</script>", encoding="utf-8")
    assert reader_data.reports(tmp_path, "mysql") == []
    output = reader_data.read(tmp_path, "report", {"path": "pipeline-workspace/reports/unrelated.md"})
    assert "<script>" not in output["html"]


@pytest.mark.parametrize("report_exists", [True, False])
def test_shared_pages_do_not_transfer_a_report_or_review_to_a_new_source(tmp_path, report_exists):
    file = fixture(tmp_path)
    mdpage.write_page(file, {"type": "concept", "status": "published", "source_refs": ["mysql", "new-book"]}, "正文")
    overview = tmp_path / "wiki/overview.md"
    mdpage.write_page(overview, {"type": "overview", "status": "published", "source_refs": ["mysql", "new-book"]}, "导航")
    rel = file.relative_to(tmp_path / "wiki").as_posix()
    report = tmp_path / "pipeline-workspace/reports/kb-qa/2026-07-20-mysql-final.md"
    if report_exists:
        report.parent.mkdir(parents=True)
        report.write_text("# 检查 mysql\n", encoding="utf-8")
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    event = {"date": "2026-07-20", "outcome": "no-findings", "scope": "whole-source", "report": "kb-qa/" + report.name}
    ledger.write_text(yaml.safe_dump({"pages": {rel: [event], "overview.md": [event]}}), encoding="utf-8")
    before = ledger.read_bytes()

    assert reader_data.reports(tmp_path, "new-book") == []
    reports = reader_data.reports(tmp_path, "mysql")
    assert len(reports) == 1 and reports[0]["sources"] == ["mysql"]
    assert reports[0]["exists"] is report_exists
    old = reader_data.review_status(tmp_path, "mysql")
    assert old["historical"] == 1 and old["unconfirmed"] == 1
    new = reader_data.review_status(tmp_path, "new-book")
    assert new["total"] == 1 and new["historical"] == 0 and new["whole"] == 0
    assert len(new["related_pages"]) == 1
    assert new["related_pages"][0]["sources"] == ["mysql"]
    assert new["related_pages"][0]["available"] is report_exists
    assert ledger.read_bytes() == before


def test_explicit_report_scope_wins_over_name_and_shared_page_sources(tmp_path):
    file = fixture(tmp_path)
    mdpage.write_page(file, {"type": "concept", "status": "published", "source_refs": ["mysql", "new-book", "third-book"]}, "正文")
    report = tmp_path / "pipeline-workspace/reports/kb-qa/2026-new-book.md"
    report.parent.mkdir(parents=True)
    report.write_text("---\nsources: [mysql, third-book]\n---\n# 多资料检查\n", encoding="utf-8")
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(yaml.safe_dump({"pages": {file.relative_to(tmp_path / "wiki").as_posix(): [
        {"date": "2026-07-20", "report": "kb-qa/" + report.name, "scope": "whole-source"}]}}), encoding="utf-8")
    assert reader_data.reports(tmp_path, "new-book") == []
    assert reader_data.reports(tmp_path, "mysql")[0]["sources"] == ["mysql", "third-book"]
    assert reader_data.review_status(tmp_path, "third-book")["historical"] == 1


def test_review_keeps_source_specific_event_when_another_source_review_is_newer(tmp_path):
    file = fixture(tmp_path)
    mdpage.write_page(file, {"type": "concept", "status": "published", "source_refs": ["mysql", "new-book"]}, "正文")
    rel = file.relative_to(tmp_path / "wiki").as_posix()
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    events = [
        {"date": "2026-07-20", "source_id": "mysql", "report": "kb-qa/run-one.md", "scope": "whole-source", "outcome": "no-findings"},
        {"date": "2026-07-21", "source_id": "new-book", "report": "kb-qa/run-two.md", "scope": "sample", "outcome": "findings-open"},
    ]
    ledger.write_text(yaml.safe_dump({"pages": {rel: events}}), encoding="utf-8")
    review = reader_data.review_status(tmp_path, "mysql")
    assert review["historical"] == 1 and review["findings_open"] == 0 and review["whole"] == 1
    assert review["pages"][0]["date"] == "2026-07-20"
    assert review["related_pages"][0]["sources"] == ["new-book"]
    assert reader_data.reports(tmp_path, "mysql")[0]["path"].endswith("run-one.md")


def test_unscoped_history_remains_visible_without_claiming_source_review(tmp_path):
    file = fixture(tmp_path)
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(yaml.safe_dump({"pages": {file.relative_to(tmp_path / "wiki").as_posix(): [
        {"date": "2026-07-20", "report": "kb-qa/domain-review.md", "scope": "whole-source", "outcome": "no-findings"}]}}), encoding="utf-8")
    review = reader_data.review_status(tmp_path, "mysql")
    assert review["historical"] == 0
    assert review["related_pages"][0]["sources"] == []
    assert reader_data.reports(tmp_path, "mysql") == []
    assert len(reader_data.reports(tmp_path)) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_current_review_requires_the_recorded_page_revision(tmp_path, changed):
    file = fixture(tmp_path)
    rel = file.relative_to(tmp_path / "wiki").as_posix()
    revision = reader_data.site_exporter._vault_pages(tmp_path / "wiki")[0]["revision"]
    ledger = tmp_path / "wiki/Review-Queue/content-review-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(yaml.safe_dump({"pages": {rel: [
        {"date": "2026-07-20", "source_id": "mysql", "revision": revision, "report": "kb-qa/run.md"}]}}), encoding="utf-8")
    if changed:
        file.write_text(file.read_text(encoding="utf-8") + "\n补充内容", encoding="utf-8")
    review = reader_data.review_status(tmp_path, "mysql")
    assert review["current"] == int(not changed) and review["changed"] == int(changed)
    assert review["unconfirmed"] == 0 and review["missing_reports"] == 1


def test_source_page_renders_without_changing_original(tmp_path):
    import fitz
    fixture(tmp_path)
    original = tmp_path / "original.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 30), "Original evidence")
        doc.save(original)
    before = original.read_bytes()
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    state_store.record_artifact(db, "mysql", kind="raw_source", path=str(original), sha256=hashlib.sha256(before).hexdigest())
    stage = tmp_path / "pipeline-workspace/staging/mysql"
    stage.mkdir(parents=True)
    (stage / "blocks.jsonl").write_text(json.dumps({"page": 1, "text": "Original evidence", "block_id": "b1"}) + "\n")
    image = reader_data.source_page(tmp_path, "mysql", 1, True)
    assert image["available"] and image["data"].startswith("iVBOR")
    assert reader_data.source_page(tmp_path, "mysql", 1)["text"] == "Original evidence"
    assert original.read_bytes() == before
    archived = tmp_path / "wiki/assets/mysql/p0001.png"
    archived.parent.mkdir(parents=True)
    import base64
    archived.write_bytes(base64.b64decode(image["data"]))
    original.unlink()
    assert reader_data.source_page(tmp_path, "mysql", 1, True) == image
    with pytest.raises(ValueError):
        reader_data.published_page(tmp_path, "../../secrets.md")


def review_fixture(root):
    file = fixture(root)
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    with state_store.connect(db) as con:
        con.execute("UPDATE sources SET current_stage='lint',current_status='published'")
    evidence = root / "pipeline-workspace/staging/mysql/source.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("原文证据", encoding="utf-8")
    return file, db


def begin_review(root, *, method="full-page", parent=None, pages=None):
    value = {"run_id": str(uuid4()), "source": "mysql", "method": method}
    if parent:
        value["parent_id"] = parent
    if pages is not None:
        value["pages"] = pages
    return content_review.execute(root, "begin", value)


def review_manifest(root, batch, findings=False):
    pages = []
    for path in batch["pages"]:
        receipt = content_review.execute(root, "read", {"run_id": batch["run_id"], "path": path})
        pages.append({"path": path, "read_id": receipt["read_id"], "outcome": "findings" if findings else "no-findings",
                      "checks": [{"claim": "正文论断", "evidence": [{"path": "pipeline-workspace/staging/mysql/source.md", "locator": "第一段"}], "judgment": "核对说明"}],
                      "findings": [{"id": "f" + str(len(pages)), "reason": "待修复断言"}] if findings else []})
    return {"format": 1, "run_id": batch["run_id"], "summary": "限定范围的核对结果", "pages": pages}


def test_review_read_snapshot_is_immutable_and_repair_does_not_launder_revision(tmp_path):
    file, _ = review_fixture(tmp_path)
    before = file.read_bytes()
    run = begin_review(tmp_path)
    manifest = review_manifest(tmp_path, run)
    file.write_bytes(before + "\n后来修复".encode())
    content_review.execute(tmp_path, "register", manifest)
    batch = content_review.batches(tmp_path)[0]
    assert batch["pages"][0]["revision"] == hashlib.sha256(before).hexdigest()
    assert batch["pages"][0]["version"] == "changed" and not batch["can_accept"]
    with pytest.raises(ValueError, match="changed"):
        content_review.execute(tmp_path, "accept", {"run_id": batch["id"], "token": batch["token"], "confirmed": True})
    recheck = begin_review(tmp_path, parent=run["run_id"])
    content_review.execute(tmp_path, "register", review_manifest(tmp_path, recheck))
    batch = content_review.batches(tmp_path)[0]
    assert batch["can_accept"] and len(batch["runs"]) == 2
    request = {"run_id": batch["id"], "token": batch["token"], "confirmed": True}
    assert not content_review.execute(tmp_path, "accept", request)["duplicate"]
    assert content_review.execute(tmp_path, "accept", request)["duplicate"]
    file.write_bytes(before + "\n共享页另一本书的修改".encode())
    batch = content_review.batches(tmp_path)[0]
    assert not batch["accepted"] and len(batch["acceptances"]) == 1
    assert not batch["acceptances"][0]["current"]


def test_manifest_registration_is_atomic_idempotent_and_keeps_original_findings(tmp_path):
    file, db = review_fixture(tmp_path)
    before = file.read_bytes()
    run = begin_review(tmp_path)
    manifest = review_manifest(tmp_path, run, findings=True)
    with state_store.connect(db) as con:
        con.execute("CREATE TRIGGER fail_findings BEFORE INSERT ON content_review_findings BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        content_review.execute(tmp_path, "register", manifest)
    assert not state_store.list_review_proposals(db)
    assert not content_review.batches(tmp_path)[0]["completed_at"]
    with state_store.connect(db) as con:
        con.execute("DROP TRIGGER fail_findings")
    content_review.execute(tmp_path, "register", manifest)
    assert content_review.execute(tmp_path, "register", manifest)["duplicate"]
    assert len(state_store.list_review_proposals(db)) == 1
    assert not content_review.batches(tmp_path)[0]["can_accept"]
    import user_study
    proposal = state_store.list_review_proposals(db)[0]
    user_study.write(tmp_path, "proposal-decision", {"id": proposal["id"], "expected_status": "open", "decision": "resolved", "reason": "已核查", "verified": True})
    assert not content_review.batches(tmp_path)[0]["can_accept"]  # still needs content recheck
    recheck = begin_review(tmp_path, parent=run["run_id"])
    content_review.execute(tmp_path, "register", review_manifest(tmp_path, recheck))
    assert content_review.batches(tmp_path)[0]["can_accept"]
    original = content_review.rows(tmp_path, "content_review_runs", "WHERE id=?", (run["run_id"],))[0]
    assert json.loads(original["manifest_json"])["pages"][0]["outcome"] == "findings"
    assert len(reader_data.read(tmp_path, "proposals")[0]["history"]) == 1
    assert file.read_bytes() == before


def test_review_scope_method_and_receipts_cannot_be_forged(tmp_path):
    file, _ = review_fixture(tmp_path)
    run = begin_review(tmp_path)
    other = begin_review(tmp_path)
    manifest = review_manifest(tmp_path, run)
    manifest["run_id"] = other["run_id"]
    with pytest.raises(ValueError, match="receipt"):
        content_review.execute(tmp_path, "register", manifest)
    manifest["run_id"] = run["run_id"]
    saved = manifest["pages"]
    for pages in ([], saved + saved):
        with pytest.raises(ValueError, match="scope"):
            content_review.execute(tmp_path, "register", {**manifest, "pages": pages})
    with pytest.raises(ValueError, match="scope"):
        content_review.execute(tmp_path, "read", {"run_id": run["run_id"], "path": "../../secrets"})
    with pytest.raises(ValueError, match="explicit page list"):
        begin_review(tmp_path, method="sampled-claims")
    sample = begin_review(tmp_path, method="sampled-claims", pages=run["pages"])
    content_review.execute(tmp_path, "register", review_manifest(tmp_path, sample))
    assert content_review.batches(tmp_path)[0]["method"] == "sampled-claims"
    assert reader_data.review_status(tmp_path, "mysql")["whole"] == 0


def test_resuming_review_keeps_the_original_scope_after_source_grows(tmp_path):
    review_fixture(tmp_path)
    run = begin_review(tmp_path)
    mdpage.write_page(tmp_path / "wiki/topics/new.md", {"type": "topic", "status": "published", "source_refs": ["mysql"]}, "New page")
    resumed = content_review.execute(tmp_path, "begin", {"run_id": run["run_id"], "source": "mysql", "method": "full-page"})
    assert resumed == run and resumed["source_total"] == 1


def test_registered_report_survives_export_failure_and_does_not_acquire_shared_sources(tmp_path, monkeypatch):
    file, _ = review_fixture(tmp_path)
    run = begin_review(tmp_path)
    manifest = review_manifest(tmp_path, run)
    original = content_review.materialize
    monkeypatch.setattr(content_review, "materialize", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))
    assert content_review.execute(tmp_path, "register", manifest)["exports_pending"]
    report = reader_data.reports(tmp_path, "mysql")[0]
    assert report["exists"]
    assert "限定范围" in reader_data.read(tmp_path, "report", {"path": report["path"]})["text"]
    meta, body = mdpage.read_page(file)
    mdpage.write_page(file, {**meta, "source_refs": ["mysql", "new-book"]}, body)
    assert not reader_data.reports(tmp_path, "new-book")
    assert reader_data.review_status(tmp_path, "new-book")["historical"] == 0
    assert len(reader_data.review_status(tmp_path, "new-book")["related_pages"]) == 1
    monkeypatch.setattr(content_review, "materialize", original)
    assert not content_review.execute(tmp_path, "register", manifest)["exports_pending"]
    assert (tmp_path / report["path"]).is_file()


def test_review_coverage_reads_sqlite_without_fabricating_a_yaml_ledger(tmp_path, monkeypatch, capsys):
    import pipeline
    review_fixture(tmp_path)
    run = begin_review(tmp_path)
    content_review.execute(tmp_path, "register", review_manifest(tmp_path, run))
    monkeypatch.setattr(pipeline, "_workspace_root", lambda: tmp_path)
    assert pipeline.cmd_review_coverage(None) == 0
    assert "已登记 1   未登记 0   覆盖率 100.0%" in capsys.readouterr().out
    assert not (tmp_path / "wiki/Review-Queue/content-review-ledger.yaml").exists()


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_old_findings_require_rule_specific_fresh_proof_and_human_confirmation(tmp_path, separator):
    file, db = review_fixture(tmp_path)
    rel = file.relative_to(tmp_path / "wiki").as_posix()
    for kind in ("placeholder-unfilled", "concepts-uncovered", "unaccounted-write", "content-qa"):
        state_store.add_review_proposal(db, "mysql", target_path=rel, kind=kind, reason="old finding")
    topic = tmp_path / "wiki/topics/demo.md"
    mdpage.write_page(topic, {"type": "topic", "status": "published", "source_refs": ["mysql"]}, f"[[{rel}]]")
    with state_store.connect(db) as con:
        con.execute("INSERT INTO work_orders VALUES ('mysql','unused','hash','[]','now',2)")
        con.execute("INSERT INTO ingest_progress(source_id,window_id,input_hash,status,write_set_json,round) VALUES ('mysql','w0068','h','finished',?,1)", (json.dumps([rel.replace('/', separator)]),))
    checks = content_review.execute(tmp_path, "checks", {"source": "mysql"})
    assert [c["passed"] for c in checks] == [True, True, False, False]
    with state_store.connect(db) as con:
        con.execute("UPDATE ingest_progress SET round=2")
    checks = content_review.execute(tmp_path, "checks", {})
    assert checks[2]["evidence"]["windows"] == ["w0068"]
    assert checks[2]["evidence"]["round"] == 2
    stage = tmp_path / "pipeline-workspace/staging/mysql"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "windows.jsonl").write_text(json.dumps({"window_id": "w0068", "page_start": 3, "page_end": 4}), encoding="utf-8")
    assert reader_data.evidence(tmp_path, rel)["sources"][0]["windows"] == [{"id": "w0068", "start": 3, "end": 4}]
    request = {"items": [{"id": c["id"], "token": c["token"]} for c in checks if c["passed"]]}
    with pytest.raises(ValueError, match="confirm"):
        content_review.execute(tmp_path, "close", request)
    topic.write_text(topic.read_text(encoding="utf-8") + "\nupdated", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        content_review.execute(tmp_path, "close", {**request, "confirmed": True})
    assert all(p["status"] == "open" for p in state_store.list_review_proposals(db))
    checks = content_review.execute(tmp_path, "checks", {})
    content_review.execute(tmp_path, "close", {"confirmed": True, "items": [{"id": c["id"], "token": c["token"]} for c in checks if c["passed"]]})
    assert [p["status"] for p in sorted(state_store.list_review_proposals(db), key=lambda p: p["id"])] == ["resolved"] * 3 + ["open"]
    assert len(content_review.rows(tmp_path, "proposal_decisions")) == 3
