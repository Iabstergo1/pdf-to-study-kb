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


COMPARISON_BODY = ("## 结论\n\n两种做法各有取舍，按场景选型。\n\n## 对比维度\n\n"
                   "| 维度 | A | B |\n|---|---|---|\n| 速度 | 快 | 慢 |\n\n"
                   "## 适用场景\n\nA 适合高频小数据，B 适合一次性大批量。\n\n"
                   "## 相关概念\n\n见上文两条路径。\n")


def test_reopen_enables_incremental_publish(tmp_path):
    # 通用"重开来源做增量补充"端到端：已发布源经 reopen 后再入库新综合页，
    # 旧 published 页不受影响、不被回滚；reopen 刷新 workorder 使 ingest-start registry 校验通过。
    db = _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    assert state_store.get_source(db, "note")["current_status"] == "published"

    # reopen：状态机重置 + workorder 刷新
    r = _run(["reopen", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")

    # 增量开工：registry 校验通过证明 workorder 已据当前 vault 重建
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w-reopen-0", "--hash", "h"],
                tmp_path).returncode == 0
    # 新增综合页（无 source frontmatter，靠 --writes 归属，避免判孤儿）
    mdpage.write_page(tmp_path / "wiki/comparisons/x.md",
                      {"type": "comparison", "status": "proposed", "managed_by": "pipeline",
                       "title": "X 对比"}, COMPARISON_BODY)
    assert _run(["window-done", "--source", "note", "--window", "w-reopen-0",
                 "--writes", '["comparisons/x.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r2 = _run(["lint", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    # 新页发布；旧页仍 published（增量、不回滚）
    assert mdpage.read_page(tmp_path / "wiki/comparisons/x.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "comparisons/x.md" in idx and "domains/misc/lessons/a.md" in idx


def test_sync_assets_copies_pngs_to_vault(tmp_path):
    # 通用：把 staging/<src>/assets 难页 PNG 复制进 wiki/assets/<src>/，公式嵌图才不断链。
    assert _run(["init-vault"], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace/staging/src1/assets"
    staging.mkdir(parents=True)
    (staging / "p0001.png").write_bytes(b"\x89PNG\r\n fakeimg 1")
    (staging / "p0002.png").write_bytes(b"\x89PNG\r\n fakeimg 2")
    r = _run(["sync-assets", "--source", "src1"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "wiki/assets/src1/p0001.png").read_bytes() == b"\x89PNG\r\n fakeimg 1"
    assert (tmp_path / "wiki/assets/src1/p0002.png").exists()
    # 幂等：已存在同 hash 不再复制
    r2 = _run(["sync-assets", "--source", "src1"], tmp_path)
    assert r2.returncode == 0 and "synced 0" in r2.stdout


def test_reopen_rejected_before_first_ingest_cli(tmp_path):
    # 还没第一次入库完成（停在 workorder_ready）reopen 必须报错退出
    _ingest_ready(tmp_path)  # 已推进到 ingesting
    # 直接对一个仅预处理完的新源 reopen
    note = tmp_path / "raw" / "n2.md"
    note.write_text("# B\n\nbbb\n", encoding="utf-8")
    for cmd in (["add-source", "--source", "n2", "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", "n2"], ["source-convert", "--source", "n2"],
                ["windows", "--source", "n2"], ["workorder", "--source", "n2"]):
        assert _run(cmd, tmp_path).returncode == 0
    r = _run(["reopen", "--source", "n2"], tmp_path)
    assert r.returncode != 0, "未完成首轮入库的源不可 reopen"


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
    # 自动 harvest：lint 失败即刷新 skill backlog（零-LLM，不必手敲 skill-mine；Claude/Codex 同生效）
    backlog = tmp_path / "pipeline-workspace/skill-evolution/backlog.yaml"
    assert backlog.exists() and "L1" in backlog.read_text(encoding="utf-8")
    # 坏页保持 proposed、index 不收录
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/bad.md")
    assert meta["status"] == "proposed"
    assert not (vault / "index.generated.md").exists() or \
        "bad.md" not in (vault / "index.generated.md").read_text(encoding="utf-8")
    # 回流：lint failed 后状态机允许回 ingest_waiting
    state_store.start_stage(db, "note", "ingest_waiting", input_hash="retry")
    assert state_store.get_source(db, "note")["current_stage"] == "ingest_waiting"


# ---------------------------------------------------------------------------
# Task 5: cmd_lint finish hook — canvas 生成 + 发布隔离
# ---------------------------------------------------------------------------

def _setup_one_proposed(tmp_path, sid="note"):
    """ingest_ready + 一个合格 proposed lesson + ingest-done（不含 lint）。返回 vault 路径。"""
    _ingest_ready(tmp_path, sid)
    mdpage.write_page(tmp_path / f"wiki/domains/misc/lessons/{sid}.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": f"{sid} 课", "source": sid}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", sid], tmp_path).returncode == 0
    return tmp_path / "wiki"


def test_lint_finish_builds_canvas(tmp_path):
    vault = _setup_one_proposed(tmp_path)
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    assert (vault / "knowledge-map.generated.canvas").exists()


def test_lint_canvas_failure_does_not_break_publish(tmp_path):
    vault = _setup_one_proposed(tmp_path)
    # 用"目标路径预先是个目录"制造真实的 write 失败（跨子进程有效，monkeypatch 不行）
    canvas_path = vault / "knowledge-map.generated.canvas"
    canvas_path.mkdir(parents=True, exist_ok=True)
    (canvas_path / "keep.txt").write_text("old", encoding="utf-8")  # 非空目录，确保 write_text 失败
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr          # canvas 失败不回滚发布
    assert (vault / "index.generated.md").exists()          # 发布已完成
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/note.md")
    assert meta["status"] == "published"                    # lesson 已发布
    assert canvas_path.is_dir()                             # 旧 canvas（此处是目录）被保留
    assert "[WARN]" in r.stdout                             # 打印了 warning
