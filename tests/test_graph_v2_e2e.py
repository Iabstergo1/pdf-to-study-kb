import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _page(vault, rel, frontmatter, body):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd), "STUDY_KB_GRAPH_TEST_MODE": "1", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd, capture_output=True, text=True, env=env)


def _fixture(root):
    vault = root / "wiki"
    _page(vault, "overview.md", {"type": "overview", "status": "published", "title": "总览"}, "# 总览\n\n[[topics/博弈论基础.md|博弈论基础]]\n[[topics/经典模型.md|经典模型]]\n")
    _page(vault, "topics/博弈论基础.md", {"type": "topic", "status": "published", "domains": ["game"], "title": "博弈论基础", "related_concepts": ["concept.game.game", "concept.game.player"]}, "# 博弈论基础\n\n[[domains/game/concepts/博弈.md|博弈]] 与 [[domains/game/concepts/参与者.md|参与者]]。\n")
    _page(vault, "topics/经典模型.md", {"type": "topic", "status": "published", "domains": ["game"], "title": "经典模型", "related_concepts": ["concept.game.cournot", "concept.game.bertrand"]}, "# 经典模型\n\n[[domains/game/concepts/古诺模型.md|古诺模型]] 与 [[domains/game/concepts/伯特兰模型.md|伯特兰模型]]。\n")
    _page(vault, "domains/game/concepts/博弈.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.game", "canonical_name": "博弈", "source_refs": ["game:2.1"]}, "# 博弈\n\n策略互动。\n")
    _page(vault, "domains/game/concepts/参与者.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.player", "canonical_name": "参与者", "source_refs": ["game:2.1"]}, "# 参与者\n\n决策主体。[[domains/game/concepts/博弈.md|博弈]]\n")
    _page(vault, "domains/game/concepts/古诺模型.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.cournot", "canonical_name": "古诺模型", "source_refs": ["game:5.1"]}, "# 古诺模型\n\n数量竞争。[[domains/game/concepts/伯特兰模型.md|伯特兰模型]] <!-- graph: confidence=inferred relation=contrasts evidence=\"§5.1 数量竞争与价格竞争对照\" -->\n")
    _page(vault, "domains/game/concepts/伯特兰模型.md", {"type": "concept", "status": "published", "domain": "game", "canonical_id": "concept.game.bertrand", "canonical_name": "伯特兰模型", "source_refs": ["game:5.1"]}, "# 伯特兰模型\n\n价格竞争。\n")
    return vault


def test_rebuild_graph_clusters_and_links_obsidian_single_domain(tmp_path):
    vault = _fixture(tmp_path)
    rebuilt = _run(["rebuild-graph"], tmp_path)
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    data = json.loads((vault / "graph-data.generated.json").read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["scope"] == "v2.0"
    # 单一 domain 的书必须分出多个 topic/共引社区，不能全塌进一个 domain 团
    communities = [c for c in data["communities"] if c["id"] != "_unassigned"]
    assert len(communities) >= 2
    assert len({n["community_id"] for n in data["nodes"] if n["type"] == "concept"}) >= 2
    # HTML 是图谱导航入口：内嵌社区标签 + obsidian 跳转；canvas 已移除
    html = (vault / "knowledge-graph.generated.html").read_text(encoding="utf-8")
    assert "博弈论基础" in html and "经典模型" in html
    assert "obsidian://open?path=" in html
    assert not (vault / "knowledge-map.generated.canvas").exists()
    # graph-lint 只校验 graph-data.generated.json，不依赖业务状态机，可在合成 vault 上跑
    linted = _run(["graph-lint"], tmp_path)
    assert linted.returncode == 0, linted.stdout + linted.stderr
