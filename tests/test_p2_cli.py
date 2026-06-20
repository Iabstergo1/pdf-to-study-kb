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
state_store = _load("state_store")


def _preprocess_md(tmp_path, sid, body):
    """真实 CLI 预处理到 converted：add-source → profile → source-convert，返回 staging 目录。"""
    raw = tmp_path / f"{sid}.md"
    raw.write_text(body, encoding="utf-8")
    assert _run(["add-source", "--source", sid, "--domain", "d",
                 "--path", str(raw), "--fmt", "md"], tmp_path).returncode == 0
    assert _run(["profile", "--source", sid], tmp_path).returncode == 0
    r = _run(["source-convert", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "pipeline-workspace" / "staging" / sid


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


# --- Spec 1：pipeline 升级（Task 8/9/10） ---

def test_source_convert_records_blocks_and_parse_report(tmp_path):
    sid = "p2blk"
    staging = _preprocess_md(tmp_path, sid, "# A\n\nbody\n")
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    kinds = {a["kind"] for a in state_store.list_artifacts(db, sid)}
    assert "blocks" in kinds and "parse_report" in kinds
    assert (staging / "blocks.jsonl").exists()
    assert (staging / "parse_report.json").exists()


def test_windows_block_mode_when_blocks_present(tmp_path):
    import json
    sid = "p2win"
    _preprocess_md(tmp_path, sid, "# A\n\naaa\n\n# B\n\nbbb\n")
    assert _run(["windows", "--source", sid], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    ws = [json.loads(l) for l in
          (staging / "windows.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert ws and all(w["mode"] == "blocks" for w in ws)
    assert all("block_ids" in w for w in ws)
