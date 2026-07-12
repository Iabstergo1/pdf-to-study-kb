"""resume-ingest.ps1 真实烟测（pwsh + Windows .cmd shim，拆分自 test_command_docs.py）。

静态文本契约（脚本旗标白名单等）仍在 test_command_docs.py；这里是唯一真跑
PowerShell 的用例：检测活动 ingest 锁状态行 → 单行 prompt + resume-packet 落盘文件。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_resume_ingest_detects_active_ingest_with_lock_status_line(tmp_path):
    if os.name != "nt":
        pytest.skip("resume-ingest.ps1 smoke uses Windows .cmd shims")
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is required for resume-ingest.ps1 smoke")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "echo note                         misc           ingesting        running\r\n"
        "echo [lock] vault held by note since 2026-06-15T00:00:00+00:00\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    arg_log = tmp_path / "codex.args.txt"
    fake_codex = bin_dir / "codex.cmd"
    fake_codex.write_text(
        "@echo off\r\n"
        "echo %*>>\"%CODEX_ARG_LOG%\"\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CODEX_ARG_LOG": str(arg_log),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }

    r = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(ROOT / "scripts" / "resume-ingest.ps1"),
         "-Agent", "codex", "-Python", str(fake_python)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert r.returncode == 0, r.stdout + r.stderr
    # codex.cmd uses `echo %*`; non-ASCII prompts land in the console code page, so assert only ASCII flags.
    args = arg_log.read_text(encoding="utf-8", errors="replace")
    assert "exec --sandbox workspace-write" in args
    assert "dangerously-bypass" not in args
    # resume packet 走"落盘文件 + 单行 prompt 引用"：多行参数经 npm 的 .cmd shim（cmd.exe）
    # 会在换行处截断命令行——prompt 必须保持单行，packet 内容只经文件传递。
    assert "resume-packet.txt" in args
    assert (ROOT / "tmp" / "resume-packet.txt").exists()
    assert "\n" not in args.strip("\n")  # echo 追加的单条记录：prompt 单行，无第二行
