import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_check_session_pass_and_fail(tmp_path):
    d = tmp_path / "pipeline-workspace" / "query-sessions" / "qs-001"
    d.mkdir(parents=True)
    (d / "question.md").write_text("# Q\n", encoding="utf-8")
    (d / "answer.md").write_text("# A\n", encoding="utf-8")
    ok = _run(["check-session", "--id", "qs-001"], tmp_path)
    assert ok.returncode == 0 and "[OK]" in ok.stdout
    # saved 契约未满足 → exit 1 且列出问题
    fail = _run(["check-session", "--id", "qs-001", "--saved"], tmp_path)
    assert fail.returncode != 0 and "decision.md" in fail.stdout
    # 不存在的 session
    nope = _run(["check-session", "--id", "qs-404"], tmp_path)
    assert nope.returncode != 0
