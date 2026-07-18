from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_store)


def _db(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="md")
    return db


def test_window_start_finish_roundtrip(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.finish_window(db, "s1", "w0000", write_set_json='["a.md"]')
    rows = state_store.window_states(db, "s1")
    assert rows[0]["window_id"] == "w0000" and rows[0]["status"] == "finished"
    assert rows[0]["write_set_json"] == '["a.md"]'


def test_should_run_window_skips_finished_same_hash(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.finish_window(db, "s1", "w0000")
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h1") is False
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h2") is True
    assert state_store.should_run_window(db, "s1", "w0001", input_hash="h1") is True


def test_window_fail_then_restart(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.fail_window(db, "s1", "w0000", error="boom")
    assert state_store.window_states(db, "s1")[0]["status"] == "failed"
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h1") is True
    state_store.start_window(db, "s1", "w0000", input_hash="h1")  # 重启同窗不报错（UPSERT）
    state_store.finish_window(db, "s1", "w0000")
    assert state_store.window_states(db, "s1")[0]["status"] == "finished"


def test_finish_unknown_window_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(state_store.InvalidTransition):
        state_store.finish_window(db, "s1", "w9999")


def test_record_and_get_work_order(tmp_path):
    db = _db(tmp_path)
    state_store.record_work_order(db, "s1", path="staging/s1/workorder.yaml",
                                  registry_hash="r" * 64, write_scope_json='["domains/d/**"]')
    wo = state_store.get_work_order(db, "s1")
    assert wo["registry_hash"] == "r" * 64
    # 重复记录 = 覆盖（幂等）
    state_store.record_work_order(db, "s1", path="staging/s1/workorder.yaml",
                                  registry_hash="x" * 64, write_scope_json="[]")
    assert state_store.get_work_order(db, "s1")["registry_hash"] == "x" * 64


def test_latest_run_id(tmp_path):
    db = _db(tmp_path)
    rid = state_store.start_stage(db, "s1", "profiled", input_hash="h")
    assert state_store.latest_run_id(db, "s1", "profiled") == rid
    assert state_store.latest_run_id(db, "s1", "converted") is None


def test_add_and_list_review_proposals(tmp_path):
    db = _db(tmp_path)
    pid = state_store.add_review_proposal(db, "s1", target_path="domains/d/lessons/x.md",
                                          kind="L1", reason="bare evidence id [E-1]")
    assert pid > 0
    rows = state_store.list_review_proposals(db, "s1")
    assert rows[0]["kind"] == "L1" and rows[0]["status"] == "open"
    assert state_store.list_review_proposals(db, "nobody") == []


def test_window_states_exposes_timestamps_with_write_set(tmp_path):
    # 时间戳消费者（2026-07-17 定案）：cmd_lint 用 started_at 做本轮（workorder 锚点）过滤，
    # ingest-stats 用 started==finished 计 instant_write_windows 软信号（同秒不做门禁——写页
    # 不强制发生在 start/done 之间）。window_progress 有时间戳但无 write_set，本视图补齐两者。
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.finish_window(db, "s1", "w0000", write_set_json='["a.md"]')
    row = state_store.window_states(db, "s1")[0]
    assert row["started_at"] and row["finished_at"]
    assert row["write_set_json"] == '["a.md"]'
