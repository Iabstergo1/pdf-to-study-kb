import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, workspace):
    env = {**os.environ, "PYTHONUTF8": "1", "STUDY_KB_ROOT": str(workspace)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", env=env)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _tree_state(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _write_page(path, meta, body="可复用知识页正文。" * 40):
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True)
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8", newline="\n")


def _fixture(tmp_path):
    origin = tmp_path / "origin"
    origin_vault = origin / "wiki"
    pdf = tmp_path / "mysql.pdf"
    pdf.write_bytes(b"mysql-pdf-source\n")
    pdf_sha = _sha(pdf)

    _write_page(origin_vault / "sources" / "mysql.md", {
        "domain": "database-systems", "format": "pdf", "managed_by": "pipeline",
        "source_id": "mysql", "status": "published",
        "title": "MySQL是怎样运行的", "type": "source",
    }, "origin canonical source page")
    origins = []
    for i in range(37):
        rel = f"domains/database-systems/concepts/origin-{i:02d}.md"
        origins.append(rel)
        _write_page(origin_vault / rel, {
            "canonical_id": f"concept.database-systems.origin-{i:02d}",
            "canonical_name": f"Origin {i:02d}", "domain": "database-systems",
            "managed_by": "pipeline", "source_refs": [{"source": "mysql", "sections": [f"{i}.1"]}],
            "status": "published", "type": "concept",
        })
    for i in range(3):
        _write_page(origin_vault / "topics" / f"origin-topic-{i}.md", {
            "domain": "database-systems", "managed_by": "pipeline",
            "source_refs": [{"source": "mysql", "sections": [f"topic-{i}"]}],
            "status": "published", "title": f"Origin Topic {i}", "type": "topic",
        })

    sys.path.insert(0, str(ROOT / "scripts"))
    import state_store
    db = origin / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    con = sqlite3.connect(db)
    now = "2026-07-28T00:00:00+00:00"
    try:
        con.execute(
            "INSERT INTO sources VALUES (?,?,?,?,?,?)",
            ("mysql", "database-systems", "pdf", now, "lint", "published"))
        con.execute(
            "INSERT INTO source_stage_runs(source_id,stage,status,started_at,finished_at,"
            "input_hash,output_hash,error) VALUES (?,?,?,?,?,?,?,NULL)",
            ("mysql", "lint", "done", now, now, "lint-hash", "lint-hash"))
        con.execute(
            "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
            ("mysql", "raw_source", str(pdf.resolve()), pdf_sha, now))
        con.execute(
            "INSERT INTO work_orders(source_id,path,registry_hash,write_scope_json,created_at,round)"
            " VALUES (?,?,?,?,?,1)",
            ("mysql", str(origin / "workorder.yaml"), "registry", "[]", now))
        con.execute(
            "INSERT INTO ingest_progress(source_id,window_id,input_hash,started_at,finished_at,status,"
            "write_set_json,proposal_set_json,error,round) VALUES (?,?,?,?,?,'finished','[]','[]',NULL,1)",
            ("mysql", "w0001", "window-hash", now, now))
        con.execute(
            "INSERT INTO window_reads(source_id,window_id,read_at,round) VALUES (?,?,?,1)",
            ("mysql", "w0001", now))
        con.commit()
    finally:
        con.close()

    target = tmp_path / "target"
    target_vault = target / "wiki"
    targets = []
    group_sizes = [7, 6, 6, 6, 6, 6, 0, 0]
    cursor = 0
    for i, size in enumerate(group_sizes):
        rel = f"domains/sql/concepts/target-{i}.md"
        mapped = origins[cursor:cursor + size]
        cursor += size
        targets.append({"target": rel, "origin_concepts": mapped})
        meta = {
            "canonical_id": f"concept.sql.target-{i}", "canonical_name": f"Target {i}",
            "domain": "sql", "managed_by": "pipeline", "status": "published", "type": "concept",
        }
        if mapped:
            meta["source_refs"] = [{"source": "mysql", "sections": [f"mapped-{i}"]}]
        _write_page(target_vault / rel, meta)

    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "version": 1, "source_id": "mysql", "targets": targets,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    args = [
        "reuse-source", "--source", "mysql", "--title", "MySQL是怎样运行的",
        "--domain", "sql", "--path", str(pdf), "--sha256", pdf_sha,
        "--origin-root", str(origin), "--origin-source", "mysql",
        "--mapping", str(mapping),
    ]
    return {"origin": origin, "target": target, "pdf": pdf, "mapping": mapping,
            "origins": origins, "targets": targets, "args": args}


