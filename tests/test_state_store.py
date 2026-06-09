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
