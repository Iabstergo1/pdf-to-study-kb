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


def _run(args, workspace, *, extra_env=None):
    env = {**os.environ, "PYTHONUTF8": "1", "STUDY_KB_ROOT": str(workspace)}
    env.update(extra_env or {})
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


def _fixture(tmp_path, *, concepts=37, topics=3, group_sizes=(7, 6, 6, 6, 6, 6, 0, 0),
             version=1, topic_group_sizes=()):
    """Build one origin+target pair of an arbitrary shape.

    The default is the MySQL migration's shape (37 concepts / 3 topics / 8 targets = 6 mapped +
    2 zero-mapping) with a **v1** mapping — the exact shape of the frozen evidence in the wild.
    ``group_sizes`` must sum to ``concepts``; the command itself asserts no particular shape,
    so every count here is a fixture parameter rather than a CLI contract.

    ``version=2`` additionally emits a ``topic_targets`` dimension driven by
    ``topic_group_sizes`` (one entry per topic target page; 0 = an explicit zero-mapping topic
    target). Unlike concepts, origin topics need **not** be covered exhaustively.
    """
    assert sum(group_sizes) == concepts, (group_sizes, concepts)
    assert version in (1, 2)
    assert not topic_group_sizes or version == 2, "topic_targets 只在 v2 存在"
    assert sum(topic_group_sizes) <= topics, (topic_group_sizes, topics)
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
    for i in range(concepts):
        rel = f"domains/database-systems/concepts/origin-{i:02d}.md"
        origins.append(rel)
        _write_page(origin_vault / rel, {
            "canonical_id": f"concept.database-systems.origin-{i:02d}",
            "canonical_name": f"Origin {i:02d}", "domain": "database-systems",
            "managed_by": "pipeline", "source_refs": [{"source": "mysql", "sections": [f"{i}.1"]}],
            "status": "published", "type": "concept",
        })
    for i in range(topics):
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

    origin_topics = [f"topics/origin-topic-{i}.md" for i in range(topics)]
    topic_targets = []
    cursor = 0
    for i, size in enumerate(topic_group_sizes):
        rel = f"topics/target-topic-{i}.md"
        mapped = origin_topics[cursor:cursor + size]
        cursor += size
        topic_targets.append({"target": rel, "origin_topics": mapped})
        meta = {"domain": "sql", "managed_by": "pipeline", "status": "published",
                "title": f"Target Topic {i}", "type": "topic"}
        if mapped:
            meta["source_refs"] = [{"source": "mysql", "sections": [f"topic-mapped-{i}"]}]
        _write_page(target_vault / rel, meta)

    mapping = tmp_path / "mapping.json"
    document = {"version": version, "source_id": "mysql", "targets": targets}
    if version == 2:
        document["topic_targets"] = topic_targets
    mapping.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
    args = [
        "reuse-source", "--source", "mysql", "--title", "MySQL是怎样运行的",
        "--domain", "sql", "--path", str(pdf), "--sha256", pdf_sha,
        "--origin-root", str(origin), "--origin-source", "mysql",
        "--mapping", str(mapping),
    ]
    mapped_count = sum(1 for size in group_sizes if size)
    mapped_topic_count = sum(1 for size in topic_group_sizes if size)
    return {"origin": origin, "target": target, "pdf": pdf, "mapping": mapping,
            "origins": origins, "targets": targets, "args": args,
            "origin_topics": origin_topics, "topic_targets": topic_targets,
            "concepts": concepts, "topics": topics, "mapped": mapped_count,
            "zero": len(group_sizes) - mapped_count, "version": version,
            "mapped_topics": mapped_topic_count,
            "zero_topics": len(topic_group_sizes) - mapped_topic_count}


def test_reuse_source_dry_run_is_byte_zero_write(tmp_path):
    fx = _fixture(tmp_path)
    target_before = _tree_bytes(fx["target"])
    origin_before = _tree_bytes(fx["origin"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run]" in result.stdout
    assert f"concepts={fx['concepts']} topics={fx['topics']}" in result.stdout
    assert f"mapped-targets={fx['mapped']} zero-mapping-targets={fx['zero']}" in result.stdout
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
    assert len(manifest["origin_concepts"]) == fx["concepts"]
    assert len(manifest["origin_topics"]) == fx["topics"]
    assert manifest["mapped_target_count"] == fx["mapped"]
    assert manifest["zero_mapping_target_count"] == fx["zero"]
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


def test_reuse_source_appends_one_log_line_and_never_duplicates_it(tmp_path):
    """log.md 追加不得破坏 byte/mtime no-op；"派生层需重建"的重跑会再次进锁，也不得重复记账。"""
    fx = _fixture(tmp_path)
    log = fx["target"] / "wiki" / "log.md"

    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if "] reuse-source |" in ln]
    assert len(lines) == 1, lines
    assert lines[0].startswith("## [") and "mysql" in lines[0]
    assert f"{fx['concepts']} concepts / {fx['topics']} topics" in lines[0]
    assert f"{fx['mapped']} mapped + {fx['zero']} zero-mapping targets" in lines[0]
    log_bytes, log_mtime = log.read_bytes(), log.stat().st_mtime_ns

    # ① 完全验证的精确重跑：加锁前返回，日志与全树 byte/mtime 都不动。
    second = _run([*fx["args"], "--apply"], fx["target"])
    assert second.returncode == 0, second.stdout + second.stderr
    assert "fully verified" in second.stdout
    assert log.read_bytes() == log_bytes and log.stat().st_mtime_ns == log_mtime

    # ② 派生产物缺失 → 重新进锁重建；state 已登记，日志不得再记一遍。
    (fx["target"] / "wiki" / "graph-data.generated.json").unlink()
    repaired = _run([*fx["args"], "--apply"], fx["target"])
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "reuse-derived-drift" in repaired.stdout and "state=verified" in repaired.stdout
    assert [ln for ln in log.read_text(encoding="utf-8").splitlines()
            if "] reuse-source |" in ln] == lines

    # ③ 修好之后仍然回到全树 byte/mtime no-op。
    stable = _tree_state(fx["target"])
    exact = _run([*fx["args"], "--apply"], fx["target"])
    assert exact.returncode == 0, exact.stdout + exact.stderr
    assert "fully verified" in exact.stdout
    assert _tree_state(fx["target"]) == stable


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
    ("missing-origin", "origin concepts exactly once"),
    ("duplicate-origin", "origin concept mapped more than once"),
    # 目标张数不再是门禁；把一个 origin 挪到不存在的目标页仍必须被逐页核验拦下。
    ("unknown-target", "mapping target does not exist"),
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
    elif mutation == "unknown-target":
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


