"""pipeline_commit 元数据（B-01）：记录产出证据的代码版本。

零迁移契约：新字段可选，缺失视为 unknown；不提升 ARTIFACT_VERSION、不重写既存 artifact。
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def pipeline_commit(repo_root: str | Path | None = None) -> str:
    """产出证据的代码版本。

    返回当前 HEAD；工作树不干净时返回 ``'<commit>-dirty'``；无法确定时返回
    ``'unknown'``（git 不可用、目录不在仓库内、仓库尚无提交）。

    ``repo_root`` 缺省为本模块所在的仓库根，因此把 vault 放在仓库外（``STUDY_KB_ROOT``
    指向别处）时仍能取到流水线自身的版本。取不到时一律降级为 ``'unknown'``，不抛异常——
    记录代码版本是为了让证据可复现，不应该反过来阻断证据的产出。
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=10)
    except Exception:
        return "unknown"
    if head.returncode != 0 or not head.stdout.strip():
        return "unknown"
    commit = head.stdout.strip()
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(root),
            capture_output=True, text=True, timeout=10)
    except Exception:
        return commit
    if status.returncode == 0 and status.stdout.strip():
        return f"{commit}-dirty"
    return commit
