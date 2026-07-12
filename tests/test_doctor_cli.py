"""运维加固 CLI（Phase 3）：window-done --writes-file + reset-source。

--writes-file：从 UTF-8 文件读 JSON 数组，绕开 Windows `conda run` 吞双引号导致
write_set_json 损坏的已知坑（与 --writes 显式互斥，不静默优先）。
reset-source：forward-only 状态机的确定性回退（默认 dry-run；只删下游 stage-run
缓存行 + 插 reset 审计行，不动 ingest_progress/artifacts/work_orders/review_proposals）。
隔离：STUDY_KB_ROOT 指向 tmp。
"""
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


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _preprocessed(tmp_path, sid="note"):
    """预处理链到 workorder_ready/done（不 ingest-start，锁空闲，可安全 reset）。"""
    assert _run(["init-vault"], tmp_path).returncode == 0
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid], ["workorder", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, f"{cmd}: {r.stderr}"
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


def _stage_runs(db, sid):
    con = state_store.connect(db)
    try:
        rows = con.execute(
            "SELECT stage,status FROM source_stage_runs WHERE source_id=? ORDER BY id",
            (sid,)).fetchall()
        return [(r["stage"], r["status"]) for r in rows]
    finally:
        con.close()


# ---- window-done --writes-file ----

def _ingesting(tmp_path, sid="note"):
    db = _preprocessed(tmp_path, sid)
    assert _run(["ingest-start", "--source", sid], tmp_path).returncode == 0
    assert _run(["window-start", "--source", sid, "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    return db


def test_window_done_writes_file_rejection_matrix_then_roundtrip(tmp_path):
    # 同一 running window 上的参数拒绝矩阵 + 最终成功 roundtrip（合并自 4 条，断言全保留）：
    # 错误输入都不会结束窗口，所以可顺序验证，只搭一次 ingesting 环境。
    db = _ingesting(tmp_path)

    # ① --writes 与 --writes-file 显式互斥（不静默优先）。
    wf = tmp_path / "writes.json"
    wf.write_text('["a.md"]', encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes", '["a.md"]', "--writes-file", str(wf)], tmp_path)
    assert r.returncode != 0
    assert "互斥" in (r.stdout + r.stderr)

    # ② 损坏 JSON（被吞引号后的典型形态）→ fail-fast，窗口仍 running、没有存入损坏值。
    wf.write_text("[a.md]", encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(wf)], tmp_path)
    assert r.returncode != 0
    assert "JSON" in (r.stdout + r.stderr)
    w = next(x for x in state_store.window_states(db, "note") if x["window_id"] == "w0000")
    assert w["status"] == "running" and not w["write_set_json"]

    # ③ 文件不存在 → 非零退出，窗口仍不受影响。
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(tmp_path / "nope.json")], tmp_path)
    assert r.returncode != 0

    # ④ 合法 UTF-8 JSON 数组 → 成功 roundtrip：窗口 finished、write_set 精确落库。
    writes = ["domains/misc/lessons/a.md", "domains/misc/concepts/b.md"]
    wf.write_text(json.dumps(writes), encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(wf)], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    w = next(x for x in state_store.window_states(db, "note") if x["window_id"] == "w0000")
    assert w["status"] == "finished"
    assert json.loads(w["write_set_json"]) == writes


# ---- reset-source ----

def test_reset_source_dry_run_then_apply_preserves_ledgers_and_allows_rerun(tmp_path):
    # 同一预处理环境上的状态推进场景（合并自 dry-run / apply+rerun / preserve-ledgers 三条，
    # 断言全保留）：dry-run 无变化 → apply 删下游缓存行 → 账本不动 → 同输入真正重跑。
    db = _preprocessed(tmp_path)
    state_store.add_review_proposal(db, "note", target_path="x.md",
                                    kind="broken-link", reason="r")

    # ① 默认 dry-run：打印 plan，但 stage-run / source 行一个字节不变。
    before_runs = _stage_runs(db, "note")
    before_src = dict(state_store.get_source(db, "note"))
    r = _run(["reset-source", "--source", "note", "--to", "registered"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout
    assert _stage_runs(db, "note") == before_runs
    assert dict(state_store.get_source(db, "note")) == before_src

    # ② --apply：回到 registered/done；下游 stage-run 缓存行全删（否则同 input_hash 永远
    # [skip]，reset 无意义）；留 reset 审计行。
    r = _run(["reset-source", "--source", "note", "--to", "registered", "--apply"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("registered", "done")
    runs = _stage_runs(db, "note")
    assert all(stage == "reset" for stage, _ in runs), runs

    # ③ review_proposals / work_orders 是历史账本，reset 绝不动。
    assert len(state_store.list_review_proposals(db, "note")) == 1
    assert state_store.get_work_order(db, "note") is not None

    # ④ 同一输入重跑 profile：不再被缓存跳过，真正重跑成功。
    r2 = _run(["profile", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    src2 = state_store.get_source(db, "note")
    assert (src2["current_stage"], src2["current_status"]) == ("profiled", "done")
    assert ("profiled", "done") in _stage_runs(db, "note")


def test_reset_source_refuses_running_or_locked(tmp_path):
    _ingesting(tmp_path)  # ingest-start 持锁 + ingesting/running
    r = _run(["reset-source", "--source", "note", "--to", "registered", "--apply"], tmp_path)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert ("lock" in out) or ("running" in out)


def test_reset_source_rejects_bad_target_and_unknown_source(tmp_path):
    _preprocessed(tmp_path)
    r = _run(["reset-source", "--source", "note", "--to", "ingesting", "--apply"], tmp_path)
    assert r.returncode != 0  # ingest 段禁止 reset 进入（有 reopen）
    r2 = _run(["reset-source", "--source", "nope", "--to", "registered"], tmp_path)
    assert r2.returncode != 0
