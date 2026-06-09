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
