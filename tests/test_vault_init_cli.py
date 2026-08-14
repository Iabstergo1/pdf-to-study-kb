import os
import subprocess
import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

_SPEC = importlib.util.spec_from_file_location("mdpage", ROOT / "scripts" / "mdpage.py")
mdpage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mdpage)

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


def test_export_anki_cli(tmp_path):
    assert _run(["init-vault"], tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/concepts/概念甲.md",
                      {"type": "concept", "status": "published", "managed_by": "pipeline",
                       "canonical_id": "concept.misc.jia", "canonical_name": "概念甲",
                       "domain": "misc", "source_refs": [{"source": "note"}]},
                      "正文。\n\n> [!question] 自测\n> 甲的定义要件是什么？\n"
                      "> > [!success]- 参考答案\n> > 要件略。\n")
    r = _run(["export-anki"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    tsv = (tmp_path / "wiki/anki-export.generated.tsv").read_text(encoding="utf-8")
    assert "甲的定义要件是什么？" in tsv
    assert "要件略。" in tsv
    assert "source::note" in tsv
