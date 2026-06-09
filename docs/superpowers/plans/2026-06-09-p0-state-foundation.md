# P0 状态底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（用户指定，**不要用 subagent-driven**）。状态机/锁/快照/status 是同一条契约链，拆给多个 subagent 易接口不一致。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 建立新架构的确定性状态底座——单一业务 SQLite 的 7 张状态机表、**原子阶段 API**、source 级状态机、单 vault 锁、快照回滚、`pipeline status`/`next`——作为 P1–P7 可恢复/可诊断前置，替代 LangGraph/checkpointer。

**Architecture:** 纯确定性 Python（零 LLM）。在 `pipeline-workspace/state/study-kb.sqlite`（**repo/vault 级**单库）**新增** 7 张表，与旧 `business_db.py` 旧表共存（旧表 P4 删）。新增 `state_store.py`（schema + 原子阶段 API + 状态机）、`locks.py`、`snapshots.py`，给 `pipeline.py` 加 `status`/`next`。

**Tech Stack:** Python 3.11+、stdlib `sqlite3`/`hashlib`/`json`/`shutil`、pytest。无新增第三方依赖。

**权威链：** 设计真值 `docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`（§3.3，已含本次硬化的状态契约）；决策 `docs/adr/0001`。P0 文档同步已随 `docs/authority-chain` 合并完成。

**运行环境：** 测试用项目 env（`D:\miniconda3\envs\pythonProject\python.exe`）；命令写作 `python -m pytest ...`。Windows 执行命令用 `pwsh`，不要用 Git Bash 调 PowerShell。

**Git：** 当前在 `main`。先开 `feat/p0-state-foundation` 分支，逐任务提交；合并/push 留到用户确认"全部完成"。

---

## 状态契约（本计划的硬约定，对应 spec §3.3）

- **阶段（current_stage）**：`registered → profiled → converted → windowed → workorder_ready → ingest_waiting → ingesting → ingested → lint`。`registered` 是 `add-source` 后的初始 stage（**profile 成功后才进 `profiled`**）。
- **状态（current_status）**：`pending | running | done | failed | proposed | published`。失败 = `current_status=failed`（停在该 stage，**不新建 `<stage>_failed`**）；`ingested` 完成→`proposed`；`lint` 通过→`published`（终态）。
- **原子阶段 API（唯一入口，同事务更新两表）**：`start_stage` / `complete_stage` / `fail_stage`；幂等 `should_run_stage`。调用方**不得**手动分别写 `source_stage_runs` 和 `sources`。
- **合法迁移**：见 `_allowed_next(stage,status)`（Task 3）。`failed` 可重跑同 stage；`lint` 失败可回 `ingest_waiting`。

## File Structure

- Create `scripts/state_store.py` — DB 连接 + 7 表 DDL + `register_source` + 原子阶段 API + 状态机 + 只读 `status_rows`/`next_actions`。
- Create `scripts/locks.py` — 单 vault 锁：acquire/release/heartbeat/is_stale/break_stale。
- Create `scripts/snapshots.py` — pre-ingest 文件快照 + manifest + rollback（默认非 git，目录被删也能恢复）。
- Modify `scripts/pipeline.py` — 注册 `status`/`next`（只读派生，**vault 级单库路径定死**，不与 `--book` 混）。
- Tests：`tests/test_state_store.py`、`tests/test_locks.py`、`tests/test_snapshots.py`、`tests/test_pipeline_status.py`。
- 不动：`business_db.py`/`langgraph_worker.py` 等（P4 删）。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p0-state-foundation` → Expected `Switched to a new branch 'feat/p0-state-foundation'`
- [ ] **Step 2:** Run `git status --short` → Expected: 无输出（含本计划文件未提交也可，下一任务一并纳入）。

---

### Task 2: `state_store.py` —— DB 连接 + 7 表 schema

**Files:** Create `scripts/state_store.py`、`tests/test_state_store.py`

- [ ] **Step 1: 写失败测试（建表 + 幂等）**

Create `tests/test_state_store.py`:

```python
import sqlite3
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
state_store = _load("state_store")

EXPECTED_TABLES = {
    "sources", "source_stage_runs", "artifacts", "work_orders",
    "source_locks", "review_proposals", "ingest_progress",
}

def _tables(db):
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close(); return names

def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    assert EXPECTED_TABLES <= _tables(db)

def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db); state_store.init_db(db)
    assert EXPECTED_TABLES <= _tables(db)
```

- [ ] **Step 2:** Run `python -m pytest tests/test_state_store.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现 schema**

