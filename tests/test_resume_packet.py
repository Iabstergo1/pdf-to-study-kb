"""RESUME_PACKET v1 契约测试：`next --source <src> --resume-packet` 输出结构化恢复包。

定位：恢复体验加固（"系统喂给"替代"模型自己拼三份文档"），不是新安全边界——
末端 lint 仍是唯一安全保障。fail-closed：状态/产物矛盾时拒绝输出"看起来能继续"的残缺包。
本文件测纯函数层（packet 组装 + fail-closed 矩阵）；CLI wiring 在 test_resume_packet_cli.py。
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
resume_packet = _load("resume_packet")

MARK_START = "<!-- resume-critical:start -->"
MARK_END = "<!-- resume-critical:end -->"
SENTINEL = "RESUME-CRITICAL-SENTINEL"


# ---------------------------------------------------------------- fixtures

def _fake_repo(tmp_path):
    """两棵 skill 树的 write-pages.md（字节对等）+ resume-critical 标记块。"""
    repo = tmp_path / "repo"
    body = (f"# fake write-pages\n\n{MARK_START}\n"
            f"恢复关键契约（测试替身）{SENTINEL}\n{MARK_END}\n\n## 其他章节\n正文\n")
    for tree in (".claude", ".agents"):
        p = repo / tree / "skills/ingest/references/write-pages.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return repo


def _mk_env(tmp_path, *, stage="ingesting", digest="- done: 无\n- next: w0000",
            workorder_yaml=True, workorder_row=True, resume_heading="## RESUME"):
    """最小可恢复环境：3 窗 staging + 状态机推进到 ingesting/running + workorder + digest。"""
    staging = tmp_path / "pipeline-workspace/staging/src1"
    staging.mkdir(parents=True)
    md = ("A" * 39 + "\n") * 3  # 120 chars → 3 windows
    (staging / "source.md").write_text(md, encoding="utf-8")
    wins = [{"window_id": f"w{i:04d}", "heading_path": f"H{i}",
             "char_start": i * 40, "char_end": (i + 1) * 40} for i in range(3)]
    (staging / "windows.jsonl").write_text(
        "\n".join(json.dumps(w) for w in wins) + "\n", encoding="utf-8")
    if digest is not None:
        (staging / "digest.md").write_text(
            f"{resume_heading}\n{digest}\n\n## 路由表\n略\n", encoding="utf-8")
    if workorder_yaml:
        (staging / "workorder.yaml").write_text(
            "source_id: src1\n"
            "registry:\n  hash: regh\n"
            "write_scope:\n- domains/misc/**\n- concepts/**\n",
            encoding="utf-8")

    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "src1", domain="misc", fmt="md")
    pre = ["profiled", "converted", "windowed", "workorder_ready", "ingest_waiting"]
    if stage in pre:
        pre = pre[:pre.index(stage) + 1]
    for st in pre:
        state_store.start_stage(db, "src1", st, input_hash=st)
        state_store.complete_stage(db, "src1", st)
    if stage == "ingesting":
        state_store.start_stage(db, "src1", "ingesting", input_hash="ih")
    if workorder_row:
        state_store.record_work_order(
            db, "src1", path=str(staging / "workorder.yaml"), registry_hash="regh",
            write_scope_json=json.dumps(["domains/misc/**", "concepts/**"]))
    return {"db": db, "staging": staging, "repo": _fake_repo(tmp_path)}


def _build(env, source_id="src1"):
    return resume_packet.build_resume_packet(
        db_path=env["db"], staging_dir=env["staging"], repo_root=env["repo"],
        vault_dir=env["db"].parents[2] / "wiki",
        source_id=source_id)


def _win_hash(env, i):
    md = (env["staging"] / "source.md").read_text(encoding="utf-8")
    return hashlib.sha256(md[i * 40:(i + 1) * 40].encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- happy path

def test_packet_happy_path_has_all_sections_and_ledger_truth(tmp_path):
    env = _mk_env(tmp_path, digest="- done: w0000\n- next: w0001")
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json="[]")
    out = _build(env)
    assert "=== RESUME_PACKET v1 ===" in out
    for sec in ("[source]", "[windows]", "[digest-resume]", "[workorder]",
                "[writing-contract]", "[resume-critical]", "[next-commands]"):
        assert sec in out, f"缺分区 {sec}"
    assert "stage=ingesting" in out and "status=running" in out
    assert "next_window=w0001" in out
    assert f"sha256:{_win_hash(env, 1)}" in out           # 下一窗建议 hash（内容 sha 前 12 位）
    assert "- next: w0001" in out                          # digest RESUME 原样进包
    assert "- domains/misc/**" in out                      # workorder 写入边界
    assert "registry_hash=regh" in out
    assert SENTINEL in out                                 # resume-critical 原样抽取
    assert "window-start --source src1 --window w0001 --hash sha256:" in out
    assert "show-window --source src1 --window w0001" in out
    assert "window-done --source src1 --window w0001" in out


def test_running_window_is_the_resume_target(tmp_path):
    # 崩溃在窗中途：running 窗就是要重做的下一窗（start_window UPSERT 允许重启同窗）。
    env = _mk_env(tmp_path, digest="- done: w0000\n- next: w0001")
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json="[]")
    state_store.start_window(env["db"], "src1", "w0001", input_hash="h")
    out = _build(env)
    assert "running=w0001" in out
    assert "next_window=w0001" in out


def test_all_windows_finished_emits_phase_e_commands(tmp_path):
    env = _mk_env(tmp_path, digest="- done: w0000 w0001 w0002\n- 全部窗口完成，待阶段 E")
    for i in range(3):
        state_store.start_window(env["db"], "src1", f"w{i:04d}", input_hash="h")
        state_store.finish_window(env["db"], "src1", f"w{i:04d}", write_set_json="[]")
    out = _build(env)
    assert "next_window=none" in out
    assert "阶段 E" in out
    assert "ingest-done --source src1" in out
    assert "lint --source src1" in out


def test_done_digest_resumes_finishing_only_after_all_windows(tmp_path):
    env = _mk_env(tmp_path, resume_heading="## DONE", digest="全部窗口已完成，待综合整理")
    for i in range(3):
        state_store.start_window(env["db"], "src1", f"w{i:04d}", input_hash="h")
        state_store.finish_window(env["db"], "src1", f"w{i:04d}", write_set_json='["concepts/existing.md"]', proposal_set_json='["Review-Queue/existing.md"]')
    out = _build(env)
    assert "phase=finish" in out and "## DONE" in out
    assert "阶段 E" in out and "ingest-done --source src1" in out
    assert "window-start --source" not in out
    assert 'last_finished_window=w0002' in out
    assert 'last_finished_writes=["concepts/existing.md"]' in out
    assert 'last_finished_proposals=["Review-Queue/existing.md"]' in out


def test_finish_inventory_separates_current_round_writes_from_source_pages(tmp_path):
    env = _mk_env(tmp_path, resume_heading="## DONE", digest="所有窗口完成")
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json='["concepts/old.md"]')
    state_store.record_work_order(env["db"], "src1", path=str(env["staging"] / "workorder.yaml"),
                                  registry_hash="regh", write_scope_json=json.dumps(["domains/misc/**", "concepts/**"]))
    for i in (1, 2):
        state_store.start_window(env["db"], "src1", f"w{i:04d}", input_hash="h")
        state_store.finish_window(env["db"], "src1", f"w{i:04d}", write_set_json=json.dumps([f"concepts/current{i}.md"]))
    pages = {"concepts/unledgered.md": {"type": "concept", "status": "proposed", "source_refs": [{"source": "src1"}]},
             "concepts/published.md": {"type": "concept", "status": "published", "source_refs": [{"source": "src1"}]},
             "concepts/other.md": {"type": "concept", "status": "published", "source_refs": [{"source": "other"}]}}
    for rel, meta in pages.items():
        _load("mdpage").write_page(tmp_path / "wiki" / rel, meta, "Content.")
    fields = dict(line.split("=", 1) for line in _build(env).splitlines() if "=" in line)
    assert json.loads(fields["round_written_pages"]) == ["concepts/current1.md", "concepts/current2.md"]
    assert json.loads(fields["source_pages"]) == ["concepts/published.md", "concepts/unledgered.md"]


def test_next_window_info_does_not_require_an_updated_digest(tmp_path):
    env = _mk_env(tmp_path)
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json="[]")
    info = resume_packet.next_window_info(db_path=env["db"], staging_dir=env["staging"], source_id="src1")
    assert info["finished"] == 1 and info["total"] == 3
    assert info["next_window"]["window_id"] == "w0001"
    assert info["next_window"]["hash"] == f"sha256:{_win_hash(env, 1)}"
    assert "digest" not in info and len(json.dumps(info)) < 1000
    assert "RESUME 未提及" in _err(env)  # Metadata never bypasses recovery validation.


def test_window_progress_reads_ledger(tmp_path):
    env = _mk_env(tmp_path)
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json="[]")
    state_store.start_window(env["db"], "src1", "w0001", input_hash="h")
    rows = state_store.window_progress(env["db"], "src1")
    assert {r["window_id"]: r["status"] for r in rows} == {
        "w0000": "finished", "w0001": "running"}


# ---------------------------------------------------------------- fail-closed

def _err(env, source_id="src1"):
    with pytest.raises(resume_packet.ResumePacketError) as ei:
        _build(env, source_id)
    return str(ei.value)


def test_missing_digest_fail_closed(tmp_path):
    env = _mk_env(tmp_path, digest=None)
    assert "digest" in _err(env)


def _unstarted_env(tmp_path):
    env = _mk_env(tmp_path, digest=None)
    vault = tmp_path / "wiki"
    vault.mkdir()
    page = vault / "overview.md"
    page.write_text("existing overview", encoding="utf-8")
    wo_file = env["staging"] / "workorder.yaml"
    wo = yaml.safe_load(wo_file.read_text(encoding="utf-8"))
    wo.update(registry={"hash": ""}, write_scope=["overview.md", "concepts/**"],
              concept_pages_snapshot=[], other_pages_snapshot=[{
                  "path": "overview.md", "sha256": hashlib.sha256(page.read_bytes()).hexdigest()}])
    wo_file.write_text(yaml.safe_dump(wo), encoding="utf-8")
    state_store.record_work_order(env["db"], "src1", path=str(wo_file), registry_hash="",
                                  write_scope_json=json.dumps(wo["write_scope"]))
    (env["staging"] / "chapters.json").write_text("[]", encoding="utf-8")
    return env


def test_missing_digest_before_any_read_or_write_returns_initialization(tmp_path):
    env = _unstarted_env(tmp_path)
    out = _build(env)
    assert "phase=initialize" in out
    assert "next_window=w0000" in out
    assert "chapters.json" in out and "content-routing.md" in out
    assert "digest_status=not_created" in out
    assert not (env["staging"] / "digest.md").exists()


def test_initialization_relative_staging_uses_workspace_not_code_repo(tmp_path, monkeypatch):
    import shutil
    env = _unstarted_env(tmp_path)
    shallow = tmp_path / "staging/src1"
    shutil.copytree(env["staging"], shallow)
    monkeypatch.chdir(tmp_path)
    env["staging"] = Path("staging/src1")
    assert "phase=initialize" in _build(env)
    (tmp_path / "wiki/overview.md").write_text("changed", encoding="utf-8")
    assert "digest" in _err(env)


@pytest.mark.parametrize("activity", ["read", "running", "finished"])
def test_missing_digest_after_window_activity_still_fails(tmp_path, activity):
    env = _unstarted_env(tmp_path)
    if activity == "read":
        state_store.record_window_read(env["db"], "src1", "w0000")
    else:
        state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
        if activity == "finished":
            state_store.finish_window(env["db"], "src1", "w0000", write_set_json="[]")
    assert "digest" in _err(env)


@pytest.mark.parametrize("change", ["edited", "added", "deleted"])
def test_missing_digest_with_unaccounted_page_changes_still_fails(tmp_path, change):
    env = _unstarted_env(tmp_path)
    page = tmp_path / "wiki/overview.md"
    if change == "edited":
        page.write_text("changed", encoding="utf-8")
    elif change == "deleted":
        page.unlink()
    else:
        page = tmp_path / "wiki/concepts/new.md"
        page.parent.mkdir()
        page.write_text("unaccounted", encoding="utf-8")
    assert "digest" in _err(env)


def test_initialization_requires_chapter_map(tmp_path):
    env = _unstarted_env(tmp_path)
    (env["staging"] / "chapters.json").unlink()
    assert "chapters.json" in _err(env)


def test_digest_without_resume_block_fail_closed(tmp_path):
    env = _mk_env(tmp_path, resume_heading="## 备忘")
    assert "## RESUME" in _err(env)


def test_digest_done_heading_fail_closed_with_phase_e_hint(tmp_path):
    env = _mk_env(tmp_path, resume_heading="## DONE")
    msg = _err(env)
    assert "DONE" in msg and "未完窗口" in msg


def test_resume_pointing_at_unknown_window_fail_closed(tmp_path):
    env = _mk_env(tmp_path, digest="- next: w9999")
    assert "w9999" in _err(env)


def test_stale_resume_not_mentioning_next_fail_closed(tmp_path):
    # RESUME 只提已完成的 w0000（账本认为下一窗是 w0001）→ digest 过期，拒绝出包。
    env = _mk_env(tmp_path, digest="- next: w0000")
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h")
    state_store.record_window_read(env["db"], "src1", "w0000")
    state_store.finish_window(env["db"], "src1", "w0000", write_set_json='["concepts/choice.md"]')
    before = (env["staging"] / "digest.md").read_bytes()
    error = _err(env)
    assert "w0001" in error and "不能只替换窗口编号" in error
    evidence = json.loads(error.split("completed_windows=", 1)[1].split("\n", 1)[0])
    assert evidence == [{"window_id": "w0000", "round": 1, "read_in_current_round": True,
                         "input_hash": "h", "write_set_json": '["concepts/choice.md"]'}]
    assert (env["staging"] / "digest.md").read_bytes() == before
    assert "=== RESUME_PACKET" not in error


def test_missing_workorder_yaml_fail_closed(tmp_path):
    env = _mk_env(tmp_path, workorder_yaml=False)
    assert "workorder" in _err(env)


def test_missing_workorder_row_fail_closed(tmp_path):
    env = _mk_env(tmp_path, workorder_row=False)
    assert "workorder" in _err(env)


def test_not_ingesting_fail_closed(tmp_path):
    env = _mk_env(tmp_path, stage="workorder_ready")
    assert "ingesting" in _err(env)


def test_unknown_source_fail_closed(tmp_path):
    env = _mk_env(tmp_path)
    assert "unknown" in _err(env, source_id="nope")


def test_running_ledger_window_missing_from_windows_jsonl_fail_closed(tmp_path):
    env = _mk_env(tmp_path)
    state_store.start_window(env["db"], "src1", "w9999", input_hash="h")
    assert "w9999" in _err(env)


def test_multiple_running_windows_fail_closed(tmp_path):
    env = _mk_env(tmp_path, digest="- next: w0000 w0001")
    state_store.start_window(env["db"], "src1", "w0000", input_hash="h0")
    state_store.start_window(env["db"], "src1", "w0001", input_hash="h1")
    assert "多个 running" in _err(env)


def test_workorder_yaml_and_db_must_agree(tmp_path):
    env = _mk_env(tmp_path)
    (env["staging"] / "workorder.yaml").write_text(
        "source_id: src1\nregistry:\n  hash: regh\nwrite_scope:\n- other/**\n",
        encoding="utf-8")
    assert "write_scope" in _err(env)


def test_contract_trees_must_be_byte_equivalent(tmp_path):
    env = _mk_env(tmp_path)
    p = env["repo"] / ".agents/skills/ingest/references/write-pages.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    assert "字节不对等" in _err(env)


def test_missing_resume_critical_markers_fail_closed(tmp_path):
    env = _mk_env(tmp_path)
    for tree in (".claude", ".agents"):
        p = env["repo"] / tree / "skills/ingest/references/write-pages.md"
        p.write_text("# no markers here\n", encoding="utf-8")
    assert "resume-critical" in _err(env)


# ---------------------------------------------------------------- 真仓守卫

def test_real_repo_write_pages_carries_resume_critical_block():
    # packet 运行时依赖真仓 write-pages.md 的标记块存在且双树一致——机器守卫，防漂移。
    texts = {}
    for tree in (".claude", ".agents"):
        p = ROOT / tree / "skills/ingest/references/write-pages.md"
        t = p.read_text(encoding="utf-8")
        assert MARK_START in t and MARK_END in t, f"{p} 缺 resume-critical 标记"
        inner = t.split(MARK_START, 1)[1].split(MARK_END, 1)[0].strip()
        assert inner, f"{p} resume-critical 块为空"
        texts[tree] = t
    assert texts[".claude"] == texts[".agents"]
