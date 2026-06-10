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
