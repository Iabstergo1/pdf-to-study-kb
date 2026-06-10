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


mdpage = _load("mdpage")
concept_store = _load("concept_store")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：vault/状态库都落 tmp
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _mk_concept(vault, *, domain, name, aliases=(), cid=None, filename=None):
    cid = cid or concept_store.canonical_id(domain, name, aliases)
    slug = filename or cid.rsplit(".", 1)[1]
    rel = (Path("concepts") / f"{slug}.md") if domain == "shared" \
        else Path("domains") / domain / "concepts" / f"{slug}.md"
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases), "scope": "shared" if domain == "shared" else "domain",
            "domain": domain, "source_refs": [], "page_path": rel.as_posix(),
            "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(Path(vault) / rel, meta, f"# {name}\n")


def test_rebuild_registry_writes_derived_files(tmp_path):
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    _mk_concept(vault, domain="shared", name="期望效用")
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (vault / "concepts" / "_registry.yaml").exists()
    assert (vault / "aliases.md").exists()
    assert "2 concepts" in r.stdout and "1 shared" in r.stdout


def test_rebuild_registry_refuses_on_duplicate_canonical_id(tmp_path):
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="d", name="A", cid="concept.d.x")
    _mk_concept(vault, domain="d", name="B", cid="concept.d.x", filename="y")
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode != 0
    assert "duplicate canonical_id" in (r.stdout + r.stderr)
    assert not (vault / "concepts" / "_registry.yaml").exists()  # 损坏时不写派生


def test_rebuild_registry_no_vault_yet(tmp_path):
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode == 0
    assert "no wiki" in r.stdout.lower()
