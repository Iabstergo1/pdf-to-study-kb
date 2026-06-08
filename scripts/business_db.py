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
    # WAL + busy_timeout：run-book 并发执行多个 unit 时，多个线程会并发写本库。
    # WAL 允许一写多读，busy_timeout 让短暂写锁自动重试而非立刻报 "database is locked"。
    conn = sqlite3.connect(path, timeout=30.0)
    # busy_timeout 必须先于 journal_mode=WAL：切换 WAL 模式本身要短暂独占锁，
    # 若先发 WAL pragma 而 busy_timeout 未生效，并发下这一句会立刻 "database is locked"。
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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


def load_evidence_ledger(book_root: Path) -> list[dict[str, Any]]:
    """读取完整 evidence_ledger 表（按 evidence_id 去重、INSERT OR REPLACE 保最新）。

    用于从持久化业务库重建聚合索引，使局部/续跑也能产出全书一致的 Claims / Formula-Ledger，
    而不依赖某次运行的进程内瞬时 memory。"""
    initialize_business_db(book_root)
    items: list[dict[str, Any]] = []
    with connect(book_root) as conn:
        rows = conn.execute(
            "SELECT evidence_id, unit_id, claim, page, source_heading, evidence_type, payload_json "
            "FROM evidence_ledger ORDER BY unit_id, evidence_id"
        ).fetchall()
    for evidence_id, unit_id, claim, page, source_heading, evidence_type, payload_json in rows:
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        items.append({
            "evidence_id": evidence_id,
            "unit_id": unit_id,
            "claim": claim,
            "page": page,
            "source_heading": source_heading,
            "evidence_type": evidence_type,
            "payload": payload,
        })
    return items


def load_latest_memory_snapshots(book_root: Path) -> list[dict[str, Any]]:
    """返回每个 unit_id 的最新 memory 快照 blob（按 id 取最大）。"""
    initialize_business_db(book_root)
    with connect(book_root) as conn:
        rows = conn.execute(
            "SELECT m.memory_json FROM memory_snapshots m "
            "JOIN (SELECT unit_id, MAX(id) AS max_id FROM memory_snapshots GROUP BY unit_id) latest "
            "ON m.id = latest.max_id"
        ).fetchall()
    snapshots = []
    for (blob,) in rows:
        try:
            snapshots.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    return snapshots


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
