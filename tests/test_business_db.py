import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_business_db_creates_business_tables_without_checkpoint_db(tmp_path):
    from business_db import business_db_path, checkpoint_db_path, initialize_business_db

    book_root = tmp_path / "books" / "phase6-book"

    initialize_business_db(book_root)

    db_path = business_db_path(book_root)
    assert db_path.exists()
    assert not checkpoint_db_path(book_root).exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert {
        "runs",
        "unit_events",
        "model_calls",
        "memory_snapshots",
        "evidence_ledger",
    }.issubset(tables)
    assert "checkpoints" not in tables
    assert "writes" not in tables


def test_record_event_writes_sqlite_and_jsonl(tmp_path):
    from business_db import initialize_business_db, record_event

    book_root = tmp_path / "books" / "phase6-book"
    initialize_business_db(book_root)

    record_event(
        book_root,
        run_id="run-1",
        unit_id="U-001-01",
        node="prepare_context",
        status="ok",
        payload={"risk_flags": []},
    )

    db_path = book_root / "pipeline-workspace" / "state" / "study-kb.sqlite"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT run_id, unit_id, node, status, payload_json FROM unit_events"
        ).fetchone()

    assert row[:4] == ("run-1", "U-001-01", "prepare_context", "ok")
    assert json.loads(row[4]) == {"risk_flags": []}

    events_path = book_root / "pipeline-workspace" / "runs" / "run-1" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["run_id"] == "run-1"
    assert event["unit_id"] == "U-001-01"
    assert event["created_at"]


def test_record_model_memory_and_evidence(tmp_path):
    from business_db import (
        initialize_business_db,
        record_evidence,
        record_memory_snapshot,
        record_model_call,
        start_run,
    )

    book_root = tmp_path / "books" / "phase6-book"
    initialize_business_db(book_root)
    start_run(book_root, run_id="run-1", book_id="phase6-book")
    record_model_call(
        book_root,
        run_id="run-1",
        unit_id="U-001-01",
        node="generate_note",
        provider="fake",
        model="fake-model",
        input_tokens=10,
        output_tokens=20,
        cost=0.01,
    )
    record_memory_snapshot(
        book_root,
        run_id="run-1",
        unit_id="U-001-01",
        memory={"running_book_summary": "summary"},
    )
    record_evidence(
        book_root,
        evidence_id="E-U-001-01-0001",
        run_id="run-1",
        unit_id="U-001-01",
        claim="claim",
        page=1,
        source_heading="heading",
        evidence_type="text",
        payload={"preview": "evidence"},
    )

    db_path = book_root / "pipeline-workspace" / "state" / "study-kb.sqlite"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM runs WHERE run_id='run-1'").fetchone()[0] == "running"
        assert conn.execute("SELECT model FROM model_calls").fetchone()[0] == "fake-model"
        memory_json = conn.execute("SELECT memory_json FROM memory_snapshots").fetchone()[0]
        evidence_json = conn.execute("SELECT payload_json FROM evidence_ledger").fetchone()[0]

    assert json.loads(memory_json)["running_book_summary"] == "summary"
    assert json.loads(evidence_json)["preview"] == "evidence"
