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


STAGES = ["registered", "profiled", "converted", "windowed", "workorder_ready",
          "ingest_waiting", "ingesting", "ingested", "lint"]
NEXT = {
    "registered": ["profiled"], "profiled": ["converted"], "converted": ["windowed"],
    "windowed": ["workorder_ready"], "workorder_ready": ["ingest_waiting"],
    "ingest_waiting": ["ingesting"], "ingesting": ["ingested"], "ingested": ["lint"],
    "lint": [],
}
# 完成某 stage 后 source 的状态（其余默认 "done"）
DONE_STATUS = {"ingested": "proposed", "lint": "published"}
# status/next 派生的下一步人工动作（按 stage）
STAGE_NEXT_ACTION = {
    "registered": "run: profile", "profiled": "run: source-convert",
    "converted": "run: windows", "windowed": "run: workorder",
    "workorder_ready": "human: /ingest", "ingest_waiting": "human: /ingest",
    "ingesting": "resume: /ingest", "ingested": "run: lint", "lint": "done (published)",
}


class InvalidTransition(Exception):
    pass


def _allowed_next(stage: str, status: str) -> set[str]:
    if status == "failed":
        allowed = {stage}
        if stage == "lint":
            allowed.add("ingest_waiting")
        return allowed
    if status in ("done", "proposed", "published"):
        return set(NEXT.get(stage, [])) | {stage}
    if status == "running":
        return set()  # 运行中：必须先 complete/fail，不能再 start（拒绝重复 running）
    return {stage}  # pending -> 只能(重)启当前 stage


def register_source(db_path, source_id: str, *, domain: str, fmt: str) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO sources(source_id,domain,format,added_at,current_stage,current_status)"
            " VALUES (?,?,?,?,?,?)",
            (source_id, domain, fmt, _now(), "registered", "done"),
        )
        con.commit()
    finally:
        con.close()


def get_source(db_path, source_id: str):
    con = connect(db_path)
    try:
        return con.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
    finally:
        con.close()


def start_stage(db_path, source_id: str, stage: str, *, input_hash: str | None = None) -> int:
    if stage not in STAGES:
        raise InvalidTransition(f"unknown stage: {stage}")
    con = connect(db_path)
    try:
        row = con.execute("SELECT current_stage,current_status FROM sources WHERE source_id=?",
                          (source_id,)).fetchone()
        if row is None:
            raise InvalidTransition(f"unknown source: {source_id}")
        if stage not in _allowed_next(row["current_stage"], row["current_status"]):
            raise InvalidTransition(f"{row['current_stage']}/{row['current_status']} -> {stage} not allowed")
        cur = con.execute(
            "INSERT INTO source_stage_runs(source_id,stage,status,started_at,input_hash)"
            " VALUES (?,?,?,?,?)", (source_id, stage, "running", _now(), input_hash))
        run_id = int(cur.lastrowid)
        con.execute("UPDATE sources SET current_stage=?, current_status='running' WHERE source_id=?",
                    (stage, source_id))
        con.commit()
        return run_id
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def complete_stage(db_path, source_id: str, stage: str, *, output_hash: str | None = None) -> None:
    con = connect(db_path)
    try:
        cur = con.execute(
            "UPDATE source_stage_runs SET status='done', finished_at=?, output_hash=?"
            " WHERE id=(SELECT id FROM source_stage_runs WHERE source_id=? AND stage=? AND status='running'"
            "           ORDER BY id DESC LIMIT 1)",
            (_now(), output_hash, source_id, stage))
        if cur.rowcount == 0:
            raise InvalidTransition(f"no running run for {source_id}/{stage} to complete")
        con.execute("UPDATE sources SET current_status=? WHERE source_id=? AND current_stage=?",
                    (DONE_STATUS.get(stage, "done"), source_id, stage))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def fail_stage(db_path, source_id: str, stage: str, *, error: str) -> None:
    con = connect(db_path)
    try:
        cur = con.execute(
            "UPDATE source_stage_runs SET status='failed', finished_at=?, error=?"
            " WHERE id=(SELECT id FROM source_stage_runs WHERE source_id=? AND stage=? AND status='running'"
            "           ORDER BY id DESC LIMIT 1)",
            (_now(), error, source_id, stage))
        if cur.rowcount == 0:
            raise InvalidTransition(f"no running run for {source_id}/{stage} to fail")
        con.execute("UPDATE sources SET current_status='failed' WHERE source_id=? AND current_stage=?",
                    (source_id, stage))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def should_run_stage(db_path, source_id: str, stage: str, *, input_hash: str | None) -> bool:
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM source_stage_runs WHERE source_id=? AND stage=? AND status='done'"
            "   AND IFNULL(input_hash,'')=IFNULL(?,'') LIMIT 1",
            (source_id, stage, input_hash)).fetchone()
        return row is None
    finally:
        con.close()


def status_rows(db_path) -> list[dict]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT source_id,domain,format,current_stage,current_status FROM sources ORDER BY source_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def next_actions(db_path) -> list[dict]:
    out = []
    for r in status_rows(db_path):
        stage, status = r["current_stage"], r["current_status"]
        if status == "running":
            act = f"resume/in-progress: {stage}"
        elif status == "failed":
            act = "fix + /ingest" if stage == "lint" else f"retry: {stage}"
        elif status == "published":
            act = "done"
        else:  # done | proposed
            act = STAGE_NEXT_ACTION.get(stage, "?")
        out.append({"source_id": r["source_id"], "current_stage": stage,
                    "current_status": status, "next_action": act})
    return out


def record_artifact(db_path, source_id: str, *, kind: str, path: str, sha256: str) -> int:
    """登记/更新一个产物（同 source+kind+path 覆盖，保证幂等重跑不堆重复行）。"""
    con = connect(db_path)
    try:
        con.execute(
            "DELETE FROM artifacts WHERE source_id=? AND kind=? AND path=?",
            (source_id, kind, path))
        cur = con.execute(
            "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
            (source_id, kind, path, sha256, _now()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def list_artifacts(db_path, source_id: str) -> list[dict]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT id,source_id,kind,path,sha256,created_at FROM artifacts WHERE source_id=? ORDER BY id",
            (source_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
