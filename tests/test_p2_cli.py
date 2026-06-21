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


def test_show_window_block_header(tmp_path):
    sid = "p2show"
    _preprocess_md(tmp_path, sid, "# A\n\naaa\n")
    assert _run(["windows", "--source", sid], tmp_path).returncode == 0
    r = _run(["show-window", "--source", sid, "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "window-meta" in r.stdout and "heading_path=A" in r.stdout
    assert "block_ids=" in r.stdout and "aaa" in r.stdout      # 块头 + 原窗正文都在


# --- Spec 2 C5：pipeline CLI auto 路由（MinerU 本机未装 → 天然 fail-closed） ---

def _preprocess_pdf(tmp_path, sid):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    raw = tmp_path / f"{sid}.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "readable body text on this page here")
    doc.save(str(raw)); doc.close()
    assert _run(["add-source", "--source", sid, "--domain", "d",
                 "--path", str(raw), "--fmt", "pdf"], tmp_path).returncode == 0
    assert _run(["profile", "--source", sid], tmp_path).returncode == 0
    return raw


def test_source_convert_backend_pymupdf(tmp_path):
    import json
    sid = "p2bp"
    _preprocess_pdf(tmp_path, sid)
    r = _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "pipeline-workspace" / "staging" / sid / "parse_report.json"
                      ).read_text(encoding="utf-8"))
    assert rep["selected_backend"] == "pymupdf"


def test_source_convert_backend_mineru_unavailable_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_DISABLE", "1")   # 确定性禁用 MinerU（不依赖是否真装）
    sid = "p2bm"
    _preprocess_pdf(tmp_path, sid)
    r = _run(["source-convert", "--source", sid, "--backend", "mineru"], tmp_path)
    assert r.returncode != 0
    assert "install_mineru" in (r.stdout + r.stderr)


def test_source_convert_default_auto_md_stays_markdown(tmp_path):
    import json
    sid = "p2am"
    _preprocess_md(tmp_path, sid, "# A\n\nbody\n")   # _preprocess_md 已跑 source-convert（默认 auto）
    rep = json.loads((tmp_path / "pipeline-workspace" / "staging" / sid / "parse_report.json"
                      ).read_text(encoding="utf-8"))
    assert rep["selected_backend"] == "markdown"


def test_source_convert_docx_auto_mineru_unavailable_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_DISABLE", "1")   # 确定性禁用 MinerU（不依赖是否真装）
    sid = "p2docx"
    raw = tmp_path / f"{sid}.docx"
    raw.write_bytes(b"PK\x03\x04 fake docx")
    assert _run(["add-source", "--source", sid, "--domain", "d",
                 "--path", str(raw), "--fmt", "docx"], tmp_path).returncode == 0
    assert _run(["profile", "--source", sid], tmp_path).returncode == 0   # docx profile → [] 不崩
    r = _run(["source-convert", "--source", sid], tmp_path)               # auto → mineru → 不可用
    assert r.returncode != 0
    assert "install_mineru" in (r.stdout + r.stderr)


# --- Spec 2 C6：windows/show-window MinerU 风险元数据 ---

def test_show_window_block_header_shows_mineru_risk(tmp_path):
    import json
    sid = "p2mru"
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    staging.mkdir(parents=True)
    (staging / "source.md").write_text(
        "<!-- block:b000001 page:1 type:table -->\n<table></table>\n\n", encoding="utf-8")
    w = {"window_id": "w0000", "mode": "blocks", "heading_path": "T", "char_start": 0,
         "char_end": 56, "overlap_before": 0, "block_ids": ["b000001"], "page_start": 1,
         "page_end": 1, "token_estimate": 10, "contains": ["table", "equation"],
         "assets": ["assets/fig1.jpg"], "risk_flags": ["table", "equation", "image"]}
    (staging / "windows.jsonl").write_text(json.dumps(w), encoding="utf-8")
    r = _run(["show-window", "--source", sid, "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "risk_flags=table,equation,image" in r.stdout
    assert "assets=assets/fig1.jpg" in r.stdout
    assert "contains=table,equation" in r.stdout      # 块类型信息


def test_sync_assets_copies_jpg(tmp_path):
    sid = "p2jpg"
    sa_dir = tmp_path / "pipeline-workspace" / "staging" / sid / "assets"
    sa_dir.mkdir(parents=True)
    (sa_dir / "fig1.jpg").write_bytes(b"\xff\xd8jpg")
    r = _run(["sync-assets", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "wiki" / "assets" / sid / "fig1.jpg").exists()   # MinerU 图片(.jpg) 入 vault
