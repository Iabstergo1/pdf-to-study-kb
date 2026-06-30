import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gh = _load("graph_html")


def _empty():
    return {"version": 2, "scope": "v2.0", "nodes": [], "edges": [], "communities": [],
            "learning_paths": [], "insights": [], "source_spine": [], "stats": {}}


def test_embeds_graph_data_and_escapes_script_end():
    data = _empty()
    data["nodes"] = [{"id": "n", "label": "</script>", "type": "concept", "path": "n.md",
                      "community_id": "c", "weight": 0.5}]
    html = gh.to_html(data)
    assert "<\\/script>" in html                                  # </ 被转义，避免提前闭合
    match = re.search(r'<script id="graph-data" type="application/json">\s*(.*?)\s*</script>', html, re.S)
    assert match
    parsed = json.loads(match.group(1).replace("<\\/script>", "</script>"))
    assert parsed["nodes"][0]["label"] == "</script>"             # 内嵌 JSON 可解析且等于输入


def test_renderer_makes_no_network_calls():
    html = gh.to_html(_empty())
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert 'src="http' not in html                                # 无外部资源/CDN（SVG 命名空间不算）


def test_has_required_controls():
    html = gh.to_html(_empty())
    assert 'id="search"' in html
    assert 'id="community-filter"' in html
    assert 'id="detail"' in html
    assert 'id="learning-path"' in html
    assert 'id="reset"' in html


def test_nodes_link_to_obsidian_and_embed_vault_root(tmp_path):
    # 点击节点 → obsidian://open?path=<vault 绝对路径>/<page path> 跳到对应 Obsidian 笔记
    assert "obsidian://open?path=" in gh.to_html(_empty())
    # 占位符替换不得破坏 JS 属性名 window.__VAULT_ROOT__（赋值左边须保持完好）
    assert 'window.__VAULT_ROOT__ = "' in gh.to_html(_empty(), vault_root="X")
    vault = tmp_path / "wiki"
    vault.mkdir()
    gh.write_html(vault, _empty())
    txt = (vault / "knowledge-graph.generated.html").read_text(encoding="utf-8")
    assert vault.resolve().as_posix() in txt          # 内嵌 vault 绝对路径供 obsidian:// 用
    assert ('window.__VAULT_ROOT__ = "' + vault.resolve().as_posix() + '"') in txt


def test_degraded_mode_flag_toggles_on_size():
    big = _empty()
    big["nodes"] = [{"id": f"n{i}", "label": f"N{i}", "type": "concept", "path": f"{i}.md",
                     "community_id": "c", "weight": 0.5} for i in range(501)]
    assert "__GRAPH_DEGRADED__ = true" in gh.to_html(big)
    assert "id=\"degraded-banner\"" in gh.to_html(big)
    assert "__GRAPH_DEGRADED__ = false" in gh.to_html(_empty())


def test_write_html_emits_file(tmp_path):
    vault = tmp_path / "wiki"
    vault.mkdir()
    out = gh.write_html(vault, _empty())
    assert out.name == "knowledge-graph.generated.html"
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
