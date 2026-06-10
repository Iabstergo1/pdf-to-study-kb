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


def _ingest_ready(tmp_path, sid="note"):
    assert _run(["init-vault"], tmp_path).returncode == 0
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid], ["workorder", "--source", sid],
                ["ingest-start", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, f"{cmd}: {r.stderr}"
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


GOOD_LESSON = ("# A\n\n这一节讲述 aaa 的核心思想，用足够长的干净散文正文展开：先给直觉，"
               "再说明它和相邻概念的依赖关系，最后给出第一遍阅读可以跳过什么、什么时候应该回到原文核对。"
               "这样的长度足以通过空课代理检查。[^e1]\n\n[^e1]: 证据：note §A\n")


def test_lint_pass_promotes_and_indexes(tmp_path):
    db = _ingest_ready(tmp_path)
    # 模拟 /ingest 产出一个合格 proposed lesson
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "published")
    meta, _ = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")
    assert meta["status"] == "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "domains/misc/lessons/a.md" in idx
    log = (tmp_path / "wiki/log.md").read_text(encoding="utf-8")
    assert "lint" in log and "note" in log


def test_lint_fail_blocks_rolls_back_and_queues(tmp_path):
    db = _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    # 既有页被就地 merge：先快照原版，再被改坏
    target = vault / "overview.md"
    original = target.read_text(encoding="utf-8")
    assert _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path).returncode == 0
    mdpage.write_page(target, {"type": "overview", "status": "proposed"}, "被改坏的版本 [E-bad]\n")
    # 另一个坏 proposed 页（裸 E-ID + 太短）
    mdpage.write_page(vault / "domains/misc/lessons/bad.md",
                      {"type": "lesson", "status": "proposed"}, "结论 [E-p1] 而且太短\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "failed")
    # 回滚：overview 恢复原版
    assert target.read_text(encoding="utf-8") == original
    # Review-Queue 文件 + proposals 行
    queue = list((vault / "Review-Queue").glob("note-lint-*.md"))
    assert len(queue) == 1 and "L1" in queue[0].read_text(encoding="utf-8")
    assert len(state_store.list_review_proposals(db, "note")) >= 1
    # 坏页保持 proposed、index 不收录
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/bad.md")
    assert meta["status"] == "proposed"
    assert not (vault / "index.generated.md").exists() or \
        "bad.md" not in (vault / "index.generated.md").read_text(encoding="utf-8")
    # 回流：lint failed 后状态机允许回 ingest_waiting
    state_store.start_stage(db, "note", "ingest_waiting", input_hash="retry")
    assert state_store.get_source(db, "note")["current_stage"] == "ingest_waiting"
