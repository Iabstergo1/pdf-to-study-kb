import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

_spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_store)


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：状态库/staging 都落 tmp，绝不写真实仓库
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_add_source_then_status(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# T\n\nbody\n", encoding="utf-8")
    r1 = _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    r2 = _run(["status"], tmp_path)
    assert "note" in r2.stdout and "registered" in r2.stdout


def test_relocate_cleanup_failure_still_releases_lock(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import pipeline
    import shutil
    from types import SimpleNamespace

    root = tmp_path / "project"
    root.mkdir()
    original = tmp_path / "note.md"
    original.write_text("registered", encoding="utf-8")
    assert _run(["add-source", "--source", "note", "--domain", "misc", "--fmt", "md",
                 "--path", str(original)], root).returncode == 0
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    before = state_store.list_artifacts(db, "note")
    target = root / "books/note/input/note.md"
    unlink = Path.unlink

    def denied(file, *args, **kwargs):
        if file == target:
            raise PermissionError("read-only cleanup")
        return unlink(file, *args, **kwargs)

    monkeypatch.setenv("STUDY_KB_ROOT", str(root))
    monkeypatch.setattr(shutil, "copystat", lambda src, dst: Path(dst).write_bytes(b"bad copy"))
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises((SystemExit, PermissionError)):
        pipeline.cmd_relocate_source(SimpleNamespace(source="note", path=None, apply=True))
    with state_store.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    assert original.read_text(encoding="utf-8") == "registered"
    assert state_store.list_artifacts(db, "note") == before


def test_relocate_source_preserves_published_state_and_content(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    original = tmp_path / "原始笔记.md"
    original.write_text("# A\n\nunchanged evidence\n", encoding="utf-8")
    content = original.read_bytes()
    assert _run(["add-source", "--source", "note", "--domain", "misc", "--fmt", "md",
                 "--path", str(original)], root).returncode == 0
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    with state_store.connect(db) as con:
        con.execute("UPDATE sources SET current_stage='lint',current_status='published'")
    before = dict(state_store.get_source(db, "note"))
    artifact = state_store.list_artifacts(db, "note")[0]
    target = root / "books/note/input" / original.name
    args = ["relocate-source", "--source", "note"]
    preview = _run(args, root)
    assert preview.returncode == 0, preview.stderr
    assert original.read_bytes() == content and not target.parent.exists()
    result = _run([*args, "--apply"], root)
    assert result.returncode == 0, result.stderr
    assert not original.exists() and target.read_bytes() == content
    assert dict(state_store.get_source(db, "note")) == before
    assert state_store.list_artifacts(db, "note") == [{**artifact, "path": str(target)}]
    assert _run([*args, "--apply"], root).returncode == 0
    assert state_store.list_artifacts(db, "note") == [{**artifact, "path": str(target)}]
    with state_store.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0


@pytest.mark.parametrize("obstacle", ["modified", "destination", "locked"])
def test_relocate_source_refuses_without_losing_original(tmp_path, obstacle):
    root = tmp_path / "project"
    root.mkdir()
    original = tmp_path / "note.md"
    original.write_text("registered content", encoding="utf-8")
    assert _run(["add-source", "--source", "note", "--domain", "misc", "--fmt", "md",
                 "--path", str(original)], root).returncode == 0
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    artifact = state_store.list_artifacts(db, "note")
    target = root / "books/note/input/note.md"
    if obstacle == "modified":
        original.write_text("new unregistered content", encoding="utf-8")
    elif obstacle == "destination":
        target.parent.mkdir(parents=True)
        target.write_text("do not overwrite", encoding="utf-8")
    else:
        with state_store.connect(db) as con:
            con.execute("INSERT INTO source_locks VALUES ('vault','busy',1,'now','now')")
    before = original.read_bytes()
    result = _run(["relocate-source", "--source", "note", "--apply"], root)
    assert result.returncode != 0
    assert original.read_bytes() == before
    assert state_store.list_artifacts(db, "note") == artifact
    if obstacle == "destination":
        assert target.read_text(encoding="utf-8") == "do not overwrite"
    else:
        assert not target.exists()


@pytest.mark.parametrize("obstacle", [None, "modified", "locked", "outside"])
def test_relocate_renamed_source_updates_only_registered_path(tmp_path, obstacle):
    root = tmp_path / "project"
    original = root / "books/demo/input/old.md"
    original.parent.mkdir(parents=True)
    original.write_text("unchanged source content", encoding="utf-8")
    assert _run(["add-source", "--source", "note", "--domain", "demo", "--fmt", "md",
                 "--path", str(original)], root).returncode == 0
    db = root / "pipeline-workspace/state/study-kb.sqlite"
    with state_store.connect(db) as con:
        con.execute("UPDATE sources SET current_stage='lint',current_status='published'")
    source = dict(state_store.get_source(db, "note"))
    artifact = state_store.list_artifacts(db, "note")
    page = root / "wiki/kept.md"
    page.parent.mkdir(exist_ok=True)
    page.write_text("knowledge stays unchanged", encoding="utf-8")
    target = original.with_name("renamed.md") if obstacle != "outside" else tmp_path / "renamed.md"
    original.rename(target)
    if obstacle == "modified":
        target.write_text("different book", encoding="utf-8")
    if obstacle == "locked":
        with state_store.connect(db) as con:
            con.execute("INSERT INTO source_locks VALUES ('vault','busy',1,'now','now')")
    before = target.read_bytes()
    args = ["relocate-source", "--source", "note", "--path", str(target)]
    preview = _run(args, root)
    assert state_store.list_artifacts(db, "note") == artifact
    result = _run([*args, "--apply"], root)
    if obstacle:
        assert result.returncode != 0
        assert state_store.list_artifacts(db, "note") == artifact
    else:
        assert preview.returncode == 0, preview.stderr
        assert result.returncode == 0, result.stderr
        assert state_store.list_artifacts(db, "note") == [{**artifact[0], "path": str(target)}]
        assert _run([*args, "--apply"], root).returncode == 0
        with state_store.connect(db) as con:
            assert con.execute("SELECT COUNT(*) FROM source_locks").fetchone()[0] == 0
    assert not original.exists() and target.read_bytes() == before
    assert dict(state_store.get_source(db, "note")) == source
    assert page.read_text(encoding="utf-8") == "knowledge stays unchanged"


def test_preprocess_chain_state_artifacts_and_l3_fields(tmp_path):
    # 同一成功预处理链（add→profile→convert→windows）上的三组追加断言（合并自
    # advance-state / windows-jsonl-hash 回归 / L3 字段三条，断言全保留）。
    import json
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa body text\n\n## B\n\nbbb body text\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
    assert (tmp_path / "pipeline-workspace/staging/note/pages.jsonl").exists()  # profile 真实产出
    assert _run(["source-convert", "--source", "note"], tmp_path).returncode == 0
    assert _run(["windows", "--source", "note"], tmp_path).returncode == 0

    # ① 产物存在 + 状态推进到 windowed/done。
    assert (tmp_path / "pipeline-workspace/staging/note/source.md").exists()
    wj = tmp_path / "pipeline-workspace/staging/note/windows.jsonl"
    assert wj.exists()
    r = _run(["status"], tmp_path)
    assert "windowed" in r.stdout

    # ② P2 回归（2026-06-11 P9 code review）：windows artifact 的 sha256 必须是
    # windows.jsonl 本体的 hash，而不是输入 source.md 的 hash。
    expected = hashlib.sha256(wj.read_bytes()).hexdigest()
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    rows = [r for r in state_store.list_artifacts(db, "note") if r["kind"] == "windows"]
    assert rows and rows[0]["sha256"] == expected

    # ③ L3：cmd_windows 读 chapters.json，windows.jsonl 每窗带 source_id/chapter_title/
    # chapter_ids/source_refs（block 窗；markdown 单章 ch00-full）。
    ws = [json.loads(l) for l in wj.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert ws
    for w in ws:
        assert w["source_id"] == "note"
        assert w["mode"] == "blocks"
        assert "chapter_title" in w and "chapter_ids" in w and "source_refs" in w
        assert w["chapter_ids"] == ["ch00-full"]
        # source_refs 与 block_ids 等长对齐
        assert len(w["source_refs"]) == len(w["block_ids"])


def _make_show_window_staging(tmp_path):
    staging = tmp_path / "pipeline-workspace" / "staging" / "book"
    staging.mkdir(parents=True)
    source_md = "<!-- page 1 -->\n\nplain text page one\n\n<!-- page 2 -->\n\nformula text page two\n"
    (staging / "source.md").write_text(source_md, encoding="utf-8")
    cs2 = source_md.index("<!-- page 2 -->")
    (staging / "windows.jsonl").write_text(
        f'{{"window_id":"w0000","heading_path":"","char_start":0,"char_end":{cs2},"overlap_before":0}}\n'
        f'{{"window_id":"w0001","heading_path":"","char_start":{cs2},"char_end":{len(source_md)},"overlap_before":0}}\n',
        encoding="utf-8")
    (staging / "pages.jsonl").write_text(
        '{"page":1,"needs_vision":false,"needs_vision_reason":[],"vision_tier":"none"}\n'
        '{"page":2,"needs_vision":true,"needs_vision_reason":["formula-borderline"],"vision_tier":"nice"}\n',
        encoding="utf-8")
    (staging / "assets").mkdir()
    (staging / "assets" / "p0002.png").write_bytes(b"png")
    return staging


def test_show_window_header_plain_and_read_ledger(tmp_path):
    # 同一 staging 上的 show-window 顺序场景（合并自 默认资产头 / --plain 抑制 / 读窗记账
    # 三条，断言全保留；记账断言改为渐进式，额外覆盖跨窗累积）。
    _make_show_window_staging(tmp_path)
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"

    # ① 默认输出：难页资产头 + 窗文本。
    r = _run(["show-window", "--source", "book", "--window", "w0001"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" in r.stdout
    assert "page=2" in r.stdout
    assert "formula-borderline" in r.stdout
    assert "pipeline-workspace/staging/book/assets/p0002.png" in r.stdout
    assert "formula text page two" in r.stdout
    # D-1 回归：运行时提示绝不输出可复制的源图嵌入串或"嵌原图"措辞——每窗都被写作 LLM
    # 看到的文本曾直接教嵌图，抵消 write-pages 的 D-1 规则（复审抓获）
    assert "![[assets/" not in r.stdout
    assert "嵌原图" not in r.stdout and "图嵌" not in r.stdout
    # 读窗即记账。
    assert state_store.window_read_ids(db, "book") == {"w0001"}

    # ② --plain 抑制资产头（正文仍在）；重复读同窗不炸、不重复记账（幂等）。
    r = _run(["show-window", "--source", "book", "--window", "w0001", "--plain"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" not in r.stdout
    assert "formula text page two" in r.stdout
    assert state_store.window_read_ids(db, "book") == {"w0001"}

    # ③ 读另一窗：记账跨窗累积（空写集跳窗是否真读过窗内容，事后可审计）。
    r = _run(["show-window", "--source", "book", "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert state_store.window_read_ids(db, "book") == {"w0000", "w0001"}


def test_next_prints_writing_contract_reminder_when_ingesting(tmp_path):
    """恢复链路软加固：存在进行中的 ingest 时，next 输出写作契约 hash 提醒
    （软提示；契约执行仍由 lint 硬门禁兜底，不接入状态机阻断）。"""
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa\n", encoding="utf-8")
    for cmd in (["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", "note"], ["source-convert", "--source", "note"],
                ["windows", "--source", "note"], ["workorder", "--source", "note"]):
        assert _run(cmd, tmp_path).returncode == 0
    r = _run(["next"], tmp_path)
    assert "[contract]" not in r.stdout  # 未进 ingesting 不打扰
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    r2 = _run(["next"], tmp_path)
    assert "[contract] write-pages.md sha256=" in r2.stdout


def test_show_window_no_header_when_no_needs_vision_page(tmp_path):
    _make_show_window_staging(tmp_path)
    # w0000 只覆盖 page 1（needs_vision=false）→ 无资产头，纯文本
    r = _run(["show-window", "--source", "book", "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" not in r.stdout
    assert "plain text page one" in r.stdout


def test_source_convert_fail_closed_on_scanned_source(tmp_path):
    # Spec 2 重新协调：整本扫描件 PDF 仍 fail-closed（不生成可 ingest 产物），但路径分两条——
    #   --backend pymupdf（route B）未 --force → 阻断（scanned_source）；
    #   默认 auto → 路由 MinerU，本机未装 → fail-closed（install_mineru.py）。
    note = tmp_path / "raw" / "s.pdf"
    note.parent.mkdir(parents=True)
    note.write_text("body", encoding="utf-8")
    _run(["add-source", "--source", "scan", "--domain", "misc", "--path", str(note), "--fmt", "pdf"], tmp_path)
    staging = tmp_path / "pipeline-workspace" / "staging" / "scan"
    staging.mkdir(parents=True)
    (staging / "pages.jsonl").write_text(
        "\n".join('{"page":%d,"text_len":0,"image_count":1,"needs_vision":true}' % i
                  for i in range(1, 11)), encoding="utf-8")
    # PyMuPDF route B 路径：整本扫描件未 --force → 阻断（auto→MinerU-unavailable 的 fail-closed
    # 见 test_conversion_backend_cli::test_source_convert_docx_auto_mineru_unavailable_fail_closed）。
    r = _run(["source-convert", "--source", "scan", "--backend", "pymupdf"], tmp_path)
    assert r.returncode != 0, r.stdout
    assert "scanned_source" in (r.stdout + r.stderr)


def test_fail_command_unsticks_crashed_running_stage(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# T\n\nbody\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    # 模拟崩溃：库层 start_stage 后不 complete/fail
    import importlib.util
    spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
    state_store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state_store)
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    state_store.start_stage(db, "note", "profiled", input_hash="h-crashed")
    r = _run(["fail", "--source", "note", "--stage", "profiled", "--error", "crashed"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert state_store.get_source(db, "note")["current_status"] == "failed"
    # 救回后该阶段可正常重跑
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
