import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, workspace):
    env = {**os.environ, "STUDY_KB_ROOT": str(workspace), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", env=env)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _write_archive(archive, vault, *, missing=(), overrides=None, extras=None):
    missing = set(missing)
    overrides = overrides or {}
    extras = extras or {}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for page in sorted(Path(vault).rglob("*.md")):
            rel = page.relative_to(vault).as_posix()
            if rel not in missing:
                zf.writestr(f"wiki/{rel}", overrides.get(rel, page.read_bytes()))
        for name, raw in extras.items():
            zf.writestr(name, raw)


def _legacy_workspace(tmp_path):
    vault = tmp_path / "wiki"
    page = vault / "domains" / "sql" / "lessons" / "legacy-note.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\nmanaged_by: pipeline\nsource: demo-legacy-vault\nstatus: published\n"
        "type: lesson\n---\n"
        "这是一张已经存在于旧知识库中的 demo knowledge note。它说明查询前应先确认数据粒度、"
        "过滤范围与时间窗口，再解释结果，并保留足够正文以通过现有的残次页检查。"
        "这段内容属于接管前已有页面，不是本次命令从外部文档重新摄取或生成的内容。\n",
        encoding="utf-8")
    archive = tmp_path / "phase0-baseline.zip"
    _write_archive(archive, vault)
    return vault, page, archive


def _args(archive, *, apply=False, digest=None):
    args = ["adopt-vault", "--source", "demo-legacy-vault",
            "--title", "Demo Legacy Vault Baseline",
            "--domain", "demo",
            "--baseline-archive", str(archive),
            "--baseline-sha256", digest or _sha(archive)]
    if apply:
        args.append("--apply")
    return args


def test_adopt_vault_dry_run_is_byte_zero_write_and_reports_plan(tmp_path):
    _vault, _page, archive = _legacy_workspace(tmp_path)
    before = _tree_bytes(tmp_path)

    result = _run(_args(archive), tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run]" in result.stdout
    assert "violations=0" in result.stdout
    assert "adopted/published" in result.stdout
    assert _tree_bytes(tmp_path) == before
    assert not (tmp_path / "pipeline-workspace").exists()


def test_adopt_vault_rejects_bad_archive_hash_without_writes(tmp_path):
    _vault, _page, archive = _legacy_workspace(tmp_path)
    before = _tree_bytes(tmp_path)

    result = _run(_args(archive, digest="0" * 64), tmp_path)

    assert result.returncode != 0
    assert "baseline archive sha256 mismatch" in (result.stdout + result.stderr)
    assert _tree_bytes(tmp_path) == before


def test_adopt_vault_rejects_non_zip_and_duplicate_members(tmp_path):
    vault, page, archive = _legacy_workspace(tmp_path)
    archive.write_bytes(b"not a zip")
    bad_zip = _run(_args(archive), tmp_path)
    assert bad_zip.returncode != 0
    assert "valid ZIP" in (bad_zip.stdout + bad_zip.stderr)

    _write_archive(archive, vault)
    member = f"wiki/{page.relative_to(vault).as_posix()}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr(member, page.read_bytes())
    duplicate = _run(_args(archive), tmp_path)
    assert duplicate.returncode != 0
    assert "duplicate entry" in (duplicate.stdout + duplicate.stderr)


def test_adopt_vault_rejects_zip_page_missing_mismatch_and_adoptable_extra(tmp_path):
    vault, page, archive = _legacy_workspace(tmp_path)
    rel = page.relative_to(vault).as_posix()

    _write_archive(archive, vault, missing={rel})
    missing = _run(_args(archive), tmp_path)
    assert missing.returncode != 0
    assert "baseline-archive-page-missing" in missing.stdout

    _write_archive(archive, vault, overrides={rel: b"different page bytes"})
    mismatch = _run(_args(archive), tmp_path)
    assert mismatch.returncode != 0
    assert "baseline-archive-page-mismatch" in mismatch.stdout

    _write_archive(
        archive, vault,
        extras={"wiki/domains/sql/lessons/archive-only.md": b"archive-only knowledge page"})
    extra = _run(_args(archive), tmp_path)
    assert extra.returncode != 0
    assert "baseline-archive-page-extra" in extra.stdout


