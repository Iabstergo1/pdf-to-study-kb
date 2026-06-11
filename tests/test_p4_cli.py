import json
import os
import subprocess
import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
locks = _load("locks")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _prep_source(tmp_path, sid="note"):
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n\n# B\n\nbbb 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, r.stderr
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


def test_workorder_advances_state_and_writes_yaml(tmp_path):
    db = _prep_source(tmp_path)
    r = _run(["workorder", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "pipeline-workspace/staging/note/workorder.yaml").exists()
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")
    assert state_store.get_work_order(db, "note") is not None
    # 幂等：重跑 [skip]
    r2 = _run(["workorder", "--source", "note"], tmp_path)
    assert "[skip]" in r2.stdout


def test_show_window_prints_window_text(tmp_path):
    _prep_source(tmp_path)
    ws = (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").read_text(encoding="utf-8")
    wid = json.loads(ws.splitlines()[0])["window_id"]
    r = _run(["show-window", "--source", "note", "--window", wid], tmp_path)
    assert r.returncode == 0 and "aaa" in r.stdout


def test_ingest_start_done_lifecycle_with_lock(tmp_path):
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingesting", "running")
    # 第二个 source 在同 vault 被锁拒绝
    _prep_source(tmp_path, sid="note2")
    assert _run(["workorder", "--source", "note2"], tmp_path).returncode == 0
    r2 = _run(["ingest-start", "--source", "note2"], tmp_path)
    assert r2.returncode != 0 and "lock" in (r2.stdout + r2.stderr).lower()
    # window 记账 + 完成
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    r3 = _run(["ingest-done", "--source", "note"], tmp_path)
    assert r3.returncode == 0, r3.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingested", "proposed")
    # 锁已释放：note2 现在能开工
    assert _run(["ingest-start", "--source", "note2"], tmp_path).returncode == 0


def test_stale_lock_visible_and_recoverable(tmp_path):
    # P1 回归（spec §3.3 / 2026-06-11 P9 code review）：
    # status 显示锁持有者；window 记账刷新 heartbeat；next 对 stale 锁给清理建议；
    # unlock 只破 stale 锁（活跃 /ingest 不可破）。
    from datetime import datetime, timedelta, timezone
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    # status 显示锁持有者
    r = _run(["status"], tmp_path)
    assert "lock" in r.stdout.lower() and "note" in r.stdout
    # 活锁不可破
    r = _run(["unlock"], tmp_path)
    assert r.returncode != 0
    assert locks.get(db, scope="vault") is not None
    # window 记账刷新 heartbeat：做旧后 window-start 应让锁不再 stale
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    locks.force_set_heartbeat(db, scope="vault", iso=old)
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert not locks.is_stale(db, scope="vault", ttl_seconds=1800)
    # 再做旧模拟崩溃残留：next 给清理建议，unlock 受控破锁
    locks.force_set_heartbeat(db, scope="vault", iso=old)
    r = _run(["next"], tmp_path)
    assert "unlock" in r.stdout
    r = _run(["unlock"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert locks.get(db, scope="vault") is None


def test_ingest_start_aborts_on_stale_registry(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    # 篡改磁盘 registry → stale
    reg = tmp_path / "wiki" / "concepts" / "_registry.yaml"
    reg.write_text(reg.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode != 0 and "stale" in (r.stdout + r.stderr).lower()


def test_resolve_concept_cli_creates_then_merges(tmp_path):
    _prep_source(tmp_path)
    r1 = _run(["resolve-concept", "--mention", "纳什均衡", "--domain", "misc",
               "--alias", "Nash Equilibrium", "--ref-source", "note", "--ref-sections", "1"],
              tmp_path)
    assert r1.returncode == 0 and "[created]" in r1.stdout
    r2 = _run(["resolve-concept", "--mention", "Nash Equilibrium", "--domain", "misc",
               "--ref-source", "note", "--ref-sections", "2"], tmp_path)
    assert r2.returncode == 0 and "[merged]" in r2.stdout
    pages = list((tmp_path / "wiki/domains/misc/concepts").glob("*.md"))
    assert len(pages) == 1  # 绝不重复建页


def test_check_write_allow_and_deny(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    ok = _run(["check-write", "--source", "note", "--path", "domains/misc/lessons/a.md"], tmp_path)
    assert ok.returncode == 0 and "ALLOW" in ok.stdout
    deny = _run(["check-write", "--source", "note", "--path", "index.generated.md"], tmp_path)
    assert deny.returncode != 0 and "DENY" in deny.stdout


def test_snapshot_page_records_manifest(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    page = tmp_path / "wiki" / "overview.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("OLD", encoding="utf-8")
    r = _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path)
    assert r.returncode == 0, r.stderr
    snaps = list((tmp_path / "pipeline-workspace/snapshots/note").rglob("manifest.json"))
    assert len(snaps) == 1
