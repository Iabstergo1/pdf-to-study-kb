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
CREATE TABLE IF NOT EXISTS window_reads (
  source_id TEXT NOT NULL, window_id TEXT NOT NULL, read_at TEXT NOT NULL,
  UNIQUE(source_id, window_id)
);
"""

# 旧库升级护栏：window_reads 是后加表，读/写它的 API 先确保表存在（幂等，不动既有表）。
_WINDOW_READS_DDL = ("CREATE TABLE IF NOT EXISTS window_reads ("
                     "source_id TEXT NOT NULL, window_id TEXT NOT NULL, read_at TEXT NOT NULL,"
                     " UNIQUE(source_id, window_id))")


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


# reset-source 允许的回退目标：只有预处理段（ingest 段有 reopen/resume，禁止 reset 进入）。
RESETTABLE_TARGETS = ["registered", "profiled", "converted", "windowed", "workorder_ready"]


def reset_source(db_path, source_id: str, to_stage: str, *, apply: bool = False) -> dict:
    """确定性重置：回到「to_stage 刚完成」，其后阶段的 stage-run 缓存行作废可重跑
    （不删则同 input_hash 永远被 should_run_stage 跳过，forward-only 状态机无法回退重预处理）。
    只删 source_stage_runs 下游行 + 插一条 reset 审计行；ingest_progress / artifacts /
    work_orders / review_proposals / staging 文件一概不动。默认 dry-run 只返回 plan。"""
    if to_stage not in RESETTABLE_TARGETS:
        raise InvalidTransition(f"reset target must be one of {RESETTABLE_TARGETS}, got {to_stage!r}")
    con = connect(db_path)
    try:
        row = con.execute("SELECT current_stage,current_status FROM sources WHERE source_id=?",
                          (source_id,)).fetchone()
        if row is None:
            raise InvalidTransition(f"unknown source: {source_id}")
        if row["current_status"] == "running":
            raise InvalidTransition(
                f"{source_id} is running ({row['current_stage']}); fail / window-fail it first")
        downstream = STAGES[STAGES.index(to_stage) + 1:]
        ph = ",".join("?" * len(downstream))
        plan_rows = con.execute(
            f"SELECT stage, COUNT(*) AS n FROM source_stage_runs"
            f" WHERE source_id=? AND stage IN ({ph}) GROUP BY stage",
            (source_id, *downstream)).fetchall()
        result = {"source_id": source_id,
                  "from": f"{row['current_stage']}/{row['current_status']}",
                  "to": f"{to_stage}/done",
                  "delete_stage_runs": {r["stage"]: r["n"] for r in plan_rows},
                  "applied": False}
        if not apply:
            return result
        con.execute(f"DELETE FROM source_stage_runs WHERE source_id=? AND stage IN ({ph})",
                    (source_id, *downstream))
        con.execute(
            "INSERT INTO source_stage_runs(source_id,stage,status,started_at,finished_at,input_hash)"
            " VALUES (?,?,?,?,?,?)",
            (source_id, "reset", "done", _now(), _now(),
             f"to:{to_stage} from:{row['current_stage']}/{row['current_status']}"))
        con.execute("UPDATE sources SET current_stage=?, current_status='done' WHERE source_id=?",
                    (to_stage, source_id))
        con.commit()
        result["applied"] = True
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# 可重开的"已收尾"态：跑完至少一轮（lint 终态或 ingested/proposed）才有发布物可增量补充。
# ingesting/running 应 resume 而非 reopen；预处理中的源（registered..workorder_ready）无发布物。
REOPENABLE = {("lint", "published"), ("lint", "done"), ("lint", "failed"),
              ("ingested", "proposed"), ("ingested", "done")}


def reopen_source(db_path, source_id: str) -> None:
    """把一个已收尾来源重置回 workorder_ready/done，允许"重开做增量补充"。
    通用入口：之后照常 ingest-start → 逐窗写页 → ingest-done → lint，lint 只 promote 新增/改写的
    proposed 页，既有 published 页不受影响（增量发布）。审计：插一条 reopened 标记 run。"""
    con = connect(db_path)
    try:
        row = con.execute("SELECT current_stage,current_status FROM sources WHERE source_id=?",
                          (source_id,)).fetchone()
        if row is None:
            raise InvalidTransition(f"unknown source: {source_id}")
        key = (row["current_stage"], row["current_status"])
        if key not in REOPENABLE:
            raise InvalidTransition(
                f"cannot reopen {source_id} at {key[0]}/{key[1]}; "
                "只有已收尾的来源（lint 终态 / ingested-proposed）可重开做增量补充"
                "（ingesting 请 resume，预处理中请直接续跑预处理链）")
        con.execute(
            "INSERT INTO source_stage_runs(source_id,stage,status,started_at,finished_at,input_hash)"
            " VALUES (?,?,?,?,?,?)",
            (source_id, "reopened", "done", _now(), _now(), f"from:{key[0]}/{key[1]}"))
        con.execute(
            "UPDATE sources SET current_stage='workorder_ready', current_status='done'"
            " WHERE source_id=?", (source_id,))
        con.commit()
    except Exception:
        con.rollback()
        raise
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


def start_window(db_path, source_id: str, window_id: str, *, input_hash: str) -> None:
    """window 级进度（spec §3.3）：UPSERT 为 running（重启同窗合法——窗口本身幂等可重做）。"""
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO ingest_progress(source_id,window_id,input_hash,started_at,status)"
            " VALUES (?,?,?,?,'running')"
            " ON CONFLICT(source_id,window_id) DO UPDATE SET"
            "   input_hash=excluded.input_hash, started_at=excluded.started_at,"
            "   status='running', error=NULL, finished_at=NULL",
            (source_id, window_id, input_hash, _now()))
        con.commit()
    finally:
        con.close()


def finish_window(db_path, source_id: str, window_id: str, *,
                  write_set_json: str | None = None, proposal_set_json: str | None = None) -> None:
    con = connect(db_path)
    try:
        cur = con.execute(
            "UPDATE ingest_progress SET status='finished', finished_at=?,"
            " write_set_json=?, proposal_set_json=? WHERE source_id=? AND window_id=?",
            (_now(), write_set_json, proposal_set_json, source_id, window_id))
        if cur.rowcount == 0:
            raise InvalidTransition(f"no window {source_id}/{window_id} to finish")
        con.commit()
    finally:
        con.close()


def fail_window(db_path, source_id: str, window_id: str, *, error: str) -> None:
    con = connect(db_path)
    try:
        cur = con.execute(
            "UPDATE ingest_progress SET status='failed', finished_at=?, error=?"
            " WHERE source_id=? AND window_id=?", (_now(), error, source_id, window_id))
        if cur.rowcount == 0:
            raise InvalidTransition(f"no window {source_id}/{window_id} to fail")
        con.commit()
    finally:
        con.close()


def should_run_window(db_path, source_id: str, window_id: str, *, input_hash: str) -> bool:
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM ingest_progress WHERE source_id=? AND window_id=?"
            "   AND status='finished' AND input_hash=? LIMIT 1",
            (source_id, window_id, input_hash)).fetchone()
        return row is None
    finally:
        con.close()


def window_progress(db_path, source_id: str) -> list[dict]:
    """本源 window 账本只读视图（resume packet / 观测用）：window_id + status + 起止时间。"""
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT window_id,status,started_at,finished_at FROM ingest_progress"
            " WHERE source_id=? ORDER BY id", (source_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def has_open_review_proposal(db_path, *, kind: str, target_path: str, reason: str) -> bool:
    """vault preflight 去重：同 (kind, target_path, reason) 的 open 行已存在则不重复登记
    （reason 内嵌页面 content hash——页面内容变了才允许再登记一条）。"""
    con = connect(db_path)
    try:
        return con.execute(
            "SELECT 1 FROM review_proposals WHERE kind=? AND target_path=? AND reason=?"
            "   AND status='open' LIMIT 1", (kind, target_path, reason)).fetchone() is not None
    finally:
        con.close()


def record_window_read(db_path, source_id: str, window_id: str) -> None:
    """show-window 留痕：读窗即记账（UPSERT 幂等）。空写集跳窗是否真读过窗内容，
    靠这张表事后可审计——文档约束防自觉，这条防绕过。"""
    init_db(db_path)  # show-window 可先于任何建库命令运行；幂等建目录+schema
    con = connect(db_path)
    try:
        con.execute(_WINDOW_READS_DDL)
        con.execute(
            "INSERT INTO window_reads(source_id,window_id,read_at) VALUES (?,?,?)"
            " ON CONFLICT(source_id,window_id) DO UPDATE SET read_at=excluded.read_at",
            (source_id, window_id, _now()))
        con.commit()
    finally:
        con.close()


def window_read_ids(db_path, source_id: str) -> set[str]:
    """已经 show-window 读过的窗口 id 集合。"""
    con = connect(db_path)
    try:
        con.execute(_WINDOW_READS_DDL)
        return {r["window_id"] for r in con.execute(
            "SELECT window_id FROM window_reads WHERE source_id=?", (source_id,))}
    finally:
        con.close()


def window_states(db_path, source_id: str) -> list[dict]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT window_id,status,input_hash,write_set_json,proposal_set_json,error"
            " FROM ingest_progress WHERE source_id=? ORDER BY window_id", (source_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def record_work_order(db_path, source_id: str, *, path: str, registry_hash: str,
                      write_scope_json: str) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO work_orders(source_id,path,registry_hash,write_scope_json,created_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET"
            "   path=excluded.path, registry_hash=excluded.registry_hash,"
            "   write_scope_json=excluded.write_scope_json, created_at=excluded.created_at",
            (source_id, path, registry_hash, write_scope_json, _now()))
        con.commit()
    finally:
        con.close()


def get_work_order(db_path, source_id: str):
    con = connect(db_path)
    try:
        return con.execute("SELECT * FROM work_orders WHERE source_id=?", (source_id,)).fetchone()
    finally:
        con.close()


def latest_run_id(db_path, source_id: str, stage: str) -> int | None:
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT id FROM source_stage_runs WHERE source_id=? AND stage=?"
            " ORDER BY id DESC LIMIT 1", (source_id, stage)).fetchone()
        return int(row["id"]) if row else None
    finally:
        con.close()


def add_review_proposal(db_path, source_id: str, *, target_path: str, kind: str,
                        reason: str, diff_path: str | None = None) -> int:
    con = connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO review_proposals(source_id,target_path,kind,diff_path,reason,created_at,status)"
            " VALUES (?,?,?,?,?,?,'open')",
            (source_id, target_path, kind, diff_path, reason, _now()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def list_review_proposals(db_path, source_id: str | None = None) -> list[dict]:
    con = connect(db_path)
    try:
        if source_id is None:
            rows = con.execute("SELECT * FROM review_proposals ORDER BY id").fetchall()
        else:
            rows = con.execute("SELECT * FROM review_proposals WHERE source_id=? ORDER BY id",
                               (source_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def resolve_review_proposals(db_path, *, ids: list[int] | None = None, kind: str | None = None,
                             source_id: str | None = None, apply: bool = False) -> dict:
    """失败信号退场：按 ids 精确或 kind[+source_id] 批量选中 status='open' 的 proposals；
    dry-run（默认）只返回匹配清单，apply 才 UPDATE status='resolved'。
    没有退场路径时账本单调累积，skill-mine 的 backlog 会被已修复条目污染——这是补上的那条路。"""
    con = connect(db_path)
    try:
        if ids:
            ph = ",".join("?" * len(ids))
            rows = con.execute(
                f"SELECT * FROM review_proposals WHERE status='open' AND id IN ({ph}) ORDER BY id",
                [int(i) for i in ids]).fetchall()
        else:
            sql = "SELECT * FROM review_proposals WHERE status='open' AND kind=?"
            params: list = [kind]
            if source_id is not None:
                sql += " AND source_id=?"
                params.append(source_id)
            rows = con.execute(sql + " ORDER BY id", params).fetchall()
        matched = [dict(r) for r in rows]
        resolved = 0
        if apply and matched:
            ph = ",".join("?" * len(matched))
            cur = con.execute(
                f"UPDATE review_proposals SET status='resolved' WHERE id IN ({ph})",
                [m["id"] for m in matched])
            resolved = cur.rowcount
            con.commit()
        return {"matched": matched, "resolved": resolved}
    finally:
        con.close()


def source_stats(db_path, source_id: str) -> dict | None:
    """只读代理指标聚合（ingest-stats 用），不改任何行。诚实口径：
    窗口耗时=最后一次尝试（start_window UPSERT 覆盖 started_at，非累计）；
    pages_estimate=finished 窗 write_set_json 去重计数（估算）；拿不到的（token/费用）不伪造。"""
    import json
    con = connect(db_path)
    try:
        src = con.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if src is None:
            return None

        def _secs(a, b):
            if not a or not b:
                return None
            try:
                return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1)
            except ValueError:
                return None

        windows = {"total": 0, "finished": 0, "failed": 0, "running": 0,
                   "empty_writes_unread": 0,
                   "last_attempt_seconds_total": 0.0, "last_attempt_seconds_max": 0.0}
        pages: set[str] = set()
        con.execute(_WINDOW_READS_DDL)
        read_ids = {r["window_id"] for r in con.execute(
            "SELECT window_id FROM window_reads WHERE source_id=?", (source_id,))}
        for r in con.execute(
                "SELECT window_id,status,started_at,finished_at,write_set_json FROM ingest_progress"
                " WHERE source_id=?", (source_id,)):
            windows["total"] += 1
            if r["status"] in ("finished", "failed", "running"):
                windows[r["status"]] += 1
            d = _secs(r["started_at"], r["finished_at"])
            if d is not None:
                windows["last_attempt_seconds_total"] = round(
                    windows["last_attempt_seconds_total"] + d, 1)
                windows["last_attempt_seconds_max"] = max(windows["last_attempt_seconds_max"], d)
            ws = []
            if r["status"] == "finished" and r["write_set_json"]:
                try:
                    ws = json.loads(r["write_set_json"])
                except ValueError:
                    ws = []
                if isinstance(ws, list):
                    pages.update(str(x).replace("\\", "/") for x in ws)
            # 静默遗漏信号：空写集收窗、又从未经 show-window 读过窗内容
            if r["status"] == "finished" and not ws and r["window_id"] not in read_ids:
                windows["empty_writes_unread"] += 1

        stages: dict[str, dict] = {}
        lint_failures = 0
        for r in con.execute(
                "SELECT stage,status,started_at,finished_at FROM source_stage_runs"
                " WHERE source_id=? ORDER BY id", (source_id,)):
            s = stages.setdefault(r["stage"], {"runs": 0, "failed": 0, "last_done_seconds": None})
            s["runs"] += 1
            if r["status"] == "failed":
                s["failed"] += 1
                if r["stage"] == "lint":
                    lint_failures += 1
            elif r["status"] == "done":
                d = _secs(r["started_at"], r["finished_at"])
                if d is not None:
                    s["last_done_seconds"] = d

        proposals: dict[str, dict] = {}
        for r in con.execute(
                "SELECT kind,status,COUNT(*) AS n FROM review_proposals"
                " WHERE source_id=? GROUP BY kind,status", (source_id,)):
            k = proposals.setdefault(r["kind"], {"total": 0, "open": 0, "resolved": 0})
            k["total"] += r["n"]
            if r["status"] in ("open", "resolved"):
                k[r["status"]] += r["n"]

        return {
            "source": dict(src),
            "windows": windows,
            "pages_estimate": len(pages),
            "stages": stages,
            "lint_failures": lint_failures,
            "proposals_by_kind": proposals,
            "notes": [
                "窗口耗时=最后一次尝试（重启同窗会覆盖 started_at，非累计）",
                "pages_estimate=finished 窗 write_set 去重计数（估算）",
                "empty_writes_unread=空写集且从未 show-window 读过的窗（静默遗漏信号；旧源无读窗记录会偏高）",
            ],
        }
    finally:
        con.close()