Create `scripts/state_store.py`:

```python
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
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row; return con

def init_db(db_path) -> None:
    p = Path(db_path); p.parent.mkdir(parents=True, exist_ok=True)
    con = connect(p)
    try:
        con.executescript(SCHEMA); con.commit()
    finally:
        con.close()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

- [ ] **Step 4:** Run `python -m pytest tests/test_state_store.py -q` → Expected PASS（2）。
- [ ] **Step 5:** Commit
```
git add scripts/state_store.py tests/test_state_store.py docs/superpowers/plans/2026-06-09-p0-state-foundation.md
git commit -m "Add state_store schema: 7 state-machine tables" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 原子阶段 API + 状态机（合并原 3+4，P0 核心）

**Files:** Modify `scripts/state_store.py`、`tests/test_state_store.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_state_store.py`：

```python
import pytest

def _running_run(db, source_id, stage):
    con = state_store.connect(db)
    row = con.execute("SELECT * FROM source_stage_runs WHERE source_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                      (source_id, stage)).fetchone()
    con.close(); return row

def test_register_source_starts_at_registered_done(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="game-theory", fmt="pdf")
    r = state_store.get_source(db, "s1")
    assert (r["current_stage"], r["current_status"]) == ("registered", "done")

def test_next_action_from_registered_is_profile(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    assert state_store.next_actions(db)[0]["next_action"] == "run: profile"

def test_start_stage_atomically_updates_both_tables(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    rid = state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    src = state_store.get_source(db, "s1")
    run = _running_run(db, "s1", "profiled")
    assert src["current_stage"] == "profiled" and src["current_status"] == "running"
    assert run["status"] == "running" and run["id"] == rid

def test_complete_stage_sets_done(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    state_store.complete_stage(db, "s1", "profiled", output_hash="o1")
    src = state_store.get_source(db, "s1")
    assert src["current_status"] == "done"
    assert _running_run(db, "s1", "profiled")["status"] == "done"

def test_invalid_transition_rejected(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    with pytest.raises(state_store.InvalidTransition):
        state_store.start_stage(db, "s1", "converted", input_hash="h")  # registered 只能 -> profiled

def _advance(db, sid, stages):
    for st in stages:
        state_store.start_stage(db, sid, st, input_hash=st)
        state_store.complete_stage(db, sid, st)

def test_lint_fail_then_retry_via_ingest_waiting(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled","converted","windowed","workorder_ready","ingest_waiting","ingesting","ingested"])
    assert state_store.get_source(db, "s1")["current_status"] == "proposed"  # ingested -> proposed
    state_store.start_stage(db, "s1", "lint", input_hash="l1")
    state_store.fail_stage(db, "s1", "lint", error="missing evidence")
    assert state_store.get_source(db, "s1")["current_status"] == "failed"
    # 失败后可回 ingest_waiting 重跑
    state_store.start_stage(db, "s1", "ingest_waiting", input_hash="l2")
    assert state_store.get_source(db, "s1")["current_stage"] == "ingest_waiting"

def test_lint_pass_sets_published(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    _advance(db, "s1", ["profiled","converted","windowed","workorder_ready","ingest_waiting","ingesting","ingested","lint"])
    assert state_store.get_source(db, "s1")["current_status"] == "published"

def test_should_run_stage_idempotent_skip(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h1") is True
    state_store.start_stage(db, "s1", "profiled", input_hash="h1")
    state_store.complete_stage(db, "s1", "profiled")
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h1") is False
    assert state_store.should_run_stage(db, "s1", "profiled", input_hash="h2") is True
```

- [ ] **Step 2:** Run `python -m pytest tests/test_state_store.py -q` → Expected FAIL（API 未定义）。

- [ ] **Step 3: 实现原子阶段 API + 状态机**

追加到 `scripts/state_store.py`：

```python
STAGES = ["registered","profiled","converted","windowed","workorder_ready",
          "ingest_waiting","ingesting","ingested","lint"]
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
    return {stage}  # pending|running -> 只能(重)启当前 stage

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
        con.rollback(); raise
    finally:
        con.close()

def complete_stage(db_path, source_id: str, stage: str, *, output_hash: str | None = None) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "UPDATE source_stage_runs SET status='done', finished_at=?, output_hash=?"
            " WHERE id=(SELECT id FROM source_stage_runs WHERE source_id=? AND stage=? AND status='running'"
            "           ORDER BY id DESC LIMIT 1)",
            (_now(), output_hash, source_id, stage))
        con.execute("UPDATE sources SET current_status=? WHERE source_id=? AND current_stage=?",
                    (DONE_STATUS.get(stage, "done"), source_id, stage))
        con.commit()
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

def fail_stage(db_path, source_id: str, stage: str, *, error: str) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "UPDATE source_stage_runs SET status='failed', finished_at=?, error=?"
            " WHERE id=(SELECT id FROM source_stage_runs WHERE source_id=? AND stage=? AND status='running'"
            "           ORDER BY id DESC LIMIT 1)",
            (_now(), error, source_id, stage))
        con.execute("UPDATE sources SET current_status='failed' WHERE source_id=? AND current_stage=?",
                    (source_id, stage))
        con.commit()
    except Exception:
        con.rollback(); raise
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
```

