"""ingest 编排 CLI（P1.1 场景化）：锁生命周期、stale 恢复、registry 校验、mutation guards。

五个场景（合并自 12 条，断言逐条迁移保留）：核心生命周期 / stale-lock 恢复 /
stale-registry 两态 / 丢锁 mutation guards / concept create→merge 去重（独立保留）。
"""
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
locks = _load("locks")


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


def _stale(db):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    locks.force_set_heartbeat(db, scope="vault", iso=old)


def test_ingest_core_lifecycle_scenario(tmp_path):
    # 场景①核心生命周期（合并自 workorder / show-window / start-done+锁互斥 /
    # check-write / snapshot 五条，断言全保留）：一次预处理环境走完整入库编排。
    db = _prep_source(tmp_path)

    # workorder：推进状态 + 落 yaml + 状态库行 + 幂等 [skip]。
    overview = tmp_path / "wiki" / "overview.md"
    overview.parent.mkdir(parents=True, exist_ok=True)
    overview.write_text("---\ntype: overview\nmanaged_by: pipeline\nstatus: published\n---\nOLD\n",
                        encoding="utf-8")
    r = _run(["workorder", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "pipeline-workspace/staging/note/workorder.yaml").exists()
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")
    assert state_store.get_work_order(db, "note") is not None
    r2 = _run(["workorder", "--source", "note"], tmp_path)
    assert "[skip]" in r2.stdout

    # show-window：读窗打印源文本。
    ws = (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").read_text(encoding="utf-8")
    wid = json.loads(ws.splitlines()[0])["window_id"]
    r = _run(["show-window", "--source", "note", "--window", wid], tmp_path)
    assert r.returncode == 0 and "aaa" in r.stdout

    # ingest-start：进入 ingesting/running；第二个 source 在同 vault 被锁拒绝。
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingesting", "running")
    _prep_source(tmp_path, sid="note2")
    assert _run(["workorder", "--source", "note2"], tmp_path).returncode == 0
    r2 = _run(["ingest-start", "--source", "note2"], tmp_path)
    assert r2.returncode != 0 and "lock" in (r2.stdout + r2.stderr).lower()

    # 持锁写作期：check-write ALLOW/DENY + snapshot-page 落 manifest。
    ok = _run(["check-write", "--source", "note", "--path", "domains/misc/lessons/a.md"], tmp_path)
    assert ok.returncode == 0 and "ALLOW" in ok.stdout
    deny = _run(["check-write", "--source", "note", "--path", "index.generated.md"], tmp_path)
    assert deny.returncode != 0 and "DENY" in deny.stdout
    r = _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path)
    assert r.returncode == 0, r.stderr
    snaps = list((tmp_path / "pipeline-workspace/snapshots/note").rglob("manifest.json"))
    assert len(snaps) == 1

    # window 记账 + ingest-done：状态到 ingested/proposed，锁释放后 note2 能开工。
    # （2026-07-17 规格：window-done 前须本轮 show-window——空写跳窗也要读）
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    r3 = _run(["ingest-done", "--source", "note"], tmp_path)
    assert r3.returncode == 0, r3.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingested", "proposed")
    assert _run(["ingest-start", "--source", "note2"], tmp_path).returncode == 0


def test_window_done_rejects_existing_page_edited_before_check_write(tmp_path):
    """写后再补 check-write/snapshot 不得洗白；恢复基线后按正确顺序可通过。"""
    db = _prep_source(tmp_path)
    target = tmp_path / "wiki" / "overview.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "---\ntype: overview\nmanaged_by: pipeline\nstatus: published\n---\nORIGINAL\n"
    target.write_text(original, encoding="utf-8")
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0

    target.write_text(original.replace("ORIGINAL", "EDITED-BEFORE-GUARD"), encoding="utf-8")
    late = _run(["check-write", "--source", "note", "--path", "overview.md"], tmp_path)
    assert late.returncode != 0 and "disk hash changed" in (late.stdout + late.stderr)
    denied = _run(["window-done", "--source", "note", "--window", "w0000",
                   "--writes", '["overview.md"]'], tmp_path)
    assert denied.returncode != 0
    assert "prewrite-snapshot" in (denied.stdout + denied.stderr)
    assert state_store.window_states(db, "note")[0]["status"] == "running"

    target.write_text(original, encoding="utf-8")
    allowed = _run(["check-write", "--source", "note", "--path", "overview.md"], tmp_path)
    assert allowed.returncode == 0 and "snapshot" in allowed.stdout
    target.write_text(original.replace("ORIGINAL", "EDITED-AFTER-GUARD"), encoding="utf-8")
    done = _run(["window-done", "--source", "note", "--window", "w0000",
                 "--writes", '["overview.md"]'], tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr


def test_resolve_concept_snapshots_before_merging_existing_page(tmp_path):
    """resolve-concept 自己会改 frontmatter，不能把快照责任推给它之后的调用方。"""
    db = _prep_source(tmp_path)
    concept = tmp_path / "wiki" / "domains" / "misc" / "concepts" / "既有概念.md"
    concept.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "---\ntype: concept\ncanonical_id: concept.misc.existing\ncanonical_name: 既有概念\n"
        "aliases: []\nscope: domain\ndomain: misc\nsource_refs: []\n"
        "page_path: domains/misc/concepts/既有概念.md\nmanaged_by: pipeline\nstatus: published\n---\n"
        "这是一张已经发布的既有概念页，正文在本轮 merge 之前必须保持可回滚。\n")
    concept.write_text(original, encoding="utf-8")
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0

    r = _run(["resolve-concept", "--mention", "既有概念", "--domain", "misc",
              "--ref-source", "note", "--ref-sections", "A"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert concept.read_text(encoding="utf-8") != original
    manifests = list((tmp_path / "pipeline-workspace/snapshots/note").rglob("manifest.json"))
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert [e["rel_path"] for e in data["entries"]] == [
        "domains/misc/concepts/既有概念.md"]

    snapshots = _load("snapshots")
    snapshots.rollback(manifests[0])
    assert concept.read_text(encoding="utf-8") == original


def test_stale_lock_resume_and_recovery_scenario(tmp_path):
    # 场景② stale-lock 恢复（合并自 same-source resume / status+heartbeat+unlock 两条，
    # 断言全保留）：status 可见 → 活锁不可破 → 做旧后同源 resume → window 记账刷
    # heartbeat → 再做旧后 next 给清理建议、unlock 受控破锁。
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0

    # status 显示锁持有者；活锁不可破。
    r = _run(["status"], tmp_path)
    assert "lock" in r.stdout.lower() and "note" in r.stdout
    r = _run(["unlock"], tmp_path)
    assert r.returncode != 0
    assert locks.get(db, scope="vault") is not None

    # 做旧模拟崩溃：同源 ingest-start 直接 resume（不必先 unlock），锁在、状态仍 running。
    _stale(db)
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resumed" in r.stdout
    assert locks.get(db, scope="vault") is not None
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("ingesting", "running")

    # window 记账刷新 heartbeat：做旧后 window-start 应让锁不再 stale。
    _stale(db)
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert not locks.is_stale(db, scope="vault", ttl_seconds=1800)

    # 再做旧模拟崩溃残留：next 给清理建议，unlock 受控破锁。
    _stale(db)
    r = _run(["next"], tmp_path)
    assert "unlock" in r.stdout
    r = _run(["unlock"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert locks.get(db, scope="vault") is None


def test_stale_registry_aborts_before_start_and_keeps_lock_after(tmp_path):
    # 场景③ stale-registry 两态（合并自 启动前 abort / 持锁后 keep-lock 两条，断言全保留）：
    # 启动前篡改 → 拒绝开工；恢复后开工成功；持锁中再篡改 → 重入拒绝且不误放锁。
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    reg = tmp_path / "wiki" / "concepts" / "_registry.yaml"
    original = reg.read_bytes()  # 字节级：恢复须与 workorder 记 hash 时逐字节一致（避免文本模式换行翻译）

    # 启动前篡改磁盘 registry → stale，拒绝 ingest-start。
    reg.write_bytes(original + b"\n# tampered\n")
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode != 0 and "stale" in (r.stdout + r.stderr).lower()

    # 恢复原文（字节一致）→ 正常开工持锁。
    reg.write_bytes(original)
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0

    # 持锁后再篡改 → 重入拒绝，且同源锁不被误放。
    reg.write_bytes(reg.read_bytes() + b"\n# tampered\n")
    r = _run(["ingest-start", "--source", "note"], tmp_path)
    assert r.returncode != 0 and "stale" in (r.stdout + r.stderr).lower()
    assert locks.get(db, scope="vault") is not None


def test_lost_lock_rejects_all_mutations(tmp_path):
    # 场景④ 丢锁 mutation guards（合并自 window 命令 / resolve-concept 两条，断言全保留）：
    # 无锁僵尸 agent 的一切写路径（window-start/done、check-write、ingest 期 resolve-concept）
    # 都被拒；非 ingest 的 resolve-concept（无 ref-source，kb-save 式）不受锁约束。
    db = _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    # 持锁中 resolve-concept 正常。
    assert _run(["resolve-concept", "--mention", "甲", "--domain", "misc",
                 "--ref-source", "note", "--ref-sections", "1"], tmp_path).returncode == 0

    assert _run(["unlock", "--ttl", "0"], tmp_path).returncode == 0
    assert locks.get(db, scope="vault") is None

    r_done = _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path)
    assert r_done.returncode != 0
    assert "vault lock" in (r_done.stdout + r_done.stderr).lower()
    r_start = _run(["window-start", "--source", "note", "--window", "w0001", "--hash", "h2"],
                   tmp_path)
    assert r_start.returncode != 0
    assert "vault lock" in (r_start.stdout + r_start.stderr).lower()
    r_check = _run(["check-write", "--source", "note", "--path", "domains/misc/lessons/a.md"],
                   tmp_path)
    assert r_check.returncode != 0
    assert "vault lock" in (r_check.stdout + r_check.stderr).lower()
    # 带 ref-source 且该 source 仍处 ingesting/running → 拒（守住约束 3「概念去重」）。
    r = _run(["resolve-concept", "--mention", "丙", "--domain", "misc",
              "--ref-source", "note"], tmp_path)
    assert r.returncode != 0
    assert "vault lock" in (r.stdout + r.stderr).lower()
    # 无 ref-source（kb-save 式）不受锁约束。
    assert _run(["resolve-concept", "--mention", "乙", "--domain", "misc"],
                tmp_path).returncode == 0


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


def test_window_done_rejects_ledger_disk_drift(tmp_path):
    # 台账↔磁盘对账：--writes 记的页必须真在磁盘上。resolve-concept 把 mention 归一成 slug
    # （`Buffer Pool` → `buffer-pool.md`），写作方却按自以为的名字记账 → 台账与产出漂移；
    # （引入本对账时 concept 页尚不受 unaccounted-write 约束；2026-07-18 起记账义务已覆盖
    # 全部非 source 页，本对账仍是最早的 fail-fast 拦截点。）
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes", '["domains/misc/concepts/Buffer Pool.md"]'], tmp_path)
    assert r.returncode != 0
    assert "Buffer Pool.md" in (r.stdout + r.stderr)


def test_window_done_accepts_writes_that_exist_on_disk(tmp_path):
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    page = tmp_path / "wiki" / "domains" / "misc" / "concepts" / "buffer-pool.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("x", encoding="utf-8")
    r = _run(["window-done", "--source", "note", "--window", "w0000",
              "--writes", '["domains/misc/concepts/buffer-pool.md"]'], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_window_done_requires_this_round_read(tmp_path):
    # 每次 window-done（含空写集跳窗）都要求该窗在本轮读过——window-skip 纪律从文档约束升为 CLI 拦截。
    # 批量通读合法：只看"本轮内读过"，不限制与 window-start 的先后。
    _prep_source(tmp_path)
    assert _run(["workorder", "--source", "note"], tmp_path).returncode == 0
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    r = _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path)
    assert r.returncode != 0
    assert "show-window" in (r.stdout + r.stderr)
    # 读窗后即可收窗（先读后 start 的批量通读顺序同样合法，由 happy-path 各测试覆盖）
    assert _run(["show-window", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000"], tmp_path).returncode == 0
