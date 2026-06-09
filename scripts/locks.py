"""单 vault 写锁（v1：scope 固定 "vault"），spec §3.3 并发。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _con(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def acquire(db_path, *, scope: str, holder: str, pid: int) -> bool:
    con = _con(db_path)
    try:
        con.execute(
            "INSERT INTO source_locks(scope,holder,pid,started_at,heartbeat_at) VALUES (?,?,?,?,?)",
            (scope, holder, pid, _now(), _now()),
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        con.close()


def release(db_path, *, scope: str, holder: str) -> None:
    con = _con(db_path)
    try:
        con.execute("DELETE FROM source_locks WHERE scope=? AND holder=?", (scope, holder))
        con.commit()
    finally:
        con.close()


def heartbeat(db_path, *, scope: str, holder: str) -> None:
    con = _con(db_path)
    try:
        con.execute("UPDATE source_locks SET heartbeat_at=? WHERE scope=? AND holder=?",
                    (_now(), scope, holder))
        con.commit()
    finally:
        con.close()


def get(db_path, *, scope: str):
    con = _con(db_path)
    try:
        return con.execute("SELECT * FROM source_locks WHERE scope=?", (scope,)).fetchone()
    finally:
        con.close()


def is_stale(db_path, *, scope: str, ttl_seconds: int) -> bool:
    row = get(db_path, scope=scope)
    if row is None:
        return False
    hb = datetime.fromisoformat(row["heartbeat_at"])
    return (datetime.now(timezone.utc) - hb).total_seconds() > ttl_seconds


def break_stale(db_path, *, scope: str, ttl_seconds: int) -> bool:
    if not is_stale(db_path, scope=scope, ttl_seconds=ttl_seconds):
        return False
    con = _con(db_path)
    try:
        con.execute("DELETE FROM source_locks WHERE scope=?", (scope,))
        con.commit()
        return True
    finally:
        con.close()


def force_set_heartbeat(db_path, *, scope: str, iso: str) -> None:
    """仅供测试/维护。"""
    con = _con(db_path)
    try:
        con.execute("UPDATE source_locks SET heartbeat_at=? WHERE scope=?", (iso, scope))
        con.commit()
    finally:
        con.close()
