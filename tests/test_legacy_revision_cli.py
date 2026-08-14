import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"
SOURCE = "demo-legacy-vault"
PAGE_REL = "domains/statistics/concepts/legacy-test.md"
PAGE_REL_2 = "domains/statistics/concepts/legacy-test-two.md"


def _run(args, workspace):
    env = {**os.environ, "STUDY_KB_ROOT": str(workspace), "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-B", str(PIPELINE), *args], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", env=env)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_state(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _write_page(path, *, managed_by="pipeline", status="published",
                source_refs=None, citations=None, suffix="", page_rel=PAGE_REL):
    slug = Path(page_rel).stem
    name = "Legacy test" if page_rel == PAGE_REL else slug.replace("-", " ").title()
    source_refs = source_refs if source_refs is not None else [
        {"source": SOURCE, "sections": [f"legacy-page:{page_rel}"]}]
    meta = {
        "aliases": [name],
        "canonical_id": f"concept.statistics.{slug}",
        "canonical_name": name,
        "citations": citations or [],
        "domain": "statistics",
        "managed_by": managed_by,
        "page_path": page_rel,
        "scope": "domain",
        "source_refs": source_refs,
        "status": status,
        "type": "concept",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    body = ("这是一张接管自旧知识库的统计学笔记，用于验证有限、可审计的修订授权。"
            "正文给出定义、适用边界、反例与检查方法，长度足以通过内容完整性门禁。"
            "本页没有原始书籍窗口，因此修订只能引用独立登记的外部证据。"
            "授权本身不代表内容已经获得书源归因，结论仍须逐条接受独立内容验收。"
            "候选页只有在全库结构检查、渲染安全检查和派生产物重建全部成功后才能发布。"
            + suffix)
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8", newline="\n")


def _existing_citation(*, suffix="one"):
    return {
        "source": f"existing-source-{suffix}",
        "title": f"Existing citation {suffix}",
        "url": f"https://example.test/existing-{suffix}",
    }


def _citation_sha256(citation):
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    return legacy_revision._citation_hash(citation)


def _adopted_workspace(tmp_path, *, page_rels=(PAGE_REL,), citations=None):
    vault = tmp_path / "wiki"
    pages = []
    for rel in page_rels:
        page = vault / rel
        _write_page(page, page_rel=rel, citations=citations)
        pages.append(page)
    archive = tmp_path / "legacy-baseline.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for rel, page in zip(page_rels, pages):
            zf.writestr(f"wiki/{rel}", page.read_bytes())
    result = _run([
        "adopt-vault", "--source", SOURCE,
        "--title", "Legacy baseline", "--domain", "demo",
        "--baseline-archive", str(archive), "--baseline-sha256", _sha(archive),
        "--apply",
    ], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return vault, pages[0]


def _request_page(page_rel, *, citation=None):
    citation = citation or {
        "source": "NIST/SEMATECH e-Handbook",
        "title": "Engineering Statistics Handbook",
        "url": "https://www.itl.nist.gov/div898/handbook/",
        "accessed_on": "2026-08-02",
        "locator": "Chapter 1",
    }
    return {
        "path": page_rel,
        "reason": "修正 legacy 页中的统计解释，并明确证据边界",
        "evidence": [{
            "citation": citation,
            "supports": "统计结论必须写明适用条件",
        }],
        "citation_removals": [],
    }


def _request(tmp_path, *, valid_until=None, pages=None, mode="edit",
             revert_operation=None):
    valid_until = valid_until or (
        datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    pages = pages or [_request_page(PAGE_REL)]
    payload = {"version": 1, "source_id": SOURCE, "valid_until": valid_until,
               "mode": mode, "pages": pages}
    if revert_operation:
        payload["revert_operation"] = revert_operation
    path = tmp_path / f"request-{mode}.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding="utf-8", newline="\n")
    return path


def _revise_args(request, *extra, apply=False):
    args = ["revise-adopted", "--source", SOURCE, "--request", str(request), *extra]
    if apply:
        args.append("--apply")
    return args


def _operation_dir(tmp_path):
    root = tmp_path / "pipeline-workspace" / "legacy-revisions" / SOURCE
    dirs = [p for p in root.iterdir() if p.is_dir()]
    prepared = []
    for op in dirs:
        events = sorted((op / "events").glob("*.json"))
        if events and events[-1].name.endswith("-prepared.json"):
            prepared.append(op)
    if len(prepared) == 1:
        return prepared[0]
    assert len(dirs) == 1
    return dirs[0]


def _edit_candidate(tmp_path, *, change_sources=False, add_citation=True,
                    page_rel=PAGE_REL, citation=None):
    op = _operation_dir(tmp_path)
    candidate = op / "candidate" / "files" / page_rel
    text = candidate.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    meta = yaml.safe_load(text[4:end + 1])
    body = text[end + 5:]
    meta["status"] = "proposed"
    if change_sources:
        meta["source_refs"].append({"source": "invented-book", "sections": ["p1"]})
    if add_citation:
        candidate_citation = dict(citation or {
            "source": "NIST/SEMATECH e-Handbook",
            "title": "Engineering Statistics Handbook",
            "url": "https://www.itl.nist.gov/div898/handbook/",
            "accessed_on": "2026-08-02",
            "locator": "Chapter 1",
            "supports": "统计结论必须写明适用条件",
        })
        candidate_citation.setdefault("supports", "统计结论必须写明适用条件")
        meta["citations"].append(candidate_citation)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    candidate.write_text(
        f"---\n{fm}---\n{body}\n本轮依据外部证据补充了适用条件。\n",
        encoding="utf-8", newline="\n")
    return candidate


def _two_page_request(tmp_path):
    second_citation = {
        "source": "Penn State Eberly College of Science",
        "title": "Conditions for inference",
        "url": "https://online.stat.psu.edu/stat200/lesson/4/4.2",
        "accessed_on": "2026-08-02",
        "locator": "Lesson 4.2",
    }
    pages = [
        _request_page(PAGE_REL),
        _request_page(PAGE_REL_2, citation=second_citation),
    ]
    return _request(tmp_path, pages=pages), second_citation


def _ledger_counts(tmp_path):
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        return tuple(con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE source_id=?", (SOURCE,)
        ).fetchone()[0] for table in ("work_orders", "ingest_progress", "window_reads"))
    finally:
        con.close()


def _source_state_contract(tmp_path, source=SOURCE):
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        source_row = con.execute(
            "SELECT source_id,domain,format,current_stage,current_status FROM sources "
            "WHERE source_id=?", (source,)).fetchone()
        stages = con.execute(
            "SELECT stage,status,input_hash,output_hash FROM source_stage_runs "
            "WHERE source_id=? ORDER BY id", (source,)).fetchall()
        artifacts = con.execute(
            "SELECT kind,path,sha256 FROM artifacts WHERE source_id=? ORDER BY id",
            (source,)).fetchall()
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {
            "source": dict(source_row), "stages": [dict(row) for row in stages],
            "artifacts": [dict(row) for row in artifacts],
            "tables": [row[0] for row in tables], "ledgers": _ledger_counts(tmp_path),
        }
    finally:
        con.close()


def test_dry_run_is_true_zero_write_and_prepare_keeps_live_and_main_state(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    adoption = tmp_path / "pipeline-workspace" / "adoptions" / SOURCE / "manifest.json"
    before = _tree_state(tmp_path)
    adoption_bytes = adoption.read_bytes()

    dry = _run(_revise_args(request), tmp_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "[dry-run]" in dry.stdout and "byte-zero-write" in dry.stdout
    assert _tree_state(tmp_path) == before

    live = page.read_bytes()
    prepared = _run(_revise_args(request, apply=True), tmp_path)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert "prepared" in prepared.stdout
    assert page.read_bytes() == live
    assert adoption.read_bytes() == adoption_bytes
    assert _ledger_counts(tmp_path) == (0, 0, 0)


@pytest.mark.parametrize(("mutator", "needle"), [
    (lambda p: p.update(valid_until="2020-01-01T00:00:00+00:00"), "expired"),
    (lambda p: p["pages"].append(dict(p["pages"][0])), "duplicate"),
    (lambda p: p["pages"][0].update(path="../escape.md"), "unsafe"),
    (lambda p: p["pages"][0]["evidence"][0]["citation"].update(url="http://bad"), "https"),
])
def test_invalid_requests_fail_without_operation(tmp_path, mutator, needle):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    payload = yaml.safe_load(request.read_text(encoding="utf-8"))
    mutator(payload)
    request.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_signing_rejects_unknown_citation_removal_with_actual_citation_details(tmp_path):
    citation = _existing_citation()
    _adopted_workspace(tmp_path, citations=[citation])
    requested_sha = hashlib.sha256(b"not-on-target").hexdigest()
    page = _request_page(PAGE_REL)
    page["citation_removals"] = [{
        "sha256": requested_sha,
        "reason": "该引用不再支撑当前结论",
    }]
    request = _request(tmp_path, pages=[page])

    result = _run(_revise_args(request, apply=True), tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert requested_sha in output
    assert _citation_sha256(citation) in output
    assert citation["source"] in output and citation["url"] in output
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_signing_accepts_citation_removal_subset_of_current_target(tmp_path):
    first = _existing_citation(suffix="first")
    second = _existing_citation(suffix="second")
    _adopted_workspace(tmp_path, citations=[first, second])
    page = _request_page(PAGE_REL)
    page["citation_removals"] = [{
        "sha256": _citation_sha256(first),
        "reason": "该引用不再支撑当前结论",
    }]
    request = _request(tmp_path, pages=[page])

    result = _run(_revise_args(request, apply=True), tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    authorization = json.loads(
        (_operation_dir(tmp_path) / "authorization.json").read_text(encoding="utf-8"))
    assert authorization["pages"][0]["citation_plan"]["remove_sha256"] == [
        _citation_sha256(first)]


def test_signing_rejects_citation_removal_when_current_target_has_none(tmp_path):
    _adopted_workspace(tmp_path)
    requested_sha = hashlib.sha256(b"not-on-target").hexdigest()
    page = _request_page(PAGE_REL)
    page["citation_removals"] = [{
        "sha256": requested_sha,
        "reason": "该引用不再支撑当前结论",
    }]
    request = _request(tmp_path, pages=[page])

    result = _run(_revise_args(request, apply=True), tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert requested_sha in output
    assert "actual citations: <none>" in output
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_human_page_and_page_outside_adoption_manifest_are_rejected(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    text = page.read_text(encoding="utf-8").replace(
        "managed_by: pipeline", "managed_by: human")
    page.write_text(text, encoding="utf-8")
    human = _run(_revise_args(request), tmp_path)
    assert human.returncode != 0 and "managed_by: human" in (human.stdout + human.stderr)

    extra = tmp_path / "wiki" / "domains" / "statistics" / "concepts" / "new.md"
    _write_page(extra)
    payload = yaml.safe_load(request.read_text(encoding="utf-8"))
    payload["pages"][0]["path"] = extra.relative_to(tmp_path / "wiki").as_posix()
    request.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    outside = _run(_revise_args(request), tmp_path)
    assert outside.returncode != 0
    assert "adoption manifest" in (outside.stdout + outside.stderr)


def test_candidate_contract_rejects_source_ref_change_and_missing_evidence(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path, change_sources=True)
    bad_sources = _run(_revise_args(request), tmp_path)
    assert bad_sources.returncode != 0
    assert "source_refs" in (bad_sources.stdout + bad_sources.stderr)

    candidate = _operation_dir(tmp_path) / "candidate" / "files" / PAGE_REL
    _write_page(candidate, status="proposed", suffix="\n已修改但没有登记 citation。")
    missing = _run(_revise_args(request), tmp_path)
    assert missing.returncode != 0
    assert "citation" in (missing.stdout + missing.stderr)


def test_finalize_publishes_batch_and_exact_replay_is_byte_mtime_noop(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)

    finalized = _run(_revise_args(request, apply=True), tmp_path)
    assert finalized.returncode == 0, finalized.stdout + finalized.stderr
    assert "completed" in finalized.stdout
    assert "status: published" in page.read_text(encoding="utf-8")
    assert "NIST/SEMATECH" in page.read_text(encoding="utf-8")
    assert _ledger_counts(tmp_path) == (0, 0, 0)
    op = _operation_dir(tmp_path)
    assert (op / "events" / "0001-prepared.json").is_file()
    assert (op / "events" / "0002-committing.json").is_file()
    assert (op / "events" / "0003-completed.json").is_file()
    assert (op / "pre" / "files" / PAGE_REL).is_file()
    assert (op / "post" / "files" / PAGE_REL).is_file()

    before = _tree_state(tmp_path)
    replay = _run(_revise_args(request, apply=True), tmp_path)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "fully verified" in replay.stdout
    assert _tree_state(tmp_path) == before


def test_completed_operation_preserves_adoption_and_main_state_contract(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    adoption = tmp_path / "pipeline-workspace" / "adoptions" / SOURCE / "manifest.json"
    adoption_before = adoption.read_bytes()
    state_before = _source_state_contract(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    completed = _run(_revise_args(request, apply=True), tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr

    assert adoption.read_bytes() == adoption_before
    assert _source_state_contract(tmp_path) == state_before
    con = sqlite3.connect(tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite")
    try:
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    finally:
        con.close()


def test_legacy_evidence_without_derived_files_snapshot_still_verifies(tmp_path, monkeypatch):
    """R1 回归：P1 之前写下的旧 transition（无 derived_files、无 anki-export）仍须验得过。

    旧 evidence 的派生集合是当时的 6 个文件；新代码若拿当前 7 文件的 ``_DERIVED`` 去卡，
    会让 cmd_lint / cmd_vault_lint 对不可再生的哈希链证据永久 SystemExit，且无补救通道。
    这里按旧集合物化一份字节忠实的 operation，再用新代码重跑 ``evidence_findings``。
    """
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision

    # 模拟 P1 之前的物化路径：派生集合只有 6 个文件，transition 不写 derived_files 字段。
    monkeypatch.setattr(legacy_revision, "_DERIVED", legacy_revision._DERIVED_V1)
    monkeypatch.setattr(legacy_revision, "_derived_files_snapshot", lambda: None)

    prepared = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request,
        apply=True, lock_ttl_seconds=1800)
    assert prepared["phase"] == "prepared"
    _edit_candidate(tmp_path)
    completed = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request,
        apply=True, lock_ttl_seconds=1800)
    assert completed["phase"] == "completed"

    # 恢复当前派生集合，模拟「新代码」重校验旧 evidence。
    monkeypatch.undo()
    assert legacy_revision.evidence_findings(tmp_path) == []


@pytest.mark.parametrize("fault", ["transition", "committing", "switch", "log", "completed"])
def test_each_commit_boundary_crash_converges_with_same_request(tmp_path, monkeypatch, fault):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original = legacy_revision._fault_point

    def injected(point):
        matched = point == fault or (fault == "switch" and point.startswith("switch:"))
        if matched:
            raise RuntimeError(f"injected {fault}")

    monkeypatch.setattr(legacy_revision, "_fault_point", injected)
    with pytest.raises(RuntimeError, match=f"injected {fault}"):
        legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                            apply=True, lock_ttl_seconds=1800)
    monkeypatch.setattr(legacy_revision, "_fault_point", original)

    replay = legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                                 apply=True, lock_ttl_seconds=1800)
    assert replay["phase"] == "completed"
    assert legacy_revision.evidence_findings(tmp_path) == []
    assert _ledger_counts(tmp_path) == (0, 0, 0)


def test_expired_prepared_operation_can_abort_but_cannot_finalize(tmp_path, monkeypatch):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    monkeypatch.setattr(
        legacy_revision, "_now", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(legacy_revision.LegacyRevisionError, match="expired"):
        legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                            apply=True, lock_ttl_seconds=1800)
    aborted = legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                                  apply=True, abort=True, lock_ttl_seconds=1800)
    assert aborted["phase"] == "aborted"


def test_partial_commit_can_rollback_without_rechecking_expiry(tmp_path, monkeypatch):
    _vault, page = _adopted_workspace(tmp_path)
    pre = page.read_bytes()
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original = legacy_revision._fault_point

    def stop_after_first_switch(point):
        if point.startswith("switch:"):
            raise RuntimeError("partial switch")

    monkeypatch.setattr(legacy_revision, "_fault_point", stop_after_first_switch)
    with pytest.raises(RuntimeError, match="partial switch"):
        legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                            apply=True, lock_ttl_seconds=1800)
    monkeypatch.setattr(
        legacy_revision, "_now", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(legacy_revision, "_fault_point", original)
    rolled = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request, apply=True,
        recover="rollback", lock_ttl_seconds=1800)
    assert rolled["phase"] == "rolled_back"
    assert page.read_bytes() == pre


def test_two_page_candidate_is_atomic_and_partial_switch_replays_forward(
        tmp_path, monkeypatch):
    vault, _page = _adopted_workspace(tmp_path, page_rels=(PAGE_REL, PAGE_REL_2))
    pre = {rel: (vault / rel).read_bytes() for rel in (PAGE_REL, PAGE_REL_2)}
    log_pre = (vault / "log.md").read_bytes()
    request, second_citation = _two_page_request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    second = _edit_candidate(
        tmp_path, page_rel=PAGE_REL_2, citation=second_citation)

    valid_second = second.read_bytes()
    _edit_candidate(
        tmp_path, page_rel=PAGE_REL_2, citation=second_citation,
        change_sources=True)
    rejected = _run(_revise_args(request), tmp_path)
    assert rejected.returncode != 0
    assert "source_refs" in (rejected.stdout + rejected.stderr)
    assert {rel: (vault / rel).read_bytes() for rel in pre} == pre
    assert (vault / "log.md").read_bytes() == log_pre
    second.write_bytes(valid_second)

    candidate_check = _run(_revise_args(request), tmp_path)
    assert candidate_check.returncode == 0, candidate_check.stdout + candidate_check.stderr
    assert "candidate verified and ready to commit" in candidate_check.stdout

    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original = legacy_revision._fault_point

    def stop_after_first_page(point):
        if any(point.endswith(f":{rel}") for rel in (PAGE_REL, PAGE_REL_2)):
            raise RuntimeError("first page switched")

    monkeypatch.setattr(legacy_revision, "_fault_point", stop_after_first_page)
    with pytest.raises(RuntimeError, match="first page switched"):
        legacy_revision.run(
            workspace=tmp_path, source=SOURCE, request_path=request,
            apply=True, lock_ttl_seconds=1800)

    op = _operation_dir(tmp_path)
    transition = json.loads((op / "transition.json").read_text(encoding="utf-8"))
    page_entries = [entry for entry in transition["entries"]
                    if entry["path"] in {PAGE_REL, PAGE_REL_2}]
    assert {entry["path"] for entry in page_entries} == {PAGE_REL, PAGE_REL_2}
    first_rel, second_rel = (entry["path"] for entry in page_entries)
    assert (vault / first_rel).read_bytes() == (
        op / "post" / "files" / first_rel).read_bytes()
    assert (vault / second_rel).read_bytes() == (
        op / "switch-pre" / "files" / second_rel).read_bytes()
    assert (vault / "log.md").read_bytes() == log_pre

    monkeypatch.setattr(legacy_revision, "_fault_point", original)
    replay = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request,
        apply=True, lock_ttl_seconds=1800)
    assert replay["phase"] == "completed"
    for rel in (PAGE_REL, PAGE_REL_2):
        assert (vault / rel).read_bytes() == (op / "post" / "files" / rel).read_bytes()
    log_line = transition["log"]["line"]
    assert (vault / "log.md").read_text(encoding="utf-8").count(log_line) == 1
    assert legacy_revision.evidence_findings(tmp_path) == []
    assert _ledger_counts(tmp_path) == (0, 0, 0)

    before = _tree_state(tmp_path)
    exact_replay = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request,
        apply=True, lock_ttl_seconds=1800)
    assert exact_replay["phase"] == "completed"
    assert _tree_state(tmp_path) == before


def test_two_page_partial_switch_rolls_back_every_page_and_log(tmp_path, monkeypatch):
    vault, _page = _adopted_workspace(tmp_path, page_rels=(PAGE_REL, PAGE_REL_2))
    pre = {rel: (vault / rel).read_bytes() for rel in (PAGE_REL, PAGE_REL_2)}
    log_pre = (vault / "log.md").read_bytes()
    request, second_citation = _two_page_request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    _edit_candidate(tmp_path, page_rel=PAGE_REL_2, citation=second_citation)

    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original = legacy_revision._fault_point

    def stop_after_first_page(point):
        if any(point.endswith(f":{rel}") for rel in (PAGE_REL, PAGE_REL_2)):
            raise RuntimeError("first page switched")

    monkeypatch.setattr(legacy_revision, "_fault_point", stop_after_first_page)
    with pytest.raises(RuntimeError, match="first page switched"):
        legacy_revision.run(
            workspace=tmp_path, source=SOURCE, request_path=request,
            apply=True, lock_ttl_seconds=1800)
    monkeypatch.setattr(legacy_revision, "_fault_point", original)

    op = _operation_dir(tmp_path)
    transition = json.loads((op / "transition.json").read_text(encoding="utf-8"))
    page_entries = [entry for entry in transition["entries"]
                    if entry["path"] in {PAGE_REL, PAGE_REL_2}]
    first_rel, second_rel = (entry["path"] for entry in page_entries)
    assert (vault / first_rel).read_bytes() == (
        op / "post" / "files" / first_rel).read_bytes()
    assert (vault / second_rel).read_bytes() == pre[second_rel]
    expected = tmp_path / "expected-live.json"
    expected.write_text("{}\n", encoding="utf-8", newline="\n")
    rolled = legacy_revision.run(
        workspace=tmp_path, source=SOURCE, request_path=request, apply=True,
        recover="rollback", expect_live_manifest=expected, lock_ttl_seconds=1800)

    assert rolled["phase"] == "rolled_back"
    assert {rel: (vault / rel).read_bytes() for rel in pre} == pre
    assert (vault / "log.md").read_bytes() == log_pre
    events = [path.name for path in sorted((op / "events").glob("*.json"))]
    assert events == [
        "0001-prepared.json",
        "0002-committing.json",
        "0003-recovery_requested.json",
        "0004-rollback_requested.json",
        "0005-rolled_back.json",
    ]
    assert legacy_revision.evidence_findings(tmp_path) == []
    assert _ledger_counts(tmp_path) == (0, 0, 0)


def test_candidate_lint_failure_and_live_pre_drift_leave_live_untouched(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    candidate = _edit_candidate(tmp_path)
    text = candidate.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    candidate.write_text(text[:end + 5] + "太短。\n", encoding="utf-8")
    live = _tree_state(tmp_path / "wiki")
    failed = _run(_revise_args(request, apply=True), tmp_path)
    assert failed.returncode != 0 and "content-too-short" in (failed.stdout + failed.stderr)
    assert _tree_state(tmp_path / "wiki") == live

    # Restore a valid candidate, then make live drift after authorization.
    _write_page(candidate, status="proposed", citations=[{
        "source": "NIST/SEMATECH e-Handbook",
        "title": "Engineering Statistics Handbook",
        "url": "https://www.itl.nist.gov/div898/handbook/",
        "accessed_on": "2026-08-02",
        "locator": "Chapter 1",
        "supports": "统计结论必须写明适用条件",
    }], suffix="\n本轮依据外部证据补充了适用条件。")
    page.write_text(page.read_text(encoding="utf-8") + "\nconcurrent live drift\n",
                    encoding="utf-8")
    drifted = page.read_bytes()
    refused = _run(_revise_args(request, apply=True), tmp_path)
    assert refused.returncode != 0 and "live pre SHA drift" in (refused.stdout + refused.stderr)
    assert page.read_bytes() == drifted


def test_second_unfinished_batch_and_stale_lock_are_fail_closed(tmp_path):
    _adopted_workspace(tmp_path)
    first = _request(tmp_path)
    assert _run(_revise_args(first, apply=True), tmp_path).returncode == 0
    second = tmp_path / "second-request.yaml"
    payload = yaml.safe_load(first.read_text(encoding="utf-8"))
    payload["pages"][0]["reason"] += "（不同批次）"
    second.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    collision = _run(_revise_args(second, apply=True), tmp_path)
    assert collision.returncode != 0
    assert "another unfinished" in (collision.stdout + collision.stderr)

    # Abort first, then a stale lock must still tell the operator to unlock instead of stealing it.
    assert _run(_revise_args(first, "--abort", apply=True), tmp_path).returncode == 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import locks
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    assert locks.acquire(db, scope="vault", holder="crashed-holder", pid=42)
    locks.force_set_heartbeat(db, scope="vault", iso="2020-01-01T00:00:00+00:00")
    stale = _run(_revise_args(second), tmp_path)
    assert stale.returncode != 0
    assert "stale vault lock" in (stale.stdout + stale.stderr)


def test_unknown_committing_live_bytes_require_manifest_and_freeze_recovery_choice(
        tmp_path, monkeypatch):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original = legacy_revision._fault_point

    def stop_at_committing(point):
        if point == "committing":
            raise RuntimeError("commit marker persisted")

    monkeypatch.setattr(legacy_revision, "_fault_point", stop_at_committing)
    with pytest.raises(RuntimeError, match="commit marker persisted"):
        legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                            apply=True, lock_ttl_seconds=1800)
    monkeypatch.setattr(legacy_revision, "_fault_point", original)

    unexpected = page.read_bytes() + b"\nunexpected concurrent bytes\n"
    page.write_bytes(unexpected)
    refused = _run(_revise_args(request, "--recover", "forward", apply=True), tmp_path)
    assert refused.returncode != 0
    assert "expect-live-manifest" in (refused.stdout + refused.stderr)

    expected_path = tmp_path / "expected-live.json"
    expected_path.write_text(json.dumps({PAGE_REL: hashlib.sha256(unexpected).hexdigest()}),
                             encoding="utf-8")
    recovered = _run(_revise_args(
        request, "--recover", "forward", "--expect-live-manifest", str(expected_path),
        apply=True), tmp_path)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    op = _operation_dir(tmp_path)
    assert (op / "conflicts" / "files" / PAGE_REL).read_bytes() == unexpected
    recovery = json.loads((op / "events" / "0003-recovery_requested.json").read_text(
        encoding="utf-8"))
    assert recovery["direction"] == "forward"
    assert recovery["expected_live_sha256"] == {
        PAGE_REL: hashlib.sha256(unexpected).hexdigest()}
    assert (op / "events" / "0004-completed.json").is_file()


def test_operation_collision_and_candidate_symlink_are_rejected(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    candidate = _edit_candidate(tmp_path)
    outside = tmp_path / "outside-candidate.md"
    outside.write_bytes(candidate.read_bytes())
    candidate.unlink()
    try:
        candidate.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Windows symlink privilege unavailable: {exc}")
    redirected = _run(_revise_args(request), tmp_path)
    assert redirected.returncode != 0
    assert "redirected" in (redirected.stdout + redirected.stderr)


def test_duplicate_operation_identity_is_fail_closed(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    op = _operation_dir(tmp_path)
    duplicate = op.parent / ("0" * 20)
    shutil.copytree(op, duplicate)
    collision = _run(_revise_args(request), tmp_path)
    assert collision.returncode != 0
    assert "operation collision" in (collision.stdout + collision.stderr)


def test_authorization_pre_event_and_log_tampering_are_all_detected(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    op = _operation_dir(tmp_path)
    paths = [
        op / "authorization.json",
        op / "pre" / "files" / PAGE_REL,
        op / "events" / "0002-committing.json",
        tmp_path / "wiki" / "log.md",
    ]
    for path in paths:
        original = path.read_bytes()
        if path.name == "log.md":
            completed = json.loads((op / "events" / "0003-completed.json").read_text(
                encoding="utf-8"))
            path.write_text(path.read_text(encoding="utf-8").replace(
                completed["log_line"], ""), encoding="utf-8")
        else:
            path.write_bytes(original + b" ")
        result = _run(["vault-lint"], tmp_path)
        assert result.returncode != 0
        assert "legacy-revision" in (result.stdout + result.stderr)
        path.write_bytes(original)


def test_prepared_expiry_rejected_but_committing_expiry_can_forward(tmp_path, monkeypatch):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)

    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    original_fault = legacy_revision._fault_point

    def stop_after_committing(point):
        if point == "committing":
            raise RuntimeError("injected crash")

    monkeypatch.setattr(legacy_revision, "_fault_point", stop_after_committing)
    with pytest.raises(RuntimeError, match="injected crash"):
        legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                            apply=True, lock_ttl_seconds=1800)
    op = _operation_dir(tmp_path)
    assert (op / "events" / "0002-committing.json").is_file()

    authorization = json.loads((op / "authorization.json").read_text(encoding="utf-8"))
    authorization["valid_until"] = "2020-01-01T00:00:00+00:00"
    # Expiry is part of the immutable credential, so simulate wall-clock expiry instead of tampering.
    monkeypatch.setattr(legacy_revision, "_now", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(legacy_revision, "_fault_point", original_fault)
    result = legacy_revision.run(workspace=tmp_path, source=SOURCE, request_path=request,
                                 apply=True, lock_ttl_seconds=1800)
    assert result["phase"] == "completed"


def test_abort_keeps_live_and_completed_live_drift_is_warning_only(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    live = page.read_bytes()
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    aborted = _run(_revise_args(request, "--abort", apply=True), tmp_path)
    assert aborted.returncode == 0 and "aborted" in aborted.stdout
    assert page.read_bytes() == live

    request2 = _request(tmp_path)
    payload = yaml.safe_load(request2.read_text(encoding="utf-8"))
    payload["pages"][0]["reason"] += "（第二次授权）"
    request2.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    assert _run(_revise_args(request2, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request2, apply=True), tmp_path).returncode == 0
    page.write_text(page.read_text(encoding="utf-8") + "\n后续合法来源更新。\n", encoding="utf-8")
    lint = _run(["vault-lint"], tmp_path)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert "post-legacy-revision-live-drift" in lint.stdout


def test_evidence_or_log_tampering_is_hard_failure_in_both_lints(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    (_operation_dir(tmp_path) / "post" / "manifest.json").write_text("{}\n", encoding="utf-8")

    vault = _run(["vault-lint"], tmp_path)
    ordinary = _run(["lint", "--source", SOURCE], tmp_path)
    for result in (vault, ordinary):
        assert result.returncode != 0
        assert "legacy-revision" in (result.stdout + result.stderr)


def test_orphan_log_anchor_is_hard_failure_in_both_lints(tmp_path):
    vault, _page = _adopted_workspace(tmp_path)
    log = vault / "log.md"
    with log.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n## [2026-08-02] revise-adopted | demo-legacy-vault | operation deadbeefdeadbeefdead post abcdef123456\n")
    for args in (["vault-lint"], ["lint", "--source", SOURCE]):
        result = _run(args, tmp_path)
        assert result.returncode != 0
        assert "orphan" in (result.stdout + result.stderr).lower()


def test_exact_revert_only_before_later_live_drift(tmp_path):
    _vault, page = _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    completed_id = _operation_dir(tmp_path).name

    revert = _request(tmp_path, mode="revert", revert_operation=completed_id)
    planned = _run(_revise_args(revert), tmp_path)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    page.write_text(page.read_text(encoding="utf-8") + "\n后来来源更新。\n", encoding="utf-8")
    refused = _run(_revise_args(revert, apply=True), tmp_path)
    assert refused.returncode != 0
    assert "revert-not-applicable-after-live-drift" in (refused.stdout + refused.stderr)


def test_revert_signing_rejects_unknown_citation_removal(tmp_path):
    citation = _existing_citation()
    _adopted_workspace(tmp_path, citations=[citation])
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0

    requested_sha = hashlib.sha256(b"not-on-target").hexdigest()
    page = _request_page(PAGE_REL)
    page["citation_removals"] = [{
        "sha256": requested_sha,
        "reason": "该引用不再支撑当前结论",
    }]
    revert = _request(
        tmp_path,
        mode="revert",
        revert_operation=_operation_dir(tmp_path).name,
        pages=[page],
    )

    result = _run(_revise_args(revert, apply=True), tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert requested_sha in output
    assert _citation_sha256(citation) in output
    assert citation["source"] in output and citation["url"] in output


def test_stale_real_source_workorder_can_reopen_after_check_write_stage(tmp_path):
    """A published source can refresh its stale page baseline through reopen."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import state_store

    _vault, page = _adopted_workspace(tmp_path)
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.register_source(db, "real-book", domain="statistics", fmt="md")
    for stage in ("profiled", "converted", "windowed"):
        state_store.start_stage(db, "real-book", stage, input_hash=f"{stage}-in")
        state_store.complete_stage(db, "real-book", stage, output_hash=f"{stage}-out")
    staging = tmp_path / "pipeline-workspace" / "staging" / "real-book"
    staging.mkdir(parents=True)
    (staging / "windows.jsonl").write_text(
        '{"window_id":"w0000","char_start":0,"char_end":1}\n', encoding="utf-8")
    initial_workorder = _run(["workorder", "--source", "real-book"], tmp_path)
    assert initial_workorder.returncode == 0, initial_workorder.stdout + initial_workorder.stderr

    # Finish one historical round.  The latest ingesting run remains queryable after publish.
    for stage in ("ingest_waiting", "ingesting", "ingested", "lint"):
        state_store.start_stage(db, "real-book", stage, input_hash=f"{stage}-in")
        state_store.complete_stage(db, "real-book", stage, output_hash=f"{stage}-out")
    row = state_store.get_source(db, "real-book")
    assert (row["current_stage"], row["current_status"]) == ("lint", "published")

    # A later legacy revision changes the live bytes while the old workorder still names H0.
    page.write_text(page.read_text(encoding="utf-8") + "\nlegacy revision H1\n",
                    encoding="utf-8")
    denied = _run(["check-write", "--source", "real-book", "--path", PAGE_REL], tmp_path)
    assert denied.returncode != 0
    assert "disk hash changed since snapshot" in (denied.stdout + denied.stderr)

    reopened = _run(["reopen", "--source", "real-book"], tmp_path)
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    started = _run(["ingest-start", "--source", "real-book"], tmp_path)
    assert started.returncode == 0, started.stdout + started.stderr
    allowed = _run(["check-write", "--source", "real-book", "--path", PAGE_REL], tmp_path)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert f"ALLOW {PAGE_REL}" in allowed.stdout


# ---------------------------------------------------------------------------
# 契约演进：受控 frontmatter_updates（任务 1 出口表 8 行）
# ---------------------------------------------------------------------------


def _frontmatter(tmp_path, page_rel=PAGE_REL):
    page = tmp_path / "wiki" / page_rel
    raw = page.read_text(encoding="utf-8")
    end = raw.find("\n---", 3)
    return yaml.safe_load(raw[3:end])


def _fu_request(tmp_path, *, remove_aliases, page_rel=PAGE_REL):
    page = _request_page(page_rel)
    page["frontmatter_updates"] = {"aliases": {"remove": remove_aliases}}
    return _request(tmp_path, pages=[page])


def _alias_edit_candidate(tmp_path, *, remove=(), add=(), drop_sources=(),
                          page_rel=PAGE_REL):
    """候选页：置 proposed、改 aliases（remove/add）、按 source 删 citation、追加 evidence citation。"""
    op = _operation_dir(tmp_path)
    candidate = op / "candidate" / "files" / page_rel
    text = candidate.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    meta = yaml.safe_load(text[4:end + 1])
    body = text[end + 5:]
    meta["status"] = "proposed"
    meta["aliases"] = [a for a in meta.get("aliases") or [] if a not in remove]
    meta["aliases"] = meta["aliases"] + list(add)
    meta["citations"] = [c for c in meta.get("citations") or []
                         if c.get("source") not in drop_sources]
    meta.setdefault("citations", []).append({
        "source": "NIST/SEMATECH e-Handbook",
        "title": "Engineering Statistics Handbook",
        "url": "https://www.itl.nist.gov/div898/handbook/",
        "accessed_on": "2026-08-02",
        "locator": "Chapter 1",
        "supports": "统计结论必须写明适用条件",
    })
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    candidate.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8", newline="\n")
    return candidate


def test_fu_exit1_undeclared_unchanged_candidate_passes(tmp_path):
    """出口 1：未声明、候选未改（今天的行为）→ pass。"""
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _edit_candidate(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0


def test_fu_exit2_undeclared_alias_change_rejected(tmp_path):
    """出口 2：未声明、候选改了 → reject immutable frontmatter changed (aliases)。"""
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, remove=["Legacy test"])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "immutable frontmatter changed (aliases)" in (result.stdout + result.stderr)


def test_fu_exit3_declared_and_applied_passes(tmp_path):
    """出口 3：声明了、候选按声明改 → pass，authorization 记录期望态/原始态。"""
    _adopted_workspace(tmp_path)
    request = _fu_request(tmp_path, remove_aliases=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, remove=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    meta = _frontmatter(tmp_path)
    assert "Legacy test" not in meta["aliases"]
    auth = json.loads((_operation_dir(tmp_path) / "authorization.json")
                      .read_text(encoding="utf-8"))
    page_auth = auth["pages"][0]
    assert page_auth["frontmatter_updates"] == {"aliases": {"remove": ["Legacy test"]}}
    assert "Legacy test" in page_auth["immutable_frontmatter_pre"]["aliases"]
    assert "Legacy test" not in page_auth["immutable_frontmatter"]["aliases"]


def test_fu_exit4_declared_but_not_applied_rejected(tmp_path):
    """出口 4：声明了、候选没改 → reject（声明未兑现）。"""
    _adopted_workspace(tmp_path)
    request = _fu_request(tmp_path, remove_aliases=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, remove=[])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "declared frontmatter update not applied" in (result.stdout + result.stderr)


def test_fu_exit5_declared_but_wrong_value_rejected(tmp_path):
    """出口 5：声明了、候选改成了别的值 → reject。"""
    _adopted_workspace(tmp_path)
    request = _fu_request(tmp_path, remove_aliases=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, add=["Wrong Alias"])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "immutable frontmatter changed (aliases)" in (result.stdout + result.stderr)


@pytest.mark.parametrize(("fu", "needle"), [
    ({"status": {"remove": ["x"]}}, "immutable frontmatter"),
    ({"canonical_id": {"remove": ["x"]}}, "only supports aliases"),
])
def test_fu_exit6_non_immutable_or_unsupported_key_rejected(tmp_path, fu, needle):
    """出口 6：声明的键不属于 _IMMUTABLE_FRONTMATTER（或暂不支持）→ 请求校验阶段 reject。"""
    _adopted_workspace(tmp_path)
    page = _request_page(PAGE_REL)
    page["frontmatter_updates"] = fu
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr)
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_fu_exit7_remove_absent_alias_rejected(tmp_path):
    """出口 7：remove 一个页面上不存在的别名 → 签名阶段 reject（避免无声空操作）。"""
    _adopted_workspace(tmp_path)
    request = _fu_request(tmp_path, remove_aliases=["No Such Alias"])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "removes absent alias" in (result.stdout + result.stderr)
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_fu_exit8_revert_restores_pre_state_aliases(tmp_path):
    """出口 8：mode: revert → 回滚恢复 pre 态 aliases（并恢复 citations）。"""
    citation = {
        "source": "NIST/SEMATECH e-Handbook",
        "title": "Engineering Statistics Handbook",
        "url": "https://www.itl.nist.gov/div898/handbook/",
        "accessed_on": "2026-08-02",
        "locator": "Chapter 1",
    }
    removed = {
        "source": "existing-source-one",
        "title": "Existing citation one",
        "url": "https://example.test/existing-one",
        "accessed_on": "2026-08-02",
        "supports": "legacy 引用，回滚后原样恢复",
    }
    _adopted_workspace(tmp_path, citations=[removed])
    page_req = _request_page(PAGE_REL, citation=citation)
    page_req["frontmatter_updates"] = {"aliases": {"remove": ["Legacy test"]}}
    page_req["citation_removals"] = [{
        "sha256": _citation_sha256(removed), "reason": "旧引用不再支撑当前结论",
    }]
    request = _request(tmp_path, pages=[page_req])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, remove=["Legacy test"],
                          drop_sources=["existing-source-one"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    assert "Legacy test" not in _frontmatter(tmp_path)["aliases"]
    completed_id = _operation_dir(tmp_path).name

    revert_page = {
        "path": PAGE_REL,
        "reason": "回滚原 operation，恢复 pre 态 aliases 与 citations",
        "evidence": [{
            "citation": {k: v for k, v in removed.items() if k != "supports"},
            "supports": removed["supports"],
        }],
        "citation_removals": [{
            "sha256": _citation_sha256(
                {**citation, "supports": "统计结论必须写明适用条件"}),
            "reason": "回滚原新增",
        }],
    }
    revert = _request(tmp_path, mode="revert", revert_operation=completed_id,
                      pages=[revert_page])
    assert _run(_revise_args(revert, apply=True), tmp_path).returncode == 0
    assert _run(_revise_args(revert, apply=True), tmp_path).returncode == 0
    meta = _frontmatter(tmp_path)
    assert "Legacy test" in meta["aliases"]
    assert [c["source"] for c in meta["citations"]] == ["existing-source-one"]


def test_fu_revert_request_must_not_declare_updates(tmp_path):
    """回滚请求不得声明 frontmatter_updates（恢复是自动的，声明会 fail-closed）。"""
    _adopted_workspace(tmp_path)
    request = _fu_request(tmp_path, remove_aliases=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _alias_edit_candidate(tmp_path, remove=["Legacy test"])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    completed_id = _operation_dir(tmp_path).name
    revert_page = _request_page(PAGE_REL)
    revert_page["frontmatter_updates"] = {"aliases": {"remove": ["Legacy test"]}}
    revert = _request(tmp_path, mode="revert", revert_operation=completed_id,
                      pages=[revert_page])
    result = _run(_revise_args(revert, apply=True), tmp_path)
    assert result.returncode != 0
    assert "revert request must not declare frontmatter_updates" in \
        (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# 契约演进：citation.url 对已登记来源放宽（任务 2 出口表 4 行）
# ---------------------------------------------------------------------------


def _no_url_page_request(source, *, title="Legacy source record"):
    page = _request_page(PAGE_REL)
    page["evidence"] = [{
        "citation": {"source": source, "title": title, "accessed_on": "2026-08-02"},
        "supports": "本地摄取的来源本身没有 URL",
    }]
    return page


def test_url_exit1_https_url_passes(tmp_path):
    """出口 1：有 url + https → pass（今天的行为）。"""
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0


def test_url_exit2_non_https_rejected(tmp_path):
    """出口 2：有 url + 非 https → reject（今天的行为）。"""
    _adopted_workspace(tmp_path)
    page = _request_page(PAGE_REL)
    page["evidence"][0]["citation"]["url"] = "http://example.test/not-https"
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "https" in (result.stdout + result.stderr)


def test_url_exit3_no_url_registered_db_source_passes(tmp_path):
    """出口 3：无 url + source 是状态库已登记 source_id → pass。"""
    _adopted_workspace(tmp_path)
    page = _no_url_page_request(SOURCE)
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_url_exit3_no_url_vault_sources_page_passes(tmp_path):
    """出口 3：无 url + source 是 vault sources/<id>.md（legacy-markdown-bundle）→ pass。"""
    _adopted_workspace(tmp_path)
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "demo-markdown-course.md").write_text(
        "---\ntype: source\nsource_id: demo-markdown-course\n"
        "format: legacy-markdown-bundle\nprovenance_status: legacy-unverified\n"
        "---\n课程资料导航。\n", encoding="utf-8")
    page = _no_url_page_request("demo-markdown-course",
                                title="Demo Markdown Course")
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def _write_raw_request(tmp_path, removal_sha_literal):
    """写原始 YAML 文本，让摘要的字面形态（有无引号）原样进入解析器。

    走 yaml.safe_dump 的话字符串会被自动加引号，测不到"未加引号的纯数字被解析成整数"。
    """
    valid_until = (
        datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    text = (
        "version: 1\n"
        f"source_id: {SOURCE}\n"
        f"valid_until: '{valid_until}'\n"
        "mode: edit\n"
        "pages:\n"
        f"- path: {PAGE_REL}\n"
        "  reason: raw-yaml digest shape\n"
        "  evidence:\n"
        "  - citation:\n"
        "      source: Example documentation\n"
        "      title: Example\n"
        "      url: https://example.invalid/docs\n"
        "      accessed_on: '2030-01-01'\n"
        "    supports: Placeholder evidence for the digest-shape check.\n"
        "  citation_removals:\n"
        f"  - sha256: {removal_sha_literal}\n"
        "    reason: raw-yaml digest shape\n")
    path = tmp_path / "request-raw.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_removal_sha_parsed_as_int_reports_the_yaml_quoting_cause(tmp_path):
    """未加引号的纯数字摘要被 YAML 解析成整数时，报错要指向 YAML 引号而不是摘要本身。

    fail-closed 行为不变（仍然拒绝），改的只是可定位性：原文案说"格式非法或重复"，
    会把作者引去检查摘要，而真正要改的是 YAML 里少了一对引号。
    """
    _adopted_workspace(tmp_path)
    request = _write_raw_request(tmp_path, "0" * 64)

    result = _run(_revise_args(request, apply=True), tmp_path)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "invalid citation removal SHA-256" in output
    assert "YAML parsed this value as int" in output
    assert "quote the sha256 value" in output
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_removal_sha_malformed_string_reports_without_yaml_hint(tmp_path):
    """字符串形态但格式非法时，不应给出 YAML 引号提示（那会误导）。"""
    _adopted_workspace(tmp_path)
    request = _write_raw_request(tmp_path, "'not-a-digest'")

    result = _run(_revise_args(request, apply=True), tmp_path)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "invalid citation removal SHA-256" in output
    assert "YAML parsed this value as" not in output


def test_url_exit4_non_source_page_in_sources_dir_is_not_registered(tmp_path):
    """sources/ 下的非来源页不算已登记来源。

    登记与否按 frontmatter 的 ``type: source`` 判定，不按文件名。否则 sources/ 里放一张
    README 之类的普通页，就能让任意 source 名通过"无 url"这条路径。
    """
    _adopted_workspace(tmp_path)
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "reading-notes.md").write_text(
        "---\ntype: concept\ncanonical_id: concept.demo.reading-notes\n"
        "---\n这不是一张来源页。\n", encoding="utf-8")

    page = _no_url_page_request("reading-notes")
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)

    assert result.returncode != 0
    assert "registered source_id" in (result.stdout + result.stderr)
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_url_exit4_no_url_unregistered_source_rejected(tmp_path):
    """出口 4：无 url + source 未登记 → reject，错误信息列出已登记 source_id。"""
    _adopted_workspace(tmp_path)
    page = _no_url_page_request("not-a-registered-source")
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "registered source_id" in output
    assert SOURCE in output
    assert "not-a-registered-source" in output
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


# ---------------------------------------------------------------------------
# B-05 选项 2：--emit-removal-sha 只读导出（任务 5）
# ---------------------------------------------------------------------------


def test_emit_removal_sha_readonly_and_hashes(tmp_path):
    first = _existing_citation(suffix="first")
    second = _existing_citation(suffix="second")
    _adopted_workspace(tmp_path, citations=[first, second])
    before = _tree_state(tmp_path)
    result = _run(["revise-adopted", "--source", SOURCE,
                   "--emit-removal-sha", PAGE_REL], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert _citation_sha256(first) in output
    assert _citation_sha256(second) in output
    assert first["source"] in output and second["source"] in output
    assert _tree_state(tmp_path) == before
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_emit_removal_sha_validates_the_page_belongs_to_source(tmp_path):
    """`--source` 必须真的限定射程。

    复审实证（prepush-audit-2026-08-08 F6）：该分支只把页路径传下去，`--source` 是
    必填却完全不参与——可以拿任意（甚至不存在的）来源 id 导出库里任意页的 citation，
    读命令日志的人会误以为导出被限定在该来源内。判据与签发同一条：页的 source_refs 含它。
    """
    _adopted_workspace(tmp_path, citations=[_existing_citation()])
    result = _run(["revise-adopted", "--source", "some-other-source",
                   "--emit-removal-sha", PAGE_REL], tmp_path)
    assert result.returncode != 0
    assert "does not belong to source" in result.stdout + result.stderr


@pytest.mark.parametrize("extra", [["--apply"], ["--abort", "--apply"],
                                   ["--recover", "forward", "--apply"]])
def test_emit_removal_sha_refuses_stage_flags(tmp_path, extra):
    """只读分支在阶段参数前早退，静默忽略它们会让调用者以为操作被执行了。"""
    _adopted_workspace(tmp_path, citations=[_existing_citation()])
    result = _run(["revise-adopted", "--source", SOURCE,
                   "--emit-removal-sha", PAGE_REL, *extra], tmp_path)
    assert result.returncode != 0
    assert "read-only and cannot be combined with" in result.stdout + result.stderr


def test_emit_removal_sha_conflicts_with_request(tmp_path):
    _adopted_workspace(tmp_path)
    request = _request(tmp_path)
    result = _run(["revise-adopted", "--source", SOURCE,
                   "--emit-removal-sha", PAGE_REL, "--request", str(request)], tmp_path)
    assert result.returncode != 0
    assert "cannot be combined" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# request_sha256 的规范形式（golden fixtures）
#
# request_sha256 是每个已签发 operation 唯一可独立复算的锚：它写进
# authorization.json，审计者据此证明"这个 operation 确实由那份请求授权"。
# 规范化过程一旦漂移，所有历史 operation 都会变得无法复算。
#
# 下面的摘要把规范形式钉死。改动 _validate_request 的规范化逻辑（键排序、strip、
# 大小写归一、可选字段的缺省表示、json.dumps 参数）都会让这些断言失败——那是预期
# 行为，不要直接更新摘要，先确认这次改动是否真的要求所有历史 authorization 重新签发。
#
# fixtures 是合成内容。真实部署的请求不进本仓库：其页面路径、修订理由与内容摘要属于
# 使用者的私有知识库，不应随开源代码分发。要验证某个具体部署的历史 operation 是否
# 仍可复算，在该部署本地跑并把结果记进该部署自己的报告。
# ---------------------------------------------------------------------------


CANONICAL_REQUEST_SOURCE = "demo-legacy-vault"

CANONICAL_REQUEST_GOLDEN = [
    # 最小请求：单页、单条 evidence、无 citation_removals、无可选 locator
    ("canonical-minimal.yaml",
     "19449225349f5584561ea052634d10c82bb93099cff5991b47638d18bfb77ab6"),
    # 乱序输入：页按路径倒序、removals 按摘要倒序、citation 键乱序
    ("canonical-unsorted.yaml",
     "1512088bae1b1f15bb011725753f0452f9992dc8e5478732ec15dde131150db0"),
    # 非 ASCII + 前后空白：ensure_ascii=False 与 strip 行为
    ("canonical-unicode-whitespace.yaml",
     "ab8ece1e3e41da741acbce339d8a158bbe25cb89adb66603c45fd3a3e94d1bce"),
]


def _canonical_fixture(name):
    return ROOT / "tests" / "fixtures" / "request-yamls" / name


@pytest.mark.parametrize(("name", "golden_sha"), CANONICAL_REQUEST_GOLDEN)
def test_request_sha256_canonical_form_is_pinned(name, golden_sha):
    """规范形式不得漂移：新增可选 schema 字段时，缺省该字段的请求必须产生同一摘要。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    _normal, _raw, request_sha = legacy_revision._validate_request(
        _canonical_fixture(name), CANONICAL_REQUEST_SOURCE)
    assert request_sha == golden_sha


def test_request_sha256_is_independent_of_declaration_order(tmp_path):
    """规范化吸收顺序：把乱序请求手工排好序后，摘要必须不变。

    单看 golden 摘要无法区分"排序正确"与"排序被整体改错、golden 跟着更新了"，
    所以这里直接断言性质本身。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    data = yaml.safe_load(
        _canonical_fixture("canonical-unsorted.yaml").read_text(encoding="utf-8"))
    data["pages"] = sorted(data["pages"], key=lambda page: page["path"])
    for page in data["pages"]:
        page["citation_removals"] = sorted(
            page["citation_removals"], key=lambda removal: removal["sha256"])
    presorted = tmp_path / "presorted.yaml"
    presorted.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    _normal, _raw, presorted_sha = legacy_revision._validate_request(
        presorted, CANONICAL_REQUEST_SOURCE)
    _normal, _raw, declared_sha = legacy_revision._validate_request(
        _canonical_fixture("canonical-unsorted.yaml"), CANONICAL_REQUEST_SOURCE)
    assert presorted_sha == declared_sha


# ---------------------------------------------------------------------------
# R-07：evidence 允许为空（纯删除页 + meta 页正文变更），no-op 守卫在候选层
# ---------------------------------------------------------------------------


def _request_page_evidence_empty(page_rel=PAGE_REL, *, removals=None):
    page = _request_page(page_rel)
    page["evidence"] = []
    if removals:
        page["citation_removals"] = removals
    return page


def _empty_evidence_candidate(tmp_path, *, drop_sources=(), body_suffix=""):
    """候选页：置 proposed，按 source 删 citation、可选追加正文，其余不动。"""
    op = _operation_dir(tmp_path)
    candidate = op / "candidate" / "files" / PAGE_REL
    text = candidate.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    meta = yaml.safe_load(text[4:end + 1])
    body = text[end + 5:]
    meta["status"] = "proposed"
    meta["citations"] = [c for c in meta.get("citations") or []
                         if c.get("source") not in drop_sources]
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    candidate.write_text(f"---\n{fm}---\n{body}{body_suffix}\n",
                         encoding="utf-8", newline="\n")
    return candidate


def test_evidence_empty_pure_delete_passes(tmp_path):
    """R-07 出口 1：evidence=[] + citation_removals 非空 → 通过（纯删除，不新增 citation）。"""
    citation = _existing_citation()
    _adopted_workspace(tmp_path, citations=[citation])
    page = _request_page_evidence_empty(removals=[{
        "sha256": _citation_sha256(citation),
        "reason": "未登记名删除：无法定位到任何已登记来源",
    }])
    request = _request(tmp_path, pages=[page])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _empty_evidence_candidate(tmp_path, drop_sources=[citation["source"]])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    assert _frontmatter(tmp_path)["citations"] == []


def test_evidence_empty_meta_body_change_passes(tmp_path):
    """R-07 出口 2：evidence=[] + removals=[] + 无 frontmatter_updates，但正文有改动 → 通过。"""
    _adopted_workspace(tmp_path)
    page = _request_page_evidence_empty()
    request = _request(tmp_path, pages=[page])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    _empty_evidence_candidate(tmp_path, body_suffix="（本轮新增证据分层说明）")
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    assert "证据分层说明" in (tmp_path / "wiki" / PAGE_REL).read_text(encoding="utf-8")
    assert _frontmatter(tmp_path)["citations"] == []


def _phase_of(op):
    events = sorted((op / "events").glob("*.json"))
    return json.loads(events[-1].read_text(encoding="utf-8"))["event"]


def test_evidence_empty_noop_candidate_cannot_complete(tmp_path):
    """R-07 出口 3：三者全空且候选逐字节等于 pre → operation 无法完成。

    `_validate_candidates` 的 `candidate page was not edited` 守卫在本轮之前已存在；
    实测其行为是「prepared; waiting for candidate edits」（exit 0、保持 prepared、
    不指名页面），与任务书预期的硬拒不同——按任务书 §5 报告该差异、不改其语义。
    本测试断言不变量：no-op 候选永远无法进入 completed，编辑后才能完成。
    """
    _adopted_workspace(tmp_path)
    page = _request_page_evidence_empty()
    request = _request(tmp_path, pages=[page])
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    first = _run(_revise_args(request, apply=True), tmp_path)
    assert first.returncode == 0
    assert "waiting for candidate edits" in first.stdout + first.stderr
    second = _run(_revise_args(request, apply=True), tmp_path)
    assert second.returncode == 0
    assert "waiting for candidate edits" in second.stdout + second.stderr
    assert _phase_of(_operation_dir(tmp_path)) == "prepared"
    _empty_evidence_candidate(tmp_path, body_suffix="（本轮新增证据分层说明）")
    assert _run(_revise_args(request, apply=True), tmp_path).returncode == 0
    assert _phase_of(_operation_dir(tmp_path)) == "completed"


@pytest.mark.parametrize("bad_evidence", [None, "string", {"citation": []}])
def test_evidence_not_a_list_rejected(tmp_path, bad_evidence):
    """R-07 出口 4：evidence 不是 list（null/字符串/字典）→ 请求校验拒绝 must be a list。"""
    _adopted_workspace(tmp_path)
    page = _request_page(PAGE_REL)
    page["evidence"] = bad_evidence
    request = _request(tmp_path, pages=[page])
    result = _run(_revise_args(request, apply=True), tmp_path)
    assert result.returncode != 0
    assert "must be a list" in (result.stdout + result.stderr)
    assert not (tmp_path / "pipeline-workspace" / "legacy-revisions").exists()


def test_evidence_empty_is_normalized_as_empty_list_not_missing(tmp_path):
    """R-07 兼容性：空 evidence 是 [] 而不是缺字段——规范形里 evidence 键仍在且为 []。"""
    _adopted_workspace(tmp_path)
    page = _request_page_evidence_empty()
    request = _request(tmp_path, pages=[page])
    sys.path.insert(0, str(ROOT / "scripts"))
    import legacy_revision
    normal, _raw, _sha = legacy_revision._validate_request(request, SOURCE)
    assert "evidence" in normal["pages"][0]
    assert normal["pages"][0]["evidence"] == []
