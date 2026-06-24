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


# --- dual-audit：source-audit CLI（PyMuPDF + MinerU 双审；MinerU 禁用 → strict fail-closed / dev 降级） ---

def test_source_audit_strict_fail_closed_when_mineru_unavailable(tmp_path, monkeypatch):
    # strict/生产验收：每个 PDF 都要 MinerU structural review；禁用 → 非零退出（不静默回退 PyMuPDF）。
    monkeypatch.setenv("MINERU_DISABLE", "1")
    sid = "p2audit_strict"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    r = _run(["source-audit", "--source", sid, "--strict"], tmp_path)
    assert r.returncode != 0
    blob = (r.stdout + r.stderr)
    assert "dual-audit" in blob.lower() or "install_mineru" in blob


def test_source_audit_nonstrict_marks_degraded(tmp_path, monkeypatch):
    # dev/non-strict：PyMuPDF-only 可产出，但 reconciliation 显式标 degraded / not dual-audited。
    import json
    monkeypatch.setenv("MINERU_DISABLE", "1")
    sid = "p2audit_dev"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    r = _run(["source-audit", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stderr
    recon = json.loads((tmp_path / "pipeline-workspace" / "staging" / sid
                        / "reconciliation.json").read_text(encoding="utf-8"))
    assert recon["degraded"] is True and recon["dual_audited"] is False
    assert recon["review_status"] == "degraded_no_review"
    assert recon["production_accepted"] is False


def test_source_audit_help(tmp_path):
    r = _run(["source-audit", "--help"], tmp_path)
    assert r.returncode == 0 and "--strict" in r.stdout and "--source" in r.stdout


# --- evidence-assembly: arbitration-apply materializes a render decision into route-B assets ---

def test_arbitration_apply_cli_materializes_render(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    import json
    sid = "arbcli"
    raw = tmp_path / f"{sid}.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "page body text here")
    doc.save(str(raw)); doc.close()
    assert _run(["add-source", "--source", sid, "--domain", "d", "--path", str(raw), "--fmt", "pdf"],
                tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    staging.mkdir(parents=True)
    blocks = [{"block_id": "b1", "type": "text", "text": "t", "page": 1, "char_start": 0, "char_end": 10,
               "heading_path": "", "asset_path": None, "risk_flags": [], "chapter_id": "", "source_ref": "p0001#b1"},
              {"block_id": "b2", "type": "text", "text": "MPL w = MPK r", "page": 2, "char_start": 10,
               "char_end": 30, "heading_path": "", "asset_path": None, "risk_flags": [], "chapter_id": "",
               "source_ref": "p0002#b2"}]
    (staging / "blocks.jsonl").write_text("\n".join(json.dumps(b) for b in blocks), encoding="utf-8")
    (staging / "pages.jsonl").write_text("\n".join(json.dumps(p) for p in [
        {"page": 1, "needs_vision": True, "needs_vision_reason": ["formula"]},
        {"page": 2, "needs_vision": False, "needs_vision_reason": []}]), encoding="utf-8")
    (staging / "evidence.json").write_text(json.dumps({"pages": {"2": {}}, "candidates": [2],
        "initial_needs_vision": [1], "reviewer_structural": [2], "final_hard_pages": [1]}), encoding="utf-8")
    (staging / "arbitration").mkdir()
    (staging / "arbitration" / "decisions.json").write_text(json.dumps({"decisions": [
        {"page": 2, "decision": "render", "risk_flags": ["formula"], "reason": "flattened fraction"}]}),
        encoding="utf-8")
    r = _run(["arbitration-apply", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stderr
    blks = [json.loads(l) for l in (staging / "blocks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    b2 = next(b for b in blks if b["page"] == 2)
    assert b2["asset_path"] == "assets/p0002.png" and "arbitrated" in b2["risk_flags"]
    assert (staging / "assets" / "p0002.png").exists()              # the page was actually rendered
    ev = json.loads((staging / "evidence.json").read_text(encoding="utf-8"))
    assert ev["pages"]["2"]["resolution"] == "materialized" and 2 in ev["final_hard_pages"]


def test_arbitration_cli_help(tmp_path):
    assert "--source" in _run(["arbitration-status", "--help"], tmp_path).stdout
    assert "--source" in _run(["arbitration-apply", "--help"], tmp_path).stdout


def test_arbitration_resolve_help(tmp_path):
    out = _run(["arbitration-resolve", "--help"], tmp_path).stdout
    assert "--page" in out and "--decision" in out and "--reason" in out


def test_arbitration_resolve_closes_needs_human(tmp_path):
    # ⑤ needs_human 经 arbitration-resolve 改 render（reason 必填）→ status 列出 → apply 闭环。
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    import json
    sid = "arbresolve"
    raw = tmp_path / f"{sid}.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "page body text here")
    doc.save(str(raw)); doc.close()
    assert _run(["add-source", "--source", sid, "--domain", "d", "--path", str(raw), "--fmt", "pdf"],
                tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    staging.mkdir(parents=True)
    (staging / "blocks.jsonl").write_text(json.dumps(
        {"block_id": "b2", "type": "text", "text": "MPL w = MPK r", "page": 2, "char_start": 0,
         "char_end": 13, "heading_path": "", "asset_path": None, "risk_flags": [], "chapter_id": "",
         "source_ref": "p0002#b2"}), encoding="utf-8")
    (staging / "pages.jsonl").write_text(json.dumps(
        {"page": 2, "needs_vision": False, "needs_vision_reason": []}), encoding="utf-8")
    (staging / "evidence.json").write_text(json.dumps(
        {"pages": {"2": {}}, "candidates": [2], "initial_needs_vision": [], "reviewer_structural": [2],
         "final_hard_pages": []}), encoding="utf-8")
    (staging / "arbitration").mkdir()
    (staging / "arbitration" / "decisions.json").write_text(json.dumps(
        {"decisions": [{"page": 2, "decision": "needs_human", "reason": "ambiguous"}]}), encoding="utf-8")
    # arbitration-status 明确列出 needs_human 页
    st = _run(["arbitration-status", "--source", sid], tmp_path)
    assert "needs_human" in st.stdout and "2" in st.stdout
    # 空 reason → 非零（reason 必填校验）
    assert _run(["arbitration-resolve", "--source", sid, "--page", "2", "--decision", "render",
                 "--reason", ""], tmp_path).returncode != 0
    # resolve render
    r = _run(["arbitration-resolve", "--source", sid, "--page", "2", "--decision", "render",
              "--reason", "real flattened formula"], tmp_path)
    assert r.returncode == 0, r.stderr
    decs = json.loads((staging / "arbitration" / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    assert next(d for d in decs if d["page"] == 2)["decision"] == "render"
    # apply 后闭环（render 物化）
    assert _run(["arbitration-apply", "--source", sid], tmp_path).returncode == 0
    assert (staging / "assets" / "p0002.png").exists()


def test_arbitration_apply_does_not_modify_source_md(tmp_path):
    # ② arbitration-apply 只动 blocks/pages/assets/evidence，绝不改 source.md（不重写主抽取文本）。
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz
    import json
    sid = "arbsrcmd"
    raw = tmp_path / f"{sid}.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "page body text here")
    doc.save(str(raw)); doc.close()
    assert _run(["add-source", "--source", sid, "--domain", "d", "--path", str(raw), "--fmt", "pdf"],
                tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    staging.mkdir(parents=True)
    (staging / "source.md").write_text(
        "<!-- page 1 -->\nintro\n<!-- page 2 -->\nMPL w = MPK r\n", encoding="utf-8")
    (staging / "blocks.jsonl").write_text(json.dumps(
        {"block_id": "b2", "type": "text", "text": "MPL w = MPK r", "page": 2, "char_start": 0,
         "char_end": 13, "heading_path": "", "asset_path": None, "risk_flags": [], "chapter_id": "",
         "source_ref": "p0002#b2"}), encoding="utf-8")
    (staging / "pages.jsonl").write_text(json.dumps(
        {"page": 2, "needs_vision": False, "needs_vision_reason": []}), encoding="utf-8")
    (staging / "evidence.json").write_text(json.dumps(
        {"pages": {"2": {}}, "candidates": [2], "initial_needs_vision": [], "reviewer_structural": [2],
         "final_hard_pages": []}), encoding="utf-8")
    (staging / "arbitration").mkdir()
    (staging / "arbitration" / "decisions.json").write_text(json.dumps({"decisions": [
        {"page": 2, "decision": "render", "risk_flags": ["formula_text_loss"], "reason": "flattened"}]}),
        encoding="utf-8")
    before = (staging / "source.md").read_bytes()
    assert _run(["arbitration-apply", "--source", sid], tmp_path).returncode == 0
    assert (staging / "source.md").read_bytes() == before    # source.md 字节不变


# --- windows 闸门：未闭环双审分歧时 fail-closed（除非显式 --dev-bypass） ---

def _stage_pending_arbitration(tmp_path, sid):
    """搭一个带未仲裁双审分歧的 staging：跑真实预处理到 converted（状态机允许 windowed）+ 塞
    evidence candidate（无 decisions）。闸门只看 evidence.candidates，不校验该页是否真实存在。"""
    import json
    staging = _preprocess_md(tmp_path, sid, "# A\n\nbody\n")
    (staging / "evidence.json").write_text(json.dumps(
        {"pages": {"2": {}}, "candidates": [2], "initial_needs_vision": [],
         "reviewer_structural": [2], "final_hard_pages": []}), encoding="utf-8")
    return staging


def test_windows_fail_closed_on_pending_arbitration(tmp_path):
    sid = "winpend"
    staging = _stage_pending_arbitration(tmp_path, sid)
    r = _run(["windows", "--source", sid], tmp_path)
    assert r.returncode != 0
    assert "未闭环" in (r.stdout + r.stderr) or "un_arbitrated" in (r.stdout + r.stderr)
    assert not (staging / "windows.jsonl").exists()       # 未闭环分歧 → 不产窗


def test_windows_dev_bypass_allows_pending_arbitration(tmp_path):
    sid = "winbypass"
    staging = _stage_pending_arbitration(tmp_path, sid)
    r = _run(["windows", "--source", sid, "--dev-bypass"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (staging / "windows.jsonl").exists()           # 显式 dev bypass → 放行（产物降级）


# --- windows 闸门：PDF 源构窗前必须已完成 source-audit（三件套齐全），否则 fail-closed ---

def test_windows_fail_closed_when_pdf_missing_source_audit(tmp_path):
    # PDF 源跑了 convert 但故意没跑 source-audit → 缺 reconciliation/evidence/queue → 拒绝构窗。
    sid = "winpdfnoaudit"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    r = _run(["windows", "--source", sid], tmp_path)
    assert r.returncode != 0
    blob = r.stdout + r.stderr
    assert "source-audit" in blob and "reconciliation.json" in blob
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    assert not (staging / "windows.jsonl").exists()       # PDF 未双审 → 不产窗


def test_windows_ok_when_pdf_source_audit_complete_no_pending(tmp_path, monkeypatch):
    # 三件套齐全且无 pending candidate（non-strict degraded：MinerU 禁用仍产 evidence/queue，candidates=[]）。
    monkeypatch.setenv("MINERU_DISABLE", "1")
    sid = "winpdfaudit"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    assert _run(["source-audit", "--source", sid], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    assert (staging / "reconciliation.json").exists() and (staging / "evidence.json").exists()
    assert (staging / "arbitration" / "queue.json").exists()
    r = _run(["windows", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (staging / "windows.jsonl").exists()


def test_windows_no_source_audit_required_for_markdown(tmp_path):
    # 非 PDF（markdown）不要求 source-audit bundle：无 evidence.json 也能正常构窗。
    sid = "winmdnoaudit"
    _preprocess_md(tmp_path, sid, "# A\n\nbody\n")
    r = _run(["windows", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    assert (staging / "windows.jsonl").exists()


def test_windows_dev_bypass_skips_pdf_source_audit_gate(tmp_path):
    # --dev-bypass 跳过 PDF 双审存在性闸门（dev 降级路径，不可用于 strict acceptance）。
    sid = "winpdfbypass"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    r = _run(["windows", "--source", sid, "--dev-bypass"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    assert (staging / "windows.jsonl").exists()


def test_windows_fail_closed_when_pdf_missing_parse_report(tmp_path):
    # parse_report.json 缺失也不能让 PDF 绕过闸门：PDF 判定以 state_store 的 source format 为权威。
    sid = "winpdfnoreport"
    _preprocess_pdf(tmp_path, sid)
    assert _run(["source-convert", "--source", sid, "--backend", "pymupdf"], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    (staging / "parse_report.json").unlink()              # 删 parse_report，模拟"靠它判 PDF"的绕过路径
    r = _run(["windows", "--source", sid], tmp_path)
    assert r.returncode != 0
    assert not (staging / "windows.jsonl").exists()       # 仍被判为 PDF → 三件套缺 → 拒绝构窗


# --- ④ show-window 默认输出不污染：只含 risk_flags 标签，不含仲裁 reason/audit（--verbose 才有） ---

def test_show_window_default_excludes_arbitration_reason(tmp_path):
    import json
    sid = "swclean"
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    staging.mkdir(parents=True)
    (staging / "source.md").write_text(
        "<!-- block:b1 page:1 type:text -->\nbody text here\n", encoding="utf-8")
    w = {"window_id": "w0000", "mode": "blocks", "heading_path": "T", "char_start": 0,
         "char_end": 40, "overlap_before": 0, "block_ids": ["b1"], "page_start": 1, "page_end": 1,
         "token_estimate": 10, "contains": ["text"], "assets": [], "risk_flags": ["formula_text_loss"],
         "source_refs": ["p0001#b1"], "chapter_ids": [], "source_id": sid, "chapter_title": ""}
    (staging / "windows.jsonl").write_text(json.dumps(w), encoding="utf-8")
    (staging / "arbitration").mkdir()
    (staging / "arbitration" / "decisions.json").write_text(json.dumps({"decisions": [
        {"page": 1, "decision": "render", "reason": "SECRET_REASON_TEXT flattened fraction"}]}),
        encoding="utf-8")
    r = _run(["show-window", "--source", sid, "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "formula_text_loss" in r.stdout            # 最小风险标签在默认输出
    assert "SECRET_REASON_TEXT" not in r.stdout       # 但仲裁 reason 不在默认输出
    rv = _run(["show-window", "--source", sid, "--window", "w0000", "--verbose"], tmp_path)
    assert rv.returncode == 0, rv.stderr
    assert "SECRET_REASON_TEXT" in rv.stdout          # --verbose 才显示仲裁详情


# --- rebuild-canvas CLI (Task 4) ---

def test_rebuild_canvas_writes_canvas(tmp_path):
    import json
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="d", name="A")          # existing helper writes a concept page (proposed)
    # promote it to published so canvas picks it up:
    import importlib.util
    spec = importlib.util.spec_from_file_location("mdpage", ROOT / "scripts" / "mdpage.py")
    mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)
    cpath = next((vault / "domains" / "d" / "concepts").glob("*.md"))
    meta, body = mp.read_page(cpath); meta["status"] = "published"; mp.write_page(cpath, meta, body)
    r = _run(["rebuild-canvas"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = vault / "knowledge-map.generated.canvas"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert any(n.get("type") == "file" for n in data["nodes"])


def test_rebuild_canvas_no_vault_fail_hard(tmp_path):
    r = _run(["rebuild-canvas"], tmp_path)
    assert r.returncode != 0                            # fail-hard when no wiki/


def test_rebuild_canvas_help(tmp_path):
    assert "rebuild-canvas" in _run(["rebuild-canvas", "--help"], tmp_path).stdout or \
           _run(["rebuild-canvas", "--help"], tmp_path).returncode == 0