def test_reuse_source_supports_a_non_mysql_shape_end_to_end(tmp_path):
    """命令对任意来源都必须走得通：5 concepts / 2 topics / 3 targets（含 1 张零映射）。"""
    fx = _fixture(tmp_path, concepts=5, topics=2, group_sizes=(3, 2, 0))
    origin_before = _tree_bytes(fx["origin"])

    dry = _run(fx["args"], fx["target"])
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "concepts=5 topics=2" in dry.stdout
    assert "mapped-targets=2 zero-mapping-targets=1" in dry.stdout
    assert not (fx["target"] / "pipeline-workspace").exists()

    applied = _run([*fx["args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr
    manifest = json.loads(
        (fx["target"] / "pipeline-workspace" / "reuses" / "mysql" / "manifest.json")
        .read_text(encoding="utf-8"))
    assert (len(manifest["origin_concepts"]), len(manifest["origin_topics"])) == (5, 2)
    assert (manifest["mapped_target_count"], manifest["zero_mapping_target_count"]) == (2, 1)
    source_page = (fx["target"] / "wiki" / "sources" / "mysql.md").read_text(encoding="utf-8")
    assert "5 张 concept、2 张 topic" in source_page and "5→目标页映射" in source_page
    assert "另有 1 张目标页被显式登记为零映射" in source_page

    stable = _tree_state(fx["target"])
    repeat = _run([*fx["args"], "--apply"], fx["target"])
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert "fully verified" in repeat.stdout
    assert _tree_state(fx["target"]) == stable
    assert _tree_bytes(fx["origin"]) == origin_before


def test_reuse_source_optional_shape_expectations_are_opt_in(tmp_path):
    """默认不限制 origin 张数；显式 --expect-* 才断言，且不匹配时 fail-closed。"""
    fx = _fixture(tmp_path, concepts=5, topics=2, group_sizes=(3, 2, 0))
    before = _tree_bytes(fx["target"])

    ok = _run([*fx["args"], "--expect-concepts", "5", "--expect-topics", "2"], fx["target"])
    assert ok.returncode == 0, ok.stdout + ok.stderr

    bad = _run([*fx["args"], "--expect-concepts", "37"], fx["target"])
    assert bad.returncode != 0
    assert "origin concepts count mismatch: expected 37, found 5" in (bad.stdout + bad.stderr)
    assert _tree_bytes(fx["target"]) == before


@pytest.mark.parametrize("fmt,accepted", [
    ("docx", True), ("pptx", True), ("md", True),
    ("legacy-vault", False), ("external-vault-reuse", False),
])
def test_reuse_source_accepts_every_ingestable_origin_format(tmp_path, fmt, accepted):
    """复用只要求 origin 走完一次正常 ingest 并发布；旁路终态格式没有 raw_source 链，仍拒绝。"""
    fx = _fixture(tmp_path, concepts=5, topics=2, group_sizes=(3, 2, 0))
    db = fx["origin"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE sources SET format=? WHERE source_id='mysql'", (fmt,))
        con.commit()
    finally:
        con.close()
    before = _tree_bytes(fx["target"])

    result = _run(fx["args"], fx["target"])

    if accepted:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "[dry-run]" in result.stdout
    else:
        assert result.returncode != 0
        assert "must be a published ingest" in (result.stdout + result.stderr)
    assert _tree_bytes(fx["target"]) == before


def _v2_fixture(tmp_path):
    """v2：5 concepts / 3 topics；concept 3 张目标（含 1 零映射）+ topic 3 张（2 映射 + 1 零映射）。

    刻意只覆盖 3 张 origin topic 中的 2 张，钉住"topic 维度不要求全覆盖"。
    """
    return _fixture(tmp_path, concepts=5, topics=3, group_sizes=(3, 2, 0),
                    version=2, topic_group_sizes=(1, 1, 0))


def _reseal_fixture(tmp_path):
    """Freeze v1 first, then add the topic attribution that only mapping v2 can account for."""
    fx = _fixture(tmp_path, concepts=5, topics=3, group_sizes=(3, 2, 0))
    applied = _run([*fx["args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr
    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    old_manifest_sha = _sha(evidence / "manifest.json")
    old_evidence = _tree_bytes(evidence)
    old_source = (fx["target"] / "wiki" / "sources" / "mysql.md").read_bytes()

    topic_targets = []
    for i, origins in enumerate(([fx["origin_topics"][0]], [fx["origin_topics"][1]], [])):
        rel = f"topics/reseal-target-{i}.md"
        topic_targets.append({"target": rel, "origin_topics": origins})
        meta = {"domain": "sql", "managed_by": "pipeline", "status": "published",
                "title": f"Reseal Target {i}", "type": "topic"}
        if origins:
            meta["source_refs"] = [{"source": "mysql", "sections": [f"topic-{i}"]}]
        _write_page(fx["target"] / "wiki" / rel, meta)

    v2_mapping = tmp_path / "reseal-v2.json"
    v1 = json.loads(fx["mapping"].read_text(encoding="utf-8"))
    v1["version"] = 2
    v1["topic_targets"] = topic_targets
    v2_mapping.write_text(json.dumps(v1, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    reseal_args = [
        "reseal-source", "--source", "mysql", "--mapping", str(v2_mapping),
        "--from-manifest-sha256", old_manifest_sha,
    ]
    replay_args = [*fx["args"]]
    replay_args[replay_args.index("--mapping") + 1] = str(v2_mapping)
    fx.update({"evidence": evidence, "old_manifest_sha": old_manifest_sha,
               "old_evidence": old_evidence, "old_source": old_source,
               "v2_mapping": v2_mapping, "reseal_args": reseal_args,
               "v2_replay_args": replay_args, "reseal_topic_targets": topic_targets})
    return fx


def test_reseal_source_v1_to_v2_round_trip_and_future_replay_is_noop(tmp_path):
    fx = _reseal_fixture(tmp_path)
    before_dry = _tree_state(fx["target"])

    dry = _run(fx["reseal_args"], fx["target"])
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "[dry-run] byte-zero-write" in dry.stdout
    assert _tree_state(fx["target"]) == before_dry

    applied = _run([*fx["reseal_args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "v2 reused/published" in applied.stdout
    manifest = json.loads((fx["evidence"] / "manifest.json").read_text(encoding="utf-8"))
    assert [row["path"] for row in manifest["topic_target_pages"]] == \
           [row["target"] for row in fx["reseal_topic_targets"]]
    assert manifest["target_pages"] == json.loads(
        fx["old_evidence"]["manifest.json"].decode("utf-8"))["target_pages"]
    assert b"mapping v2" in (fx["target"] / "wiki" / "sources" / "mysql.md").read_bytes()

    operations = list((fx["target"] / "pipeline-workspace" / "reuse-reseals" /
                       "mysql").iterdir())
    assert len(operations) == 1
    operation = operations[0]
    assert _tree_bytes(operation / "old-evidence") == fx["old_evidence"]
    transition = json.loads((operation / "transition.json").read_text(encoding="utf-8"))
    assert transition["old_manifest_sha256"] == fx["old_manifest_sha"]
    assert transition["new_manifest_sha256"] == _sha(fx["evidence"] / "manifest.json")

    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        new_sha = _sha(fx["evidence"] / "manifest.json")
        assert con.execute(
            "SELECT input_hash,output_hash FROM source_stage_runs "
            "WHERE source_id='mysql' AND stage='reused'").fetchone() == (new_sha, new_sha)
        assert con.execute(
            "SELECT sha256 FROM artifacts WHERE source_id='mysql' AND kind='reuse_evidence'"
        ).fetchone()[0] == new_sha
    finally:
        con.close()

    stable = _tree_state(fx["target"])
    replay = _run(fx["v2_replay_args"], fx["target"])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "version=2" in replay.stdout and "[dry-run]" in replay.stdout
    assert _tree_state(fx["target"]) == stable
    exact = _run([*fx["v2_replay_args"], "--apply"], fx["target"])
    assert exact.returncode == 0, exact.stdout + exact.stderr
    assert "fully verified" in exact.stdout
    assert _tree_state(fx["target"]) == stable
    exact_reseal = _run([*fx["reseal_args"], "--apply"], fx["target"])
    assert exact_reseal.returncode == 0, exact_reseal.stdout + exact_reseal.stderr
    assert "whole-tree byte/mtime no-op" in exact_reseal.stdout
    assert _tree_state(fx["target"]) == stable
    assert _tree_bytes(operation / "old-evidence") == fx["old_evidence"]


@pytest.mark.parametrize("case,needle", [
    ("origin-page", "origin snapshot drift"),
    ("pdf-content", "PDF sha256 mismatch"),
    ("pdf-path", "PDF not found"),
    ("old-evidence", "reuse-evidence-corrupt"),
    ("source-page", "hand-edited"),
    ("concept-mapping", "topic-only: concept targets"),
])
def test_reseal_source_rejects_drift_or_non_topic_changes(tmp_path, case, needle):
    fx = _reseal_fixture(tmp_path)
    if case == "origin-page":
        page = fx["origin"] / "wiki" / fx["origins"][0]
        page.write_bytes(page.read_bytes() + b"origin drift")
    elif case == "pdf-content":
        fx["pdf"].write_bytes(fx["pdf"].read_bytes() + b"pdf drift")
    elif case == "pdf-path":
        fx["pdf"].rename(tmp_path / "moved.pdf")
    elif case == "old-evidence":
        copied = fx["evidence"] / "target-files" / fx["targets"][0]["target"]
        copied.write_bytes(copied.read_bytes() + b"tampered")
    elif case == "source-page":
        page = fx["target"] / "wiki" / "sources" / "mysql.md"
        page.write_text(page.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    else:
        data = json.loads(fx["v2_mapping"].read_text(encoding="utf-8"))
        left = data["targets"][0]["origin_concepts"].pop()
        right = data["targets"][1]["origin_concepts"].pop()
        data["targets"][0]["origin_concepts"].append(right)
        data["targets"][1]["origin_concepts"].append(left)
        for row in data["targets"]:
            row["origin_concepts"].sort()
        fx["v2_mapping"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8", newline="\n")
        fx["reseal_args"][fx["reseal_args"].index("--from-manifest-sha256") + 1] = \
            fx["old_manifest_sha"]
    before = _tree_state(fx["target"])

    result = _run(fx["reseal_args"], fx["target"])

    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr)
    assert _tree_state(fx["target"]) == before


def test_reseal_source_metadata_has_no_override_surface_and_source_id_is_pinned(tmp_path):
    fx = _reseal_fixture(tmp_path)
    before = _tree_state(fx["target"])
    for extra in (("--domain", "other"), ("--title", "Other")):
        result = _run([*fx["reseal_args"], *extra], fx["target"])
        assert result.returncode != 0
        assert "unrecognized arguments" in (result.stdout + result.stderr)
    wrong_source = [*fx["reseal_args"]]
    wrong_source[wrong_source.index("--source") + 1] = "other"
    result = _run(wrong_source, fx["target"])
    assert result.returncode != 0
    assert "exactly one intact old evidence" in (result.stdout + result.stderr)
    assert _tree_state(fx["target"]) == before


def test_reseal_source_dry_run_rejects_active_lock_without_writes(tmp_path):
    fx = _reseal_fixture(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import locks
    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    assert locks.acquire(db, scope="vault", holder="other", pid=123)
    try:
        before = _tree_state(fx["target"])
        result = _run(fx["reseal_args"], fx["target"])
        assert result.returncode != 0
        assert "active vault lock" in (result.stdout + result.stderr)
        assert _tree_state(fx["target"]) == before
    finally:
        locks.release(db, scope="vault", holder="other")


@pytest.mark.parametrize("boundary", [
    "prepare", "begin-state", "archive-old", "activate-new", "replace-source",
    "derived", "finish-state",
])
def test_reseal_source_failure_at_each_persistent_boundary_rolls_forward(tmp_path, boundary):
    fx = _reseal_fixture(tmp_path)
    failed = _run([*fx["reseal_args"], "--apply"], fx["target"],
                  extra_env={"STUDY_KB_TEST_RESEAL_FAIL_AFTER": boundary})
    assert failed.returncode != 0
    assert f"injected reseal failure after {boundary}" in (failed.stdout + failed.stderr)
    db = fx["target"] / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        stage, status = con.execute(
            "SELECT current_stage,current_status FROM sources WHERE source_id='mysql'"
        ).fetchone()
        assert stage == "reused"
        assert status == ("published" if boundary in ("prepare", "finish-state") else "running")
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    finally:
        con.close()
    if status == "published":
        active_sha = _sha(fx["evidence"] / "manifest.json")
        source_bytes = (fx["target"] / "wiki" / "sources" / "mysql.md").read_bytes()
        if boundary == "prepare":
            assert active_sha == fx["old_manifest_sha"] and source_bytes == fx["old_source"]
        else:
            assert active_sha != fx["old_manifest_sha"] and source_bytes != fx["old_source"]

    recovered = _run([*fx["reseal_args"], "--apply"], fx["target"])
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    stable = _tree_state(fx["target"])
    replay = _run([*fx["reseal_args"], "--apply"], fx["target"])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "whole-tree byte/mtime no-op" in replay.stdout
    assert _tree_state(fx["target"]) == stable


def test_reseal_source_archive_collision_is_fail_closed(tmp_path):
    fx = _reseal_fixture(tmp_path)
    failed = _run([*fx["reseal_args"], "--apply"], fx["target"],
                  extra_env={"STUDY_KB_TEST_RESEAL_FAIL_AFTER": "prepare"})
    assert failed.returncode != 0
    operation = next((fx["target"] / "pipeline-workspace" / "reuse-reseals" /
                      "mysql").iterdir())
    transition = operation / "transition.json"
    transition.write_bytes(transition.read_bytes() + b"collision")
    before = _tree_state(fx["target"])

    retry = _run([*fx["reseal_args"], "--apply"], fx["target"])

    assert retry.returncode != 0
    assert "archive collision" in (retry.stdout + retry.stderr)
    assert _tree_state(fx["target"]) == before


def test_reuse_source_v2_topic_dimension_end_to_end(tmp_path):
    fx = _v2_fixture(tmp_path)
    origin_before = _tree_bytes(fx["origin"])

    dry = _run(fx["args"], fx["target"])
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "version=2" in dry.stdout
    assert "mapped-topic-targets=2 zero-mapping-topic-targets=1" in dry.stdout
    assert "[topic-mapping] topics/target-topic-0.md <- 1 origin topic(s)" in dry.stdout
    assert not (fx["target"] / "pipeline-workspace").exists()

    applied = _run([*fx["args"], "--apply"], fx["target"])
    assert applied.returncode == 0, applied.stdout + applied.stderr
    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in manifest["topic_target_pages"]] == [
        "topics/target-topic-0.md", "topics/target-topic-1.md", "topics/target-topic-2.md"]
    assert [e["mapped_origin_count"] for e in manifest["topic_target_pages"]] == [1, 1, 0]
    # topic 目标页的字节同样冻进 target-files/。
    for rel in ("topics/target-topic-0.md", "topics/target-topic-2.md"):
        assert (evidence / "target-files" / rel).read_bytes() == \
               (fx["target"] / "wiki" / rel).read_bytes()
    source_page = (fx["target"] / "wiki" / "sources" / "mysql.md").read_text(encoding="utf-8")
    assert "映射到本 vault 的 topic 页" in source_page
    assert "另有 1 张 topic 页被显式登记为零映射" in source_page

    stable = _tree_state(fx["target"])
    repeat = _run([*fx["args"], "--apply"], fx["target"])
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert "fully verified" in repeat.stdout
    assert _tree_state(fx["target"]) == stable
    assert _tree_bytes(fx["origin"]) == origin_before


def test_reuse_source_v1_manifest_and_source_page_are_byte_identical_under_v2_code(tmp_path):
    """向后兼容硬要求：v1 mapping 走下来，manifest 与 source 页不得多出任何 topic 维度字节。"""
    fx = _fixture(tmp_path, concepts=5, topics=3, group_sizes=(3, 2, 0))
    assert fx["version"] == 1
    assert _run([*fx["args"], "--apply"], fx["target"]).returncode == 0

    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert "topic_target_pages" not in manifest, sorted(manifest)
    assert sorted(manifest) == [
        "domain", "format", "mapped_target_count", "mapping_sha256", "origin_concepts",
        "origin_root", "origin_source", "origin_source_page", "origin_state_sha256",
        "origin_topics", "pdf_path", "pdf_sha256", "source_id", "target_pages", "title",
        "version", "zero_mapping_target_count"]
    # target-files/ 只冻 concept 目标页；没有任何 topics/ 条目混进来。
    frozen = {p.relative_to(evidence / "target-files").as_posix()
              for p in (evidence / "target-files").rglob("*") if p.is_file()}
    assert frozen == {"domains/sql/concepts/target-0.md", "domains/sql/concepts/target-1.md",
                      "domains/sql/concepts/target-2.md"}
    source_page = (fx["target"] / "wiki" / "sources" / "mysql.md").read_text(encoding="utf-8")
    assert "topic 页" not in source_page and "mapping v2" not in source_page


def test_reuse_source_v1_frozen_evidence_replays_as_byte_noop_under_v2_code(tmp_path):
    """回归用例：先用 v1 落一份证据，再原样重放——必须仍是全树 byte/mtime no-op。"""
    fx = _fixture(tmp_path, concepts=5, topics=3, group_sizes=(3, 2, 0))
    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    frozen_tree = _tree_state(fx["target"])

    # ① 用原始 v1 mapping 重放
    replay = _run([*fx["args"], "--apply"], fx["target"])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "fully verified" in replay.stdout and "version=1" in replay.stdout
    assert _tree_state(fx["target"]) == frozen_tree

    # ② 用 evidence 自带的 mapping.json 重放（正式重放锚点）
    evidence_mapping = (fx["target"] / "pipeline-workspace" / "reuses" / "mysql" / "mapping.json")
    replay_args = [*fx["args"]]
    replay_args[replay_args.index("--mapping") + 1] = str(evidence_mapping)
    from_evidence = _run([*replay_args, "--apply"], fx["target"])
    assert from_evidence.returncode == 0, from_evidence.stdout + from_evidence.stderr
    assert "fully verified" in from_evidence.stdout
    assert _tree_state(fx["target"]) == frozen_tree

    # ③ 把同一份证据改喂 v2 mapping：维度对不上，必须 fail-closed 而不是悄悄升级
    v2_mapping = tmp_path / "upgraded.json"
    data = json.loads(fx["mapping"].read_text(encoding="utf-8"))
    data["version"] = 2
    data["topic_targets"] = []
    v2_mapping.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    upgrade_args = [*fx["args"]]
    upgrade_args[upgrade_args.index("--mapping") + 1] = str(v2_mapping)
    upgraded = _run([*upgrade_args, "--apply"], fx["target"])
    assert upgraded.returncode != 0
    assert "metadata/mapping/PDF drift" in (upgraded.stdout + upgraded.stderr)
    assert _tree_state(fx["target"]) == frozen_tree


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.__setitem__("version", 3), "mapping version must be one of [1, 2]"),
    (lambda d: d.pop("topic_targets"), "top-level fields must be exactly"),
    (lambda d: d["topic_targets"][0].__setitem__("origin_topics",
                                                 ["topics/origin-topic-9.md"]),
     "origin topic(s) the source does not own"),
    (lambda d: d["topic_targets"][1].__setitem__("origin_topics",
                                                 d["topic_targets"][0]["origin_topics"]),
     "origin topic mapped more than once"),
    (lambda d: d["topic_targets"][0].__setitem__("target", d["targets"][0]["target"]),
     "mapping target is duplicated"),
    (lambda d: d["topic_targets"][0].__setitem__("origin_concepts", []),
     "fields must be exactly target/origin_topics"),
])
def test_reuse_source_v2_mapping_fail_closed(tmp_path, mutate, needle):
    fx = _v2_fixture(tmp_path)
    data = json.loads(fx["mapping"].read_text(encoding="utf-8"))
    mutate(data)
    fx["mapping"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    before = _tree_bytes(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr)
    assert _tree_bytes(fx["target"]) == before


@pytest.mark.parametrize("case,needle", [
    ("mapped-topic-without-ref", "mapped-target-missing-source-ref"),
    ("zero-topic-with-ref", "zero-mapping-target-false-attribution"),
    ("topic-target-is-concept", "must be a published topic page"),
])
def test_reuse_source_v2_topic_attribution_boundary(tmp_path, case, needle):
    fx = _v2_fixture(tmp_path)
    vault = fx["target"] / "wiki"
    if case == "mapped-topic-without-ref":
        page = vault / "topics/target-topic-0.md"
        meta = yaml.safe_load(page.read_text(encoding="utf-8").split("---\n", 2)[1])
        meta.pop("source_refs")
        _write_page(page, meta)
    elif case == "zero-topic-with-ref":
        page = vault / "topics/target-topic-2.md"
        meta = yaml.safe_load(page.read_text(encoding="utf-8").split("---\n", 2)[1])
        meta["source_refs"] = [{"source": "mysql", "sections": ["fabricated"]}]
        _write_page(page, meta)
    else:
        page = vault / "topics/target-topic-0.md"
        meta = yaml.safe_load(page.read_text(encoding="utf-8").split("---\n", 2)[1])
        meta["type"] = "concept"
        meta["canonical_id"] = "concept.sql.wrong-type"
        _write_page(page, meta)
    before = _tree_bytes(fx["target"])

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert needle in (result.stdout + result.stderr)
    assert _tree_bytes(fx["target"]) == before


def test_reuse_source_v2_makes_an_aggregating_topic_page_auditable(tmp_path):
    """v2 存在的理由：一张合法聚合本来源内容的 topic 页，在 v1 下只能被判成"未声明归因"。"""
    (tmp_path / "v1").mkdir()
    (tmp_path / "v2").mkdir()
    v1 = _fixture(tmp_path / "v1", concepts=5, topics=3, group_sizes=(3, 2, 0))
    stray = v1["target"] / "wiki" / "topics" / "roadmap.md"
    _write_page(stray, {"domain": "sql", "managed_by": "pipeline", "status": "published",
                        "title": "Roadmap", "type": "topic",
                        "source_refs": [{"source": "mysql", "sections": ["1.1"]}]})
    blocked = _run(v1["args"], v1["target"])
    assert blocked.returncode != 0
    assert "unmapped-target-false-attribution" in (blocked.stdout + blocked.stderr)

    # 同样一张页，在 v2 里作为 topic 目标声明出来即可通过核验。
    v2 = _fixture(tmp_path / "v2", concepts=5, topics=3, group_sizes=(3, 2, 0),
                  version=2, topic_group_sizes=(1,))
    ok = _run(v2["args"], v2["target"])
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "[topic-mapping] topics/target-topic-0.md <- 1 origin topic(s)" in ok.stdout


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
    assert "must be a published ingest" in (origin_state_drift.stdout + origin_state_drift.stderr)
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


# ── origin diagnostics 漂移解锁（2026-07-31）──────────────────────────────────
# `origin_state_sha256` 只证明 evidence 内归档快照未被篡改；重放的硬判据是**生产状态投影**。
# review_proposals 是运营账本：对**任何**来源跑 lint，vault preflight 都会以违规页自己的
# owner 新增 proposal，被复用来源的账本因此会在没人碰它的情况下增长。把它当重放前提会让
# 正常运营锁死复用（reuse 拒绝、reseal 拒绝、retract 不支持 reused 终态 = 无合法出口）。

def _origin_db(fx):
    return fx["origin"] / "pipeline-workspace" / "state" / "study-kb.sqlite"


def _origin_exec(fx, sql, params=()):
    con = sqlite3.connect(_origin_db(fx))
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


_INSERT_PROPOSAL = (
    "INSERT INTO review_proposals(source_id,target_path,kind,diff_path,reason,created_at,status)"
    " VALUES ('mysql','domains/database-systems/concepts/origin-00.md','broken-link',NULL,"
    "'found by vault preflight while linting a different source','2026-07-30T00:00:00+00:00',?)")
_SEED_PROPOSAL = (lambda fx: _origin_exec(fx, _INSERT_PROPOSAL, ("open",)))


@pytest.mark.parametrize("label,setup,mutate", [
    ("insert", None, _SEED_PROPOSAL),
    ("resolve", _SEED_PROPOSAL, lambda fx: _origin_exec(
        fx, "UPDATE review_proposals SET status='resolved' WHERE source_id='mysql'")),
    ("delete", _SEED_PROPOSAL, lambda fx: _origin_exec(
        fx, "DELETE FROM review_proposals WHERE source_id='mysql'")),
])
def test_reuse_source_origin_diagnostics_drift_warns_and_stays_byte_noop(
        tmp_path, label, setup, mutate):
    """proposal 新增 / resolve / 删除都不再阻断重放：warning + 全树 byte/mtime no-op。"""
    fx = _fixture(tmp_path)
    if setup is not None:
        setup(fx)
    first = _run([*fx["args"], "--apply"], fx["target"])
    assert first.returncode == 0, first.stdout + first.stderr
    target_before = _tree_state(fx["target"])
    origin_wiki_before = _tree_state(fx["origin"] / "wiki")

    mutate(fx)
    second = _run([*fx["args"], "--apply"], fx["target"])

    assert second.returncode == 0, second.stdout + second.stderr
    assert "post-reuse-origin-diagnostics-drift" in second.stdout
    assert "fully verified" in second.stdout
    assert _tree_state(fx["target"]) == target_before          # 目标 vault 零写
    assert _tree_state(fx["origin"] / "wiki") == origin_wiki_before   # origin 仍只读


def test_reuse_source_exact_replay_without_diagnostics_drift_reports_no_warning(tmp_path):
    """未发生 diagnostics 漂移时不得凭空多出 warning（防把新 warning 变成常态噪音）。"""
    fx = _fixture(tmp_path)
    assert _run([*fx["args"], "--apply"], fx["target"]).returncode == 0
    before = _tree_state(fx["target"])

    again = _run([*fx["args"], "--apply"], fx["target"])

    assert again.returncode == 0, again.stdout + again.stderr
    assert "post-reuse-origin-diagnostics-drift" not in again.stdout
    assert _tree_state(fx["target"]) == before


@pytest.mark.parametrize("label,sql", [
    # 六张生产表逐一：字段更新 / 增行 / 删行。刻意选**显式断言抓不到**的字段，
    # 这样通过与否只取决于生产投影本身。
    ("sources.added_at",
     "UPDATE sources SET added_at='2026-07-31T00:00:00+00:00' WHERE source_id='mysql'"),
    ("source_stage_runs.output_hash",
     "UPDATE source_stage_runs SET output_hash='tampered' WHERE source_id='mysql'"),
    ("source_stage_runs.insert",
     "INSERT INTO source_stage_runs(source_id,stage,status,started_at,finished_at,input_hash,"
     "output_hash,error) VALUES ('mysql','profile','done','2026-07-29T00:00:00+00:00',"
     "'2026-07-29T00:00:00+00:00','h','h',NULL)"),
    ("artifacts.insert",
     "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES "
     "('mysql','digest','/tmp/x.md','" + "0" * 64 + "','2026-07-29T00:00:00+00:00')"),
    ("work_orders.registry_hash",
     "UPDATE work_orders SET registry_hash='rotated' WHERE source_id='mysql'"),
    ("work_orders.write_scope_json",
     "UPDATE work_orders SET write_scope_json='[\"topics/**\"]' WHERE source_id='mysql'"),
    ("work_orders.round", "UPDATE work_orders SET round=2 WHERE source_id='mysql'"),
    ("ingest_progress.write_set_json",
     "UPDATE ingest_progress SET write_set_json='[\"a.md\"]' WHERE source_id='mysql'"),
    ("ingest_progress.status",
     "UPDATE ingest_progress SET status='failed' WHERE source_id='mysql'"),
    ("ingest_progress.round", "UPDATE ingest_progress SET round=2 WHERE source_id='mysql'"),
    ("ingest_progress.delete", "DELETE FROM ingest_progress WHERE source_id='mysql'"),
    # window ID 集合不变、只有 round 变——旧的窄投影会漏掉，这里必须拒绝。
    ("window_reads.round", "UPDATE window_reads SET round=2 WHERE source_id='mysql'"),
    ("window_reads.delete", "DELETE FROM window_reads WHERE source_id='mysql'"),
])
def test_reuse_source_origin_production_drift_is_fail_closed(tmp_path, label, sql):
    fx = _fixture(tmp_path)
    assert _run([*fx["args"], "--apply"], fx["target"]).returncode == 0
    before = _tree_state(fx["target"])

    _origin_exec(fx, sql)
    result = _run([*fx["args"], "--apply"], fx["target"])

    assert result.returncode != 0, f"{label} 未被拒绝: {result.stdout}"
    assert "origin production state drift" in (result.stdout + result.stderr)
    assert _tree_state(fx["target"]) == before


def test_reuse_source_rejects_unclassified_live_production_column(tmp_path):
    """运行时 schema 护栏：物理列多出未分类字段即 fail-closed（不能只靠测试期断言）。"""
    fx = _fixture(tmp_path)
    _origin_exec(fx, "ALTER TABLE window_reads ADD COLUMN experimental_note TEXT")

    result = _run(fx["args"], fx["target"])

    out = result.stdout + result.stderr
    assert result.returncode != 0
    assert "origin-production-contract-mismatch" in out
    assert "experimental_note" in out


def _rewrite_canonical(path, payload):
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    Path(path).write_bytes(raw)
    return raw


@pytest.mark.parametrize("label,mutate", [
    ("missing-table", lambda d: d.pop("window_reads")),
    ("missing-field", lambda d: [row.pop("round") for row in d["window_reads"]]),
    ("extra-field", lambda d: [row.update({"note": "x"}) for row in d["window_reads"]]),
])
def test_reuse_source_frozen_snapshot_contract_mismatch_is_fail_closed(tmp_path, label, mutate):
    """归档快照缺生产表、缺 included 字段或出现未声明字段 → contract mismatch，不得当空列表放行。

    "未声明" 是关键：合同显式列出的 excluded 诊断字段不算多余（旧快照可以没有它们，
    见 `test_production_projection_allows_legacy_snapshot_to_omit_later_excluded_field`）。
    """
    fx = _fixture(tmp_path)
    assert _run([*fx["args"], "--apply"], fx["target"]).returncode == 0
    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"

    state = json.loads((evidence / "origin-state.json").read_text(encoding="utf-8"))
    mutate(state)
    raw = _rewrite_canonical(evidence / "origin-state.json", state)
    # 同步 manifest 的 SHA，否则先撞 evidence-corrupt 而测不到合同检查本身。
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    manifest["origin_state_sha256"] = hashlib.sha256(raw).hexdigest()
    _rewrite_canonical(evidence / "manifest.json", manifest)

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "origin-production-contract-mismatch" in (result.stdout + result.stderr)


def test_reuse_source_tampered_frozen_origin_state_still_reports_evidence_corrupt(tmp_path):
    """只改归档字节而不改 manifest SHA：仍然是 evidence 损坏，不是合同不符。"""
    fx = _fixture(tmp_path)
    assert _run([*fx["args"], "--apply"], fx["target"]).returncode == 0
    evidence = fx["target"] / "pipeline-workspace" / "reuses" / "mysql"
    state = json.loads((evidence / "origin-state.json").read_text(encoding="utf-8"))
    state["review_proposals"] = [{"forged": True}]
    _rewrite_canonical(evidence / "origin-state.json", state)

    result = _run(fx["args"], fx["target"])

    assert result.returncode != 0
    assert "reuse-evidence-corrupt" in (result.stdout + result.stderr)


def test_reseal_source_survives_diagnostics_drift_and_inherits_frozen_origin_state(tmp_path):
    """proposal 漂移后 mapping reseal 仍可进行；新代必须原样继承旧代 origin-state 与 SHA。"""
    fx = _reseal_fixture(tmp_path)
    evidence = fx["evidence"]
    frozen_state_bytes = (evidence / "origin-state.json").read_bytes()
    old_manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    _SEED_PROPOSAL(fx)   # origin 侧运营账本在冻结之后增长

    resealed = _run([*fx["reseal_args"], "--apply"], fx["target"])

    assert resealed.returncode == 0, resealed.stdout + resealed.stderr
    assert (evidence / "origin-state.json").read_bytes() == frozen_state_bytes
    new_manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert new_manifest["origin_state_sha256"] == old_manifest["origin_state_sha256"]
    for field in ("source_id", "domain", "title", "format", "version", "pdf_path", "pdf_sha256",
                  "origin_root", "origin_source", "origin_source_page", "origin_concepts",
                  "origin_topics"):
        assert new_manifest[field] == old_manifest[field], field
    assert new_manifest["mapping_sha256"] != old_manifest["mapping_sha256"]
    assert "topic_target_pages" in new_manifest

    replay = _run([*fx["v2_replay_args"], "--apply"], fx["target"])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "post-reuse-origin-diagnostics-drift" in replay.stdout


def test_reseal_source_still_refuses_real_origin_production_drift(tmp_path):
    """diagnostics 放行不得顺带放过真实生产漂移。"""
    fx = _reseal_fixture(tmp_path)
    _origin_exec(fx, "UPDATE work_orders SET round=2 WHERE source_id='mysql'")

    resealed = _run([*fx["reseal_args"], "--apply"], fx["target"])

    assert resealed.returncode != 0
    assert "origin production state drift" in (resealed.stdout + resealed.stderr)


def _source_reuse_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    import source_reuse
    return source_reuse


def test_origin_table_classification_is_mutually_exclusive_and_total():
    sr = _source_reuse_module()
    production = set(sr._PRODUCTION_CONTRACT_V1)
    assert production | sr._DIAGNOSTICS_TABLES | sr._TRANSIENT_TABLES == sr._ORIGIN_TABLES
    assert not (production & sr._DIAGNOSTICS_TABLES)
    assert not (production & sr._TRANSIENT_TABLES)
    assert not (sr._DIAGNOSTICS_TABLES & sr._TRANSIENT_TABLES)
    assert set(sr._PRODUCTION_EXCLUDED_FIELDS_V1) == production
    assert set(sr._PRODUCTION_SORT_KEYS_V1) == production


def test_production_contract_matches_live_schema_columns(tmp_path):
    """合同字段清单必须等于真实物理列——加列时这条先红，强迫显式归类。"""
    sr = _source_reuse_module()
    import state_store
    db = tmp_path / "schema.sqlite"
    state_store.init_db(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("CREATE TABLE IF NOT EXISTS window_reads (source_id TEXT NOT NULL,"
                    " window_id TEXT NOT NULL, read_at TEXT NOT NULL, round INTEGER,"
                    " UNIQUE(source_id, window_id))")
        for table, fields in sr._PRODUCTION_CONTRACT_V1.items():
            cols = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
            declared = set(fields) | set(sr._PRODUCTION_EXCLUDED_FIELDS_V1[table])
            assert cols == declared, (table, sorted(cols ^ declared))
    finally:
        con.close()


def test_frozen_and_live_share_one_projection_implementation():
    """禁止 frozen/live 各维护一份字段或排序逻辑：合同常量只许 _production_contract 读。"""
    import ast
    src = (ROOT / "scripts" / "source_reuse.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    versioned = {"_PRODUCTION_CONTRACT_V1", "_PRODUCTION_SORT_KEYS_V1",
                 "_PRODUCTION_EXCLUDED_FIELDS_V1"}
    versioned_readers, registry_readers, callers = set(), set(), {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if names & versioned:
            versioned_readers.add(node.name)
        if "_PRODUCTION_CONTRACTS" in names:
            registry_readers.add(node.name)
        callers[node.name] = {n.func.id for n in ast.walk(node)
                              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    # 版本化字段清单不许被任何函数直接读；一律经注册表 + 单一访问器，
    # 这样新增合同版本时不会有第二处字段/排序逻辑悄悄分叉。
    assert versioned_readers == set(), versioned_readers
    assert registry_readers == {"_production_contract"}, registry_readers
    assert "_origin_production_bytes" in callers["build_plan"]
    assert "_origin_production_bytes" in callers["verify_live_inputs"]
    assert "_assert_live_production_schema" in callers["_origin_state_snapshot"]


def test_production_projection_is_order_independent():
    """两侧共用的投影必须按稳定主键规范化，采集顺序不得影响判定。"""
    sr = _source_reuse_module()
    base = {"schema_version": 1}
    for table, fields in sr._PRODUCTION_CONTRACT_V1.items():
        base[table] = []
    base["window_reads"] = [
        {"source_id": "mysql", "window_id": "w0002", "read_at": "t", "round": 1},
        {"source_id": "mysql", "window_id": "w0001", "read_at": "t", "round": 1},
    ]
    shuffled = {**base, "window_reads": list(reversed(base["window_reads"]))}
    assert sr._origin_production_bytes(base) == sr._origin_production_bytes(shuffled)
    # diagnostics 表整表缺失也不影响投影（它根本不参与）
    assert sr._origin_production_bytes({**base, "review_proposals": [{"x": 1}]}) == \
        sr._origin_production_bytes(base)


def test_production_projection_allows_legacy_snapshot_to_omit_later_excluded_field(monkeypatch):
    """把新物理列归为纯诊断（excluded）后，早于该列的旧 evidence 必须仍可重放。

    行字段判据是 `included ⊆ row ⊆ included ∪ excluded`，不是严格相等——严格相等会让
    excluded 这条声明好的扩展路径实际不可用（所有历史快照当场 contract-mismatch）。
    """
    sr = _source_reuse_module()
    monkeypatch.setitem(sr._PRODUCTION_EXCLUDED_FIELDS_V1, "window_reads", ("diagnostic_note",))

    base = {"schema_version": 1}
    for table in sr._PRODUCTION_CONTRACT_V1:
        base[table] = []
    legacy_row = {"source_id": "mysql", "window_id": "w0001", "read_at": "t", "round": 1}
    live_row = {**legacy_row, "diagnostic_note": "column added after this evidence was frozen"}
    frozen = {**base, "window_reads": [legacy_row]}
    live = {**base, "window_reads": [live_row]}

    # 旧快照缺该列、live 行带该列 → 生产投影必须逐字节相同（只投影 included 字段）
    assert sr._origin_production_bytes(frozen) == sr._origin_production_bytes(live)

    # 边界不得被这次放宽顺带削弱：缺 included 字段仍拒绝
    with pytest.raises(sr.ReuseError) as missing:
        sr._origin_production_bytes(
            {**base, "window_reads": [{k: v for k, v in legacy_row.items() if k != "round"}]})
    assert "origin-production-contract-mismatch" in str(missing.value)
    assert "missing=['round']" in str(missing.value)

    # included/excluded 之外的未知字段仍拒绝
    with pytest.raises(sr.ReuseError) as unknown:
        sr._origin_production_bytes({**base, "window_reads": [{**legacy_row, "mystery": 1}]})
    assert "unknown=['mystery']" in str(unknown.value)

    # 生产字段本身的漂移仍 fail-closed（excluded 放宽不影响 included 的比较）
    drifted = {**base, "window_reads": [{**live_row, "round": 2}]}
    assert sr._origin_production_bytes(drifted) != sr._origin_production_bytes(frozen)
