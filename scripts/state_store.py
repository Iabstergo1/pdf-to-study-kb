"""新架构状态底座：单一业务 SQLite 的状态机表 + 原子阶段 API（spec §3.3）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id      TEXT PRIMARY KEY,
  domain         TEXT NOT NULL,
  format         TEXT NOT NULL,            -- pdf|docx|pptx|md
  added_at       TEXT NOT NULL,
  current_stage  TEXT NOT NULL,            -- registered..lint
  current_status TEXT NOT NULL             -- pending|running|done|failed|proposed|published
);
CREATE TABLE IF NOT EXISTS source_stage_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id   TEXT NOT NULL,
  stage       TEXT NOT NULL,
  status      TEXT NOT NULL,               -- running|done|failed
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  input_hash  TEXT,
  output_hash TEXT,
  error       TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
  kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
  source_id TEXT PRIMARY KEY, path TEXT NOT NULL, registry_hash TEXT,
  write_scope_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_locks (
  scope TEXT PRIMARY KEY, holder TEXT NOT NULL, pid INTEGER NOT NULL,
  started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, target_path TEXT NOT NULL,
  kind TEXT NOT NULL, diff_path TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, window_id TEXT NOT NULL,
  input_hash TEXT NOT NULL, started_at TEXT, finished_at TEXT, status TEXT NOT NULL,
  write_set_json TEXT, proposal_set_json TEXT, error TEXT, UNIQUE(source_id, window_id)
);
"""


def connect(db_path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path) -> None:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = connect(p)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
