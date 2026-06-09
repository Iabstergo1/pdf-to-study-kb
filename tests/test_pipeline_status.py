import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def test_status_smoke_runs(tmp_path):
    # 在干净临时 cwd 跑：无 state db 时也应 exit 0 且给出提示
    r = subprocess.run([sys.executable, str(PIPELINE), "status"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "no state" in r.stdout.lower() or r.stdout.strip() == ""


def test_next_smoke_runs(tmp_path):
    r = subprocess.run([sys.executable, str(PIPELINE), "next"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_state_db_path_is_vault_level():
    text = PIPELINE.read_text(encoding="utf-8")
    assert "pipeline-workspace/state/study-kb.sqlite" in text
