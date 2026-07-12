"""`next --resume-packet` CLI 集成（subprocess，真实预处理链）。

packet 组装/fail-closed 纯函数层在 test_resume_packet.py（本文件拆分自它）；
这里只测 CLI wiring：happy path 输出结构 + 未 ingesting 的 fail-closed 退出码。
"""
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


def test_cli_resume_packet_happy_path(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n\n# B\n\nbbb 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", "note", "--domain", "misc",
                 "--path", str(note), "--fmt", "md"],
                ["profile", "--source", "note"], ["source-convert", "--source", "note"],
                ["windows", "--source", "note"], ["workorder", "--source", "note"],
                ["ingest-start", "--source", "note"]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, r.stderr
    staging = tmp_path / "pipeline-workspace/staging/note"
    (staging / "digest.md").write_text("## RESUME\n- next: w0000\n", encoding="utf-8")
    r = _run(["next", "--source", "note", "--resume-packet"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "=== RESUME_PACKET v1 ===" in r.stdout
    assert "[resume-critical]" in r.stdout
    assert "window-start --source note --window w0000" in r.stdout


def test_cli_resume_packet_fail_closed_and_requires_source(tmp_path):
    r = _run(["next", "--resume-packet"], tmp_path)
    assert r.returncode != 0
    assert "--source" in (r.stdout + r.stderr)
    # 未进入 ingesting 的 source → fail-closed 非零退出
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa\n", encoding="utf-8")
    for cmd in (["add-source", "--source", "note", "--domain", "misc",
                 "--path", str(note), "--fmt", "md"],
                ["profile", "--source", "note"], ["source-convert", "--source", "note"],
                ["windows", "--source", "note"]):
        assert _run(cmd, tmp_path).returncode == 0
    r2 = _run(["next", "--source", "note", "--resume-packet"], tmp_path)
    assert r2.returncode != 0
    assert "ingesting" in (r2.stdout + r2.stderr)
