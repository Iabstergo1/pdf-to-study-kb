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


def test_lint_scopes_to_own_source_pages(tmp_path):
    # P1 回归（2026-06-11 P9 code review）：lint --source B 不得 promote 其他
    # source 的 proposed 页；无 frontmatter 归属的页靠本 source 的 window write_set 归属。
    db = _ingest_ready(tmp_path, sid="a")
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "a"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "a"], tmp_path).returncode == 0
    _ingest_ready(tmp_path, sid="b")
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/b.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "B 课", "source": "b"}, GOOD_LESSON)
    # b2：无 source 字段，只靠 window-done --writes 归属
    assert _run(["window-start", "--source", "b", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/b2.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "B2 课"}, GOOD_LESSON)
    assert _run(["window-done", "--source", "b", "--window", "w0000",
                 "--writes", '["domains/misc/lessons/b2.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "b"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "b"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # b/b2 发布；a 留在 proposed，source 状态不被 b 的收尾推进
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/b.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/b2.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "proposed"
    assert state_store.get_source(db, "a")["current_status"] != "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "lessons/b.md" in idx and "lessons/a.md" not in idx
    # 跳过的他源页要明示
    assert "lessons/a.md" in r.stdout
    # a 自己收尾后照常发布
    r2 = _run(["lint", "--source", "a"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "published"


def test_lint_blocks_on_unattributed_proposed(tmp_path):
    # P1 余项回归（2026-06-11 P9 review 复验）：孤儿 proposed 页
    # （不归属任何已注册 source）必须阻断 lint——不发布 source、写 Review-Queue、lint/failed。
    # 归属其他 source 的页放行跳过（见 test_lint_scopes_to_own_source_pages），孤儿页阻断。
    db = _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    # 孤儿页：/ingest 忘了 window-done --writes 记账，frontmatter 也无归属
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/orphan.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "孤儿课"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0, "孤儿 proposed 页必须阻断 lint"
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "failed")
    # 谁都不发布
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "proposed"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")[0]["status"] == "proposed"
    queue = list((tmp_path / "wiki/Review-Queue").glob("note-lint-*.md"))
    assert len(queue) == 1 and "unattributed" in queue[0].read_text(encoding="utf-8")
    assert len(state_store.list_review_proposals(db, "note")) >= 1
    # 修复归属后重试：lint 通过、全部发布、source published
    meta, body = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")
    meta["source"] = "note"
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/orphan.md", meta, body)
    r2 = _run(["lint", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")[0]["status"] == "published"
    assert state_store.get_source(db, "note")["current_status"] == "published"


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
                      {"type": "lesson", "status": "proposed", "source": "note"},
                      "结论 [E-p1] 而且太短\n")
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