def test_adopt_vault_allows_zip_extras_excluded_from_knowledge_pages(tmp_path):
    vault, _page, archive = _legacy_workspace(tmp_path)
    _write_archive(archive, vault, extras={
        "wiki/assets/scan.png": b"png bytes",
        "wiki/_meta/private.md": b"excluded metadata",
        "README.txt": b"non-wiki file",
    })
    result = _run(_args(archive), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_adopt_vault_rejects_redirected_vault_and_output_ancestors(tmp_path):
    outside_vault = tmp_path / "outside-vault"
    page = outside_vault / "domains" / "d" / "lessons" / "x.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nmanaged_by: pipeline\nstatus: published\ntype: lesson\n---\n正文。\n",
                    encoding="utf-8")
    root = tmp_path / "redirected-vault"
    root.mkdir()
    try:
        (root / "wiki").symlink_to(outside_vault, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    archive = root / "baseline.zip"
    _write_archive(archive, outside_vault)
    redirected_vault = _run(_args(archive), root)
    assert redirected_vault.returncode != 0
    assert "wiki vault" in (redirected_vault.stdout + redirected_vault.stderr)

    root2 = tmp_path / "redirected-output"
    _vault2, _page2, archive2 = _legacy_workspace(root2)
    outside_output = tmp_path / "outside-output"
    outside_output.mkdir()
    (root2 / "pipeline-workspace").symlink_to(outside_output, target_is_directory=True)
    redirected_output = _run(_args(archive2), root2)
    assert redirected_output.returncode != 0
    assert "adoption evidence" in (redirected_output.stdout + redirected_output.stderr)


def test_adopt_vault_rejects_symlinked_evidence_manifest(tmp_path):
    root = tmp_path / "workspace"
    _vault, _page, archive = _legacy_workspace(root)
    assert _run(_args(archive, apply=True), root).returncode == 0
    manifest = (root / "pipeline-workspace" / "adoptions" /
                "demo-legacy-vault" / "manifest.json")
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    try:
        manifest.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    before = _tree_bytes(root)
    result = _run(_args(archive, apply=True), root)
    assert result.returncode != 0
    assert "adoption evidence manifest" in (result.stdout + result.stderr)
    assert _tree_bytes(root) == before


def test_adopt_vault_dry_run_rejects_active_lock(tmp_path):
    _vault, _page, archive = _legacy_workspace(tmp_path)
    sys.path.insert(0, str(ROOT / "scripts"))
    import locks
    import state_store

    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    assert locks.acquire(db, scope="vault", holder="other-source", pid=123)
    before = _tree_bytes(tmp_path)

    result = _run(_args(archive), tmp_path)

    assert result.returncode != 0
    assert "active vault lock" in (result.stdout + result.stderr)
    assert _tree_bytes(tmp_path) == before


def test_adopt_vault_apply_writes_verified_evidence_state_and_derived(tmp_path):
    vault, page, archive = _legacy_workspace(tmp_path)
    original = page.read_bytes()

    result = _run(_args(archive, apply=True), tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert page.read_bytes() == original
    evidence = tmp_path / "pipeline-workspace" / "adoptions" / "demo-legacy-vault"
    manifest_path = evidence / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["pages"]
                 if e["path"] == "domains/sql/lessons/legacy-note.md")
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()
    copied = evidence / "files" / entry["path"]
    assert copied.read_bytes() == original
    assert _sha(copied) == entry["sha256"]

    source_page = vault / "sources" / "demo-legacy-vault.md"
    text = source_page.read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    assert meta["format"] == "legacy-vault"
    assert meta["source_id"] == "demo-legacy-vault"
    assert meta["status"] == "published"
    assert "没有 processing window、block 或 window read/write ledger" in text

    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        src = con.execute("SELECT * FROM sources WHERE source_id='demo-legacy-vault'").fetchone()
        assert (src["current_stage"], src["current_status"], src["format"]) == \
               ("adopted", "published", "legacy-vault")
        for table in ("work_orders", "ingest_progress", "window_reads"):
            assert con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_id='demo-legacy-vault'").fetchone()[0] == 0
    finally:
        con.close()

    for rel in ("concepts/_registry.yaml", "index.generated.md",
                "graph-data.generated.json", "knowledge-graph.generated.html",
                "quiz-index.generated.md", "propositions.generated.md"):
        assert (vault / rel).exists(), rel


def test_adopt_vault_derived_failure_leaves_state_unpublished_and_rerun_recovers(tmp_path):
    vault, _page, archive = _legacy_workspace(tmp_path)
    blocked = vault / "graph-data.generated.json"
    blocked.mkdir()

    failed = _run(_args(archive, apply=True), tmp_path)
    assert failed.returncode != 0
    evidence = tmp_path / "pipeline-workspace" / "adoptions" / "demo-legacy-vault"
    assert (evidence / "manifest.json").is_file()
    assert (vault / "sources" / "demo-legacy-vault.md").is_file()
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM sources WHERE source_id='demo-legacy-vault'"
        ).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    finally:
        con.close()

    blocked.rmdir()
    recovered = _run(_args(archive, apply=True), tmp_path)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT current_status FROM sources WHERE source_id='demo-legacy-vault'"
        ).fetchone()[0] == "published"
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    finally:
        con.close()


