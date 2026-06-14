"""SessionStart hook 辅助：会话启动/压缩/恢复时，把"进行中 ingest 的续跑指引"注入上下文。

设计：stdlib-only（与 pipeline.py 同约束，可用任意 python 跑）；先看 `pipeline.py status`，
仅当存在 `ingesting` 的 source 时才注入，否则完全静默避免噪声。注入两层：
  1) 机器派生骨干 `pipeline.py next`（任意来源都有、不依赖任何手写约定）；
  2) 各 staging digest 顶部的 `## ⏩ RESUME` 块（ingest skill 每窗维护的富上下文，若存在）。
供 `.claude/settings.json` 的 SessionStart hook 调用（matcher `compact|resume`，stdout 注入上下文）。
文档无关：遍历所有 staging，任意领域/任意来源的中断 ingest 都能被续上。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> str:
    py = sys.executable or "python"
    try:
        return subprocess.run(
            [py, str(ROOT / "scripts" / "pipeline.py"), *args],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return ""


def main() -> None:
    status = _run("status")
    if "ingesting" not in status:
        return  # 无进行中 ingest，静默

    print("【ingest 进行中 — 续跑指引（SessionStart hook 自动注入）】")
    print(status.strip())

    # 机器派生骨干：每个 source 的下一步人工动作（不依赖 digest 约定，任意来源都有）。
    nxt = _run("next").strip()
    if nxt:
        print("\n# 下一步（pipeline.py next）：")
        print(nxt)

    # 富上下文：各 staging digest 顶部 `## ⏩ RESUME` 块（ingest skill 每窗维护）。
    staging = ROOT / "pipeline-workspace" / "staging"
    if not staging.is_dir():
        return
    for d in sorted(p for p in staging.iterdir() if p.is_dir()):
        digest = d / "digest.md"
        if not digest.is_file():
            continue
        text = digest.read_text(encoding="utf-8", errors="replace")
        i = text.find("## ⏩ RESUME")
        if i == -1:
            continue
        j = text.find("\n## ", i + 12)
        block = text[i: j if j != -1 else i + 1500].strip()
        print(f"\n# {d.name} — digest RESUME：")
        print(block)


if __name__ == "__main__":
    main()
