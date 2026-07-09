"""staging-clean（Phase 4）：staging 磁盘治理，三分类 + 双护栏。

分类：审计/续跑必需（reconciliation/evidence/parse_report/workorder/digest/preflight_eval/
chapters/pages/blocks/source.md/windows + arbitration/ + assets/）一律保留；可再生重物
（mineru_raw/ audit/ diag/ dump_*.txt）可删；**unknown 一律保留并列出**（fail-safe）。
默认 dry-run；--apply 双护栏：source 必须 lint/published 且 assets 与 wiki/assets/<src>/
逐文件 sha256 对齐。隔离：STUDY_KB_ROOT 指向 tmp。
"""
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
mdpage = _load("mdpage")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


GOOD_LESSON = ("# A\n\n这一节讲述 aaa 的核心思想，用足够长的干净散文正文展开：先给直觉，"
               "再说明它和相邻概念的依赖关系，最后给出第一遍阅读可以跳过什么、什么时候应该回到原文核对。"
               "这样的长度足以通过空课代理检查。[^e1]\n\n[^e1]: 证据：note §A\n")


def _preprocessed(tmp_path, sid="note"):
    assert _run(["init-vault"], tmp_path).returncode == 0
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid], ["workorder", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, f"{cmd}: {r.stderr}"
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


def _published(tmp_path, sid="note"):
    db = _preprocessed(tmp_path, sid)
    assert _run(["ingest-start", "--source", sid], tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": sid}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", sid], tmp_path).returncode == 0
    r = _run(["lint", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return db


def _fabricate_junk(tmp_path, sid="note", synced=True):
    """伪造各分类的 staging 产物；synced=True 时把 assets 同步进 vault。"""
    staging = tmp_path / "pipeline-workspace/staging" / sid
    (staging / "mineru_raw" / "sub").mkdir(parents=True, exist_ok=True)
    (staging / "mineru_raw" / "sub" / "big.bin").write_bytes(b"x" * 4096)
    (staging / "audit").mkdir(exist_ok=True)
    (staging / "audit" / "raw.json").write_text("{}", encoding="utf-8")
    (staging / "diag").mkdir(exist_ok=True)
    (staging / "diag" / "p1.png").write_bytes(b"\x89PNGdiag")
    (staging / "dump_ch1.txt").write_text("dump", encoding="utf-8")
    (staging / "arbitration").mkdir(exist_ok=True)
    (staging / "arbitration" / "queue.json").write_text("[]", encoding="utf-8")
    (staging / "assets").mkdir(exist_ok=True)
    (staging / "assets" / "p0001.png").write_bytes(b"\x89PNGasset1")
    (staging / "reconciliation.json").write_text("{}", encoding="utf-8")
    (staging / "_win_range.py").write_text("# stray helper", encoding="utf-8")
    if synced:
        r = _run(["sync-assets", "--source", sid], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
    return staging


def test_dry_run_classifies_and_deletes_nothing(tmp_path):
    _published(tmp_path)
    staging = _fabricate_junk(tmp_path)

    r = _run(["staging-clean", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout
    # 三分类都要出现在报告里；unknown fail-safe 列出但保留。
    assert "mineru_raw" in r.stdout and "dump_ch1.txt" in r.stdout
    assert "_win_range.py" in r.stdout
    # 一个字节都不删。
    assert (staging / "mineru_raw" / "sub" / "big.bin").exists()
    assert (staging / "audit" / "raw.json").exists()
    assert (staging / "dump_ch1.txt").exists()


def test_apply_refuses_unpublished(tmp_path):
    _preprocessed(tmp_path)  # 只到 workorder_ready，未发布
    staging = _fabricate_junk(tmp_path, synced=False)
    r = _run(["staging-clean", "--source", "note", "--apply"], tmp_path)
    assert r.returncode != 0
    assert "published" in (r.stdout + r.stderr)
    assert (staging / "mineru_raw" / "sub" / "big.bin").exists()


def test_apply_refuses_unsynced_assets(tmp_path):
    _published(tmp_path)
    staging = _fabricate_junk(tmp_path, synced=True)
    # 同步之后再塞一张未同步的图 → 护栏必须拦。
    (staging / "assets" / "p0002.png").write_bytes(b"\x89PNGnew")
    r = _run(["staging-clean", "--source", "note", "--apply"], tmp_path)
    assert r.returncode != 0
    assert (staging / "mineru_raw" / "sub" / "big.bin").exists()


def test_apply_deletes_only_regenerables(tmp_path):
    _published(tmp_path)
    staging = _fabricate_junk(tmp_path, synced=True)

    r = _run(["staging-clean", "--source", "note", "--apply"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # 可再生重物：删。
    assert not (staging / "mineru_raw").exists()
    assert not (staging / "audit").exists()
    assert not (staging / "diag").exists()
    assert not (staging / "dump_ch1.txt").exists()
    # 审计件 / 续跑必需 / arbitration / assets / unknown：一个不动。
    assert (staging / "reconciliation.json").exists()
    assert (staging / "arbitration" / "queue.json").exists()
    assert (staging / "assets" / "p0001.png").exists()
    assert (staging / "_win_range.py").exists()
    assert (staging / "source.md").exists()
    assert (staging / "windows.jsonl").exists()


def test_apply_idempotent(tmp_path):
    _published(tmp_path)
    _fabricate_junk(tmp_path, synced=True)
    assert _run(["staging-clean", "--source", "note", "--apply"], tmp_path).returncode == 0
    r2 = _run(["staging-clean", "--source", "note", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr


def test_unknown_source_or_missing_staging_exits_nonzero(tmp_path):
    assert _run(["init-vault"], tmp_path).returncode == 0
    r = _run(["staging-clean", "--source", "nope"], tmp_path)
    assert r.returncode != 0
