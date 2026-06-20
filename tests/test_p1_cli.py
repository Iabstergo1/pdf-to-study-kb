import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

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


def test_source_convert_and_windows_advance_state(tmp_path):
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa\n\n# B\n\nbbb\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
    assert (tmp_path / "pipeline-workspace/staging/note/pages.jsonl").exists()  # profile 真实产出
    assert _run(["source-convert", "--source", "note"], tmp_path).returncode == 0
    assert _run(["windows", "--source", "note"], tmp_path).returncode == 0
    # 产物存在
    assert (tmp_path / "pipeline-workspace/staging/note/source.md").exists()
    assert (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").exists()
    # 状态推进到 windowed/done
    r = _run(["status"], tmp_path)
    assert "windowed" in r.stdout


def test_windows_artifact_records_windows_jsonl_hash(tmp_path):
    # P2 回归（2026-06-11 P9 code review）：windows artifact 的 sha256
    # 必须是 windows.jsonl 本体的 hash，而不是输入 source.md 的 hash。
    note = tmp_path / "raw" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa\n\n# B\n\nbbb\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
    assert _run(["source-convert", "--source", "note"], tmp_path).returncode == 0
    assert _run(["windows", "--source", "note"], tmp_path).returncode == 0
    wj = tmp_path / "pipeline-workspace/staging/note/windows.jsonl"
    expected = hashlib.sha256(wj.read_bytes()).hexdigest()
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    rows = [r for r in state_store.list_artifacts(db, "note") if r["kind"] == "windows"]
    assert rows and rows[0]["sha256"] == expected


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


def test_show_window_prints_assets_header_by_default(tmp_path):
    _make_show_window_staging(tmp_path)
    r = _run(["show-window", "--source", "book", "--window", "w0001"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" in r.stdout
    assert "page=2" in r.stdout
    assert "formula-borderline" in r.stdout
    assert "pipeline-workspace/staging/book/assets/p0002.png" in r.stdout
    assert "![[assets/book/p0002.png]]" in r.stdout
    assert "formula text page two" in r.stdout


def test_show_window_no_header_when_no_needs_vision_page(tmp_path):
    _make_show_window_staging(tmp_path)
    # w0000 只覆盖 page 1（needs_vision=false）→ 无资产头，纯文本
    r = _run(["show-window", "--source", "book", "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" not in r.stdout
    assert "plain text page one" in r.stdout


def test_show_window_plain_suppresses_assets_header(tmp_path):
    _make_show_window_staging(tmp_path)
    r = _run(["show-window", "--source", "book", "--window", "w0001", "--plain"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<!-- route-b-assets" not in r.stdout
    assert "formula text page two" in r.stdout


def test_source_convert_fail_closed_on_scanned_source(tmp_path):
    # 整本扫描件：source-convert 应 fail-closed（不渲染、不生成可 ingest 产物），提示需 OCR route
    note = tmp_path / "raw" / "s.md"
    note.parent.mkdir(parents=True)
    note.write_text("body", encoding="utf-8")
    _run(["add-source", "--source", "scan", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    staging = tmp_path / "pipeline-workspace" / "staging" / "scan"
    staging.mkdir(parents=True)
    (staging / "pages.jsonl").write_text(
        "\n".join('{"page":%d,"text_len":0,"image_count":1,"needs_vision":true}' % i
                  for i in range(1, 11)), encoding="utf-8")
    r = _run(["source-convert", "--source", "scan"], tmp_path)
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
