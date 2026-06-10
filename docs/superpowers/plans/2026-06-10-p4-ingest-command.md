# P4 命令层 + /ingest + Work Order 事务协议 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P3 同，单契约链不拆 subagent）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 落地 spec §9 的 source 级 work order 事务协议（写入边界 + registry hash 守卫 + 页面快照 + 覆盖保护）与 §3.4 的显式命令层（`.claude/commands/ingest.md` + `docs/skill-runtime/*`），并给 `/ingest` 提供全套确定性 CLI 支撑（workorder 生成、锁、window 级进度、窗口文本读取、概念归一、写入守卫、页面快照），含 rolling digest 协议（参考对照评估 C1）。

**Architecture:** 确定性部分全在 Python CLI（守卫先于信任：stale registry 在 `ingest-start` 由 CLI 硬校验，不靠 LLM 自觉）；`/ingest` 本身是人工触发的 Claude Code 显式 slash command，其协议文档指挥 Claude 逐窗读取（`show-window`）、维护滚动摘要（`digest.md`）、走 `resolve-concept`/`check-write`/`snapshot-page` CLI 完成概念归一与受保护写入、用 `window-start/done/fail` 记录可续跑进度。**所有写出页一律 `status: proposed`**；promote/lint 是 P6。

**Tech Stack:** Python 3.11+、stdlib、`pyyaml`、pytest。无新增依赖。

