import os
import subprocess
import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
concept_store = _load("concept_store")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _two_domain_vault(tmp_path):
    vault = tmp_path / "wiki"
    concept_store.create_concept(vault, domain="econ", name="Utility")
    concept_store.create_concept(vault, domain="cs", name="效用", aliases=["Utility"])
    return vault


def test_promotion_candidates_lists_and_proposes(tmp_path):
    _two_domain_vault(tmp_path)
    r = _run(["promotion-candidates"], tmp_path)
    assert r.returncode == 0 and "utility" in r.stdout.lower()
    r2 = _run(["promotion-candidates", "--propose"], tmp_path)
    assert r2.returncode == 0
    queue = list((tmp_path / "wiki/Review-Queue").glob("promotion-*.md"))
    assert len(queue) == 1 and "utility" in queue[0].read_text(encoding="utf-8").lower()
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    rows = state_store.list_review_proposals(db)
    assert any(p["kind"] == "promotion-candidate" for p in rows)
    # 概念页本身没被改（绝不自动提升）
    assert (tmp_path / "wiki/domains/econ/concepts/utility.md").exists()


def test_promote_concept_cli(tmp_path):
    _two_domain_vault(tmp_path)
    r = _run(["promote-concept", "--id", "concept.econ.utility"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "wiki/concepts/utility.md").exists()
    assert not (tmp_path / "wiki/domains/econ/concepts/utility.md").exists()
    # 提升后 rebuild-registry 应仍可用（shared 与 cs 同名只算 warning）
    r2 = _run(["rebuild-registry"], tmp_path)
    assert r2.returncode == 0