def test_adopt_vault_apply_is_byte_idempotent_and_live_drift_is_history_warning(tmp_path):
    _vault, page, archive = _legacy_workspace(tmp_path)
    first = _run(_args(archive, apply=True), tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    evidence = tmp_path / "pipeline-workspace" / "adoptions" / "demo-legacy-vault"
    manifest = evidence / "manifest.json"
    manifest_bytes = manifest.read_bytes()
    manifest_mtime = manifest.stat().st_mtime_ns
    workspace_bytes = _tree_bytes(tmp_path)

    second = _run(_args(archive, apply=True), tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "fully verified" in second.stdout
    assert _tree_bytes(tmp_path) == workspace_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert manifest.stat().st_mtime_ns == manifest_mtime

    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM source_stage_runs WHERE source_id='demo-legacy-vault'"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM artifacts WHERE source_id='demo-legacy-vault'"
        ).fetchone()[0] == 1
    finally:
        con.close()

    page.write_text(page.read_text(encoding="utf-8") + "\n基线之后发生漂移。\n", encoding="utf-8")
    drift_tree = _tree_bytes(tmp_path)
    drift = _run(_args(archive, apply=True), tmp_path)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "post-adoption-live-drift" in drift.stdout
    assert "fully verified" in drift.stdout
    assert _tree_bytes(tmp_path) == drift_tree
    assert manifest.read_bytes() == manifest_bytes


def _log_lines(tmp_path, verb):
    log = tmp_path / "wiki" / "log.md"
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if f"] {verb} |" in ln]


