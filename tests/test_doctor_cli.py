"""运维加固 CLI（Phase 3）：window-done --writes-file + reset-source。

--writes-file：从 UTF-8 文件读 JSON 数组，绕开 Windows `conda run` 吞双引号导致
write_set_json 损坏的已知坑（与 --writes 显式互斥，不静默优先）。
reset-source：forward-only 状态机的确定性回退（默认 dry-run；只删下游 stage-run
缓存行 + 插 reset 审计行，不动 ingest_progress/artifacts/work_orders/review_proposals）。
隔离：STUDY_KB_ROOT 指向 tmp。
"""
import json
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


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _preprocessed(tmp_path, sid="note"):
    """预处理链到 workorder_ready/done（不 ingest-start，锁空闲，可安全 reset）。"""
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


def _stage_runs(db, sid):
    con = state_store.connect(db)
    try:
        rows = con.execute(
            "SELECT stage,status FROM source_stage_runs WHERE source_id=? ORDER BY id",
            (sid,)).fetchall()
        return [(r["stage"], r["status"]) for r in rows]
    finally:
        con.close()


# ---- window-done --writes-file ----

def _ingesting(tmp_path, sid="note"):
    db = _preprocessed(tmp_path, sid)
    assert _run(["ingest-start", "--source", sid], tmp_path).returncode == 0
    assert _run(["window-start", "--source", sid, "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    return db


def test_window_done_writes_file_rejection_matrix_then_roundtrip(tmp_path):
    # 同一 running window 上的参数拒绝矩阵 + 最终成功 roundtrip（合并自 4 条，断言全保留）：
    # 错误输入都不会结束窗口，所以可顺序验证，只搭一次 ingesting 环境。
    db = _ingesting(tmp_path)

    # ① --writes 与 --writes-file 显式互斥（不静默优先）。
    wf = tmp_path / "writes.json"
    wf.write_text('["a.md"]', encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes", '["a.md"]', "--writes-file", str(wf)], tmp_path)
    assert r.returncode != 0
    assert "互斥" in (r.stdout + r.stderr)

    # ② 损坏 JSON（被吞引号后的典型形态）→ fail-fast，窗口仍 running、没有存入损坏值。
    wf.write_text("[a.md]", encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(wf)], tmp_path)
    assert r.returncode != 0
    assert "JSON" in (r.stdout + r.stderr)
    w = next(x for x in state_store.window_states(db, "note") if x["window_id"] == "w0000")
    assert w["status"] == "running" and not w["write_set_json"]

    # ③ 文件不存在 → 非零退出，窗口仍不受影响。
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(tmp_path / "nope.json")], tmp_path)
    assert r.returncode != 0

    # ④ 合法 UTF-8 JSON 数组 → 成功 roundtrip：窗口 finished、write_set 精确落库。
    # （本轮读窗校验：收窗前须 show-window——①②③ 各在更早的校验分支失败，不需要读）
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    writes = ["domains/misc/lessons/a.md", "domains/misc/concepts/b.md"]
    for rel in writes:              # 台账↔磁盘对账：记的页必须真在磁盘上（window-done 时页已写好）
        p = tmp_path / "wiki" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    wf.write_text(json.dumps(writes), encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes-file", str(wf)], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    w = next(x for x in state_store.window_states(db, "note") if x["window_id"] == "w0000")
    assert w["status"] == "finished"
    assert json.loads(w["write_set_json"]) == writes


# ---- reset-source ----

def test_reset_source_dry_run_then_apply_preserves_ledgers_and_allows_rerun(tmp_path):
    # 同一预处理环境上的状态推进场景（合并自 dry-run / apply+rerun / preserve-ledgers 三条，
    # 断言全保留）：dry-run 无变化 → apply 删下游缓存行 → 账本不动 → 同输入真正重跑。
    db = _preprocessed(tmp_path)
    state_store.add_review_proposal(db, "note", target_path="x.md",
                                    kind="broken-link", reason="r")

    # ① 默认 dry-run：打印 plan，但 stage-run / source 行一个字节不变。
    before_runs = _stage_runs(db, "note")
    before_src = dict(state_store.get_source(db, "note"))
    r = _run(["reset-source", "--source", "note", "--to", "registered"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout
    assert _stage_runs(db, "note") == before_runs
    assert dict(state_store.get_source(db, "note")) == before_src

    # ② --apply：回到 registered/done；下游 stage-run 缓存行全删（否则同 input_hash 永远
    # [skip]，reset 无意义）；留 reset 审计行。
    r = _run(["reset-source", "--source", "note", "--to", "registered", "--apply"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("registered", "done")
    runs = _stage_runs(db, "note")
    assert all(stage == "reset" for stage, _ in runs), runs

    # ③ review_proposals / work_orders 是历史账本，reset 绝不动。
    assert len(state_store.list_review_proposals(db, "note")) == 1
    assert state_store.get_work_order(db, "note") is not None

    # ④ 同一输入重跑 profile：不再被缓存跳过，真正重跑成功。
    r2 = _run(["profile", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    src2 = state_store.get_source(db, "note")
    assert (src2["current_stage"], src2["current_status"]) == ("profiled", "done")
    assert ("profiled", "done") in _stage_runs(db, "note")


def test_reset_source_refuses_running_or_locked(tmp_path):
    _ingesting(tmp_path)  # ingest-start 持锁 + ingesting/running
    r = _run(["reset-source", "--source", "note", "--to", "registered", "--apply"], tmp_path)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert ("lock" in out) or ("running" in out)


def test_reset_source_rejects_bad_target_and_unknown_source(tmp_path):
    _preprocessed(tmp_path)
    r = _run(["reset-source", "--source", "note", "--to", "ingesting", "--apply"], tmp_path)
    assert r.returncode != 0  # ingest 段禁止 reset 进入（有 reopen）
    r2 = _run(["reset-source", "--source", "nope", "--to", "registered"], tmp_path)
    assert r2.returncode != 0


# ---- retract-source ----

mdpage = _load("mdpage")

_RETRACT_LESSON = ("# A\n\n这一节讲述 aaa 的核心思想，用足够长的干净散文正文展开：先给直觉，"
                   "再说明它和相邻概念的依赖关系，最后给出第一遍阅读可以跳过什么、什么时候应该"
                   "回到原文核对。这样的长度足以通过空课代理检查。[^e1]\n\n[^e1]: 证据：note §A\n")


def _publish_lesson(tmp_path, sid, rel):
    """一轮完整发布：预处理→读窗→写 lesson→记账→lint promote。"""
    assert _run(["ingest-start", "--source", sid], tmp_path).returncode == 0
    assert _run(["show-window", "--source", sid, "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", sid, "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki" / rel,
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": sid, "source": sid}, _RETRACT_LESSON)
    assert _run(["window-done", "--source", sid, "--window", "w0000",
                 "--writes", json.dumps([rel])], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", sid], tmp_path).returncode == 0
    r = _run(["lint", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_retract_source_dry_run_then_apply_roundtrip(tmp_path):
    # 证据先行的撤库全链：dry-run 零改动 → apply 导证据包→删页→清账→重置→重建派生层
    db = _preprocessed(tmp_path, sid="keep")
    _publish_lesson(tmp_path, "keep", "domains/misc/lessons/keep.md")
    _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")

    # dry-run：打印计划、零改动
    r = _run(["retract-source", "--source", "gone"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "domains/misc/lessons/gone.md" in r.stdout and "dry-run" in r.stdout
    assert (tmp_path / "wiki/domains/misc/lessons/gone.md").exists()
    assert len(state_store.window_states(db, "gone")) == 1

    # apply：硬步骤全部落地；正常场景五个派生层必须全部重建成功（P2-2：不许静默失败——
    # 派生层故障的容忍度由 test_retract_source_reports_failed_rebuild_layers 单独验证）
    r2 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not (tmp_path / "wiki/domains/misc/lessons/gone.md").exists()
    assert (tmp_path / "wiki/domains/misc/lessons/keep.md").exists()
    # 证据包：页面副本 + manifest + DB 账本导出，且核验通过
    ev = list((tmp_path / "pipeline-workspace/evidence").glob("retract-gone-*"))
    assert len(ev) == 1, ev
    assert (ev[0] / "pages/domains/misc/lessons/gone.md").exists()
    assert json.loads((ev[0] / "db/ingest_progress.json").read_text(encoding="utf-8"))
    # 账本清空、他源不动；状态回 workorder_ready
    assert state_store.window_states(db, "gone") == []
    assert len(state_store.window_states(db, "keep")) == 1
    src = state_store.get_source(db, "gone")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")
    # index 重建：gone 出局、keep 保留；log 有撤库审计行
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "gone.md" not in idx and "keep.md" in idx
    assert "retract | gone" in (tmp_path / "wiki/log.md").read_text(encoding="utf-8")
    # 幂等的第二次 apply：无页可删也不该炸，派生层照常全部重建成功
    r3 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    # 撤库锁已释放（finally 保证）
    _locks = _load("locks")
    assert _locks.get(db, scope="vault") is None


def test_retract_source_reports_failed_rebuild_layers(tmp_path):
    # P2-2（Codex 2026-07-18）：派生层重建失败必须显式暴露——撤库本体完成、返回 3、
    # stdout 列出失败层，绝不静默；证据包与删除仍然有效。
    _preprocessed(tmp_path, sid="keep")
    _publish_lesson(tmp_path, "keep", "domains/misc/lessons/keep.md")
    _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    gdir = tmp_path / "wiki" / "graph-data.generated.json"
    if gdir.exists():
        gdir.unlink()
    gdir.mkdir()                                            # 目标被占成目录 → graph 写入失败
    (gdir / "keep.txt").write_text("x", encoding="utf-8")
    r = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r.returncode == 3, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "rebuild graph failed" in out and "retract done with warnings" in out, out
    # 撤库本体已完成：页删了、证据包在、其余派生层照常重建（index 已无 gone）
    assert not (tmp_path / "wiki/domains/misc/lessons/gone.md").exists()
    assert list((tmp_path / "pipeline-workspace/evidence").glob("retract-gone-*"))
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "gone.md" not in idx and "keep.md" in idx


def test_retract_source_refuses_running_or_locked(tmp_path):
    # 两个拒绝分支：①目标源自身 running ②vault 锁被他源持有——撤库改共享 vault 与派生层，必须独占时机
    _ingesting(tmp_path, sid="note")   # note ingesting/running 且持有 vault 锁
    r = _run(["retract-source", "--source", "note", "--apply"], tmp_path)
    assert r.returncode != 0
    assert "running" in (r.stdout + r.stderr)
    # 另一源 other 处于 workorder_ready（非 running），但锁仍被 note 持有 → 锁分支拒绝
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    _preprocessed(tmp_path, sid="other")
    r2 = _run(["retract-source", "--source", "other", "--apply"], tmp_path)
    assert r2.returncode != 0
    assert "lock" in (r2.stdout + r2.stderr).lower()
    assert (tmp_path / "wiki").exists()  # 双拒绝下 vault 未被动过


# ---- retract-source: overview 是 vault 永久基础设施（产品决策 2026-07-23）----

pipeline_mod = _load("pipeline")
_OVERVIEW_TEMPLATE = (ROOT / "templates" / "overview.md").read_text(encoding="utf-8")


def _set_overview(tmp_path, **meta_updates):
    """改 init-vault 落的 overview.md frontmatter（模拟 /ingest 维护后的归属/管理者）。"""
    ov = tmp_path / "wiki" / "overview.md"
    meta, body = mdpage.read_page(ov)
    meta.update(meta_updates)
    mdpage.write_page(ov, meta, body)
    return ov


def test_seed_overview_helper_idempotent(tmp_path):
    # req8：共享的 seed helper 幂等——首次写出 templates/overview.md，已存在则永不覆盖。
    v = tmp_path / "wiki"
    v.mkdir()
    assert pipeline_mod._seed_overview(v) is True
    assert (v / "overview.md").read_text(encoding="utf-8") == _OVERVIEW_TEMPLATE
    (v / "overview.md").write_text("HUMAN EDIT", encoding="utf-8")
    assert pipeline_mod._seed_overview(v) is False          # 已存在 → 不覆盖
    assert (v / "overview.md").read_text(encoding="utf-8") == "HUMAN EDIT"


def test_retract_reseeds_exclusive_pipeline_overview(tmp_path):
    # 独占 pipeline overview：旧版进证据包 → 删除 → apply 后重建为 templates seed
    # （published/managed_by:pipeline、不含被撤 source_refs）。
    db = _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    _set_overview(tmp_path, status="published", managed_by="pipeline",
                  source_refs=[{"source": "gone"}])
    r = _run(["retract-source", "--source", "gone"], tmp_path)          # dry-run 显示 reseed
    assert r.returncode == 0 and "overview.md" in r.stdout and "reseed" in r.stdout
    r2 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    ov = tmp_path / "wiki" / "overview.md"
    assert ov.read_text(encoding="utf-8") == _OVERVIEW_TEMPLATE         # 原样模板 seed
    meta, _ = mdpage.read_page(ov)
    assert meta["status"] == "published" and meta["managed_by"] == "pipeline"
    assert not meta.get("source_refs")                                 # 不含被撤 refs
    ev = list((tmp_path / "pipeline-workspace/evidence").glob("retract-gone-*"))[0]
    assert (ev / "pages/overview.md").exists()                         # 旧 overview 进证据
    old_meta, _ = mdpage.read_page(ev / "pages/overview.md")
    assert old_meta.get("source_refs") == [{"source": "gone"}]         # 证据是旧版（带归属）


def test_retract_keeps_shared_overview_byte_identical(tmp_path):
    # shared overview（source_refs 含他源）：apply 前后字节不变，只报告人工去引。
    _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    _set_overview(tmp_path, status="published", managed_by="pipeline",
                  source_refs=[{"source": "gone"}, {"source": "other"}])
    ov = tmp_path / "wiki" / "overview.md"
    before = ov.read_bytes()
    r = _run(["retract-source", "--source", "gone"], tmp_path)
    assert "overview.md" in r.stdout and "keep" in r.stdout
    r2 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert ov.read_bytes() == before                                   # shared → 字节不变


def test_retract_keeps_human_overview_byte_identical(tmp_path):
    # managed_by:human overview：apply 前后字节不变（永不覆盖/重建）。
    _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    _set_overview(tmp_path, status="published", managed_by="human",
                  source_refs=[{"source": "gone"}])
    ov = tmp_path / "wiki" / "overview.md"
    before = ov.read_bytes()
    r2 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert ov.read_bytes() == before                                   # human → 字节不变


def test_retract_dry_run_leaves_overview_state_evidence_unchanged(tmp_path):
    # req5：dry-run 绝不写文件——overview、状态库、证据目录均不变（但输出显示 reseed/keep）。
    db = _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    _set_overview(tmp_path, status="published", managed_by="pipeline",
                  source_refs=[{"source": "gone"}])
    ov = tmp_path / "wiki" / "overview.md"
    before = ov.read_bytes()
    r = _run(["retract-source", "--source", "gone"], tmp_path)
    assert r.returncode == 0 and "overview.md" in r.stdout
    assert ov.read_bytes() == before                                   # overview 不变
    assert len(state_store.window_states(db, "gone")) == 1             # 状态库不变
    assert not list((tmp_path / "pipeline-workspace/evidence").glob("retract-gone-*"))  # 无证据目录


def test_retract_seeds_missing_overview_and_rebuilds(tmp_path):
    # req4/req7：撤库前 overview 已意外缺失 → apply 补 seed；派生层照常重建；再次 apply 幂等不覆盖。
    _preprocessed(tmp_path, sid="gone")
    _publish_lesson(tmp_path, "gone", "domains/misc/lessons/gone.md")
    (tmp_path / "wiki" / "overview.md").unlink()
    r = _run(["retract-source", "--source", "gone"], tmp_path)
    assert "reseed" in r.stdout
    r2 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    ov = tmp_path / "wiki" / "overview.md"
    assert ov.read_text(encoding="utf-8") == _OVERVIEW_TEMPLATE
    assert (tmp_path / "wiki/index.generated.md").exists()             # 派生层重建正常
    before = ov.read_bytes()
    r3 = _run(["retract-source", "--source", "gone", "--apply"], tmp_path)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert ov.read_bytes() == before                                   # 幂等：不重复覆盖


# ---- profile UX：docx/pptx 不再打印误导性 "0 pages" ----

def test_profile_docx_no_misleading_zero_pages(tmp_path):
    # docx/pptx 无页概念，profile 返回空 pages；用户输出不再显示 "0 pages"，改说页数由 source-convert 确定。
    assert _run(["init-vault"], tmp_path).returncode == 0
    doc = tmp_path / "raw" / "d.docx"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"PK\x03\x04 dummy")                              # profile 对 docx 返回 []，无需真 docx
    assert _run(["add-source", "--source", "d1", "--domain", "misc",
                 "--path", str(doc), "--fmt", "docx"], tmp_path).returncode == 0
    r = _run(["profile", "--source", "d1"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 pages" not in r.stdout                                  # 不再误导
    assert "source-convert" in r.stdout                              # 说明页数何时确定
    assert (tmp_path / "pipeline-workspace/staging/d1/pages.jsonl").exists()  # 语义不变：仍落 pages.jsonl


def test_profile_md_still_reports_page_count(tmp_path):
    # Markdown/PDF 原有页数输出不变。
    assert _run(["init-vault"], tmp_path).returncode == 0
    note = tmp_path / "raw" / "n.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa\n", encoding="utf-8")
    assert _run(["add-source", "--source", "m1", "--domain", "misc",
                 "--path", str(note), "--fmt", "md"], tmp_path).returncode == 0
    r = _run(["profile", "--source", "m1"], tmp_path)
    assert "1 pages" in r.stdout and "needs_vision" in r.stdout       # md 页数输出不变
