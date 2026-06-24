import importlib.util, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(n):
    s = importlib.util.spec_from_file_location(n, ROOT / "scripts" / f"{n}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


wg = _load("wiki_gate")


def _pg(rel, body, type="concept"):
    return {"rel_path": rel, "meta": {"type": type, "status": "proposed"}, "body": body}


def test_callout_whitelist_ok(tmp_path):
    pages = [_pg("c.md", "> [!warning] 易错\n内容\n\n> [!question]\n自测\n")]
    vs = [v for v in wg.lint_pages(tmp_path, pages) if v["rule"] == "callout-unknown"]
    assert vs == []


def test_callout_unknown_type_fails(tmp_path):
    pages = [_pg("c.md", "> [!banana]\n乱编类型\n")]
    vs = [v for v in wg.lint_pages(tmp_path, pages) if v["rule"] == "callout-unknown"]
    assert len(vs) == 1 and "banana" in vs[0]["detail"]
