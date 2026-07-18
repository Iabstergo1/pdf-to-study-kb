import sqlite3
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")

EXPECTED_TABLES = {
    "sources", "source_stage_runs", "artifacts", "work_orders",
    "source_locks", "review_proposals", "ingest_progress",
}


def _tables(db):
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return names


def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    assert EXPECTED_TABLES <= _tables(db)


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.init_db(db)
    assert EXPECTED_TABLES <= _tables(db)


import pytest


def _running_run(db, source_id, stage):
    con = state_store.connect(db)
    row = con.execute(
        "SELECT * FROM source_stage_runs WHERE source_id=? AND stage=? ORDER BY id DESC LIMIT 1",
        (source_id, stage),
    ).fetchone()
    con.close()
    return row


def test_register_source_starts_at_registered_done(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="game-theory", fmt="pdf")
    r = state_store.get_source(db, "s1")
    assert (r["current_stage"], r["current_status"]) == ("registered", "done")


def test_next_action_from_registered_is_profile(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    assert state_store.next_actions(db)[0]["next_action"] == "run: profile"


def test_start_stage_atomically_updates_both_tables(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    rid = state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    src = state_store.get_source(db, "s1")
    run = _running_run(db, "s1", "profiled")
    assert src["current_stage"] == "profiled" and src["current_status"] == "running"
    assert run["status"] == "running" and run["id"] == rid


def test_complete_stage_sets_done(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    state_store.complete_stage(db, "s1", "profiled", output_hash="o1")
    src = state_store.get_source(db, "s1")
    assert src["current_status"] == "done"
    assert _running_run(db, "s1", "profiled")["status"] == "done"


def test_invalid_transition_rejected(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    with pytest.raises(state_store.InvalidTransition):
        state_store.start_stage(db, "s1", "converted", input_hash="h")


def _advance(db, sid, stages):
    for st in stages:
        state_store.start_stage(db, sid, st, input_hash=st)
        state_store.complete_stage(db, sid, st)


def test_lint_fail_then_retry_via_ingest_waiting(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready",
                        "ingest_waiting", "ingesting", "ingested"])
    assert state_store.get_source(db, "s1")["current_status"] == "proposed"
    state_store.start_stage(db, "s1", "lint", input_hash="l1")
    state_store.fail_stage(db, "s1", "lint", error="missing evidence")
    assert state_store.get_source(db, "s1")["current_status"] == "failed"
    state_store.start_stage(db, "s1", "ingest_waiting", input_hash="l2")
    assert state_store.get_source(db, "s1")["current_stage"] == "ingest_waiting"


def test_lint_pass_sets_published(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready",
                        "ingest_waiting", "ingesting", "ingested", "lint"])
    assert state_store.get_source(db, "s1")["current_status"] == "published"


def test_reopen_published_source_resets_to_workorder_ready(tmp_path):
    # 通用"重开来源做增量补充"：已发布源经 reopen 回到 workorder_ready/done，可再入库。
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready",
                        "ingest_waiting", "ingesting", "ingested", "lint"])
    assert state_store.get_source(db, "s1")["current_status"] == "published"
    state_store.reopen_source(db, "s1")
    src = state_store.get_source(db, "s1")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")
    # 重开后照常推进回 ingesting（增量再入库）
    state_store.start_stage(db, "s1", "ingest_waiting", input_hash="r1")
    assert state_store.get_source(db, "s1")["current_stage"] == "ingest_waiting"
    # 审计：留下一条 reopened 标记
    con = state_store.connect(db)
    n = con.execute("SELECT COUNT(*) FROM source_stage_runs WHERE source_id='s1' AND stage='reopened'").fetchone()[0]
    con.close()
    assert n == 1


def test_reopen_from_lint_failed_allowed(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready",
                        "ingest_waiting", "ingesting", "ingested"])
    state_store.start_stage(db, "s1", "lint", input_hash="l1")
    state_store.fail_stage(db, "s1", "lint", error="x")
    state_store.reopen_source(db, "s1")
    assert state_store.get_source(db, "s1")["current_stage"] == "workorder_ready"


def test_reopen_from_ingested_proposed_allowed(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready",
                        "ingest_waiting", "ingesting", "ingested"])
    assert state_store.get_source(db, "s1")["current_status"] == "proposed"
    state_store.reopen_source(db, "s1")
    assert state_store.get_source(db, "s1")["current_stage"] == "workorder_ready"


def test_reopen_rejected_before_first_ingest(tmp_path):
    # 还没第一次入库完成（停在 workorder_ready）不允许 reopen——无可增量的发布物
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready"])
    with pytest.raises(state_store.InvalidTransition):
        state_store.reopen_source(db, "s1")


def test_reopen_rejected_during_active_ingest(tmp_path):
    # 正在 ingesting/running 的源应 resume 而非 reopen（守住跨 agent 互斥语义）
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled", "converted", "windowed", "workorder_ready", "ingest_waiting"])
    state_store.start_stage(db, "s1", "ingesting", input_hash="x")  # running
    with pytest.raises(state_store.InvalidTransition):
        state_store.reopen_source(db, "s1")


