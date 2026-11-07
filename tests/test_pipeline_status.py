import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PIPELINE = ROOT / "scripts" / "pipeline.py"


def test_command_description_is_complete_and_does_not_create_workspace(tmp_path):
    result = _run(["--describe-commands"], tmp_path)
    assert result.returncode == 0, result.stderr
    catalogue = {entry["name"]: entry for entry in json.loads(result.stdout)}
    assert {"adopt-vault", "reuse-source", "reopen", "skill-adopt", "next"} <= catalogue.keys()
    for name, entry in catalogue.items():
        help_result = _run([name, "--help"], tmp_path)
        assert help_result.returncode == 0
        for field in entry["fields"]:
            assert field["flag"] in help_result.stdout
    assert not (tmp_path / "pipeline-workspace").exists()


def _isolated_env(tmp_path):
    # _workspace_root() 锚点由 STUDY_KB_ROOT 决定（不是 cwd）；用它把 state db 指向空临时目录，
    # 否则测试会读到 repo 根的真实 state db（一次真实 ingest 后即非空）而误判。
    return {**os.environ, "STUDY_KB_ROOT": str(tmp_path)}


def test_arbitration_status_json(tmp_path):
    staging = tmp_path / "pipeline-workspace/staging/demo"
    (staging / "arbitration").mkdir(parents=True)
    for rel, data in {
        "evidence.json": {"candidates": [1, 2]},
        "arbitration/decisions.json": {"decisions": [{"page": 1, "decision": "needs_human"}]},
        "arbitration/queue.json": {"packets": [{"page": 1, "disagreement_kinds": ["figure"],
                                               "page_image": "arbitration/p0001.png"}]},
    }.items():
        (staging / rel).write_text(json.dumps(data), encoding="utf-8")
    r = _run(["arbitration-status", "--source", "demo", "--json"], tmp_path)
    assert r.returncode == 0, r.stderr
    value = json.loads(r.stdout)
    assert value["pending"] == 1
    assert value["pending_pages"] == [2]
    assert value["needs_human"] == [{"page": 1, "reasons": ["figure"],
                                     "page_image": "arbitration/p0001.png"}]


