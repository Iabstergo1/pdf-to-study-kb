#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI（新架构：确定性预处理 + 收尾门禁 + 状态跟踪，零 LLM）

预处理：add-source → profile → source-convert → windows → workorder
/ingest 会话支撑：ingest-start/done、window-start/done/fail、show-window、
                resolve-concept、check-write、snapshot-page
收尾：lint（promote 或 回滚+Review-Queue）、rebuild-registry
vault 与维护：init-vault、status、next、fail、promotion-candidates、
              promote-concept、check-session

用法：python scripts/pipeline.py <command> [options]
架构真值：docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md
"""

import argparse
import sys
from pathlib import Path

LOCK_TTL_SECONDS = 1800  # vault 锁 stale 判定：heartbeat 超过此秒数视为崩溃残留（spec §3.3）


def _workspace_root() -> Path:
    """状态库/staging 锚点：默认 repo 根；STUDY_KB_ROOT 覆盖（测试隔离/多库场景）。"""
    import os
    env = os.environ.get("STUDY_KB_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[1]


def _vault_state_db() -> Path:
    """vault 级单库（不接 --book）；路径 pipeline-workspace/state/study-kb.sqlite。"""
    return _workspace_root() / "pipeline-workspace/state/study-kb.sqlite"


def _staging_dir(source_id: str) -> Path:
    return _workspace_root() / "pipeline-workspace/staging" / source_id


def cmd_add_source(args):
    """注册一个来源到状态库（记 raw 路径为 artifact）。"""
    import state_store
    import hashlib
    db = _vault_state_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    state_store.init_db(db)
    state_store.register_source(db, args.source, domain=args.domain, fmt=args.fmt)
    raw = Path(args.path)
    sha = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else ""
    state_store.record_artifact(db, args.source, kind="raw_source", path=str(raw), sha256=sha)
    print(f"[OK] registered source '{args.source}' (domain={args.domain}, fmt={args.fmt})")


def _raw_path(db, state_store, source_id: str) -> Path:
    for a in state_store.list_artifacts(db, source_id):
        if a["kind"] == "raw_source":
            return Path(a["path"])
    raise SystemExit(f"no raw_source artifact for {source_id}; run add-source first")


def cmd_profile(args):
    """逐页 profile（产出 staging/<source>/pages.jsonl，needs_vision 标记）。"""
    import state_store
    import source_profile
    import json
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "profiled", input_hash=ihash):
        print("[skip] profiled up-to-date")
        return
    state_store.start_stage(db, args.source, "profiled", input_hash=ihash)
    try:
        pages = source_profile.profile_source(raw, fmt=src_row["format"])
        out = _staging_dir(args.source)
        out.mkdir(parents=True, exist_ok=True)
        pages_path = out / "pages.jsonl"
        pages_path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pages),
                              encoding="utf-8")
        ohash = hashlib.sha256(pages_path.read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="pages", path=str(pages_path), sha256=ohash)
        state_store.complete_stage(db, args.source, "profiled", output_hash=ohash)
        n_vision = sum(1 for p in pages if p.get("needs_vision"))
        print(f"[OK] profiled → {len(pages)} pages ({n_vision} needs_vision)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "profiled", error=str(e))
        raise


def cmd_source_convert(args):
    """source-convert：raw → staging/<source>/source.md + 难页 PNG。"""
    import state_store
    import source_convert
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "converted", input_hash=ihash):
        print("[skip] converted up-to-date")
        return
    state_store.start_stage(db, args.source, "converted", input_hash=ihash)
    try:
        out = _staging_dir(args.source)
        res = source_convert.convert(raw, out_dir=out, fmt=src_row["format"])
        # pages.jsonl 已由 profile 阶段产出；convert 内部用同一批纯函数复算 needs_vision，结果一致
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.complete_stage(db, args.source, "converted", output_hash=res["sha256"])
        print(f"[OK] converted → {res['source_md']} (needs_vision pages: {res['needs_vision_pages']})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "converted", error=str(e))
        raise


def cmd_windows(args):
    """确定性 processing windows：source.md → windows.jsonl。"""
    import state_store
    import windowing
    import json
    import hashlib
    db = _vault_state_db()
    out = _staging_dir(args.source)
    source_md = out / "source.md"
    if not source_md.exists():
        raise SystemExit("run source-convert first")
    md = source_md.read_text(encoding="utf-8")
    ihash = hashlib.sha256(md.encode("utf-8")).hexdigest()
    if not state_store.should_run_stage(db, args.source, "windowed", input_hash=ihash):
        print("[skip] windowed up-to-date")
        return
    state_store.start_stage(db, args.source, "windowed", input_hash=ihash)
    try:
        ws = windowing.build_windows(md)
        (out / "windows.jsonl").write_text(
            "\n".join(json.dumps(w, ensure_ascii=False) for w in ws), encoding="utf-8")
        ohash = hashlib.sha256((out / "windows.jsonl").read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="windows",
                                    path=str(out / "windows.jsonl"), sha256=ohash)
        state_store.complete_stage(db, args.source, "windowed", output_hash=ohash)
        print(f"[OK] windowed → {len(ws)} windows")
    except Exception as e:
        state_store.fail_stage(db, args.source, "windowed", error=str(e))
        raise


def _vault_dir() -> Path:
    """新架构输出 vault（spec §4），与状态库同锚点。"""
    return _workspace_root() / "wiki"


def cmd_init_vault(args):
    """建 wiki/ 脚手架（spec §4）+ overview/log/purpose 种子。幂等：已存在的文件/目录绝不覆盖。"""
    vault = _vault_dir()
    for d in ["_meta", "domains", "concepts", "topics", "comparisons", "synthesis",
              "sources", "assets", "Review-Queue"]:
        (vault / d).mkdir(parents=True, exist_ok=True)
    seeds = {
        "overview.md": (Path(__file__).resolve().parents[1] / "templates" / "overview.md"
                        ).read_text(encoding="utf-8"),
        "log.md": "# 操作日志（append-only：/ingest 与收尾 lint 各自追加）\n",
        "_meta/purpose.md": ("# 学习目标与偏好（用户维护）\n\n"
                             "<写下你的学习目标、当前重点、偏好的讲解风格——/ingest 会参考>\n"),
    }
    for rel, content in seeds.items():
        target = vault / rel
        if not target.exists():
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"[OK] seeded {rel}")
        else:
            print(f"[keep] {rel} exists")
    print(f"[OK] vault skeleton at {vault}")


def cmd_rebuild_registry(args):
    """从概念页 frontmatter 确定性重建 concepts/_registry.yaml + aliases.md（派生，勿手改）。"""
    import concept_store
    vault = _vault_dir()
    if not vault.exists():
        print("no wiki/ vault yet")
        return
    metas = concept_store.scan_concept_pages(vault)
    registry, errors, warnings = concept_store.build_registry(metas)
    for w in warnings:
        print(f"[warn] {w}")
    if errors:
        for e in errors:
            print(f"[error] {e}", file=sys.stderr)
        raise SystemExit("registry not written (fix duplicate/missing canonical_id first)")
    sha = concept_store.write_registry(vault, registry)
    concept_store.write_aliases(vault, registry)
    shared = sum(1 for e in registry.values() if e["scope"] == "shared")
    print(f"[OK] registry: {len(registry)} concepts ({shared} shared), sha256={sha[:12]}")


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


def cmd_ingest_start(args):
    """/ingest 开工：取 vault 锁 + stale registry 硬校验 + 推进到 ingesting。
    幂等可恢复：source 已处于 ingesting/running（崩溃/空闲后 resume）时，回收同源残留/stale 锁、
    重取并刷新心跳，跳过阶段转换并报 resumed。锁被**他源**持有才拒（守住跨 agent 互斥）。"""
    import state_store
    import locks
    import ingest_guards
    import os
    db = _vault_state_db()
    wo_row = state_store.get_work_order(db, args.source)
    if wo_row is None:
        raise SystemExit("run workorder first")
    src = state_store.get_source(db, args.source)
    resuming = (src is not None and src["current_stage"] == "ingesting"
                and src["current_status"] == "running")
    held = locks.get(db, scope="vault")
    if held is not None and held["holder"] != args.source:
        raise SystemExit(f"vault lock held by {held['holder']} since {held['started_at']}")
    if held is not None:  # 同源残留/stale 锁 → 回收后重取（resume / 崩溃恢复友好）
        locks.release(db, scope="vault", holder=args.source)
    if not locks.acquire(db, scope="vault", holder=args.source, pid=os.getpid()):
        held = locks.get(db, scope="vault")
        raise SystemExit(f"vault lock held by {held['holder'] if held else '?'}")
    try:
        if not ingest_guards.registry_fresh(_vault_dir(), wo_row["registry_hash"]):
            raise SystemExit("stale registry: disk _registry.yaml != work order hash; re-run workorder")
        if resuming:
            print(f"[OK] resumed ingesting '{args.source}'（vault 锁重取）；续 window 循环")
            return
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
    import locks
    db = _vault_state_db()
    state_store.start_window(db, args.source, args.window, input_hash=args.hash)
    locks.heartbeat(db, scope="vault", holder=args.source)
    print(f"[OK] window {args.window} running")


def cmd_window_done(args):
    import state_store
    import locks
    db = _vault_state_db()
    state_store.finish_window(db, args.source, args.window,
                              write_set_json=args.writes, proposal_set_json=args.proposals)
    locks.heartbeat(db, scope="vault", holder=args.source)
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


def cmd_lint(args):
    """收尾门禁（spec §10/§11）：lint proposed 集合 → 过则 promote+重建派生；败则回滚+Review-Queue。"""
    import state_store
    import wiki_gate
    import concept_store
    import snapshots
    import hashlib
    import json
    import shutil
    from datetime import date
    db = _vault_state_db()
    vault = _vault_dir()
    proposed_all = wiki_gate.collect_proposed(vault) if vault.exists() else []
    # 范围隔离：只 lint/promote 归属本 source 的 proposed 页（frontmatter 归属 ∪ window write_set）。
    # 归属其他已注册 source 的页放行跳过（等各自收尾）；不归属任何 source 的孤儿页阻断
    # （fail-closed：多半是 /ingest 漏了 window-done --writes 记账）。
    source_ids = [r["source_id"] for r in state_store.status_rows(db)]
    if args.source not in source_ids:
        source_ids.append(args.source)
    written_by: dict[str, set[str]] = {}
    for sid in source_ids:
        ws: set[str] = set()
        for w in state_store.window_states(db, sid):
            if w["write_set_json"]:
                ws.update(str(x).replace("\\", "/") for x in json.loads(w["write_set_json"]))
        written_by[sid] = ws
    proposed, orphans = [], []
    for p in proposed_all:
        if wiki_gate.belongs_to_source(p["rel_path"], p["meta"], args.source, written_by[args.source]):
            proposed.append(p)
        elif any(wiki_gate.belongs_to_source(p["rel_path"], p["meta"], s, written_by[s])
                 for s in source_ids if s != args.source):
            print(f"[skip] proposed 页归属其他 source，留待其所属 source 收尾: {p['rel_path']}")
        else:
            orphans.append(p)
    ihash = hashlib.sha256(("\n".join(
        f"{p['rel_path']}:{hashlib.sha256(p['body'].encode('utf-8')).hexdigest()}"
        for p in proposed) + "\n!orphans:" + ",".join(p["rel_path"] for p in orphans)
    ).encode("utf-8")).hexdigest()
    if not state_store.should_run_stage(db, args.source, "lint", input_hash=ihash):
        print("[skip] lint up-to-date")
        return
    state_store.start_stage(db, args.source, "lint", input_hash=ihash)
    violations = [{"path": p["rel_path"], "rule": "unattributed-proposed",
                   "detail": "proposed 页不归属任何 source（缺 window-done --writes 记账"
                             "或 frontmatter 归属），fail-closed 阻断发布"}
                  for p in orphans] + wiki_gate.lint_pages(vault, proposed)
    if violations:
        for v in violations:
            print(f"[lint] {v['rule']} {v['path']}: {v['detail']}")
            state_store.add_review_proposal(db, args.source, target_path=v["path"],
                                            kind=v["rule"], reason=v["detail"])
        # 回滚本 source 的全部就地 merge 快照
        snap_dir = _workspace_root() / "pipeline-workspace/snapshots" / args.source
        for manifest in sorted(snap_dir.rglob("manifest.json")):
            snapshots.rollback(manifest)
            print(f"[rollback] {manifest}")
        queue = vault / "Review-Queue" / f"{args.source}-lint-{date.today().isoformat()}.md"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(
            "# Lint 未过（不 promote；就地 merge 已回滚）\n\n" +
            "\n".join(f"- **{v['rule']}** `{v['path']}`：{v['detail']}" for v in violations) +
            "\n\n处理后回流：修复 → 重新 /ingest（状态机已允许 lint failed → ingest_waiting）。\n",
            encoding="utf-8", newline="\n")
        state_store.fail_stage(db, args.source, "lint",
                               error=f"{len(violations)} lint violations")
        raise SystemExit(f"lint failed: {len(violations)} violations -> {queue}")
    # 通过：promote + 重建派生 + 日志 + 清快照
    n = wiki_gate.promote(vault, proposed)
    registry, errors, _w = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        state_store.fail_stage(db, args.source, "lint", error="; ".join(errors))
        raise SystemExit("registry corrupt: " + "; ".join(errors))
    concept_store.write_registry(vault, registry)
    concept_store.write_aliases(vault, registry)
    wiki_gate.write_index(vault)
    log = vault / "log.md"
    with open(log, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n## [{date.today().isoformat()}] lint | {args.source} | promoted {n} pages\n")
    snap_dir = _workspace_root() / "pipeline-workspace/snapshots" / args.source
    if snap_dir.exists():
        shutil.rmtree(snap_dir)
    state_store.complete_stage(db, args.source, "lint", output_hash=ihash)
    print(f"[OK] lint passed: promoted {n} pages; index/registry/aliases rebuilt; source published")


def cmd_promotion_candidates(args):
    """检测跨域提升候选（spec §6/§13：只给候选，提升一律人工确认）。--propose 落 Review-Queue。"""
    import state_store
    import concept_store
    import promotion
    from datetime import date
    vault = _vault_dir()
    if not vault.exists():
        print("no wiki/ vault yet")
        return
    registry, errors, _w = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        raise SystemExit("corrupt concept pages: " + "; ".join(errors))
    cands = promotion.find_candidates(registry)
    if not cands:
        print("no promotion candidates")
        return
    for c in cands:
        print(f"[candidate] {c['term']}: domains={','.join(c['domains'])} ids={','.join(c['canonical_ids'])}")
    if getattr(args, "propose", False):
        db = _vault_state_db()
        db.parent.mkdir(parents=True, exist_ok=True)
        state_store.init_db(db)
        lines = ["# 跨域提升候选（人工确认后用 promote-concept --id <canonical_id> 执行）", ""]
        for c in cands:
            lines.append(f"- `{c['term']}`：{', '.join(c['canonical_ids'])}（语义确实复用才提升；同名异义保持各自页）")
            state_store.add_review_proposal(db, "vault", target_path=c["canonical_ids"][0],
                                            kind="promotion-candidate",
                                            reason=f"term '{c['term']}' in domains {','.join(c['domains'])}")
        queue = vault / "Review-Queue" / f"promotion-{date.today().isoformat()}.md"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"[OK] proposals -> {queue}")


def cmd_promote_concept(args):
    """人工确认后的机械提升：移动到顶层 concepts/ + frontmatter 改写 + 全 vault 链接重写。"""
    import promotion
    new_cid, new_rel = promotion.promote_to_shared(_vault_dir(), args.id)
    print(f"[OK] promoted -> {new_cid} ({new_rel}); 建议随后 rebuild-registry")


def cmd_check_session(args):
    """Q1 确定性检查：query-session 目录契约（--saved 按 /kb-save 后完整契约）。"""
    import query_session
    d = _workspace_root() / "pipeline-workspace/query-sessions" / args.id
    problems = query_session.check_session(d, saved=getattr(args, "saved", False))
    if problems:
        for p in problems:
            print(f"[Q1] {p}")
        raise SystemExit(f"check-session failed: {len(problems)} problems")
    print(f"[OK] session {args.id} passes Q1 ({'saved' if args.saved else 'query'} contract)")


def cmd_fail(args):
    """维护命令：把崩溃残留的 running 阶段标记为 failed（之后可重跑该阶段）。"""
    import state_store
    db = _vault_state_db()
    state_store.fail_stage(db, args.source, args.stage, error=args.error)
    print(f"[OK] {args.source}/{args.stage} marked failed: {args.error}")


def cmd_status(args):
    """列出每个 source 的阶段/状态 + vault 锁持有者（spec §3.3）。"""
    import state_store
    import locks

    db = _vault_state_db()
    if not db.exists():
        print("no state db yet (run a source through preprocess first)")
        return
    for r in state_store.status_rows(db):
        print(f"{r['source_id']:<28} {r['domain']:<14} {r['current_stage']:<16} {r['current_status']}")
    row = locks.get(db, scope="vault")
    if row:
        stale = locks.is_stale(db, scope="vault", ttl_seconds=LOCK_TTL_SECONDS)
        mark = "  [STALE → pipeline.py unlock]" if stale else ""
        print(f"[lock] vault held by {row['holder']} since {row['started_at']}"
              f" (heartbeat {row['heartbeat_at']}){mark}")


def cmd_next(args):
    """列出每个 source 的下一步人工动作 + stale 锁清理建议（spec §3.3）。"""
    import state_store
    import locks

    db = _vault_state_db()
    if not db.exists():
        print("no state db yet")
        return
    for r in state_store.next_actions(db):
        print(f"{r['source_id']:<28} {r['current_stage']:<16} -> {r['next_action']}")
    row = locks.get(db, scope="vault")
    if row and locks.is_stale(db, scope="vault", ttl_seconds=LOCK_TTL_SECONDS):
        print(f"vault-lock ({row['holder']}){'':<13} -> stale (heartbeat {row['heartbeat_at']});"
              f" 运行: pipeline.py unlock")


def cmd_unlock(args):
    """受控回收 stale vault 锁（spec §3.3）：heartbeat 未超时的活锁绝不破。"""
    import locks

    db = _vault_state_db()
    if not db.exists():
        print("no state db yet")
        return
    row = locks.get(db, scope="vault")
    if row is None:
        print("no vault lock held")
        return
    if locks.break_stale(db, scope="vault", ttl_seconds=args.ttl):
        print(f"[OK] stale vault lock released (was held by {row['holder']},"
              f" heartbeat {row['heartbeat_at']})")
        return
    raise SystemExit(f"vault lock held by {row['holder']} is not stale"
                     f" (heartbeat {row['heartbeat_at']}, ttl {args.ttl}s)；"
                     f"可能有活跃 /ingest，等待或先 window-fail 收尾")


def main():
    parser = argparse.ArgumentParser(description="PDF to Study KB 流水线 CLI")
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # status / next（vault 级单库状态视图，不接 --book）
    subparsers.add_parser("status", help="列出每个 source 的阶段/状态（vault 级单库）")
    subparsers.add_parser("next", help="列出每个 source 的下一步人工动作")
    ulp = subparsers.add_parser("unlock", help="回收 stale vault 锁（heartbeat 超时才允许；活锁拒绝）")
    ulp.add_argument("--ttl", type=int, default=LOCK_TTL_SECONDS, help="stale 判定秒数")

    # P1 新架构预处理阶段（vault 级单库，不接 --book）
    asp = subparsers.add_parser("add-source", help="注册一个来源到状态库")
    asp.add_argument("--source", required=True, help="source_id")
    asp.add_argument("--domain", required=True, help="所属领域")
    asp.add_argument("--path", required=True, help="原始文件路径")
    asp.add_argument("--fmt", required=True, choices=["pdf", "md", "docx", "pptx"], help="来源格式")
    for name, help_text in [("profile", "逐页 profile + needs_vision 标记"),
                            ("source-convert", "转成 staging/<source>/source.md + 难页 PNG"),
                            ("windows", "生成确定性 processing windows")]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--source", required=True, help="source_id")
    subparsers.add_parser("init-vault", help="建 wiki/ 脚手架 + overview/log/purpose 种子（幂等）")
    subparsers.add_parser("rebuild-registry", help="从概念页 frontmatter 重建 _registry.yaml + aliases.md")
    wop = subparsers.add_parser("workorder", help="生成 source 级 ingest work order")
    wop.add_argument("--source", required=True)
    swp = subparsers.add_parser("show-window", help="打印指定 window 的源文本")
    swp.add_argument("--source", required=True)
    swp.add_argument("--window", required=True)
    for name, help_text in [("ingest-start", "/ingest 开工：锁 + stale registry 校验 + ingesting"),
                            ("ingest-done", "/ingest 收工：ingested(proposed) + 释放锁")]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--source", required=True)
    wsp2 = subparsers.add_parser("window-start", help="记录一个 window 开始")
    wsp2.add_argument("--source", required=True)
    wsp2.add_argument("--window", required=True)
    wsp2.add_argument("--hash", required=True)
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
    lp = subparsers.add_parser("lint", help="收尾门禁：lint proposed → promote 或 回滚+Review-Queue")
    lp.add_argument("--source", required=True)
    pcp = subparsers.add_parser("promotion-candidates", help="检测跨域提升候选（--propose 落 Review-Queue）")
    pcp.add_argument("--propose", action="store_true")
    pmp = subparsers.add_parser("promote-concept", help="人工确认后机械提升一个概念为 shared")
    pmp.add_argument("--id", required=True, help="canonical_id（concept.<domain>.<slug>）")
    csp = subparsers.add_parser("check-session", help="Q1：query-session 目录契约检查")
    csp.add_argument("--id", required=True, help="session run_id")
    csp.add_argument("--saved", action="store_true", help="按 /kb-save 后完整契约检查")
    fp = subparsers.add_parser("fail", help="维护：把崩溃残留的 running 阶段标记为 failed")
    fp.add_argument("--source", required=True, help="source_id")
    fp.add_argument("--stage", required=True, help="卡死的 stage 名")
    fp.add_argument("--error", required=True, help="失败原因（记入 source_stage_runs.error）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        'status': cmd_status,
        'next': cmd_next,
        'unlock': cmd_unlock,
        'add-source': cmd_add_source,
        'profile': cmd_profile,
        'source-convert': cmd_source_convert,
        'windows': cmd_windows,
        'fail': cmd_fail,
        'init-vault': cmd_init_vault,
        'rebuild-registry': cmd_rebuild_registry,
        'workorder': cmd_workorder,
        'show-window': cmd_show_window,
        'ingest-start': cmd_ingest_start,
        'ingest-done': cmd_ingest_done,
        'window-start': cmd_window_start,
        'window-done': cmd_window_done,
        'window-fail': cmd_window_fail,
        'resolve-concept': cmd_resolve_concept,
        'check-write': cmd_check_write,
        'snapshot-page': cmd_snapshot_page,
        'lint': cmd_lint,
        'promotion-candidates': cmd_promotion_candidates,
        'promote-concept': cmd_promote_concept,
        'check-session': cmd_check_session,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
