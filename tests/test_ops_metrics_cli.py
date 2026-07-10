"""运营层 CLI（Phase 1）：proposals-resolve 退场 + ingest-stats 代理指标。

review_proposals 曾是只增不减的账本（add 只 INSERT status='open'，无 UPDATE 路径），
skill-mine 扫全量导致 backlog 单调累积——已修复条目永不退场。proposals-resolve 补上退场：
默认 dry-run；--id 精确 / --signature 批量（批量落库须显式 --all-matching）。
ingest-stats 是只读代理指标（不伪造 token/费用；窗口耗时=最后一次尝试）。
隔离同其余 CLI 测试：STUDY_KB_ROOT 指向 tmp，状态库/产物绝不写真实仓库。
"""
import os
import sys
import json
import subprocess
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

_spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_store)


def _run(cwd, *args):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：状态库/产物都落 tmp
    return subprocess.run([sys.executable, str(PIPELINE), *args],
                          cwd=cwd, env=env, capture_output=True, text=True)


def _seed_db(cwd):
    db = cwd / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="algorithms", fmt="md")
    return db


def _statuses(db, kind=None):
    con = state_store.connect(db)
    try:
        sql = "SELECT id,source_id,kind,status FROM review_proposals"
        rows = con.execute(sql + (" WHERE kind=?" if kind else ""),
                           ((kind,) if kind else ())).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---- proposals-resolve ----

def test_proposals_resolve_dry_run_changes_nothing(tmp_path):
    db = _seed_db(tmp_path)
    for i in range(2):
        state_store.add_review_proposal(db, "s1", target_path=f"a{i}.md",
                                        kind="broken-link", reason="dangling wikilink")

    r = _run(tmp_path, "proposals-resolve", "--signature", "broken-link", "--all-matching")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout
    # 默认 dry-run：只列匹配行，一行都不改。
    assert all(row["status"] == "open" for row in _statuses(db))


