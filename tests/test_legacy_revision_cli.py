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
SOURCE = "legacy-resume-wiki"
PAGE_REL = "domains/statistics/concepts/legacy-test.md"


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
                source_refs=None, citations=None, suffix=""):
    source_refs = source_refs if source_refs is not None else [
        {"source": SOURCE, "sections": [f"legacy-page:{PAGE_REL}"]}]
    meta = {
        "aliases": ["Legacy test"],
        "canonical_id": "concept.statistics.legacy-test",
        "canonical_name": "Legacy test",
        "citations": citations or [],
        "domain": "statistics",
        "managed_by": managed_by,
        "page_path": PAGE_REL,
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


def _adopted_workspace(tmp_path):
    vault = tmp_path / "wiki"
    page = vault / PAGE_REL
    _write_page(page)
    archive = tmp_path / "legacy-baseline.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"wiki/{PAGE_REL}", page.read_bytes())
    result = _run([
        "adopt-vault", "--source", SOURCE,
        "--title", "Legacy baseline", "--domain", "data-analysis-interview",
        "--baseline-archive", str(archive), "--baseline-sha256", _sha(archive),
        "--apply",
    ], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return vault, page


def _request(tmp_path, *, valid_until=None, pages=None, mode="edit",
             revert_operation=None):
    valid_until = valid_until or (
        datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    pages = pages or [{
        "path": PAGE_REL,
        "reason": "修正 legacy 页中的统计解释，并明确证据边界",
        "evidence": [{
            "citation": {
                "source": "NIST/SEMATECH e-Handbook",
                "title": "Engineering Statistics Handbook",
                "url": "https://www.itl.nist.gov/div898/handbook/",
                "accessed_on": "2026-08-02",
                "locator": "Chapter 1",
            },
            "supports": "统计结论必须写明适用条件",
        }],
        "citation_removals": [],
    }]
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


def _edit_candidate(tmp_path, *, change_sources=False, add_citation=True):
    op = _operation_dir(tmp_path)
    candidate = op / "candidate" / "files" / PAGE_REL
    text = candidate.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    meta = yaml.safe_load(text[4:end + 1])
    body = text[end + 5:]
    meta["status"] = "proposed"
    if change_sources:
        meta["source_refs"].append({"source": "invented-book", "sections": ["p1"]})
    if add_citation:
        meta["citations"].append({
            "source": "NIST/SEMATECH e-Handbook",
            "title": "Engineering Statistics Handbook",
            "url": "https://www.itl.nist.gov/div898/handbook/",
            "accessed_on": "2026-08-02",
            "locator": "Chapter 1",
            "supports": "统计结论必须写明适用条件",
        })
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    candidate.write_text(
        f"---\n{fm}---\n{body}\n本轮依据外部证据补充了适用条件。\n",
        encoding="utf-8", newline="\n")
    return candidate


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
        fh.write("\n## [2026-08-02] revise-adopted | legacy-resume-wiki | operation deadbeefdeadbeefdead post abcdef123456\n")
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
