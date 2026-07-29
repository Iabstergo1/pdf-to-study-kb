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