def test_proposals_resolve_by_id_marks_only_selected_open_rows(tmp_path):
    db = _seed_db(tmp_path)
    ids = [state_store.add_review_proposal(db, "s1", target_path=f"a{i}.md",
                                           kind="broken-link", reason="dangling wikilink")
           for i in range(3)]

    r = _run(tmp_path, "proposals-resolve", "--id", str(ids[0]), "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    by_id = {row["id"]: row["status"] for row in _statuses(db)}
    assert by_id[ids[0]] == "resolved"
    assert by_id[ids[1]] == "open" and by_id[ids[2]] == "open"

    # 幂等：同 --id 再跑，匹配 0 行（已非 open），仍 exit 0。
    r2 = _run(tmp_path, "proposals-resolve", "--id", str(ids[0]), "--apply")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "0" in r2.stdout


def test_proposals_resolve_batch_apply_requires_all_matching(tmp_path):
    db = _seed_db(tmp_path)
    state_store.add_review_proposal(db, "s1", target_path="a.md",
                                    kind="broken-link", reason="dangling wikilink")

    # 批量落库缺 --all-matching → 拒绝（防把同类但未修复的 proposal 一起退场）。
    r = _run(tmp_path, "proposals-resolve", "--signature", "broken-link", "--apply")
    assert r.returncode != 0
    assert "all-matching" in (r.stdout + r.stderr)
    assert all(row["status"] == "open" for row in _statuses(db))


def test_proposals_resolve_batch_scoped_by_source(tmp_path):
    db = _seed_db(tmp_path)
    state_store.register_source(db, "s2", domain="economics", fmt="pdf")
    state_store.add_review_proposal(db, "s1", target_path="a.md",
                                    kind="broken-link", reason="dangling wikilink")
    state_store.add_review_proposal(db, "s2", target_path="b.md",
                                    kind="broken-link", reason="dangling wikilink")

    r = _run(tmp_path, "proposals-resolve", "--signature", "broken-link",
             "--source", "s1", "--all-matching", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    by_src = {row["source_id"]: row["status"] for row in _statuses(db)}
    assert by_src["s1"] == "resolved"
    assert by_src["s2"] == "open"


def test_proposals_resolve_id_and_signature_mutually_exclusive(tmp_path):
    _seed_db(tmp_path)
    r = _run(tmp_path, "proposals-resolve", "--id", "1", "--signature", "broken-link")
    assert r.returncode != 0
    r2 = _run(tmp_path, "proposals-resolve")
    assert r2.returncode != 0


def test_backlog_excludes_resolved_and_carries_last_seen(tmp_path):
    db = _seed_db(tmp_path)
    for i in range(2):
        state_store.add_review_proposal(db, "s1", target_path=f"a{i}.md",
                                        kind="broken-link", reason="dangling wikilink")
    state_store.add_review_proposal(db, "s1", target_path="q.md",
                                    kind="missing-section", reason="missing evidence")

    # resolve 整簇 broken-link → 该簇应从 backlog 退场（--apply 后自动刷新 backlog.yaml）。
    r = _run(tmp_path, "proposals-resolve", "--signature", "broken-link",
             "--all-matching", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr

    backlog_file = tmp_path / "pipeline-workspace" / "skill-evolution" / "backlog.yaml"
    assert backlog_file.exists(), "--apply 应顺手刷新 backlog.yaml"
    data = yaml.safe_load(backlog_file.read_text(encoding="utf-8"))
    sigs = {e["signature"]: e for e in data["backlog"]}
    assert "broken-link" not in sigs, "已 resolve 的簇必须退场"
    assert sigs["missing-section"]["count"] == 1

    # last_seen = 该簇 open 行的 max created_at（供人判断信号新旧）。
    open_rows = [row for row in _statuses(db, kind="missing-section") if row["status"] == "open"]
    assert open_rows
    con = state_store.connect(db)
    try:
        max_created = con.execute(
            "SELECT MAX(created_at) AS m FROM review_proposals"
            " WHERE kind='missing-section' AND status='open'").fetchone()["m"]
    finally:
        con.close()
    assert sigs["missing-section"]["last_seen"] == max_created


# ---- ingest-stats ----

def _iso(h, m=0, s=0):
    return f"2026-07-09T{h:02d}:{m:02d}:{s:02d}+00:00"


def _seed_stats_db(cwd):
    """直接种表构造已知场景：profile 重跑一次、lint 失败一次、
    3 个窗（2 finished 带交叠 write_set、1 failed）、violations 2 open + 1 resolved。"""
    db = _seed_db(cwd)
    con = state_store.connect(db)
    try:
        runs = [
            ("s1", "profiled", "done", _iso(0), _iso(0, 1), None, None, None),
            ("s1", "profiled", "done", _iso(1), _iso(1, 2), None, None, None),   # 重跑
            ("s1", "lint", "failed", _iso(2), _iso(2, 1), None, None, "3 lint violations"),
            ("s1", "lint", "done", _iso(3), _iso(3, 5), None, None, None),
        ]
        con.executemany(
            "INSERT INTO source_stage_runs(source_id,stage,status,started_at,finished_at,"
            " input_hash,output_hash,error) VALUES (?,?,?,?,?,?,?,?)", runs)
        wins = [
            ("s1", "w1", "h1", _iso(4), _iso(4, 2), "finished",
             json.dumps(["domains/d/concepts/a.md", "domains/d/topics/t.md"]), None, None),
            ("s1", "w2", "h2", _iso(5), _iso(5, 3), "finished",
             json.dumps(["domains/d/concepts/a.md", "domains/d/concepts/b.md"]), None, None),  # a.md 交叠
            ("s1", "w3", "h3", _iso(6), _iso(6, 1), "failed", None, None, "boom"),
        ]
        con.executemany(
            "INSERT INTO ingest_progress(source_id,window_id,input_hash,started_at,finished_at,"
            " status,write_set_json,proposal_set_json,error) VALUES (?,?,?,?,?,?,?,?,?)", wins)
        con.commit()
    finally:
        con.close()
    state_store.add_review_proposal(db, "s1", target_path="x.md",
                                    kind="broken-link", reason="dangling")
    state_store.add_review_proposal(db, "s1", target_path="y.md",
                                    kind="broken-link", reason="dangling")
    state_store.resolve_review_proposals(db, kind="broken-link", apply=False)  # dry-run 不改
    ids = [row["id"] for row in _statuses(db, kind="broken-link")]
    state_store.resolve_review_proposals(db, ids=ids[:1], apply=True)
    return db


def test_ingest_stats_json_proxy_metrics(tmp_path):
    _seed_stats_db(tmp_path)

    r = _run(tmp_path, "ingest-stats", "--source", "s1", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    stats = json.loads(r.stdout)

    w = stats["windows"]
    assert (w["total"], w["finished"], w["failed"]) == (3, 2, 1)
    assert stats["pages_estimate"] == 3            # a.md 跨窗交叠只算一次
    assert stats["lint_failures"] == 1             # ≈ 回滚次数
    assert stats["stages"]["profiled"]["runs"] == 2   # 重跑可见
    assert stats["stages"]["lint"]["failed"] == 1
    bk = stats["proposals_by_kind"]["broken-link"]
    assert (bk["total"], bk["open"], bk["resolved"]) == (2, 1, 1)
    # 诚实口径：不伪造 token/费用字段。
    assert "token" not in json.dumps(stats).lower()
    assert "cost" not in json.dumps(stats).lower()


def test_ingest_stats_human_readable(tmp_path):
    _seed_stats_db(tmp_path)
    r = _run(tmp_path, "ingest-stats", "--source", "s1")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "s1" in out and "windows" in out
    assert "pages_estimate" in out or "页数" in out


def test_ingest_stats_unknown_source_exits_nonzero(tmp_path):
    _seed_db(tmp_path)
    r = _run(tmp_path, "ingest-stats", "--source", "nope")
    assert r.returncode != 0
    assert "unknown source" in (r.stdout + r.stderr)


def test_ingest_stats_counts_empty_writes_unread(tmp_path):
    """静默遗漏信号：finished 且空写集、又从未经 show-window 读过的窗计数。"""
    db = _seed_stats_db(tmp_path)
    con = state_store.connect(db)
    try:
        wins = [
            ("s1", "w4", "h4", _iso(7), _iso(7, 1), "finished", json.dumps([]), None, None),
            ("s1", "w5", "h5", _iso(8), _iso(8, 1), "finished", json.dumps([]), None, None),
        ]
        con.executemany(
            "INSERT INTO ingest_progress(source_id,window_id,input_hash,started_at,finished_at,"
            " status,write_set_json,proposal_set_json,error) VALUES (?,?,?,?,?,?,?,?,?)", wins)
        con.commit()
    finally:
        con.close()
    state_store.record_window_read(db, "s1", "w4")  # w4 读过再跳；w5 未读即跳

    r = _run(tmp_path, "ingest-stats", "--source", "s1", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    stats = json.loads(r.stdout)
    assert stats["windows"]["empty_writes_unread"] == 1


def test_ingest_stats_device_usage_from_vault_pages(tmp_path):
    """装置使用统计：扫本源 write_set 页计数命题/推导折叠/自测题（全书归零=偏好未执行信号）。"""
    _seed_stats_db(tmp_path)
    page = tmp_path / "wiki" / "domains" / "d" / "concepts" / "a.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntype: concept\nstatus: published\n---\n\n"
        "**命题（先发优势）**：领导者利润严格更高。\n\n"
        "> [!abstract]- 完整推导\n> 第一步……\n\n"
        "> [!question] 自测\n> 为什么？\n> > [!success]- 参考答案\n> > 因为。\n",
        encoding="utf-8")

    r = _run(tmp_path, "ingest-stats", "--source", "s1", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    u = json.loads(r.stdout)["device_usage"]
    assert (u["propositions"], u["derivation_folds"], u["questions"]) == (1, 1, 1)
    assert u["pages_scanned"] == 1  # write_set 里其余页不在盘上，只扫到 a.md


def test_ingest_stats_is_read_only(tmp_path):
    db = _seed_stats_db(tmp_path)
    before = _statuses(db)
    r = _run(tmp_path, "ingest-stats", "--source", "s1", "--json")
    assert r.returncode == 0
    assert _statuses(db) == before
