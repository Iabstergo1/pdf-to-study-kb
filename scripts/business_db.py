"""Business SQLite and JSONL event storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  node TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  node TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  memory_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_ledger (
  evidence_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  claim TEXT NOT NULL,
  page INTEGER NOT NULL,
  source_heading TEXT,
  evidence_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def business_db_path(book_root: Path) -> Path:
    return book_root / "pipeline-workspace" / "state" / "study-kb.sqlite"


def checkpoint_db_path(book_root: Path) -> Path:
    return book_root / "pipeline-workspace" / "checkpoints" / "langgraph.sqlite"


def events_jsonl_path(book_root: Path, run_id: str) -> Path:
    return book_root / "pipeline-workspace" / "runs" / run_id / "events.jsonl"


def connect(book_root: Path) -> sqlite3.Connection:
    path = business_db_path(book_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def initialize_business_db(book_root: Path) -> Path:
    with connect(book_root) as conn:
        conn.executescript(SCHEMA)
    return business_db_path(book_root)


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def start_run(book_root: Path, run_id: str, book_id: str, status: str = "running") -> None:
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (run_id, book_id, started_at, finished_at, status)
            VALUES (?, ?, COALESCE((SELECT started_at FROM runs WHERE run_id = ?), ?), NULL, ?)
            """,
            (run_id, book_id, run_id, utc_now(), status),
        )


def finish_run(book_root: Path, run_id: str, status: str) -> None:
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (utc_now(), status, run_id),
        )


def append_event_jsonl(book_root: Path, event: dict[str, Any]) -> None:
    path = events_jsonl_path(book_root, event["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(event) + "\n")


def record_event(
    book_root: Path,
    run_id: str,
    unit_id: str,
    node: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    initialize_business_db(book_root)
    created_at = utc_now()
    payload = payload or {}
    with connect(book_root) as conn:
        conn.execute(
            """
            INSERT INTO unit_events (run_id, unit_id, node, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, unit_id, node, status, json_dumps(payload), created_at),
        )
    event = {
        "run_id": run_id,
        "unit_id": unit_id,
        "node": node,
        "status": status,
        "payload": payload,
        "created_at": created_at,
    }
    append_event_jsonl(book_root, event)
    return event


def record_model_call(
    book_root: Path,
    run_id: str,
    unit_id: str,
    node: str,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: float | None = None,
) -> None:
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        conn.execute(
            """
            INSERT INTO model_calls
              (run_id, unit_id, node, provider, model, input_tokens, output_tokens, cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, unit_id, node, provider, model, input_tokens, output_tokens, cost, utc_now()),
        )


def record_memory_snapshot(
    book_root: Path,
    run_id: str,
    unit_id: str,
    memory: dict[str, Any],
) -> None:
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        conn.execute(
            """
            INSERT INTO memory_snapshots (run_id, unit_id, memory_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, unit_id, json_dumps(memory), utc_now()),
        )


def record_evidence(
    book_root: Path,
    evidence_id: str,
    run_id: str,
    unit_id: str,
    claim: str,
    page: int,
    source_heading: str | None,
    evidence_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO evidence_ledger
              (evidence_id, run_id, unit_id, claim, page, source_heading, evidence_type, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                run_id,
                unit_id,
                claim,
                page,
                source_heading,
                evidence_type,
                json_dumps(payload or {}),
            ),
        )