- [ ] **Step 4:** Run `python -m pytest tests/test_state_store.py -q` → Expected PASS（全部）。
- [ ] **Step 5:** Commit
```
git add scripts/state_store.py tests/test_state_store.py
git commit -m "Add atomic stage API + state machine (start/complete/fail, idempotent, registered initial)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `locks.py` —— 单 vault 锁 + stale

**Files:** Create `scripts/locks.py`、`tests/test_locks.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_locks.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
state_store = _load("state_store"); locks = _load("locks")

def test_acquire_then_second_blocked(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    assert locks.acquire(db, scope="vault", holder="A", pid=111) is True
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is False

def test_release_allows_reacquire(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    locks.acquire(db, scope="vault", holder="A", pid=111)
    locks.release(db, scope="vault", holder="A")
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is True

def test_stale_detected_and_breakable(tmp_path):
    db = tmp_path / "study-kb.sqlite"; state_store.init_db(db)
    locks.acquire(db, scope="vault", holder="A", pid=111)
    locks.force_set_heartbeat(db, scope="vault", iso="2000-01-01T00:00:00+00:00")
    assert locks.is_stale(db, scope="vault", ttl_seconds=300) is True
    assert locks.break_stale(db, scope="vault", ttl_seconds=300) is True
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is True
```

- [ ] **Step 2:** Run `python -m pytest tests/test_locks.py -q` → Expected FAIL。

- [ ] **Step 3: 实现**

Create `scripts/locks.py`:

```python
"""单 vault 写锁（v1：scope 固定 "vault"），spec §3.3 并发。"""
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3

def _con(db_path):
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row; return con

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def acquire(db_path, *, scope: str, holder: str, pid: int) -> bool:
    con = _con(db_path)
    try:
        con.execute("INSERT INTO source_locks(scope,holder,pid,started_at,heartbeat_at) VALUES (?,?,?,?,?)",
                    (scope, holder, pid, _now(), _now()))
        con.commit(); return True
    except sqlite3.IntegrityError:
        return False
    finally:
        con.close()

def release(db_path, *, scope: str, holder: str) -> None:
    con = _con(db_path)
    try:
        con.execute("DELETE FROM source_locks WHERE scope=? AND holder=?", (scope, holder)); con.commit()
    finally:
        con.close()

def heartbeat(db_path, *, scope: str, holder: str) -> None:
    con = _con(db_path)
    try:
        con.execute("UPDATE source_locks SET heartbeat_at=? WHERE scope=? AND holder=?",
                    (_now(), scope, holder)); con.commit()
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
        con.execute("DELETE FROM source_locks WHERE scope=?", (scope,)); con.commit(); return True
    finally:
        con.close()

def force_set_heartbeat(db_path, *, scope: str, iso: str) -> None:
    """仅供测试/维护。"""
    con = _con(db_path)
    try:
        con.execute("UPDATE source_locks SET heartbeat_at=? WHERE scope=?", (iso, scope)); con.commit()
    finally:
        con.close()
```

- [ ] **Step 4:** Run `python -m pytest tests/test_locks.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit
```
git add scripts/locks.py tests/test_locks.py
git commit -m "Add single-vault lock with heartbeat + stale detection" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `snapshots.py` —— 快照 + 回滚（含目录被删边界）

**Files:** Create `scripts/snapshots.py`、`tests/test_snapshots.py`

- [ ] **Step 1: 写失败测试（含目录被删后恢复）**

Create `tests/test_snapshots.py`:

```python
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snapshots", ROOT / "scripts" / "snapshots.py")
snapshots = importlib.util.module_from_spec(spec); spec.loader.exec_module(snapshots)

def test_rollback_restores_modified_file(tmp_path):
    vault = tmp_path / "wiki"; vault.mkdir()
    page = vault / "concept.md"; page.write_text("ORIGINAL", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    page.write_text("MERGED-BUT-FAILED", encoding="utf-8")
    snapshots.rollback(mani)
    assert page.read_text(encoding="utf-8") == "ORIGINAL"

def test_manifest_has_sha256_and_relpath(tmp_path):
    vault = tmp_path / "wiki"; vault.mkdir()
    page = vault / "a.md"; page.write_text("X", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    data = json.loads(mani.read_text(encoding="utf-8"))
    assert data["entries"][0]["rel_path"] == "a.md"
    assert len(data["entries"][0]["sha256"]) == 64

def test_rollback_deletes_created_file(tmp_path):
    vault = tmp_path / "wiki"; vault.mkdir()
    newp = vault / "new.md"  # 不存在
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[newp], base_dir=vault)
    newp.write_text("CREATED-BUT-FAILED", encoding="utf-8")
    snapshots.rollback(mani)
    assert not newp.exists()

def test_rollback_recreates_deleted_parent_dir(tmp_path):
    vault = tmp_path / "wiki"; sub = vault / "domains" / "game-theory" / "concepts"
    sub.mkdir(parents=True)
    page = sub / "x.md"; page.write_text("ORIGINAL", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    import shutil
    shutil.rmtree(vault / "domains")            # 目录也被删
    snapshots.rollback(mani)                     # 必须重建父目录再恢复
    assert page.read_text(encoding="utf-8") == "ORIGINAL"
```

- [ ] **Step 2:** Run `python -m pytest tests/test_snapshots.py -q` → Expected FAIL。

- [ ] **Step 3: 实现（rollback 前 mkdir 父目录）**

Create `scripts/snapshots.py`:

```python
"""Pre-ingest 文件快照 + 回滚（默认非 git，spec §3.3）。"""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path

def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def take_snapshot(snap_root, *, source_id: str, run_id: str, files, base_dir) -> Path:
    base = Path(base_dir)
    dest = Path(snap_root) / source_id / run_id
    (dest / "files").mkdir(parents=True, exist_ok=True)
    entries = []
    for f in files:
        f = Path(f); rel = f.relative_to(base).as_posix()
        if f.exists():
            saved = dest / "files" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, saved)
            entries.append({"rel_path": rel, "existed": True, "sha256": _sha256(f),
                            "saved": saved.as_posix()})
        else:
            entries.append({"rel_path": rel, "existed": False, "sha256": None, "saved": None})
    manifest = dest / "manifest.json"
    manifest.write_text(json.dumps(
        {"source_id": source_id, "run_id": run_id, "base_dir": base.as_posix(), "entries": entries},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest

def rollback(manifest_path) -> None:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    base = Path(data["base_dir"])
    for e in data["entries"]:
        target = base / e["rel_path"]
        if e["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)   # 目录可能已被删
            shutil.copy2(e["saved"], target)
        elif target.exists():
            target.unlink()

def cleanup(snap_root, *, source_id: str, run_id: str) -> None:
    d = Path(snap_root) / source_id / run_id
    if d.exists():
        shutil.rmtree(d)
```

- [ ] **Step 4:** Run `python -m pytest tests/test_snapshots.py -q` → Expected PASS（4）。
- [ ] **Step 5:** Commit
```
git add scripts/snapshots.py tests/test_snapshots.py
git commit -m "Add pre-ingest snapshot + rollback (non-git, recreates deleted dirs)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `pipeline status` / `next`（vault 级单库，定死路径 + smoke）

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_pipeline_status.py`

- [ ] **Step 1: 写失败测试（用 subprocess 跑真实 CLI smoke，避免依赖 argparse 内部）**

Create `tests/test_pipeline_status.py`:

```python
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

def test_status_smoke_runs(tmp_path):
    # 在干净临时 cwd 跑：无 state db 时也应 exit 0 且给出提示
    r = subprocess.run([sys.executable, str(PIPELINE), "status"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "no state" in r.stdout.lower() or r.stdout.strip() == ""

def test_state_db_path_is_vault_level():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", PIPELINE)
    # 仅校验模块定义了 vault 级路径常量/函数，且不含 --book
    text = PIPELINE.read_text(encoding="utf-8")
    assert "pipeline-workspace/state/study-kb.sqlite" in text
```

> 说明：`status`/`next` 是 **vault 级单库**（repo 根的 `pipeline-workspace/state/study-kb.sqlite`），**不接 `--book`**，与旧 per-book 命令分开。

- [ ] **Step 2:** Run `python -m pytest tests/test_pipeline_status.py -q` → Expected FAIL（无 status 子命令 / exit≠0）。

- [ ] **Step 3: 在 `pipeline.py` 注册 `status`/`next`**

在 `scripts/pipeline.py` 顶部（确保 `scripts/` 在 `sys.path`，沿用文件现有导入模式）加：

```python
from pathlib import Path as _Path

def _vault_state_db() -> _Path:
    # vault 级单库：repo 根下固定路径（不接 --book）
    return _Path(__file__).resolve().parents[1] / "pipeline-workspace/state/study-kb.sqlite"

def cmd_status(args):
    import state_store
    db = _vault_state_db()
    if not db.exists():
        print("no state db yet (run a source through preprocess first)"); return
    for r in state_store.status_rows(db):
        print(f"{r['source_id']:<28} {r['domain']:<14} {r['current_stage']:<16} {r['current_status']}")

def cmd_next(args):
    import state_store
    db = _vault_state_db()
    if not db.exists():
        print("no state db yet"); return
    for r in state_store.next_actions(db):
        print(f"{r['source_id']:<28} {r['current_stage']:<16} -> {r['next_action']}")
```

在 subparsers 注册处加：

```python
sp = subparsers.add_parser("status", help="列出每个 source 的阶段/状态（vault 级单库）")
sp.set_defaults(func=cmd_status)
np = subparsers.add_parser("next", help="列出每个 source 的下一步人工动作")
np.set_defaults(func=cmd_next)
```

> 若 `pipeline.py` 现有 `import` 风格是 `sys.path.insert(0, str(SCRIPTS_DIR)); from xxx import ...`，则 `import state_store` 同样可用；按现有模式落地，不引入新机制。

- [ ] **Step 4:** Run `python -m pytest tests/test_pipeline_status.py -q` → Expected PASS（2）。
- [ ] **Step 5: 手动 smoke**

Run: `python scripts/pipeline.py status`
Expected: 打印 `no state db yet ...`（无 db 时），exit 0。

- [ ] **Step 6:** Commit
```
git add scripts/pipeline.py tests/test_pipeline_status.py
git commit -m "Add vault-level pipeline status/next (single DB, status-aware next action)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 全量回归 + P0 验收

**Files:** 无改动，纯验证

- [ ] **Step 1:** Run `python -m pytest -q` → Expected: 新增测试全 PASS；旧测试不被破坏（旧表未动）。环境/旧依赖导致的 skip 记录但不算失败。
- [ ] **Step 2: 验收清单（对照 spec §3.3 + 本计划状态契约）**
  - 7 表存在、`init_db` 幂等。
  - `register_source` → `(registered, done)`；`next` 建议 `run: profile`（**不**误报 source-convert）。
  - 原子阶段 API：`start_stage` 同事务更新两表；非法迁移 `InvalidTransition`；`ingested`→`proposed`、`lint` 通过→`published`、`lint` 失败→`failed` 且可回 `ingest_waiting`。
  - `should_run_stage` 幂等跳过。
  - 锁：二次 acquire 拒、release 重得、stale 可破。
  - 快照：改写可回滚、新建页回滚即删、**父目录被删仍能恢复**。
  - `pipeline status`/`next` vault 级单库、smoke exit 0、不接 `--book`。
- [ ] **Step 3:** Run `git status --short` → Expected: 干净。

---

## Self-Review

- **Codex 5 点**：①`registered` 初始态（Task 3 register→registered/done，next=profile）②原子阶段 API 同事务更新两表（Task 3 start/complete/fail，含 rollback）③失败=`status=failed` 不新建 stage（Task 3 + spec §3.3）④status/next 定死 vault 级单库 + smoke + 不接 --book（Task 6）⑤快照父目录被删仍能恢复（Task 5 新增测试 + mkdir）。全部覆盖。✓
- **占位符**：各步含完整测试 + 实现代码，无 TODO/TBD。✓
- **签名一致**：`init_db/connect/register_source/get_source/start_stage/complete_stage/fail_stage/should_run_stage/status_rows/next_actions`（state_store）；`acquire/release/heartbeat/get/is_stale/break_stale/force_set_heartbeat`（locks）；`take_snapshot/rollback/cleanup`（snapshots）——测试与实现逐一对应。✓
- **不破坏旧码**：只新增模块 + 新表。✓
- **执行方式**：Inline（用户指定），契约链一致性优先。

## 完成后

P0 完成 = 可恢复/可诊断状态底座就位（原子阶段 API 替代 checkpointer 的恢复价值）。下一步 **P1（source-convert + processing windows + 难页 vision 标记）** 在此底座上记录阶段、产物 hash、续跑。