**权威链：** spec §9（work order 契约/事务协议）、§3.3（ingest_progress window 级续跑、并发锁、两阶段发布）、§3.4（显式命令 vs 自动 Skill、最小上下文加载）、§3.1（rolling digest）、§6（resolve 协议）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`。

**Git：** 从 `feat/p3-page-templates` 开 `feat/p4-ingest-command`。逐任务提交；合并/push 留到用户确认。

---

## 真实 P0–P3 API（本期在其上构建，勿改既有函数/测试）

- `state_store`：原子阶段 API、`record_artifact/list_artifacts`、`ingest_progress` 表（已建未用，本期 T2 加 window 助手）、`work_orders` 表（已建未用，本期 T5 加 record/get 助手）。
- `locks`：`acquire/release/get/is_stale/break_stale`（scope="vault"）。
- `snapshots`：`take_snapshot(snap_root, *, source_id, run_id, files, base_dir) -> manifest Path`、`rollback(manifest)`。
- `concept_store`：`scan_concept_pages/build_registry/write_registry/write_aliases/resolve_or_create_concept`。
- `mdpage`：`read_page/write_page`。`page_rules`：自检原语。`templates/*`：写页格式契约。
- `pipeline.py`：`_workspace_root()/_vault_state_db()/_staging_dir()/_vault_dir()`、`commands` dict。

## 本期范围与取舍（请先看）

- **做**：① `ingest_progress` window 助手（status=`running|finished|failed`，spec §3.3 用 `finished`）；② `ingest_guards.py`（write_scope glob、覆盖保护三条件、registry hash 校验）；③ `workorder.py` 生成器（registry 重建+hash、domain+shared 概念页快照、其它目标页快照、写入边界）；④ CLI 9 个子命令：`workorder`/`show-window`/`ingest-start`/`ingest-done`/`window-start`/`window-done`/`window-fail`/`resolve-concept`/`check-write`/`snapshot-page`（10 个）；⑤ 命令层文档 4 件：`.claude/commands/ingest.md`、`docs/skill-runtime/{routing,schema,concept-resolution}.md`。
- **硬守卫放 CLI 而非协议文本**：stale registry → `ingest-start` 中止；锁 → `ingest-start` 获取、`ingest-done` 释放；写入边界与覆盖保护 → `check-write` 给出 ALLOW/DENY。LLM 侧协议要求"写前必查"，但即使忘了，P6 门禁仍兜底。
- **resolve-concept 不写派生文件**：每次调用从概念页**实时扫描**重建内存 registry（始终新鲜），创建/合并只动概念页 frontmatter；`_registry.yaml`/`aliases.md` 重建留收尾（spec §6）。
- **rolling digest**：协议规定每窗结束把"本窗要点 + 未决线索"追加进 `staging/<source>/digest.md`，下一窗开工先读 digest——文件由 Claude 维护，CLI 不管语义（C1 落实在协议文档 + 文档测试断言）。
- **不做**：lint/promote/回滚门禁组装（P6）、Review-Queue 回流（P6）、`/kb-query`/`/kb-save`/`/kb-review`/`/wiki-lint-semantic` 命令（P8）、自动触发 SKILL.md（spec §3.4 显式命令路线，无此需求）、删除旧管线代码（单列清理，避免与本期混做）。

## File Structure

- Modify `scripts/state_store.py` — 加 `start_window/finish_window/fail_window/should_run_window/window_states` + `record_work_order/get_work_order` + `latest_run_id`（仅新增函数）。
- Create `scripts/ingest_guards.py` — `in_write_scope`/`can_overwrite`/`registry_fresh`（纯函数 + 只读文件 hash）。
- Create `scripts/workorder.py` — `build_workorder`/`write_workorder`。
- Modify `scripts/pipeline.py` — 10 个新子命令（沿用 `commands` dict）。
- Create `.claude/commands/ingest.md`、`docs/skill-runtime/routing.md`、`docs/skill-runtime/schema.md`、`docs/skill-runtime/concept-resolution.md`。
- Tests：`tests/test_ingest_progress.py`、`tests/test_ingest_guards.py`、`tests/test_workorder.py`、`tests/test_p4_cli.py`、`tests/test_command_docs.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p4-ingest-command`（基于 feat/p3-page-templates）→ Expected 切到新分支。
- [ ] **Step 2:** Run `git status --short` → Expected 干净（报告目录未跟踪可忽略）。

---

### Task 2: `state_store` window 进度 + work order 助手

**Files:** Modify `scripts/state_store.py`、Test `tests/test_ingest_progress.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_ingest_progress.py`:

```python
from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_store)


def _db(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="md")
    return db


def test_window_start_finish_roundtrip(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.finish_window(db, "s1", "w0000", write_set_json='["a.md"]')
    rows = state_store.window_states(db, "s1")
    assert rows[0]["window_id"] == "w0000" and rows[0]["status"] == "finished"
    assert rows[0]["write_set_json"] == '["a.md"]'


def test_should_run_window_skips_finished_same_hash(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.finish_window(db, "s1", "w0000")
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h1") is False
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h2") is True
    assert state_store.should_run_window(db, "s1", "w0001", input_hash="h1") is True


def test_window_fail_then_restart(tmp_path):
    db = _db(tmp_path)
    state_store.start_window(db, "s1", "w0000", input_hash="h1")
    state_store.fail_window(db, "s1", "w0000", error="boom")
    assert state_store.window_states(db, "s1")[0]["status"] == "failed"
    assert state_store.should_run_window(db, "s1", "w0000", input_hash="h1") is True
    state_store.start_window(db, "s1", "w0000", input_hash="h1")  # 重启同窗不报错（UPSERT）
    state_store.finish_window(db, "s1", "w0000")
    assert state_store.window_states(db, "s1")[0]["status"] == "finished"


def test_finish_unknown_window_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(state_store.InvalidTransition):
        state_store.finish_window(db, "s1", "w9999")


def test_record_and_get_work_order(tmp_path):
    db = _db(tmp_path)
    state_store.record_work_order(db, "s1", path="staging/s1/workorder.yaml",
                                  registry_hash="r" * 64, write_scope_json='["domains/d/**"]')
    wo = state_store.get_work_order(db, "s1")
    assert wo["registry_hash"] == "r" * 64
    # 重复记录 = 覆盖（幂等）
    state_store.record_work_order(db, "s1", path="staging/s1/workorder.yaml",
                                  registry_hash="x" * 64, write_scope_json="[]")
    assert state_store.get_work_order(db, "s1")["registry_hash"] == "x" * 64


def test_latest_run_id(tmp_path):
    db = _db(tmp_path)
    rid = state_store.start_stage(db, "s1", "profiled", input_hash="h")
    assert state_store.latest_run_id(db, "s1", "profiled") == rid
    assert state_store.latest_run_id(db, "s1", "converted") is None
```

- [ ] **Step 2:** Run `python -m pytest tests/test_ingest_progress.py -q` → Expected FAIL（函数未定义）。

- [ ] **Step 3: 实现（追加到 `scripts/state_store.py` 末尾）**

```python
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
```

- [ ] **Step 4:** Run `python -m pytest tests/test_ingest_progress.py tests/test_state_store.py -q` → Expected PASS（6 + 13 回归）。
- [ ] **Step 5:** Commit

```
git add scripts/state_store.py tests/test_ingest_progress.py docs/superpowers/plans/2026-06-10-p4-ingest-command.md
git commit -m "Add window progress + work-order helpers on state_store" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `ingest_guards.py` —— 写入边界 + 覆盖保护 + registry 守卫

**Files:** Create `scripts/ingest_guards.py`、Test `tests/test_ingest_guards.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_ingest_guards.py`:

```python
import hashlib
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ingest_guards", ROOT / "scripts" / "ingest_guards.py")
ingest_guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest_guards)

SCOPE = ["domains/game-theory/**", "concepts/**", "sources/wp.md", "overview.md", "log.md"]


def test_in_write_scope_glob_and_exact():
    assert ingest_guards.in_write_scope("domains/game-theory/lessons/5.2.md", SCOPE)
    assert ingest_guards.in_write_scope("concepts/_registry.yaml", SCOPE)  # glob 命中（派生文件禁写在协议层）
    assert ingest_guards.in_write_scope("sources/wp.md", SCOPE)
    assert not ingest_guards.in_write_scope("sources/other.md", SCOPE)
    assert not ingest_guards.in_write_scope("domains/math-econ/lessons/1.md", SCOPE)
    assert not ingest_guards.in_write_scope("index.generated.md", SCOPE)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_can_overwrite_three_conditions(tmp_path):
    page = tmp_path / "domains" / "d" / "concepts" / "x.md"
    page.parent.mkdir(parents=True)
    page.write_text("V1", encoding="utf-8")
    snap = [{"path": "domains/d/concepts/x.md", "sha256": _sha(page), "managed_by": "pipeline"}]
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap)
    assert ok, reason
    # 条件③破坏：磁盘 hash 变了
    page.write_text("V2-human-edited", encoding="utf-8")
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap)
    assert not ok and "hash" in reason
    # 条件②破坏：managed_by human
    snap2 = [{"path": "domains/d/concepts/x.md", "sha256": _sha(page), "managed_by": "human"}]
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap2)
    assert not ok and "human" in reason
    # 条件①破坏：不在 snapshot
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", [])
    assert not ok and "snapshot" in reason


def test_can_overwrite_new_page_allowed(tmp_path):
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/lessons/new.md", [])
    assert ok and reason == "new page"


def test_registry_fresh(tmp_path):
    reg = tmp_path / "concepts" / "_registry.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text("a: 1\n", encoding="utf-8")
    assert ingest_guards.registry_fresh(tmp_path, _sha(reg)) is True
    assert ingest_guards.registry_fresh(tmp_path, "0" * 64) is False
    # registry 不存在：期望空 hash 才算新鲜
    assert ingest_guards.registry_fresh(tmp_path / "no-vault", "") is True
    assert ingest_guards.registry_fresh(tmp_path / "no-vault", "0" * 64) is False
```

- [ ] **Step 2:** Run `python -m pytest tests/test_ingest_guards.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/ingest_guards.py`:

```python
"""/ingest 写入守卫（spec §9）：写入边界 glob、覆盖保护三条件、registry hash 守卫。纯函数 + 只读。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _glob_to_re(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern[i: i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def in_write_scope(rel_path: str, write_scope: list[str]) -> bool:
    p = rel_path.replace("\\", "/")
    return any(_glob_to_re(g).match(p) for g in write_scope)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def can_overwrite(vault, rel_path: str, snapshot_entries: list[dict]) -> tuple[bool, str]:
    """覆盖保护（spec §9 三条件，全过才许覆盖）：①在 snapshot 中 ②managed_by != human ③磁盘 hash == snapshot hash。
    目标页不存在 = 新建，放行（写入边界另由 in_write_scope 把守）。"""
    target = Path(vault) / rel_path
    if not target.exists():
        return True, "new page"
    entry = next((e for e in snapshot_entries if e.get("path") == rel_path), None)
    if entry is None:
        return False, "not in work-order snapshot"
    if entry.get("managed_by") == "human":
        return False, "managed_by human"
    if _sha256_file(target) != entry.get("sha256"):
        return False, "disk hash changed since snapshot"
    return True, "ok"


def registry_fresh(vault, expected_hash: str) -> bool:
    """开工守卫：磁盘 _registry.yaml 的 hash 必须等于 work order 记录的 hash（spec §9）。"""
    reg = Path(vault) / "concepts" / "_registry.yaml"
    if not reg.exists():
        return expected_hash == ""
    return _sha256_file(reg) == expected_hash
```

- [ ] **Step 4:** Run `python -m pytest tests/test_ingest_guards.py -q` → Expected PASS（4）。
- [ ] **Step 5:** Commit

```
git add scripts/ingest_guards.py tests/test_ingest_guards.py
git commit -m "Add ingest guards: write-scope glob, overwrite triple-check, registry freshness" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `workorder.py` —— work order 生成器

**Files:** Create `scripts/workorder.py`、Test `tests/test_workorder.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_workorder.py`:

```python
from pathlib import Path
import importlib.util

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


concept_store = _load("concept_store")
workorder = _load("workorder")


def _vault_with_concepts(tmp_path):
    vault = tmp_path / "wiki"
    concept_store.create_concept(vault, domain="game-theory", name="信号博弈",
                                 aliases=["Signaling Game"])
    concept_store.create_concept(vault, domain="shared", name="期望效用")
    concept_store.create_concept(vault, domain="other-domain", name="无关概念")
    (vault / "overview.md").write_text("# overview\n", encoding="utf-8")
    return vault


def test_build_workorder_contract(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    assert wo["source_id"] == "wp" and wo["domain"] == "game-theory"
    assert "domains/game-theory/**" in wo["write_scope"]
    assert "sources/wp.md" in wo["write_scope"]
    assert len(wo["registry"]["hash"]) == 64
    assert wo["registry"]["scope"] == ["domain:game-theory", "shared"]
    # 概念快照：本域 + shared，全量；排除其它域
    cids = {e["canonical_id"] for e in wo["concept_pages_snapshot"]}
    assert "concept.game-theory.signaling-game" in cids
    assert "concept.shared.期望效用" in cids
    assert all("other-domain" not in c for c in cids)
    assert all(len(e["sha256"]) == 64 and e["managed_by"] for e in wo["concept_pages_snapshot"])
    # 其它目标页快照：已存在的 overview.md
    other_paths = {e["path"] for e in wo["other_pages_snapshot"]}
    assert "overview.md" in other_paths
    assert wo["on_failure"] == "route_to_review_queue"
    assert wo["source"]["processing_windows"].endswith("windows.jsonl")


def test_write_workorder_yaml_roundtrip(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    path = workorder.write_workorder(staging, wo)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["registry"]["hash"] == wo["registry"]["hash"]


def test_registry_hash_matches_disk_after_build(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    ig = _load("ingest_guards")
    assert ig.registry_fresh(vault, wo["registry"]["hash"]) is True  # build 后磁盘即新鲜
```

- [ ] **Step 2:** Run `python -m pytest tests/test_workorder.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/workorder.py`:

```python
"""source 级 work order 生成（spec §9）：写入边界 + registry hash 守卫 + 页面快照。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _managed_by(p: Path) -> str:
    meta, _ = mdpage.read_page(p)
    return meta.get("managed_by", "pipeline")


def build_workorder(vault, *, source_id: str, domain: str, staging_dir) -> dict:
    vault = Path(vault)
    staging = Path(staging_dir)
    # registry 重建保证新鲜（vault 可能尚无概念页 → 空 registry，hash 仍确定）
    metas = concept_store.scan_concept_pages(vault) if vault.exists() else []
    registry, errors, _warnings = concept_store.build_registry(metas)
    if errors:
        raise ValueError("corrupt concept pages: " + "; ".join(errors))
    reg_hash = concept_store.write_registry(vault, registry)

    concept_snap = []
    for cid in sorted(registry):
        e = registry[cid]
        if e["domain"] not in (domain, "shared"):
            continue
        page = vault / e["page_path"]
        concept_snap.append({"canonical_id": cid, "path": e["page_path"],
                             "sha256": _sha256_file(page), "managed_by": _managed_by(page)})

    other_snap = []
    fixed = [f"sources/{source_id}.md", "overview.md", "log.md"]
    lessons_dir = vault / "domains" / domain / "lessons"
    candidates = [vault / rel for rel in fixed]
    if lessons_dir.exists():
        candidates += sorted(lessons_dir.glob("*.md"))
    for p in candidates:
        if p.exists():
            rel = p.relative_to(vault).as_posix()
            other_snap.append({"path": rel, "sha256": _sha256_file(p), "managed_by": _managed_by(p)})

    return {
        "source_id": source_id,
        "domain": domain,
        "write_scope": [f"domains/{domain}/**", "concepts/**", "topics/**", "comparisons/**",
                        "synthesis/**", f"sources/{source_id}.md", "overview.md", "log.md"],
        "registry": {"path": "concepts/_registry.yaml", "hash": reg_hash,
                     "scope": [f"domain:{domain}", "shared"]},
        "concept_pages_snapshot": concept_snap,
        "other_pages_snapshot": other_snap,
        "source": {"text_md": str(staging / "source.md"),
                   "page_images_dir": str(staging / "assets"),
                   "processing_windows": str(staging / "windows.jsonl")},
        "on_failure": "route_to_review_queue",
    }


def write_workorder(staging_dir, wo: dict) -> Path:
    path = Path(staging_dir) / "workorder.yaml"
    path.write_text(yaml.safe_dump(wo, allow_unicode=True, sort_keys=True,
                                   default_flow_style=False), encoding="utf-8")
    return path
```

- [ ] **Step 4:** Run `python -m pytest tests/test_workorder.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit

```
git add scripts/workorder.py tests/test_workorder.py
git commit -m "Add work-order generator (write scope, registry hash, page snapshots)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: CLI —— `workorder` / `show-window`

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p4_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_p4_cli.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _prep_source(tmp_path, sid="note"):
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n\n# B\n\nbbb 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, r.stderr
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


def test_workorder_advances_state_and_writes_yaml(tmp_path):
    db = _prep_source(tmp_path)
    r = _run(["workorder", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "pipeline-workspace/staging/note/workorder.yaml").exists()
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")
    assert state_store.get_work_order(db, "note") is not None
    # 幂等：重跑 [skip]
    r2 = _run(["workorder", "--source", "note"], tmp_path)
    assert "[skip]" in r2.stdout


def test_show_window_prints_window_text(tmp_path):
    _prep_source(tmp_path)
    ws = (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").read_text(encoding="utf-8")
    wid = json.loads(ws.splitlines()[0])["window_id"]
    r = _run(["show-window", "--source", "note", "--window", wid], tmp_path)
    assert r.returncode == 0 and "aaa" in r.stdout
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p4_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`）**

在 `cmd_rebuild_registry` 之后加：

```python
def cmd_workorder(args):
    """生成 source 级 ingest work order（spec §9）：windowed → workorder_ready。"""
    import state_store
    import workorder
    import json
    import hashlib
    db = _vault_state_db()
    src_row = state_store.get_source(db, args.source)
    if src_row is None:
        raise SystemExit(f"unknown source: {args.source}")
    staging = _staging_dir(args.source)
    windows_file = staging / "windows.jsonl"
    if not windows_file.exists():
        raise SystemExit("run windows first")
    ihash = hashlib.sha256(windows_file.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "workorder_ready", input_hash=ihash):
        print("[skip] workorder up-to-date")
        return
    state_store.start_stage(db, args.source, "workorder_ready", input_hash=ihash)
    try:
        wo = workorder.build_workorder(_vault_dir(), source_id=args.source,
                                       domain=src_row["domain"], staging_dir=staging)
        path = workorder.write_workorder(staging, wo)
        ohash = hashlib.sha256(path.read_bytes()).hexdigest()
        state_store.record_work_order(db, args.source, path=str(path),
                                      registry_hash=wo["registry"]["hash"],
                                      write_scope_json=json.dumps(wo["write_scope"]))
        state_store.record_artifact(db, args.source, kind="workorder", path=str(path), sha256=ohash)
        state_store.complete_stage(db, args.source, "workorder_ready", output_hash=ohash)
        print(f"[OK] workorder → {path} (registry {wo['registry']['hash'][:12]})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "workorder_ready", error=str(e))
        raise


def cmd_show_window(args):
    """打印指定 processing window 的源文本（/ingest 逐窗读取用）。"""
    import json
    staging = _staging_dir(args.source)
    md = (staging / "source.md").read_text(encoding="utf-8")
    for line in (staging / "windows.jsonl").read_text(encoding="utf-8").splitlines():
        w = json.loads(line)
        if w["window_id"] == args.window:
            print(md[w["char_start"]:w["char_end"]])
            return
    raise SystemExit(f"window not found: {args.window}")
```

argparse 注册（P1 命令注册块之后）：

```python
    wop = subparsers.add_parser("workorder", help="生成 source 级 ingest work order")
    wop.add_argument("--source", required=True)
    swp = subparsers.add_parser("show-window", help="打印指定 window 的源文本")
    swp.add_argument("--source", required=True)
    swp.add_argument("--window", required=True)
```

并入 `commands` dict：`'workorder': cmd_workorder, 'show-window': cmd_show_window`。

- [ ] **Step 4:** Run `python -m pytest tests/test_p4_cli.py -q` → Expected PASS（2）。
- [ ] **Step 5:** Commit

```
git add scripts/pipeline.py tests/test_p4_cli.py
git commit -m "Add workorder + show-window CLI (windowed -> workorder_ready)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: CLI —— ingest 会话支撑（start/done、window 记账、resolve、守卫、快照）

**Files:** Modify `scripts/pipeline.py`、追加 `tests/test_p4_cli.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_p4_cli.py`：

```python
def test_ingest_start_done_lifecycle_with_lock(tmp_path):
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingesting", "running")
    # 第二个 source 在同 vault 被锁拒绝
    _prep_source(tmp_path, sid="note2")
    assert _run(["workorder", "--source", "note2"], tmp_path).returncode == 0
    r2 = _run(["ingest-start", "--source", "note2"], tmp_path)
    assert r2.returncode != 0 and "lock" in (r2.stdout + r2.stderr).lower()
    # window 记账 + 完成
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    r3 = _run(["ingest-done", "--source", "note"], tmp_path)
    assert r3.returncode == 0, r3.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingested", "proposed")
    # 锁已释放：note2 现在能开工
    assert _run(["ingest-start", "--source", "note2"], tmp_path).returncode == 0


def test_ingest_start_aborts_on_stale_registry(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    # 篡改磁盘 registry → stale
    reg = tmp_path / "wiki" / "concepts" / "_registry.yaml"
    reg.write_text(reg.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode != 0 and "stale" in (r.stdout + r.stderr).lower()


def test_resolve_concept_cli_creates_then_merges(tmp_path):
    _prep_source(tmp_path)
    r1 = _run(["resolve-concept", "--mention", "纳什均衡", "--domain", "misc",
               "--alias", "Nash Equilibrium", "--ref-source", "note", "--ref-sections", "1"],
              tmp_path)
    assert r1.returncode == 0 and "[created]" in r1.stdout
    r2 = _run(["resolve-concept", "--mention", "Nash Equilibrium", "--domain", "misc",
               "--ref-source", "note", "--ref-sections", "2"], tmp_path)
    assert r2.returncode == 0 and "[merged]" in r2.stdout
    pages = list((tmp_path / "wiki/domains/misc/concepts").glob("*.md"))
    assert len(pages) == 1  # 绝不重复建页


def test_check_write_allow_and_deny(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    ok = _run(["check-write", "--source", "note", "--path", "domains/misc/lessons/a.md"], tmp_path)
    assert ok.returncode == 0 and "ALLOW" in ok.stdout
    deny = _run(["check-write", "--source", "note", "--path", "index.generated.md"], tmp_path)
    assert deny.returncode != 0 and "DENY" in deny.stdout


def test_snapshot_page_records_manifest(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    page = tmp_path / "wiki" / "overview.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("OLD", encoding="utf-8")
    r = _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path)
    assert r.returncode == 0, r.stderr
    snaps = list((tmp_path / "pipeline-workspace/snapshots/note").rglob("manifest.json"))
    assert len(snaps) == 1
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p4_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`，加在 `cmd_show_window` 之后）**

```python
def cmd_ingest_start(args):
    """/ingest 开工：取 vault 锁 + stale registry 硬校验 + 推进到 ingesting。"""
    import state_store
    import locks
    import ingest_guards
    import os
    db = _vault_state_db()
    wo_row = state_store.get_work_order(db, args.source)
    if wo_row is None:
        raise SystemExit("run workorder first")
    if not locks.acquire(db, scope="vault", holder=args.source, pid=os.getpid()):
        row = locks.get(db, scope="vault")
        raise SystemExit(f"vault lock held by {row['holder']} since {row['started_at']}")
    try:
        if not ingest_guards.registry_fresh(_vault_dir(), wo_row["registry_hash"]):
            raise SystemExit("stale registry: disk _registry.yaml != work order hash; re-run workorder")
        ihash = wo_row["registry_hash"]
        state_store.start_stage(db, args.source, "ingest_waiting", input_hash=ihash)
        state_store.complete_stage(db, args.source, "ingest_waiting")
        state_store.start_stage(db, args.source, "ingesting", input_hash=ihash)
    except BaseException:
        locks.release(db, scope="vault", holder=args.source)
        raise
    print(f"[OK] ingesting '{args.source}' (vault lock held); per window: window-start → 写页 → window-done")


def cmd_ingest_done(args):
    """/ingest 收工：完成 ingesting + ingested（status=proposed），释放锁。"""
    import state_store
    import locks
    db = _vault_state_db()
    state_store.complete_stage(db, args.source, "ingesting")
    state_store.start_stage(db, args.source, "ingested")
    state_store.complete_stage(db, args.source, "ingested")
    locks.release(db, scope="vault", holder=args.source)
    print(f"[OK] '{args.source}' ingested (status=proposed); 收尾 lint/promote 见 P6")


def cmd_window_start(args):
    import state_store
    state_store.start_window(_vault_state_db(), args.source, args.window, input_hash=args.hash)
    print(f"[OK] window {args.window} running")


def cmd_window_done(args):
    import state_store
    state_store.finish_window(_vault_state_db(), args.source, args.window,
                              write_set_json=args.writes, proposal_set_json=args.proposals)
    print(f"[OK] window {args.window} finished")


def cmd_window_fail(args):
    import state_store
    state_store.fail_window(_vault_state_db(), args.source, args.window, error=args.error)
    print(f"[OK] window {args.window} failed: {args.error}")


def cmd_resolve_concept(args):
    """概念归一唯一入口（spec §6）：实时扫描概念页构建 registry，命中合并、未命中新建。不写派生文件。"""
    import concept_store
    vault = _vault_dir()
    metas = concept_store.scan_concept_pages(vault) if vault.exists() else []
    registry, errors, _w = concept_store.build_registry(metas)
    if errors:
        raise SystemExit("corrupt concept pages: " + "; ".join(errors))
    source_ref = None
    if args.ref_source:
        source_ref = {"source": args.ref_source,
                      "sections": (args.ref_sections or "").split(",") if args.ref_sections else []}
    cid, path, action = concept_store.resolve_or_create_concept(
        vault, mention=args.mention, domain=args.domain, registry=registry,
        aliases=args.alias or [], source_ref=source_ref)
    print(f"[{action}] {cid} -> {path}")


def cmd_check_write(args):
    """写前守卫：写入边界 + 覆盖保护三条件，DENY 时 exit 1（spec §9）。"""
    import state_store
    import ingest_guards
    import yaml as _yaml
    db = _vault_state_db()
    wo_row = state_store.get_work_order(db, args.source)
    if wo_row is None:
        raise SystemExit("run workorder first")
    wo = _yaml.safe_load(Path(wo_row["path"]).read_text(encoding="utf-8"))
    rel = args.path.replace("\\", "/")
    if not ingest_guards.in_write_scope(rel, wo["write_scope"]):
        print(f"DENY {rel}: outside write_scope")
        raise SystemExit(1)
    snap = list(wo.get("concept_pages_snapshot") or []) + list(wo.get("other_pages_snapshot") or [])
    ok, reason = ingest_guards.can_overwrite(_vault_dir(), rel, snap)
    if not ok:
        print(f"DENY {rel}: {reason}; 改走 Review-Queue proposal")
        raise SystemExit(1)
    print(f"ALLOW {rel}: {reason}")


def cmd_snapshot_page(args):
    """就地 merge 前的 pre-ingest 快照（spec §3.3，非 git）。"""
    import state_store
    import snapshots
    db = _vault_state_db()
    rid = state_store.latest_run_id(db, args.source, "ingesting")
    run_id = f"r{rid}" if rid else "manual"
    manifest = snapshots.take_snapshot(
        _workspace_root() / "pipeline-workspace/snapshots", source_id=args.source,
        run_id=run_id, files=[_vault_dir() / args.path], base_dir=_vault_dir())
    print(f"[OK] snapshot {args.path} -> {manifest}")
```

argparse 注册：

```python
    for name, help_text in [("ingest-start", "/ingest 开工：锁 + stale registry 校验 + ingesting"),
                            ("ingest-done", "/ingest 收工：ingested(proposed) + 释放锁")]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--source", required=True)
    wsp = subparsers.add_parser("window-start", help="记录一个 window 开始")
    wsp.add_argument("--source", required=True)
    wsp.add_argument("--window", required=True)
    wsp.add_argument("--hash", required=True)
    wdp = subparsers.add_parser("window-done", help="记录一个 window 完成")
    wdp.add_argument("--source", required=True)
    wdp.add_argument("--window", required=True)
    wdp.add_argument("--writes", default=None)
    wdp.add_argument("--proposals", default=None)
    wfp = subparsers.add_parser("window-fail", help="记录一个 window 失败")
    wfp.add_argument("--source", required=True)
    wfp.add_argument("--window", required=True)
    wfp.add_argument("--error", required=True)
    rcp = subparsers.add_parser("resolve-concept", help="概念归一唯一入口（命中合并/未命中新建）")
    rcp.add_argument("--mention", required=True)
    rcp.add_argument("--domain", required=True)
    rcp.add_argument("--alias", action="append", default=[])
    rcp.add_argument("--ref-source", default=None)
    rcp.add_argument("--ref-sections", default=None)
    cwp = subparsers.add_parser("check-write", help="写前守卫：边界 + 覆盖保护（DENY 则 exit 1）")
    cwp.add_argument("--source", required=True)
    cwp.add_argument("--path", required=True)
    spp = subparsers.add_parser("snapshot-page", help="就地 merge 前快照该页")
    spp.add_argument("--source", required=True)
    spp.add_argument("--path", required=True)
```

并入 `commands` dict：

```python
        'ingest-start': cmd_ingest_start,
        'ingest-done': cmd_ingest_done,
        'window-start': cmd_window_start,
        'window-done': cmd_window_done,
        'window-fail': cmd_window_fail,
        'resolve-concept': cmd_resolve_concept,
        'check-write': cmd_check_write,
        'snapshot-page': cmd_snapshot_page,
```

- [ ] **Step 4:** Run `python -m pytest tests/test_p4_cli.py -q` → Expected PASS（7）。
- [ ] **Step 5:** Commit

```
git add scripts/pipeline.py tests/test_p4_cli.py
git commit -m "Add ingest session CLI: lock+stale guard, window accounting, resolve/check-write/snapshot" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 命令层文档 —— `/ingest` 协议 + skill-runtime

**Files:** Create `.claude/commands/ingest.md`、`docs/skill-runtime/routing.md`、`docs/skill-runtime/schema.md`、`docs/skill-runtime/concept-resolution.md`、Test `tests/test_command_docs.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_command_docs.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ingest_command_doc_protocol_complete():
    text = (ROOT / ".claude/commands/ingest.md").read_text(encoding="utf-8")
    for must in ["workorder.yaml", "ingest-start", "show-window", "window-start", "window-done",
                 "resolve-concept", "check-write", "snapshot-page", "ingest-done",
                 "digest.md", "滚动摘要", "status: proposed", "write_scope"]:
        assert must in text, f"ingest.md 缺协议要素: {must}"
    # 派生文件禁写
    assert "_registry.yaml" in text and "aliases.md" in text and "index.generated.md" in text


def test_routing_doc_has_negative_examples():
    text = (ROOT / "docs/skill-runtime/routing.md").read_text(encoding="utf-8")
    assert "/ingest" in text and "负例" in text and "总结这篇文章" in text


def test_schema_and_resolution_docs():
    schema = (ROOT / "docs/skill-runtime/schema.md").read_text(encoding="utf-8")
    assert "templates/" in schema and "page_rules" in schema and "proposed" in schema
    res = (ROOT / "docs/skill-runtime/concept-resolution.md").read_text(encoding="utf-8")
    assert "resolve-concept" in res and "绝不新建" in res and "canonical_id" in res
```

- [ ] **Step 2:** Run `python -m pytest tests/test_command_docs.py -q` → Expected FAIL（文件不存在）。

- [ ] **Step 3: 创建 4 个文档**

Create `.claude/commands/ingest.md`:

````markdown
---
description: 把一个已预处理的 source 织入 wiki（唯一 LLM 步骤，人工触发；写 status: proposed）
argument-hint: <source_id>
---

# /ingest $1 — 整源织入 wiki

你是知识库的维护者。把 source `$1` 的内容**以概念/主题为主**织进 wiki（lessons 跟随源 TOC 为辅），
全程遵守 work order 事务协议。架构真值：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md` §9。
按需读取：`docs/skill-runtime/schema.md`（页面类型/frontmatter）、`docs/skill-runtime/concept-resolution.md`（概念归一）。

## 0. 开工（守卫由 CLI 硬执行）

1. 读 `pipeline-workspace/staging/$1/workorder.yaml`——它定义你的全部写入边界（`write_scope`）、
   registry hash、页面快照。**没有 work order 不开工**（先 `python scripts/pipeline.py workorder --source $1`）。
2. 运行 `python scripts/pipeline.py ingest-start --source $1`。
   它会取 vault 锁并校验 stale registry——若中止，按提示重新生成 work order，不要绕过。

## 1. 逐窗处理（rolling digest，长源外部记忆）

对 `staging/$1/windows.jsonl` 里的每个 window（按 window_id 升序）：

1. **续跑检查**：若该 window 已在前次会话完成且输入未变，跳过（`window_states` 可经
   `python scripts/pipeline.py status` 辅助判断；重复完成无害但浪费）。
2. `python scripts/pipeline.py window-start --source $1 --window <id> --hash <windows.jsonl 行的 sha 或 char 范围串>`
3. **先读滚动摘要**：读 `staging/$1/digest.md`（首窗不存在则跳过）——它是你跨窗的连续性记忆。
4. `python scripts/pipeline.py show-window --source $1 --window <id>` 读取本窗源文本；
   该窗涉及 `needs_vision` 页时，直接读 `staging/$1/assets/pXXXX.png` 图片，公式写成 KaTeX `$$…$$`。
5. **织入 wiki**（写页规则见下 §2）：概念走 resolve-concept；lesson 跟随源 TOC；topic/comparison/synthesis/overview 增量更新。
6. **更新滚动摘要**：把"本窗要点、引入/更新的概念、未决线索（悬而未解的引用、跨窗概念）"
   追加进 `staging/$1/digest.md`（保持 ≤ 约 50 行，过长就压缩旧条目）。下一窗靠它衔接。
7. `python scripts/pipeline.py window-done --source $1 --window <id> --writes '["<写过的页>"]'`
   （失败时改用 `window-fail --error "<原因>"`，下次续跑只重做未完成窗。）

## 2. 写页纪律（每一笔写入都适用）

- **写前守卫**：`python scripts/pipeline.py check-write --source $1 --path <vault 相对路径>`。
  DENY（越界 / 不在快照 / hash 已变 / `managed_by: human`）→ **不写该页**，把拟议改动写成
  `wiki/Review-Queue/<page>-proposal.md`（说明想改什么、为什么）。
- **覆盖已存在页前先快照**：`python scripts/pipeline.py snapshot-page --source $1 --path <相对路径>`。
- **所有新建/修改页 frontmatter 一律 `status: proposed` + `managed_by: pipeline`**；模板见 `templates/`
  （source/lesson/concept/topic/comparison/synthesis），必需小节不可缺。
- **概念只走 `resolve-concept`**（命中合并、绝不新建重复页）：
  `python scripts/pipeline.py resolve-concept --mention "<提及>" --domain <domain> [--alias "<英文名>"] --ref-source $1 --ref-sections "<5.2>"`
  然后编辑它返回的页面填充正文。别名只写概念页 frontmatter `aliases:`。
- **派生文件绝不手写**：`concepts/_registry.yaml`、`aliases.md`、`index.generated.md` 由收尾 CLI 重建。
- lesson 正文：干净散文、无裸 E-ID、核心论断挂脚注 `[^e1]`、公式 KaTeX、难页内嵌源页截图
  （自检原语：`scripts/page_rules.py`）。
- 追加 `log.md`：`## [YYYY-MM-DD] ingest | $1 | <created/updated 页列表>`（append-only）。

## 3. 收工

1. 全部 window 完成后：写/更新 `sources/$1.md`（来源摘要页，模板 `templates/source.md`）。
2. `python scripts/pipeline.py ingest-done --source $1` —— 状态进 `ingested/proposed`，锁释放。
3. 提示用户：运行收尾 lint/promote（P6）后内容才进入 published/index。
````

Create `docs/skill-runtime/routing.md`:

```markdown
# 命令路由（决策树 + 正/负样本）

架构真值：spec §3.4。所有写库命令 = 显式 slash command，用户敲了才跑；模型不得自行触发。

## 决策树

- 新外部来源（PDF/DOCX/PPTX/MD）要进知识库 → 预处理 CLI → `/ingest <source_id>`
- 问已有知识 → `/kb-query "<question>"`（只读，P8）
- query 后想留存 → `/kb-save <session_id>`（P8）
- 处理复核队列 → `/kb-review`（P8）
- 语义体检 → `/wiki-lint-semantic`（P8）

## 正例

- 「把这个 PDF / 这本书加入知识库」「ingest game-theory-whitepaper」→ `/ingest`
- 「知识库里关于信号博弈怎么说」→ `/kb-query`
- 「把刚才的对比存进 wiki」→ `/kb-save`

## 负例（绝不触发写库 / ingest）

- 「总结这篇文章」「解释这段话」「翻译一下」→ 普通回答，不进 wiki 流程
- 「帮我配 Obsidian」「修这个代码 bug」→ 与知识库无关
- 「这个 PDF 讲了什么？」（仅询问，未要求入库）→ 普通回答；除非用户明说"加入知识库"
```

Create `docs/skill-runtime/schema.md`:

```markdown
# 页面类型与 frontmatter 规则（指针文档，按命令最小加载）

- **6 类页面模板（写页格式契约）**：`templates/source.md` / `lesson.md` / `concept.md` /
  `topic.md` / `comparison.md` / `synthesis.md`。frontmatter 全带 Dataview 字段。
- **两阶段发布**：任何命令写出的页一律 `status: proposed`；只有收尾门禁 promote 成 `published`
  并纳入 `index.generated.md`。`managed_by: pipeline` 是覆盖保护的前提（human 页绝不覆盖）。
- **必需小节**：以 `scripts/page_rules.py::REQUIRED_SECTIONS` 为准（concept 六节、topic 三节、
  comparison 四节、synthesis 四节、source 六节）；lesson 无强制小节但须干净散文
  （无裸 E-ID、脚注 ref/def 配对——`find_bare_evidence_ids` / `missing_footnote_defs`）。
- **概念页 frontmatter 是唯一真值**（spec §6）：`canonical_id` / `canonical_name` / `aliases` /
  `scope` / `domain` / `source_refs` / `page_path`。派生文件（`_registry.yaml`/`aliases.md`/
  `index.generated.md`）由收尾 CLI 重建，任何命令不得手写。
```

Create `docs/skill-runtime/concept-resolution.md`:

```markdown
# 概念归一协议（resolve_or_create_concept）

spec §6：所有 concept 创建/更新的**唯一入口**。命中 canonical_id 则 merge 进既有页（**绝不新建**重复页）；
未命中按 `concept.<domain>.<slug>` 新建骨架页并登记。

## 用法（/ingest 与 /kb-save 共用）

```
python scripts/pipeline.py resolve-concept --mention "<正文中的提及>" --domain <domain> \
    [--alias "<别名>" ...] [--ref-source <source_id> --ref-sections "5.2,12.2"]
```

- 输出 `[merged] <canonical_id> -> <页路径>`：去编辑该页填充/补充正文（先 check-write + snapshot-page）。
- 输出 `[created] <canonical_id> -> <页路径>`：骨架页已建好（status: proposed），填充五个小节 + 自测。
- CLI 每次调用从概念页**实时扫描**重建内存 registry——会话内新建的概念立即可被后续 resolve 命中。
- 同名异义（econ 的 utility vs cs 的 utility）天然被 `concept.<domain>.<slug>` 命名空间隔离，不会合并。
- 跨域提升（domain → shared）必须经 Review-Queue 人工确认（P7 流程），命令不得自行提升。
- 别名只写概念页 frontmatter `aliases:`；`aliases.md` 是派生视图，不得手写。
```

- [ ] **Step 4:** Run `python -m pytest tests/test_command_docs.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit

```
git add .claude/commands/ingest.md docs/skill-runtime/routing.md docs/skill-runtime/schema.md docs/skill-runtime/concept-resolution.md tests/test_command_docs.py
git commit -m "Add /ingest command protocol + skill-runtime docs (rolling digest, guards, routing)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 全量回归 + P4 验收

**Files:** 无改动，纯验证

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected: 全 PASS（P0–P3/旧测试零回归）。
- [ ] **Step 2: P4 验收清单（对照 spec §9/§3.3/§3.4/§14）**
  - work order 含写入边界、registry hash、domain+shared 概念页快照（hash+managed_by）、其它目标页快照、on_failure。
  - `ingest-start`：取锁 + stale registry 硬中止（篡改 registry 实测 DENY）；第二个 `/ingest` 被锁拒绝。
  - window 级续跑：`should_run_window` 同 hash finished 跳过；failed/换 hash 重做。
  - `check-write`：越界 DENY、覆盖保护三条件 DENY（不在快照/hash 变/human）、新页 ALLOW。
  - `resolve-concept`：created→merged 生命周期，单页不重复；实时扫描保证会话内新鲜。
  - `snapshot-page`：manifest 落 `pipeline-workspace/snapshots/<source>/<run_id>/`。
  - `ingest-done`：状态 `ingested/proposed`（两阶段发布成立），锁释放。
  - `/ingest` 协议文档含 rolling digest（C1）、写页纪律、派生文件禁写；routing 有负例。
- [ ] **Step 3:** Run `git status --short` → Expected 干净（报告目录未跟踪可忽略）。

---

## Self-Review

- **Spec 覆盖**：§9 契约逐条（write_scope/registry hash 守卫/双快照/覆盖三条件/resolve 协议/幂等 window/两阶段/log 追加）→ T2–T7 ✓；§3.3 锁与 window 续跑 → T2/T6 ✓；§3.4 显式命令 + 最小上下文加载 + routing 负例 → T7 ✓；§3.1 rolling digest → T7 协议 §1 + 文档测试断言 ✓（C1 落实）。
- **占位符扫描**：协议文档中 `<id>`/`$1` 是命令参数约定；各任务含完整测试与实现。✓
- **类型一致性**：`start_window/finish_window/fail_window/should_run_window/window_states`、`record_work_order/get_work_order/latest_run_id`（T2 定义，T6 使用）；`in_write_scope/can_overwrite/registry_fresh`（T3 定义，T6 使用）；`build_workorder/write_workorder`（T4 定义，T5 使用）；CLI 参数名 `--ref-source/--ref-sections` 与 `args.ref_source/args.ref_sections`（argparse 自动转换）。✓
- **不越界**：不做 lint/promote/回滚组装（P6）、不做 query 命令（P8）、不删旧码；守卫硬路径在 CLI。✓
- **真实 API**：全部基于 P0–P3 已落地实物（含 `ingest_progress`/`work_orders` 两张 P0 已建空表）。✓

## 完成后

P4 完成 = `/ingest` 可用：人工触发、整源织入、proposed 输出、窗口级可续跑、全程守卫。下一步 **P5（综合层一等产物 + vault 脚手架）**，随后 P6 把 `page_rules` + 守卫组装成阻断性门禁完成两阶段发布闭环。