def test_export_site_json_cli(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/overview.md").write_text(
        "---\ntype: overview\nstatus: published\ntitle: 总览\n---\n正文。", encoding="utf-8")
    target = tmp_path / "reader"
    r = _run(["export-site", "--format", "json", "--out", str(target)], tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads((target / "site.json").read_text(encoding="utf-8"))["pages"][0]["path"] == "overview.md"


def test_status_smoke_runs(tmp_path):
    # 无 state db 时也应 exit 0 且给出提示
    r = subprocess.run([sys.executable, str(PIPELINE), "status"],
                       cwd=tmp_path, env=_isolated_env(tmp_path),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "no state" in r.stdout.lower() or r.stdout.strip() == ""


def test_next_smoke_runs(tmp_path):
    r = subprocess.run([sys.executable, str(PIPELINE), "next"],
                       cwd=tmp_path, env=_isolated_env(tmp_path),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _make_db(tmp_path):
    import state_store
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "demo-src", domain="demo", fmt="md")
    return db


def _run(args, tmp_path):
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=tmp_path,
                          env=_isolated_env(tmp_path), capture_output=True, text=True,
                          encoding="utf-8")


def test_status_text_output_is_unchanged(tmp_path):
    # 特征化：加 --json 前后，不带参数的文本输出必须逐字节相同（列宽 28/14/16）
    _make_db(tmp_path)
    r = _run(["status"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "demo-src" + " " * 21 + "demo" + " " * 11 + "registered" + " " * 7 + "done\n"


def test_next_text_output_is_unchanged(tmp_path):
    _make_db(tmp_path)
    r = _run(["next"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "demo-src" + " " * 21 + "registered" + " " * 7 + "-> run: profile\n"


def test_status_json_without_state_db(tmp_path):
    r = _run(["status", "--json"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"sources": [], "lock": None}


def test_status_json_lists_sources_and_lock(tmp_path):
    import locks
    db = _make_db(tmp_path)
    data = json.loads(_run(["status", "--json"], tmp_path).stdout)
    assert data == {"sources": [{"source_id": "demo-src", "domain": "demo",
                                 "current_stage": "registered", "current_status": "done"}],
                    "lock": None}
    locks.acquire(db, scope="vault", holder="ingest:demo-src", pid=1)
    lock = json.loads(_run(["status", "--json"], tmp_path).stdout)["lock"]
    assert lock["holder"] == "ingest:demo-src" and lock["stale"] is False
    assert lock["started_at"] and lock["heartbeat_at"]
    locks.force_set_heartbeat(db, scope="vault", iso="2000-01-01T00:00:00+00:00")
    assert json.loads(_run(["status", "--json"], tmp_path).stdout)["lock"]["stale"] is True


def test_next_json(tmp_path):
    import locks
    db = _make_db(tmp_path)
    assert json.loads(_run(["next", "--json"], tmp_path).stdout) == {
        "actions": [{"source_id": "demo-src", "current_stage": "registered",
                     "next_action": "run: profile"}],
        "stale_lock": False}
    locks.acquire(db, scope="vault", holder="ingest:demo-src", pid=1)
    locks.force_set_heartbeat(db, scope="vault", iso="2000-01-01T00:00:00+00:00")
    assert json.loads(_run(["next", "--json"], tmp_path).stdout)["stale_lock"] is True


def test_next_json_without_state_db(tmp_path):
    assert json.loads(_run(["next", "--json"], tmp_path).stdout) == {"actions": [], "stale_lock": False}


def test_next_json_rejects_resume_packet_and_source(tmp_path):
    _make_db(tmp_path)
    r = _run(["next", "--json", "--source", "demo-src", "--resume-packet"], tmp_path)
    assert r.returncode != 0
    assert "--json" in (r.stderr + r.stdout)


def test_ingest_stats_counts_planned_windows_before_ingest(tmp_path):
    _make_db(tmp_path)
    staging = tmp_path / "pipeline-workspace/staging/demo-src"
    staging.mkdir(parents=True)
    (staging / "windows.jsonl").write_text('{"window_id":"w0000"}\n{"window_id":"w0001"}\n', encoding="utf-8")
    result = _run(["ingest-stats", "--source", "demo-src", "--json"], tmp_path)
    assert result.returncode == 0, result.stderr
    windows = json.loads(result.stdout)["windows"]
    assert windows["planned"] == 2
    assert windows["total"] == windows["finished"] == 0


def test_source_goal_and_current_evidence_are_source_scoped(tmp_path):
    original = tmp_path / "book.md"
    original.write_text("正文", encoding="utf-8")
    for source, goal in [("one", "只关注本书的案例"), ("two", "")]:
        args = ["add-source", "--source", source, "--domain", "demo", "--fmt", "md", "--path", str(original)]
        result = _run(args + (["--purpose", goal] if goal else []), tmp_path)
        assert result.returncode == 0, result.stderr
    staging = tmp_path / "pipeline-workspace/staging/one"
    (staging / "pages.jsonl").write_text('{"page":1,"needs_vision":true}\n{"page":2,"needs_vision":true}\n', encoding="utf-8")
    (staging / "parse_report.json").write_text('{"needs_vision_pages":[1]}', encoding="utf-8")
    one = json.loads(_run(["ingest-stats", "--source", "one", "--json"], tmp_path).stdout)
    two = json.loads(_run(["ingest-stats", "--source", "two", "--json"], tmp_path).stdout)
    assert one["learning_goal"] == "只关注本书的案例\n"
    assert two["learning_goal"] == ""
    assert one["evidence"] == {"pages": 2, "needs_vision": 2}
    assert two["evidence"] == {"pages": None, "needs_vision": None}
    assert not (tmp_path / "wiki/_meta/purpose.md").exists()