def test_reuse_source_dry_run_is_byte_zero_write(tmp_path):
    fx = _fixture(tmp_path)
    target_before = _tree_bytes(fx["target"])
    origin_before = _tree_bytes(fx["origin"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run]" in result.stdout
    assert "concepts=37 topics=3" in result.stdout
    assert "mapped-targets=6 zero-mapping-targets=2" in result.stdout
    assert _tree_bytes(fx["target"]) == target_before
    assert _tree_bytes(fx["origin"]) == origin_before
    assert not (fx["target"] / "pipeline-workspace").exists()


def test_reuse_source_apply_writes_evidence_source_state_and_derived_without_touching_origin(tmp_path):
    fx = _fixture(tmp_path)
    origin_before = _tree_bytes(fx["origin"])
    target_pages_before = {
        row["target"]: (fx["target"] / "wiki" / row["target"]).read_bytes()
        for row in fx["targets"]
    }

    result = _run([*fx["args"], "--apply"], fx["target"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree_bytes(fx["origin"]) == origin_before
    for rel, raw in target_pages_before.items():
        assert (fx["target"] / "wiki" / rel).read_bytes() == raw

    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "external-vault-reuse"
    assert len(manifest["origin_concepts"]) == 37
    assert len(manifest["origin_topics"]) == 3
    assert manifest["mapped_target_count"] == 6
    assert manifest["zero_mapping_target_count"] == 2
    assert (evidence / "mapping.json").read_bytes() == fx["mapping"].read_bytes()
    assert (evidence / "origin-state.json").is_file()
    assert (evidence / "origin-files" / "sources" / "mysql.md").is_file()
    assert (evidence / "target-files" / fx["targets"][0]["target"]).is_file()

    source_page = fx["target"] / "wiki" / "sources" / "mysql.md"
    meta = yaml.safe_load(source_page.read_text(encoding="utf-8").split("---\n", 2)[1])
    assert (meta["format"], meta["source_id"], meta["status"]) == \
           ("external-vault-reuse", "mysql", "published")

    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        src = con.execute("SELECT * FROM sources WHERE source_id='mysql'").fetchone()
        assert (src["current_stage"], src["current_status"], src["format"]) == \
               ("reused", "published", "external-vault-reuse")
        assert con.execute(
            "SELECT COUNT(*) FROM source_stage_runs WHERE source_id='mysql' AND stage='reused'"
        ).fetchone()[0] == 1
        for table in ("work_orders", "ingest_progress", "window_reads"):
            assert con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_id='mysql'"
            ).fetchone()[0] == 0
    finally:
        con.close()
    for rel in ("concepts/_registry.yaml", "index.generated.md",
                "graph-data.generated.json", "knowledge-graph.generated.html",
                "quiz-index.generated.md", "propositions.generated.md"):
        assert (fx["target"] / "wiki" / rel).exists(), rel
    next_result = _run(["next"], fx["target"])
    assert next_result.returncode == 0
    assert "mysql" in next_result.stdout and "-> done" in next_result.stdout


def test_reuse_source_apply_is_whole_tree_byte_noop_on_exact_repeat(tmp_path):
    fx = _fixture(tmp_path)
    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    before = _tree_state(fx["target"])

    second = _run([*fx["args"], "--apply"], fx["target"])

    assert second.returncode == 0, second.stdout + second.stderr
    assert "fully verified" in second.stdout
    assert _tree_state(fx["target"]) == before


def test_reuse_source_missing_graph_is_rebuilt_before_fully_verified_noop(tmp_path):
    fx = _fixture(tmp_path)
    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    graph = fx["target"] / "wiki" / "graph-data.generated.json"
    graph.unlink()

    repaired = _run([*fx["args"], "--apply"], fx["target"])

    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "reuse-derived-drift" in repaired.stdout
    assert "fully verified" not in repaired.stdout
    assert graph.is_file()
    stable = _tree_state(fx["target"])
    exact = _run([*fx["args"], "--apply"], fx["target"])
    assert exact.returncode == 0, exact.stdout + exact.stderr
    assert "fully verified" in exact.stdout
    assert _tree_state(fx["target"]) == stable


def test_reuse_source_live_target_drift_warns_noop_and_evidence_mapping_can_replay(tmp_path):
    fx = _fixture(tmp_path)
    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    target = fx["target"] / "wiki" / fx["targets"][0]["target"]
    target.write_text(target.read_text(encoding="utf-8") + "\n后续 SQL 来源的合法增量。\n",
                      encoding="utf-8", newline="\n")
    drift_tree = _tree_bytes(fx["target"])

    drift = _run([*fx["args"], "--apply"], fx["target"])

    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "post-reuse-target-live-drift" in drift.stdout
    assert "fully verified" in drift.stdout
    assert _tree_bytes(fx["target"]) == drift_tree

    # 临时 mapping 输入可删除；不可变 evidence 自带 mapping 是正式 replay 锚点。
    fx["mapping"].unlink()
    evidence_mapping = (fx["target"] / "pipeline-workspace" / "reuses" /
                        "mysql" / "mapping.json")
    replay_args = [*fx["args"]]
    replay_args[replay_args.index("--mapping") + 1] = str(evidence_mapping)
    replay = _run([*replay_args, "--apply"], fx["target"])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "fully verified" in replay.stdout
    assert _tree_bytes(fx["target"]) == drift_tree


@pytest.mark.parametrize("mutation,needle", [
    ("missing-origin", "37 origin concepts exactly once"),
    ("duplicate-origin", "origin concept mapped more than once"),
    ("wrong-shape", "exactly 8 targets (6 mapped + 2 zero-mapping)"),
    ("mapped-without-ref", "mapped-target-missing-source-ref"),
    ("zero-with-ref", "zero-mapping-target-false-attribution"),
])
def test_reuse_source_mapping_fail_closed(tmp_path, mutation, needle):
    fx = _fixture(tmp_path)
    data = json.loads(fx["mapping"].read_text(encoding="utf-8"))
    if mutation == "missing-origin":
        data["targets"][0]["origin_concepts"].pop()
    elif mutation == "duplicate-origin":
        data["targets"][1]["origin_concepts"].append(
            data["targets"][0]["origin_concepts"][0])
        data["targets"][1]["origin_concepts"].sort()
    elif mutation == "wrong-shape":
        moved = data["targets"][0]["origin_concepts"].pop()
        data["targets"].append({
            "target": "domains/sql/concepts/target-extra.md",
            "origin_concepts": [moved],
        })
        data["targets"].sort(key=lambda item: item["target"])
    elif mutation == "mapped-without-ref":
        page = fx["target"] / "wiki" / data["targets"][0]["target"]
        text = page.read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        meta.pop("source_refs")
        _write_page(page, meta)
    else:
        page = fx["target"] / "wiki" / data["targets"][-1]["target"]
        text = page.read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        meta["source_refs"] = [{"source": "mysql", "sections": ["fabricated"]}]
        _write_page(page, meta)
    fx["mapping"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8", newline="\n")
    before = _tree_bytes(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr)
    assert _tree_bytes(fx["target"]) == before


def test_reuse_source_rejects_wal_origin_without_creating_sidecars_or_touching_mtime(tmp_path):
    fx = _fixture(tmp_path)
    db = fx["origin"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    header = bytearray(db.read_bytes())
    assert header[:16] == b"SQLite format 3\x00"
    header[18] = 2
    header[19] = 2
    db.write_bytes(header)
    sidecars = [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]
    assert not any(os.path.lexists(str(path)) for path in sidecars)
    origin_before = _tree_state(fx["origin"])
    target_before = _tree_state(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "journal_mode WAL is forbidden" in (result.stdout + result.stderr)
    assert _tree_state(fx["origin"]) == origin_before
    assert _tree_state(fx["target"]) == target_before
    assert not any(os.path.lexists(str(path)) for path in sidecars)


def test_reuse_source_read_only_origin_honors_sqlite_exclusive_lock(tmp_path):
    fx = _fixture(tmp_path)
    db = fx["origin"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    origin_before = _tree_state(fx["origin"])
    target_before = _tree_state(fx["target"])
    writer = sqlite3.connect(db)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        result = _run(fx["args"], fx["target"])
    finally:
        writer.rollback()
        writer.close()

    assert result.returncode != 0
    assert "locked" in (result.stdout + result.stderr).lower()
    assert _tree_state(fx["origin"]) == origin_before
    assert _tree_state(fx["target"]) == target_before


def test_reuse_source_rejects_pdf_origin_and_historical_evidence_drift(tmp_path):
    fx = _fixture(tmp_path)
    bad_hash = [*fx["args"]]
    bad_hash[bad_hash.index("--sha256") + 1] = "0" * 64
    mismatch = _run(bad_hash, fx["target"])
    assert mismatch.returncode != 0
    assert "PDF sha256 mismatch" in (mismatch.stdout + mismatch.stderr)

    applied = _run([*fx["args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr
    origin_page = fx["origin"] / "wiki" / fx["origins"][0]
    origin_page.write_bytes(origin_page.read_bytes() + b"drift")
    target_before = _tree_bytes(fx["target"])
    origin_drift = _run([*fx["args"], "--apply"], fx["target"])
    assert origin_drift.returncode != 0
    assert "origin snapshot drift" in (origin_drift.stdout + origin_drift.stderr)
    assert _tree_bytes(fx["target"]) == target_before

    # Restore origin, then corrupt the immutable copy rather than the live target.
    origin_page.write_bytes(origin_page.read_bytes()[:-5])
    copied = (fx["target"] / "pipeline-workspace" / "reuses" / "mysql" /
              "origin-files" / fx["origins"][0])
    copied.write_bytes(copied.read_bytes() + b"tampered")
    before_corrupt_check = _tree_bytes(fx["target"])
    corrupt = _run([*fx["args"], "--apply"], fx["target"])
    assert corrupt.returncode != 0
    assert "reuse-evidence-corrupt" in (corrupt.stdout + corrupt.stderr)
    assert _tree_bytes(fx["target"]) == before_corrupt_check


def test_reuse_source_mapping_origin_state_and_target_state_drift_fail_closed(tmp_path):
    fx = _fixture(tmp_path)
    mapping_bytes = fx["mapping"].read_bytes()
    applied = _run([*fx["args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr

    fx["mapping"].write_bytes(mapping_bytes + b" ")
    target_before = _tree_bytes(fx["target"])
    mapping_drift = _run([*fx["args"], "--apply"], fx["target"])
    assert mapping_drift.returncode != 0
    assert "metadata/mapping/PDF drift" in (mapping_drift.stdout + mapping_drift.stderr)
    assert _tree_bytes(fx["target"]) == target_before
    fx["mapping"].write_bytes(mapping_bytes)

    origin_db = fx["origin"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(origin_db)
    try:
        con.execute("UPDATE sources SET current_status='done' WHERE source_id='mysql'")
        con.commit()
    finally:
        con.close()
    target_before = _tree_bytes(fx["target"])
    origin_state_drift = _run([*fx["args"], "--apply"], fx["target"])
    assert origin_state_drift.returncode != 0
    assert "must be pdf lint/published" in (origin_state_drift.stdout + origin_state_drift.stderr)
    assert _tree_bytes(fx["target"]) == target_before

    con = sqlite3.connect(origin_db)
    try:
        con.execute("UPDATE sources SET current_status='published' WHERE source_id='mysql'")
        con.commit()
    finally:
        con.close()
    target_db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(target_db)
    try:
        con.execute("UPDATE sources SET current_status='done' WHERE source_id='mysql'")
        con.commit()
    finally:
        con.close()
    target_before = _tree_bytes(fx["target"])
    target_state_drift = _run([*fx["args"], "--apply"], fx["target"])
    assert target_state_drift.returncode != 0
    assert "reuse-state-conflict" in (target_state_drift.stdout + target_state_drift.stderr)
    assert _tree_bytes(fx["target"]) == target_before


def test_reuse_source_derived_failure_leaves_state_unpublished_and_rerun_recovers(tmp_path):
    fx = _fixture(tmp_path)
    blocked = fx["target"] / "wiki" / "graph-data.generated.json"
    blocked.mkdir(parents=True)

    failed = _run([*fx["args"], "--apply"], fx["target"])
    assert failed.returncode != 0
    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    assert (evidence / "manifest.json").is_file()
    assert (fx["target"] / "wiki" / "sources" / "mysql.md").is_file()
    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM sources WHERE source_id='mysql'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    finally:
        con.close()

    blocked.rmdir()
    recovered = _run([*fx["args"], "--apply"], fx["target"])
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


def test_reuse_source_dry_run_rejects_active_target_lock_without_writes(tmp_path):
    fx = _fixture(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import locks
    import state_store
    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    assert locks.acquire(db, scope="vault", holder="other", pid=123)
    before = _tree_bytes(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "active vault lock" in (result.stdout + result.stderr)
    assert _tree_bytes(fx["target"]) == before


def test_reuse_source_rejects_redirected_origin_root_and_output_parent(tmp_path):
    fx = _fixture(tmp_path)
    origin_link = tmp_path / "origin-link"
    try:
        origin_link.symlink_to(fx["origin"], target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    args = [*fx["args"]]
    args[args.index("--origin-root") + 1] = str(origin_link)
    redirected_origin = _run(args, fx["target"])
    assert redirected_origin.returncode != 0
    assert "direct, non-redirected directory" in (redirected_origin.stdout + redirected_origin.stderr)

    outside = tmp_path / "outside-reuses"
    outside.mkdir()
    pipeline_workspace = fx["target"] / "pipeline-workspace"
    pipeline_workspace.mkdir()
    (pipeline_workspace / "reuses").symlink_to(outside, target_is_directory=True)
    before = _tree_bytes(fx["target"])
    redirected_output = _run(fx["args"], fx["target"])
    assert redirected_output.returncode != 0
    assert "reuse evidence" in (redirected_output.stdout + redirected_output.stderr)
    assert _tree_bytes(fx["target"]) == before


def test_reuse_source_rejects_origin_page_symlink_before_reading_it(tmp_path):
    fx = _fixture(tmp_path)
    origin_page = fx["origin"] / "wiki" / fx["origins"][0]
    outside = tmp_path / "outside-origin-page.md"
    outside.write_bytes(origin_page.read_bytes())
    origin_page.unlink()
    try:
        origin_page.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    target_before = _tree_state(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "page escapes its vault" in (result.stdout + result.stderr)
    assert _tree_state(fx["target"]) == target_before


def test_reuse_source_rejects_unmapped_target_page_symlink_before_source_ref_scan(tmp_path):
    fx = _fixture(tmp_path)
    outside = tmp_path / "outside-target-page.md"
    _write_page(outside, {
        "canonical_id": "concept.sql.outside", "canonical_name": "Outside",
        "domain": "sql", "managed_by": "pipeline", "status": "published",
        "type": "concept",
    })
    linked = fx["target"] / "wiki" / "domains" / "sql" / "concepts" / "unmapped.md"
    try:
        linked.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    target_before = _tree_state(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "target Markdown page escapes its vault" in (result.stdout + result.stderr)
    assert _tree_state(fx["target"]) == target_before
