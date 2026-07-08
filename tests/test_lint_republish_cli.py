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
    # 新增综合页（靠 --writes 归属，避免判孤儿；G2：综合页必带 source_refs 溯源）
    mdpage.write_page(tmp_path / "wiki/comparisons/x.md",
                      {"type": "comparison", "status": "proposed", "managed_by": "pipeline",
                       "title": "X 对比", "source_refs": [{"source": "note", "sections": ["1"]}]},
                      COMPARISON_BODY)
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
# Task 5: cmd_lint finish hook — graph 生成（graph-data + HTML）+ 发布隔离
# ---------------------------------------------------------------------------

def _setup_one_proposed(tmp_path, sid="note"):
    """ingest_ready + 一个合格 proposed lesson + ingest-done（不含 lint）。返回 vault 路径。"""
    _ingest_ready(tmp_path, sid)
    mdpage.write_page(tmp_path / f"wiki/domains/misc/lessons/{sid}.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": f"{sid} 课", "source": sid}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", sid], tmp_path).returncode == 0
    return tmp_path / "wiki"


def test_lint_finish_builds_graph_html(tmp_path):
    vault = _setup_one_proposed(tmp_path)
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    assert (vault / "knowledge-graph.generated.html").exists()      # HTML 导航入口
    assert (vault / "graph-data.generated.json").exists()
    assert not (vault / "knowledge-map.generated.canvas").exists()  # canvas 已移除


def test_lint_finish_builds_graph_and_passes_graph_lint(tmp_path):
    # publish-path 集成（Knowledge Graph v2.0）：真实状态机驱动 source 到 published 后，
    # publish-isolated 钩子写出 graph-data + 力导向 HTML，且 graph-lint 在真实 vault 上 returncode 0
    # ——验证 A2 门禁（graph_model.topic_membership）+ 钩子集成不打断发布。
    import json
    vault = _setup_one_proposed(tmp_path)
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    gdata = vault / "graph-data.generated.json"
    assert gdata.exists()
    data = json.loads(gdata.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["scope"] == "v2.0"
    assert (vault / "knowledge-graph.generated.html").exists()
    r = _run(["graph-lint"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr           # 真实 vault graph-lint 无 fail-hard


def test_graph_build_failure_does_not_change_lint_exit_code(tmp_path):
    # 图谱构建失败（graph-data 目标预先占成目录）只 warn、保留发布；lint 仍 returncode 0。
    vault = _setup_one_proposed(tmp_path)
    gdir = vault / "graph-data.generated.json"
    gdir.mkdir(parents=True, exist_ok=True)                 # 目标是目录 → write 失败
    (gdir / "keep.txt").write_text("x", encoding="utf-8")
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr           # 图谱失败不改 lint 退出码
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/note.md")
    assert meta["status"] == "published"                    # 发布已完成
    assert gdir.is_dir()                                    # 旧产物（此处目录）保留
    assert "[WARN]" in r.stdout


def test_lint_success_clears_stale_review_queue(tmp_path):
    # 回归（2026-07-04 手动删过一次）：首败写 Review-Queue，修复重跑通过后，
    # 本源过时的 -lint-*.md 失败报告应被自动清理（不清别源的）。
    _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    mdpage.write_page(vault / "domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, "结论 [E-p1] 而且太短\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode != 0
    assert list((vault / "Review-Queue").glob("note-lint-*.md"))
    other = vault / "Review-Queue" / "other-lint-2026-01-01.md"
    other.write_text("别源的报告，不许动\n", encoding="utf-8")
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/a.md")
    mdpage.write_page(vault / "domains/misc/lessons/a.md", meta, GOOD_LESSON)
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert list((vault / "Review-Queue").glob("note-lint-*.md")) == []
    assert other.exists()


QUESTION_LESSON = GOOD_LESSON + (
    "\n> [!question] 自测\n"
    "> 这一节的核心权衡是什么？\n"
    "> > [!success]- 参考答案\n"
    "> > 在直觉与依赖关系之间取舍。\n")


def test_lint_finish_builds_quiz_index(tmp_path):
    # 收尾派生阅读层：lint 通过后重建 quiz-index.generated.md（published 页题干 + 回链，无答案）
    _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, QUESTION_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    qi = (tmp_path / "wiki/quiz-index.generated.md").read_text(encoding="utf-8")
    assert "这一节的核心权衡是什么？" in qi
    assert "domains/misc/lessons/a.md" in qi
    assert "在直觉与依赖关系之间取舍" not in qi  # 索引不泄露答案


def test_quiz_build_failure_does_not_change_lint_exit_code(tmp_path):
    # publish-isolated：quiz 索引目标被占成目录 → 只 warn，发布与 lint 退出码不受影响
    vault = _setup_one_proposed(tmp_path)
    qdir = vault / "quiz-index.generated.md"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "keep.txt").write_text("x", encoding="utf-8")
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/note.md")
    assert meta["status"] == "published"


def test_rebuild_quiz_cli(tmp_path):
    # 手动重建入口（与 rebuild-graph 同型）：rebuild-quiz 独立可跑
    assert _run(["init-vault"], tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/concepts/概念甲.md",
                      {"type": "concept", "status": "published", "managed_by": "pipeline",
                       "canonical_id": "concept.misc.jia", "canonical_name": "概念甲",
                       "domain": "misc"},
                      "正文。\n\n> [!question] 自测\n> 甲的定义要件是什么？\n"
                      "> > [!success]- 参考答案\n> > 要件略。\n")
    r = _run(["rebuild-quiz"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    qi = (tmp_path / "wiki/quiz-index.generated.md").read_text(encoding="utf-8")
    assert "甲的定义要件是什么？" in qi


def test_lint_warns_on_unanswered_question(tmp_path):
    # 有题无解 → 软警告不阻断：lint 仍通过，stdout 出现 [warn] 提示
    _ingest_ready(tmp_path)
    bare_q = GOOD_LESSON + "\n> [!question] 自测\n> 这道题没有给出任何解答？\n"
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, bare_q)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[warn]" in r.stdout and "这道题没有给出任何解答？" in r.stdout
    meta, _ = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")
    assert meta["status"] == "published"
