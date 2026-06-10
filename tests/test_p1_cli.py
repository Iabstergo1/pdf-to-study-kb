import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：状态库/staging 都落 tmp，绝不写真实仓库
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_add_source_then_status(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# T\n\nbody\n", encoding="utf-8")
    r1 = _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    r2 = _run(["status"], tmp_path)
    assert "note" in r2.stdout and "registered" in r2.stdout


def test_source_convert_and_windows_advance_state(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa\n\n# B\n\nbbb\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
    assert (tmp_path / "pipeline-workspace/staging/note/pages.jsonl").exists()  # profile 真实产出
    assert _run(["source-convert", "--source", "note"], tmp_path).returncode == 0
    assert _run(["windows", "--source", "note"], tmp_path).returncode == 0
    # 产物存在
    assert (tmp_path / "pipeline-workspace/staging/note/source.md").exists()
    assert (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").exists()
    # 状态推进到 windowed/done
    r = _run(["status"], tmp_path)
    assert "windowed" in r.stdout