def test_adopt_vault_appends_one_log_line_and_never_duplicates_it(tmp_path):
    """log.md 追加不得破坏"精确重跑全树 byte/mtime no-op"，也不得重复记账。"""
    _vault, _page, archive = _legacy_workspace(tmp_path)
    log = tmp_path / "wiki" / "log.md"
    assert not log.exists()   # legacy vault 从未跑过 init-vault

    first = _run(_args(archive, apply=True), tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    lines = _log_lines(tmp_path, "adopt-vault")
    assert len(lines) == 1, lines
    assert "demo-legacy-vault" in lines[0] and "immutable baseline" in lines[0]
    assert lines[0].startswith("## [")           # 与 ingest/lint/retract 同格式层级
    log_bytes, log_mtime = log.read_bytes(), log.stat().st_mtime_ns

    # ① 完全验证的精确重跑：在加锁前返回，日志与全树都不动。
    second = _run(_args(archive, apply=True), tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "fully verified" in second.stdout
    assert log.read_bytes() == log_bytes and log.stat().st_mtime_ns == log_mtime

    # ② 接管后 live 页正常演进（warning-only 分支）也仍然走加锁前返回：不追加、全树不动。
    page = tmp_path / "wiki" / "domains" / "sql" / "lessons" / "legacy-note.md"
    page.write_text(page.read_text(encoding="utf-8") + "\n接管之后的正常增量。\n",
                    encoding="utf-8")
    drift_tree = _tree_bytes(tmp_path)
    drift = _run(_args(archive, apply=True), tmp_path)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "post-adoption-live-drift" in drift.stdout and "fully verified" in drift.stdout
    assert _log_lines(tmp_path, "adopt-vault") == lines
    assert _tree_bytes(tmp_path) == drift_tree

    # ③ state 已 published 时，证据/source 页的任何缺口都是 fail-closed 违规，
    #    根本不存在"带着已登记 state 再次进锁"的路径——所以 adopt-vault 结构上只会追加一次。
    (tmp_path / "wiki" / "sources" / "demo-legacy-vault.md").unlink()
    refused = _run(_args(archive, apply=True), tmp_path)
    assert refused.returncode == 2
    assert "source-page-missing-after-publish" in refused.stdout
    assert _log_lines(tmp_path, "adopt-vault") == lines


def test_adopt_vault_log_line_does_not_disturb_derived_artifacts(tmp_path):
    """log.md 不进任何派生产物（全部按 published frontmatter 过滤）——这是 no-op 契约的前提。"""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    import wiki_gate

    _vault, _page, archive = _legacy_workspace(tmp_path)
    assert _run(_args(archive, apply=True), tmp_path).returncode == 0
    vault = tmp_path / "wiki"
    assert wiki_gate.derived_violations(vault) == []

    before = _tree_bytes(tmp_path)
    wiki_gate.append_log(vault, "probe", "x", "extra line", "2026-07-29")
    findings = wiki_gate.derived_violations(vault)
    assert findings == [], findings
    # 只有 log.md 变了；派生产物一个字节都没受影响。
    after = _tree_bytes(tmp_path)
    assert set(before) == set(after)
    assert [k for k in before if before[k] != after[k]] == ["wiki/log.md"]


def test_adopt_vault_archive_evidence_and_state_drift_fail_closed(tmp_path):
    # evidence copy drift
    vault, _page, archive = _legacy_workspace(tmp_path)
    assert _run(_args(archive, apply=True), tmp_path).returncode == 0
    evidence = tmp_path / "pipeline-workspace" / "adoptions" / "demo-legacy-vault"
    copied = evidence / "files" / "domains" / "sql" / "lessons" / "legacy-note.md"
    copied.write_bytes(copied.read_bytes() + b"tampered")
    before = _tree_bytes(tmp_path)
    bad_evidence = _run(_args(archive, apply=True), tmp_path)
    assert bad_evidence.returncode != 0
    assert "adoption-evidence-corrupt" in bad_evidence.stdout
    assert _tree_bytes(tmp_path) == before

    # fresh workspace: archive content/hash no longer matches immutable manifest metadata
    vault2, _page2, archive2 = _legacy_workspace(tmp_path / "archive-drift")
    assert _run(_args(archive2, apply=True), tmp_path / "archive-drift").returncode == 0
    _write_archive(archive2, vault2, extras={"wiki/assets/new.bin": b"archive changed"})
    before2 = _tree_bytes(tmp_path / "archive-drift")
    bad_archive = _run(_args(archive2, apply=True), tmp_path / "archive-drift")
    assert bad_archive.returncode != 0
    assert "adoption-manifest-metadata-drift" in bad_archive.stdout
    assert _tree_bytes(tmp_path / "archive-drift") == before2

    # fresh workspace: terminal state drift
    _vault3, _page3, archive3 = _legacy_workspace(tmp_path / "state-drift")
    root3 = tmp_path / "state-drift"
    assert _run(_args(archive3, apply=True), root3).returncode == 0
    db3 = root3 / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db3)
    try:
        con.execute("UPDATE sources SET current_status='done' "
                    "WHERE source_id='demo-legacy-vault'")
        con.commit()
    finally:
        con.close()
    before3 = _tree_bytes(root3)
    bad_state = _run(_args(archive3, apply=True), root3)
    assert bad_state.returncode != 0
    assert "adoption-state-conflict" in bad_state.stdout
    assert _tree_bytes(root3) == before3


def test_adopt_vault_revalidates_complete_page_set_after_lock(tmp_path, monkeypatch):
    vault, _page, archive = _legacy_workspace(tmp_path)
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import locks
    import pipeline

    original_acquire = locks.acquire

    def acquire_then_mutate(*args, **kwargs):
        acquired = original_acquire(*args, **kwargs)
        if acquired:
            late = vault / "domains" / "sql" / "lessons" / "late.md"
            late.write_text(
                "---\nmanaged_by: pipeline\nstatus: published\ntype: lesson\n---\n"
                + ("锁后出现的新知识页。" * 20), encoding="utf-8")
        return acquired

    monkeypatch.setattr(locks, "acquire", acquire_then_mutate)
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    args = SimpleNamespace(
        source="demo-legacy-vault", title="Demo Legacy Vault Baseline",
        domain="demo", baseline_archive=str(archive),
        baseline_sha256=_sha(archive), apply=True)
    try:
        pipeline.cmd_adopt_vault(args)
    except SystemExit as exc:
        assert "post-lock adoption validation failed" in str(exc)
    else:
        raise AssertionError("post-lock page-set drift must fail closed")
    assert not (tmp_path / "pipeline-workspace" / "adoptions").exists()
    assert not (vault / "sources" / "demo-legacy-vault.md").exists()
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    finally:
        con.close()


def test_adopt_vault_reports_mermaid_legacy_debt_as_warning_and_can_apply(tmp_path):
    vault, page, archive = _legacy_workspace(tmp_path)
    page.write_text(page.read_text(encoding="utf-8") +
                    "\n```mermaid\nflowchart LR\nA[\"[[domains/sql/concepts/x|X]]\"]\n```\n",
                    encoding="utf-8")
    _write_archive(archive, vault)

    result = _run(_args(archive, apply=True), tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warning] mermaid-wikilink" in result.stdout
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT current_status FROM sources WHERE source_id='demo-legacy-vault'"
        ).fetchone()[0] == "published"
    finally:
        con.close()


def test_adopt_vault_reports_legacy_topic_coverage_as_warning(tmp_path):
    vault, _page, archive = _legacy_workspace(tmp_path)
    body = ("这是接管前已有概念的完整散文说明，给出定义、适用边界、判断方法与一个最小例子。"
            "它的内容长度足以通过残次页检查，但 legacy 基线尚未用 topic 页面收编这些概念；"
            "接管命令应如实报告后续治理机会，而不能要求用户为了接管历史字节而伪造新摄取阶段的拓扑证据。")
    for i in range(6):
        target = vault / "domains" / "sql" / "concepts" / f"legacy-{i}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\n"
            f"canonical_id: concept.sql.legacy-{i}\ncanonical_name: Legacy {i}\n"
            "domain: sql\nmanaged_by: pipeline\nsource_refs:\n- source: demo-legacy-vault\n"
            "status: published\ntype: concept\n---\n" + body,
            encoding="utf-8")
    _write_archive(archive, vault)

    before = _tree_bytes(tmp_path)
    result = _run(_args(archive), tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warning] concepts-uncovered" in result.stdout
    assert "violations=0" in result.stdout
    assert _tree_bytes(tmp_path) == before