def test_reopen_unknown_source_rejected(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    with pytest.raises(state_store.InvalidTransition):
        state_store.reopen_source(db, "nope")


def test_should_run_stage_idempotent_skip(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h1") is True
    state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    state_store.complete_stage(db, "s1", "profiled")
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h1") is False
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h2") is True


def test_double_start_running_rejected(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    with pytest.raises(state_store.InvalidTransition):
        state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    con = state_store.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM source_stage_runs"
        " WHERE source_id='s1' AND stage='profiled' AND status='running'"
    ).fetchone()[0]
    con.close()
    assert n == 1  # 只有一条 running，没产生重复


def test_complete_without_running_rejected_and_state_unchanged(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    with pytest.raises(state_store.InvalidTransition):
        state_store.complete_stage(db, "s1", "registered")
    assert state_store.get_source(db, "s1")["current_status"] == "done"  # 未被改动


def test_fail_without_running_rejected_and_state_unchanged(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    with pytest.raises(state_store.InvalidTransition):
        state_store.fail_stage(db, "s1", "registered", error="x")
    assert state_store.get_source(db, "s1")["current_status"] == "done"  # 未被改成 failed


def _seed_two_sources_ledgers(db):
    state_store.init_db(db)
    for sid in ("s1", "s2"):
        state_store.register_source(db, sid, domain="d", fmt="md")
        state_store.start_window(db, sid, "w0000", input_hash="h")
        state_store.finish_window(db, sid, "w0000", write_set_json='["a.md"]')
        state_store.record_window_read(db, sid, "w0000")
        state_store.add_review_proposal(db, sid, target_path="x.md", kind="L1", reason="r")


def test_export_source_rows_reads_all_tables_for_one_source(tmp_path):
    # retract 证据包的 DB 侧：导出该源在全部相关表的行（只读），他源行不掺入
    db = tmp_path / "study-kb.sqlite"
    _seed_two_sources_ledgers(db)
    rows = state_store.export_source_rows(db, "s1")
    for table in ("sources", "source_stage_runs", "ingest_progress",
                  "window_reads", "review_proposals", "work_orders", "artifacts"):
        assert table in rows, table
    assert len(rows["ingest_progress"]) == 1
    assert rows["ingest_progress"][0]["write_set_json"] == '["a.md"]'
    assert len(rows["window_reads"]) == 1 and len(rows["review_proposals"]) == 1
    assert all(r["source_id"] == "s1" for t in rows.values() for r in t)


def test_purge_source_ledgers_deletes_only_target_source(tmp_path):
    # retract 清账：只清 ingest_progress/window_reads/review_proposals 三张账本表、只清本源
    db = tmp_path / "study-kb.sqlite"
    _seed_two_sources_ledgers(db)
    counts = state_store.purge_source_ledgers(db, "s1")
    assert counts == {"ingest_progress": 1, "window_reads": 1, "review_proposals": 1}
    assert state_store.window_states(db, "s1") == []
    assert state_store.window_read_ids(db, "s1") == set()
    assert state_store.list_review_proposals(db, "s1") == []
    # s2 完好；s1 的 sources/stage_runs 行保留（状态机身份不清除）
    assert len(state_store.window_states(db, "s2")) == 1
    assert state_store.get_source(db, "s1") is not None


def test_round_token_isolates_rounds_even_within_same_second(tmp_path):
    # P1-1（Codex 2026-07-18）：轮次隔离不得依赖时间戳跨秒。轮次 = work_orders.round 显式
    # 计数器（record_work_order 递增），读/写行在落账时盖当轮 token——本测试全程同一秒内
    # 制造碰撞（零 sleep），旧轮读仍必须被新轮排除。
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="md")
    assert state_store.round_anchor(db, "s1") is None          # 无 workorder → 无轮次
    state_store.record_window_read(db, "s1", "w_pre")          # workorder 前的读：无轮次章
    state_store.record_work_order(db, "s1", path="p", registry_hash="r" * 64,
                                  write_scope_json="[]")
    r1 = state_store.round_anchor(db, "s1")
    assert r1 == 1
    assert state_store.window_reads_in_round(db, "s1", r1) == set()   # 同秒旧读不入新轮
    state_store.record_window_read(db, "s1", "w_a")            # 本轮读 → 盖 round 1
    assert state_store.window_reads_in_round(db, "s1", r1) == {"w_a"}
    # 同一秒内 reopen（再记 workorder）→ round 2；round-1 的读立即失效
    state_store.record_work_order(db, "s1", path="p", registry_hash="r" * 64,
                                  write_scope_json="[]")
    r2 = state_store.round_anchor(db, "s1")
    assert r2 == 2
    assert state_store.window_reads_in_round(db, "s1", r2) == set()
    state_store.record_window_read(db, "s1", "w_a")            # 重读 → 盖 round 2
    assert state_store.window_reads_in_round(db, "s1", r2) == {"w_a"}
    assert state_store.window_reads_in_round(db, "s1", r1) == set()   # UPSERT 后旧轮视图不残留


def test_start_window_clears_stale_ledger_and_stamps_round(tmp_path):
    # P1-2（Codex 2026-07-18）：重启窗口必须清掉上一轮 write_set/proposal_set——否则
    # start→fail 就能把旧轮账"带尸还魂"进新轮。行还要盖当轮 round token。
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="md")
    state_store.record_work_order(db, "s1", path="p", registry_hash="r" * 64,
                                  write_scope_json="[]")
    state_store.start_window(db, "s1", "w0", input_hash="h")
    state_store.finish_window(db, "s1", "w0", write_set_json='["old.md"]',
                              proposal_set_json='["p.md"]')
    state_store.record_work_order(db, "s1", path="p", registry_hash="r" * 64,
                                  write_scope_json="[]")            # reopen → round 2
    state_store.start_window(db, "s1", "w0", input_hash="h2")       # 新轮重启同窗
    row = state_store.window_states(db, "s1")[0]
    assert row["write_set_json"] is None and row["proposal_set_json"] is None  # 旧账清空
    assert row["round"] == 2                                        # 盖新轮章
    state_store.fail_window(db, "s1", "w0", error="boom")
    row = state_store.window_states(db, "s1")[0]
    assert row["write_set_json"] is None    # start→fail 不复活旧 write_set
