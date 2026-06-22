import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

DIRS = ["_meta", "domains", "concepts", "topics", "comparisons", "synthesis",
        "sources", "assets", "Review-Queue"]


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_init_vault_creates_skeleton_and_seeds(tmp_path):
    r = _run(["init-vault"], tmp_path)
    assert r.returncode == 0, r.stderr
    vault = tmp_path / "wiki"
    for d in DIRS:
        assert (vault / d).is_dir(), f"missing dir: {d}"
    assert "## 核心概念地图" in (vault / "overview.md").read_text(encoding="utf-8")
    assert (vault / "log.md").exists()
    assert (vault / "_meta" / "purpose.md").exists()
    # Obsidian 图谱配置随每库自动落地（任意领域通用：按页面 type 着色）
    import json
    graph = json.loads((vault / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    queries = [g["query"] for g in graph["colorGroups"]]
    assert '["type":"concept"]' in queries and '["type":"topic"]' in queries
    assert (vault / ".obsidian" / "app.json").exists()


def test_init_vault_idempotent_never_overwrites(tmp_path):
    _run(["init-vault"], tmp_path)
    overview = tmp_path / "wiki" / "overview.md"
    overview.write_text("HUMAN EDITED\n", encoding="utf-8")
    r = _run(["init-vault"], tmp_path)
    assert r.returncode == 0
    assert overview.read_text(encoding="utf-8") == "HUMAN EDITED\n"  # 绝不覆盖已有文件
