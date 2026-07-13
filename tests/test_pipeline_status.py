import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _isolated_env(tmp_path):
    # _workspace_root() 锚点由 STUDY_KB_ROOT 决定（不是 cwd）；用它把 state db 指向空临时目录，
    # 否则测试会读到 repo 根的真实 state db（一次真实 ingest 后即非空）而误判。
    return {**os.environ, "STUDY_KB_ROOT": str(tmp_path)}


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
