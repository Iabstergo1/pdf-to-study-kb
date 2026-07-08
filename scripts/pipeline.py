#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI（新架构：确定性预处理 + 收尾门禁 + 状态跟踪，零 LLM）

预处理：add-source → profile → source-convert → source-audit →[ arbitration-* ]→ windows → workorder
/ingest 会话支撑：ingest-start/done、window-start/done/fail、show-window、
                resolve-concept、check-write、snapshot-page
增量重开：reopen（已收尾来源重建 workorder + 状态机回 workorder_ready 做增量补充）
收尾：lint（promote 或 回滚+Review-Queue）、rebuild-registry
vault 与维护：init-vault、status、next、fail、promotion-candidates、
              promote-concept、check-session

用法：python scripts/pipeline.py <command> [options]
架构真值：CLAUDE.md（Claude）/ AGENTS.md（Codex）
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


def _require_vault_lock(db, source_id: str):
    """写 ingest 进度/收工前必须确认当前 source 仍持有 vault 锁。"""
    import locks
    row = locks.get(db, scope="vault")
    if row is None:
        raise SystemExit(f"vault lock not held; run ingest-start --source {source_id} first")
    if row["holder"] != source_id:
        raise SystemExit(f"vault lock held by {row['holder']} since {row['started_at']}; "
                         f"cannot proceed for {source_id}")
    return row


def _source_is_running_ingest(db, source_id: str) -> bool:
    import state_store
    src = state_store.get_source(db, source_id)
    return (src is not None and src["current_stage"] == "ingesting"
            and src["current_status"] == "running")


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
    import thresholds
    import json
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    # 混入 profiler 版本 + 检测阈值指纹：启发式升级或 env 覆盖阈值即失效缓存（对任意来源通用）。
    ihash = (hashlib.sha256(raw.read_bytes()).hexdigest() + ":" + source_profile.PROFILER_VERSION
             + ":" + thresholds.fingerprint())
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
        if source_profile.is_scanned_source(pages):
            import sys as _sys
            print(f"[WARN] scanned_source / requires_ocr：{len(pages)} 页近乎整本零文本+图像——"
                  f"route B 不适用（不让 LLM 临场 OCR 上千整页图）；source-convert 将 fail-closed，需 OCR route。",
                  file=_sys.stderr)
        print(f"[OK] profiled → {len(pages)} pages ({n_vision} needs_vision)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "profiled", error=str(e))
        raise


def cmd_source_convert(args):
    """source-convert：raw → staging/<source>/ 全 artifact。按 --backend/--mineru-policy 选后端。"""
    import state_store
    import source_convert
    import source_profile
    import json as _json
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    fmt = src_row["format"]
    backend = getattr(args, "backend", "auto")
    policy = getattr(args, "mineru_policy", "conservative")
    # profile pages（供 auto 路由 + 扫描件再协调）；docx/pptx 为空。
    pj = _staging_dir(args.source) / "pages.jsonl"
    pages = ([_json.loads(l) for l in pj.read_text(encoding="utf-8").splitlines() if l.strip()]
             if pj.exists() else [])
    name, _consumed = source_convert.select_backend(fmt, pages, backend=backend, policy=policy)
    # route-B 仅适合 born-digital 少数难页：整本扫描件若仍要走 PyMuPDF 且未 --force → 阻断，
    # 引导用 MinerU（默认 auto 已把扫描件路由给 MinerU；仅在选定后端确为 pymupdf 时才探测此条）。
    if name == "pymupdf" and not getattr(args, "force", False) and source_profile.is_scanned_source(pages):
        raise SystemExit(
            "scanned_source / requires_ocr：整本扫描件不适合 PyMuPDF route B。"
            "用 --backend auto（默认，扫描件走 MinerU）或安装 MinerU（python scripts/install_mineru.py）；"
            "确要 PyMuPDF 渲染加 --force。")
    # 版本化缓存键（单一真值，与 dispatcher 同源）：raw sha + PROFILER_VERSION + ARTIFACT_VERSION
    # + backend + policy + MINERU_ADAPTER_VERSION（切后端/策略不复用彼此产物）。
    ihash = source_convert.converted_input_hash(raw, backend=backend, policy=policy)
    if not state_store.should_run_stage(db, args.source, "converted", input_hash=ihash):
        print("[skip] converted up-to-date")
        return
    state_store.start_stage(db, args.source, "converted", input_hash=ihash)
    try:
        out = _staging_dir(args.source)
        res = source_convert.convert(raw, out_dir=out, fmt=fmt, backend=backend,
                                     mineru_policy=policy, profile_pages=pages)
        # pages.jsonl 已由 profile 阶段产出；convert 内部用同一批纯函数复算 needs_vision，结果一致
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.record_artifact(db, args.source, kind="chapters", path=res["chapters_path"], sha256=res["chapters_sha"])
        state_store.record_artifact(db, args.source, kind="blocks", path=res["blocks_path"], sha256=res["blocks_sha"])
        state_store.record_artifact(db, args.source, kind="parse_report", path=res["parse_report_path"], sha256=res["parse_report_sha"])
        n_assets = _sync_assets(args.source)  # 难页 PNG 入 vault（公式嵌图依赖；任意源通用）
        state_store.complete_stage(db, args.source, "converted", output_hash=res["sha256"])
        print(f"[OK] converted → {res['source_md']} (backend={res['backend']}; "
              f"needs_vision pages: {res['needs_vision_pages']}; synced {n_assets} PNG → vault assets)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "converted", error=str(e))
        raise


def _sync_assets(source_id: str) -> int:
    """把 staging 难页 PNG 复制进 wiki/assets/<src>/（确定性、幂等）。
    公式 lesson/concept 嵌入 `![[assets/<src>/pXXXX.png]]` 需图在 vault 内才不断链——
    对任意有 needs_vision 页的来源通用（不止某本书）。返回本次复制/更新的文件数。
    C2：源目录含 staging/<src>/assets（PyMuPDF/MinerU 难页图）**与 staging/<src>/arbitration
    （仲裁 render 决策补渲染的整页图）**——后者曾被漏同步，导致引用 arbitration 渲染页的 lesson 断链。"""
    import shutil
    import hashlib
    staging = _staging_dir(source_id)
    dst_dir = _vault_dir() / "assets" / source_id
    n = 0
    imgs, seen = [], set()
    for sub in ("assets", "arbitration"):        # assets 优先；同名只取一次（避免重复覆盖）
        d = staging / sub
        if not d.exists():
            continue
        for ext in ("*.png", "*.jpg", "*.jpeg"):  # PyMuPDF 难页 PNG + MinerU 图片(.jpg/.png) + 仲裁整页图
            for img in d.glob(ext):
                if img.name not in seen:
                    seen.add(img.name)
                    imgs.append(img)
    if not imgs:
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    for img in sorted(imgs):
        dst = dst_dir / img.name
        if (not dst.exists()) or (hashlib.sha256(dst.read_bytes()).hexdigest()
                                  != hashlib.sha256(img.read_bytes()).hexdigest()):
            shutil.copy2(img, dst)
            n += 1
    return n


def cmd_sync_assets(args):
    """把本源 staging 难页 PNG 同步进 vault（供公式页嵌图）。预处理/重开会自动调用，亦可单独跑。"""
    n = _sync_assets(args.source)
    print(f"[OK] synced {n} source-page PNG(s) -> wiki/assets/{args.source}/")


def cmd_source_audit(args):
    """source-audit：PDF 双审——跑 MinerU structural review 复核 PyMuPDF 抽取，写 reconciliation.json。

    PyMuPDF 的 needs_vision 阈值刻意宽、不可作 PDF 验收单一真值；本步让 MinerU 作独立结构 reviewer，
    产出可审计的双审证据（哪后端给哪证据/对了哪些页/哪里不一致/是否接受/是否降级）。
    --strict：MinerU 复读必需但不可用/失败 → 非零退出（fail-closed，不静默回退 PyMuPDF）。
    无 --strict（dev）：MinerU 缺时 PyMuPDF-only 仍出，但 reconciliation 显式标 degraded/未双审。"""
    import json as _json
    import hashlib
    import state_store
    import source_audit
    import source_convert
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    staging = _staging_dir(args.source)
    report_path = staging / "parse_report.json"
    if not report_path.exists():
        raise SystemExit("缺 parse_report.json：先跑 source-convert")
    report = _json.loads(report_path.read_text(encoding="utf-8"))
    source_type = report.get("source_type", "")
    primary_backend = report.get("selected_backend", "")
    # 复用 converted 缓存键派生 audit input_hash（自洽即可：同源重跑命中缓存，不重跑 MinerU）。
    ihash = source_convert.converted_input_hash(raw, backend="auto", policy="conservative")
    try:
        rep = source_audit.audit(staging, raw, source_type=source_type,
                                 primary_backend=primary_backend,
                                 strict=getattr(args, "strict", False),
                                 source_id=args.source, input_hash=ihash)
    except source_audit.DualAuditUnavailable as e:
        raise SystemExit(f"dual-audit fail-closed: {e}")
    recon_path = staging / "reconciliation.json"
    sha = hashlib.sha256(recon_path.read_bytes()).hexdigest()
    state_store.record_artifact(db, args.source, kind="reconciliation",
                                path=str(recon_path), sha256=sha)
    print(f"[OK] source-audit → {rep['review_status']} (dual_audited={rep['dual_audited']}, "
          f"degraded={rep['degraded']}, disagreements={len(rep['disagreements'])}) → {recon_path}")


def cmd_arbitration_status(args):
    """打印分歧仲裁队列状态（确定性，零 LLM）：候选 / 已决策 / pending / render·ignore·needs_human。
    source-preflight/ingest skill 据此决定是否需要 agent 自动仲裁；无候选则无需调 LLM。"""
    import json as _json
    import arbitration as arb
    staging = _staging_dir(args.source)
    ev = staging / arb.EVIDENCE_FILE
    if not ev.exists():
        raise SystemExit("缺 evidence.json；先跑 source-audit")
    model = _json.loads(ev.read_text(encoding="utf-8"))
    candidates = model.get("candidates", [])
    dec_path = staging / arb.DECISIONS_FILE
    decisions = (_json.loads(dec_path.read_text(encoding="utf-8")).get("decisions", [])
                 if dec_path.exists() else [])
    by_page = {int(d["page"]): d.get("decision") for d in decisions}
    pending = [p for p in candidates if int(p) not in by_page]
    kinds = {}
    for v in by_page.values():
        kinds[v] = kinds.get(v, 0) + 1
    print(f"[arbitration] candidates={len(candidates)} decided={len(by_page)} pending={len(pending)} "
          f"render={kinds.get('render', 0)} ignore={kinds.get('ignore', 0)} needs_human={kinds.get('needs_human', 0)}")
    if pending:
        print(f"  pending pages need agent arbitration: {pending}")
        print(f"  read packets → {staging / arb.QUEUE_FILE}; write decisions → {staging / arb.DECISIONS_FILE}")
    nh_pages = sorted(int(p) for p, dec in by_page.items() if dec == arb.NEEDS_HUMAN)
    if nh_pages:
        print(f"  needs_human pages (resolve with: arbitration-resolve --page <n> "
              f"--decision render|ignore --reason ...): {nh_pages}")


def cmd_arbitration_resolve(args):
    """把某 needs_human 页改成合法裁决（render|ignore），人工/agent 闭环（确定性，零 LLM）。

    reason 必填（记入 audit.jsonl）；只允许 needs_human → render|ignore。改 decisions.json 后须重跑
    arbitration-apply → windows → preflight-eval 才真正闭环（windows/preflight 仍对未决 needs_human fail-closed）。"""
    import json as _json
    import arbitration as arb
    staging = _staging_dir(args.source)
    dec_path = staging / arb.DECISIONS_FILE
    if not dec_path.exists():
        raise SystemExit(f"缺 {arb.DECISIONS_FILE}（先 source-audit + 仲裁产出裁决）")
    reason = (args.reason or "").strip()
    if not reason:
        raise SystemExit("--reason 必填：人工/agent 裁决须给出理由（记入 audit）")
    doc = _json.loads(dec_path.read_text(encoding="utf-8"))
    decisions = doc.get("decisions", [])
    page = int(args.page)
    found = next((d for d in decisions if int(d.get("page", 0)) == page), None)
    if found is None:
        raise SystemExit(f"page {page} 不在 {arb.DECISIONS_FILE}")
    if found.get("decision") != arb.NEEDS_HUMAN:
        raise SystemExit(f"page {page} 当前裁决是 {found.get('decision')!r}，不是 needs_human（无需 resolve）")
    found["decision"] = args.decision
    found["reason"] = reason
    found["resolved_from"] = arb.NEEDS_HUMAN
    dec_path.write_text(_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    ap = staging / arb.AUDIT_FILE
    ap.parent.mkdir(parents=True, exist_ok=True)
    with open(ap, "a", encoding="utf-8") as f:
        f.write(_json.dumps({"page": page, "decision": args.decision, "reason": reason,
                             "resolved_from": arb.NEEDS_HUMAN}, ensure_ascii=False) + "\n")
    print(f"[OK] arbitration-resolve → page {page}: needs_human → {args.decision}。"
          f"现在重跑 arbitration-apply → windows → preflight-eval 闭环。")


def _apply_resolutions(staging, decisions):
    """把裁决回写 evidence.json 的 arbitration/resolution + 重算 final_hard_pages，并追加 audit.jsonl（可审计）。"""
    import json as _json
    import arbitration as arb
    ev_path = Path(staging) / arb.EVIDENCE_FILE
    model = _json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {"pages": {}}
    res_map = {arb.RENDER: "materialized", arb.IGNORE: "ignored", arb.NEEDS_HUMAN: "blocked"}
    for d in decisions:
        pg, dec = str(int(d["page"])), d.get("decision")
        if pg in model.get("pages", {}):
            model["pages"][pg]["arbitration"] = dec
            model["pages"][pg]["resolution"] = res_map.get(dec)
    model["final_hard_pages"] = sorted(set(model.get("initial_needs_vision", [])) | set(arb.render_pages(decisions)))
    ev_path.write_text(_json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    ap = Path(staging) / arb.AUDIT_FILE
    ap.parent.mkdir(parents=True, exist_ok=True)
    with open(ap, "a", encoding="utf-8") as f:
        for d in decisions:
            f.write(_json.dumps({"page": int(d["page"]), "decision": d.get("decision"),
                                 "reason": d.get("reason", "")}, ensure_ascii=False) + "\n")


def cmd_arbitration_apply(args):
    """物化 arbitration/decisions.json（确定性，零 LLM）：render→补整页图 + 置 needs_vision + 风险标记；
    ignore→记原因；needs_human→标 blocked。幂等 + 追加 audit.jsonl。**须在 windows 之前跑**，
    使 windows 首次构窗即携带视觉资产（避免对已 windowed 源回退重跑的状态机限制）。"""
    import json as _json
    import state_store
    import source_artifacts as sa
    import source_profile
    import arbitration as arb
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    staging = _staging_dir(args.source)
    dec_path = staging / arb.DECISIONS_FILE
    if not dec_path.exists():
        raise SystemExit(f"缺 {arb.DECISIONS_FILE}（agent 在 skill 流里仲裁后才有）；先 source-audit + 自动仲裁")
    decisions = _json.loads(dec_path.read_text(encoding="utf-8")).get("decisions", [])
    blocks = sa.read_blocks(staging / "blocks.jsonl")
    pj = staging / "pages.jsonl"
    pages = ([_json.loads(l) for l in pj.read_text(encoding="utf-8").splitlines() if l.strip()]
             if pj.exists() else [])
    render_pgs = arb.render_pages(decisions)
    if render_pgs:
        source_profile.render_pages_png(raw, render_pgs, staging / "assets", prefix="p")  # 补整页图进 assets/
    sa.write_blocks(staging / "blocks.jsonl", arb.materialize_blocks(blocks, decisions))
    pj.write_text("\n".join(_json.dumps(p, ensure_ascii=False) for p in arb.materialize_pages(pages, decisions)),
                  encoding="utf-8")
    _apply_resolutions(staging, decisions)
    n = _sync_assets(args.source)
    kinds = {}
    for d in decisions:
        kinds[d.get("decision")] = kinds.get(d.get("decision"), 0) + 1
    print(f"[OK] arbitration-apply → render={len(render_pgs)} ignore={kinds.get('ignore', 0)} "
          f"needs_human={kinds.get('needs_human', 0)}; synced {n} PNG → vault. "
          f"现在(重)跑 windows 让窗口携带视觉资产,再 preflight-eval --strict 验收闭环。")


def cmd_windows(args):
    """确定性 processing windows：有 blocks.jsonl 走 block-aware，否则退回旧 char 窗。"""
    import state_store
    import windowing
    import source_artifacts
    import json
    import hashlib
    db = _vault_state_db()
    out = _staging_dir(args.source)
    blocks_path = out / "blocks.jsonl"
    source_md = out / "source.md"
    if not source_md.exists():
        raise SystemExit("run source-convert first")
    # 双审闸门（确定性，零 LLM；在状态机转换之前）。--dev-bypass 显式跳过（dev 降级路径，不可用于
    # strict 验收）。windows 是交给 ingest LLM 的输入，所以构窗前必须满足两道门：
    if not getattr(args, "dev_bypass", False):
        import arbitration as arb
        import source_audit
        # 闸门 B（PDF 双审存在性）：PDF 源构窗前必须已完成 source-audit——reconciliation + evidence
        # + arbitration/queue 三件套齐全。否则窗口会在"未双审/未生成分歧队列"下被交给 ingest LLM。
        # 非 PDF 源不受影响。PDF 判定以 state_store 记录的 source format 为权威（add-source 写入），
        # parse_report.json 仅作补充——缺 parse_report.json 不能让 PDF 漏过闸门。
        src_row = state_store.get_source(db, args.source)
        fmt = src_row["format"] if src_row else None
        report = {}
        rp = out / "parse_report.json"
        if rp.exists():
            report = json.loads(rp.read_text(encoding="utf-8"))
        is_pdf = (fmt == "pdf"
                  or report.get("source_type") in source_audit.PDF_TYPES
                  or bool(report.get("dual_audit_required")))
        if is_pdf:
            missing = [n for n, p in [
                ("reconciliation.json", out / "reconciliation.json"),
                ("evidence.json", out / arb.EVIDENCE_FILE),
                ("arbitration/queue.json", out / arb.QUEUE_FILE)] if not p.exists()]
            if missing:
                raise SystemExit(
                    "PDF 源未完成 source-audit（缺 " + ", ".join(missing) + "），拒绝构窗（fail-closed）："
                    "windows 是交给 ingest LLM 的输入，PDF 必须先 PyMuPDF+MinerU 双审并生成分歧队列。"
                    "先跑 source-audit（+自动仲裁+arbitration-apply），或加 --dev-bypass 跳过"
                    "（dev 降级路径，不可用于 strict 验收）。")
        # 闸门 A（分歧未闭环）：有 evidence 且存在未仲裁/render未物化/needs_human/ignore缺因 → 拒绝构窗。
        ev_path = out / arb.EVIDENCE_FILE
        if ev_path.exists():
            model = json.loads(ev_path.read_text(encoding="utf-8"))
            dec_path = out / arb.DECISIONS_FILE
            decisions = (json.loads(dec_path.read_text(encoding="utf-8")).get("decisions", [])
                         if dec_path.exists() else [])
            gate_blocks = source_artifacts.read_blocks(blocks_path) if blocks_path.exists() else []
            blockers = arb.windows_blockers(model, decisions, gate_blocks)
            if blockers:
                counts: dict = {}
                for k, _pg in blockers:
                    counts[k] = counts.get(k, 0) + 1
                pages = sorted({pg for _k, pg in blockers})
                raise SystemExit(
                    "未闭环双审分歧，拒绝构窗（fail-closed）："
                    + ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
                    + f"；涉及页 {pages[:20]}。先读 arbitration/queue.json 仲裁→写 decisions.json"
                      "→arbitration-apply，或加 --dev-bypass 跳过（产物降级，不可用于 strict 验收）。")
    # chapters.json（L3 chapter_title 查询源）；缺则空表（chapter_title 退化为 ""，不报错）。
    chapters_path = out / "chapters.json"
    chapters = (json.loads(chapters_path.read_text(encoding="utf-8"))
                if chapters_path.exists() else [])
    # 有 blocks → 以 blocks.jsonl 为切窗依据（block-aware）；无 → 退回 source.md char 窗。
    if blocks_path.exists():
        basis = blocks_path.read_bytes()
        build = lambda: windowing.build_windows_from_blocks(
            source_artifacts.read_blocks(blocks_path), source_id=args.source, chapters=chapters)
    else:
        basis = source_md.read_text(encoding="utf-8").encode("utf-8")
        # char-fallback 窗也注入 source_id（其余 L3 字段 block 窗才有）。
        def build():
            ws = windowing.build_windows(source_md.read_text(encoding="utf-8"))
            for w in ws:
                w["source_id"] = args.source
            return ws
    # 混入窗口算法版本：切分逻辑升级即失效缓存（对任意来源通用）。
    ihash = hashlib.sha256(basis).hexdigest() + ":" + windowing.WINDOWING_VERSION
    if not state_store.should_run_stage(db, args.source, "windowed", input_hash=ihash):
        print("[skip] windowed up-to-date")
        return
    state_store.start_stage(db, args.source, "windowed", input_hash=ihash)
    try:
        ws = build()
        (out / "windows.jsonl").write_text(
            "\n".join(json.dumps(w, ensure_ascii=False) for w in ws), encoding="utf-8")
        ohash = hashlib.sha256((out / "windows.jsonl").read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="windows",
                                    path=str(out / "windows.jsonl"), sha256=ohash)
        state_store.complete_stage(db, args.source, "windowed", output_hash=ohash)
        print(f"[OK] windowed → {len(ws)} windows ({'blocks' if blocks_path.exists() else 'chars'})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "windowed", error=str(e))
        raise


def cmd_preflight_eval(args):
    """L4：对 staging/<source>/ 跑确定性预处理验收（零-LLM），落 JSON + 打印 summary。
    --strict：任一 high/fail → 非零退出码（可 CI 化）；非 strict → 退出 0（report 标注）。"""
    import preflight_eval
    import json
    staging = _staging_dir(args.source)
    if not staging.exists():
        raise SystemExit(f"staging 不存在（{staging}）；先跑 add-source→profile→source-convert→windows")
    report = preflight_eval.evaluate(staging)
    out_path = Path(args.json) if getattr(args, "json", None) else staging / "preflight_eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s = report["summary"]
    for c in report["checks"]:
        print(f"[{c['status'].upper():4}] {c['name']:22} ({c['severity']}) {c['detail']}")
    print(f"[summary] ok={s['ok']} warn={s['warn']} fail={s['fail']} → {out_path}")
    has_high_fail = any(c["status"] == "fail" and c["severity"] == "high"
                        for c in report["checks"])
    if getattr(args, "strict", False) and has_high_fail:
        raise SystemExit(2)


def _vault_dir() -> Path:
    """新架构输出 vault（spec §4），与状态库同锚点。"""
    return _workspace_root() / "wiki"


def cmd_init_vault(args):
    """建 wiki/ 脚手架（spec §4）+ overview/log/purpose 种子 + Obsidian 图谱配置。
    幂等：已存在的文件/目录绝不覆盖。Obsidian 配置随每库自动落地（任意领域通用），
    让原生关系图按页面 type 着色、直接当导航入口——零 LLM、对任意来源生效。"""
    import json
    vault = _vault_dir()
    for d in ["_meta", "domains", "concepts", "topics", "comparisons", "synthesis",
              "sources", "assets", "Review-Queue", ".obsidian"]:
        (vault / d).mkdir(parents=True, exist_ok=True)
    # 关系图按 frontmatter type 着色（concept/topic/comparison/synthesis/source/overview）——
    # 与具体领域无关；Obsidian 会忽略未知键、补齐缺省，故部分配置即可。
    _graph_cfg = {
        "showTags": False, "showAttachments": False, "hideUnresolved": True, "showOrphans": True,
        "colorGroups": [
            {"query": '["type":"overview"]', "color": {"a": 1, "rgb": 15054183}},
            {"query": '["type":"topic"]', "color": {"a": 1, "rgb": 5214681}},
            {"query": '["type":"comparison"]', "color": {"a": 1, "rgb": 10181558}},
            {"query": '["type":"synthesis"]', "color": {"a": 1, "rgb": 10181558}},
            {"query": '["type":"concept"]', "color": {"a": 1, "rgb": 5744499}},
            {"query": '["type":"source"]', "color": {"a": 1, "rgb": 9806246}},
        ],
        "nodeSizeMultiplier": 1.3, "lineSizeMultiplier": 1,
        "centerStrength": 0.3, "repelStrength": 12, "linkStrength": 1, "linkDistance": 250, "scale": 1,
    }
    seeds = {
        "overview.md": (Path(__file__).resolve().parents[1] / "templates" / "overview.md"
                        ).read_text(encoding="utf-8"),
        "log.md": "# 操作日志（append-only：/ingest 与收尾 lint 各自追加）\n",
        "_meta/purpose.md": ("# 学习目标与偏好（用户维护）\n\n"
                             "<写下你的学习目标、当前重点、偏好的讲解风格——/ingest 会参考>\n"),
        ".obsidian/graph.json": json.dumps(_graph_cfg, ensure_ascii=False, indent=2) + "\n",
        ".obsidian/app.json": json.dumps(
            {"propertiesInDocument": "hidden", "readableLineLength": True},
            ensure_ascii=False, indent=2) + "\n",
    }
    for rel, content in seeds.items():
        target = vault / rel
        if not target.exists():
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"[OK] seeded {rel}")
        else:
            print(f"[keep] {rel} exists")
    print(f"[OK] vault skeleton at {vault}")


# 学习库观感 CSS snippet（零内容改动，纯 .obsidian 配置层）：给概念页六段式标题加色条/卡片感。
# 不碰任何 md 内容，对现有页面立即生效。社区共识：好看 = 主题 + snippet + Style Settings + cssclasses。
_STUDY_KB_SNIPPET = """\
/* study-kb：知识库观感增强（由 `pipeline.py apply-obsidian-style` 落地，幂等可重跑）。
   设计目标：把概念页六段式（一句话/直觉/形式化/各章如何处理/与其他概念的关系/自测）渲染成卡片感，
   不改任何 md 内容、对全部已发布页立即生效。可在 Obsidian 设置→外观→CSS 片段里开关。 */

/* 阅读视图：H2 小节加左色条 + 轻微背景，形成「卡片分段」观感 */
.markdown-rendered h2 {
  border-left: 3px solid var(--interactive-accent);
  padding-left: 0.6em;
  margin-top: 1.4em;
}

/* 正文行宽与行距：长概念页更易读 */
.markdown-rendered p,
.markdown-rendered li {
  line-height: 1.7;
}

/* 行内代码 / KaTeX 公式块：轻边框，突出「形式化」节 */
.markdown-rendered :not(pre) > code {
  border-radius: 4px;
  padding: 0.1em 0.35em;
}

/* 表格（对比页差异维度）：表头底色 + 单元格内边距 */
.markdown-rendered table thead {
  background: var(--background-secondary);
}
.markdown-rendered table th,
.markdown-rendered table td {
  padding: 0.5em 0.8em;
}

/* 引用块（一句话 / 导航提示）：左色条加粗、背景更柔 */
.markdown-rendered blockquote {
  border-left: 4px solid var(--interactive-accent);
  background: var(--background-secondary-alt);
  border-radius: 0 6px 6px 0;
}
"""

_SNIPPET_NAME = "study-kb"


def cmd_apply_obsidian_style(args):
    """落地学习库观感（纯 .obsidian 配置层，零内容改动，幂等）：
    1) 写 wiki/.obsidian/snippets/study-kb.css（不存在才写，已存在保留）；
    2) 安全 merge wiki/.obsidian/appearance.json 的 enabledCssSnippets，启用该 snippet，保留用户既有键。
    与 init-vault 的"已存在不覆盖"语义解耦：本命令显式调用、专门改用户 Obsidian 偏好，故单列。"""
    import json
    vault = _vault_dir()
    if not vault.exists():
        raise SystemExit("no wiki/ vault yet；先跑 init-vault")
    obs = vault / ".obsidian"
    snippets = obs / "snippets"
    snippets.mkdir(parents=True, exist_ok=True)

    # 1) snippet：不存在才写（保留用户已自定义的同名 snippet）
    snippet_path = snippets / f"{_SNIPPET_NAME}.css"
    if not snippet_path.exists():
        snippet_path.write_text(_STUDY_KB_SNIPPET, encoding="utf-8", newline="\n")
        print(f"[OK] wrote .obsidian/snippets/{_SNIPPET_NAME}.css")
    else:
        print(f"[keep] .obsidian/snippets/{_SNIPPET_NAME}.css exists（不覆盖用户自定义）")

    # 2) appearance.json：读取→合并→写回（保留用户既有键；已启用则幂等跳过）
    app_path = obs / "appearance.json"
    data = {}
    if app_path.exists():
        try:
            data = json.loads(app_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            print("[warn] appearance.json 无法解析为 JSON，按空对象重建（已备份为 .bak）")
            app_path.replace(app_path.with_suffix(".json.bak"))
            data = {}
    enabled = data.get("enabledCssSnippets")
    if not isinstance(enabled, list):
        enabled = []
    if _SNIPPET_NAME in enabled:
        print(f"[keep] appearance.json 已启用 snippet '{_SNIPPET_NAME}'（幂等跳过）")
    else:
        enabled.append(_SNIPPET_NAME)
        data["enabledCssSnippets"] = enabled
        app_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"[OK] merged appearance.json：启用 snippet '{_SNIPPET_NAME}'（保留既有键）")
    print("[OK] obsidian style applied（在 Obsidian 设置→外观→CSS 片段可开关；可另装 Minimal 主题增强）")


def cmd_rebuild_registry(args):
    """从概念页 frontmatter 确定性重建 concepts/_registry.yaml（派生，勿手改）。aliases.md 已废弃（B2）。"""
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
    concept_store.remove_stale_aliases(vault)
    shared = sum(1 for e in registry.values() if e["scope"] == "shared")
    print(f"[OK] registry: {len(registry)} concepts ({shared} shared), sha256={sha[:12]}")


class GraphBuildError(Exception):
    """graph fail-hard：lint errors（list[str] 在 args[0]）。"""


def _rebuild_graph_artifacts(vault):
    """Knowledge Graph v2.0 单一重建路径（零 LLM）：published 页 → 建模 → 分析 → graph-data → lint →
    写 graph-data + 力导向交互 HTML（HTML 即图谱导航入口，点击节点跳对应 Obsidian 笔记）。canvas 已移除。
    fail-hard：lint errors → raise GraphBuildError；调用方（手动）退出，发布钩子吞掉。"""
    import json as _json
    import graph_analysis
    import graph_data
    import graph_html
    import graph_lint
    import graph_model
    vault = _vault_dir() if vault is None else vault
    model = graph_model.build_graph_model(vault)
    analyzed = graph_analysis.analyze_graph(model)
    data = graph_data.to_graph_data(analyzed)
    result = graph_lint.validate_graph_data(data, vault=vault)
    if result["errors"]:
        raise GraphBuildError(result["errors"])
    (Path(vault) / graph_data.GRAPH_DATA_FILE).write_text(
        _json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    graph_html.write_html(vault, data)
    return data, result


def cmd_rebuild_graph(args):
    """重建知识图谱（graph-data + 力导向交互 HTML）。手动 fail-hard（errors → 退出 2）。"""
    vault = _vault_dir()
    if not vault.exists():
        raise SystemExit("no wiki/ vault yet")
    try:
        data, result = _rebuild_graph_artifacts(vault)
    except GraphBuildError as e:
        for msg in e.args[0]:
            print(f"[ERR] graph fail-hard: {msg}")
        raise SystemExit(2)
    s = data["stats"]
    print(f"[OK] rebuild-graph -> {s['node_count']} nodes / {s['edge_count']} edges / "
          f"{s['community_count']} communities; wrote graph-data + html")
    for w in result["warnings"]:
        print(f"[warn] {w}")


def cmd_rebuild_quiz(args):
    """重建自测题库索引 quiz-index.generated.md（零 LLM：published 页 [!question] 题干 + 回链原页）。"""
    import wiki_gate
    vault = _vault_dir()
    if not vault.exists():
        raise SystemExit("no wiki/ vault yet")
    wiki_gate.write_quiz_index(vault)
    print(f"[OK] rebuild-quiz -> {vault / 'quiz-index.generated.md'}")


def cmd_rebuild_propositions(args):
    """重建命题总表 propositions.generated.md（零 LLM：published 页具名命题 + 回链原页）。"""
    import wiki_gate
    vault = _vault_dir()
    if not vault.exists():
        raise SystemExit("no wiki/ vault yet")
    props = wiki_gate.collect_propositions(vault)
    wiki_gate.write_propositions_index(vault)
    for d in wiki_gate.duplicate_proposition_names(props):
        print(f"[warn] 命题重名（名字即锚点，域内应唯一）：{d}")
    print(f"[OK] rebuild-propositions -> {len(props)} 条 -> {vault / 'propositions.generated.md'}")


def cmd_graph_lint(args):
    """校验 wiki/graph-data.generated.json（+ 已生成的 HTML）；写报告；errors → 退出 2。"""
    import json as _json
    from datetime import date
    import graph_data
    import graph_html
    import graph_lint
    vault = _vault_dir()
    gpath = vault / graph_data.GRAPH_DATA_FILE
    if not gpath.exists():
        raise SystemExit(f"graph-data 不存在（{gpath}）；先跑 rebuild-graph")
    data = _json.loads(gpath.read_text(encoding="utf-8"))
    result = graph_lint.validate_graph_data(data, vault=vault)
    hpath = vault / graph_html.HTML_FILE
    if hpath.exists():
        result["errors"] += graph_lint.validate_html(hpath.read_text(encoding="utf-8"))
    report = _workspace_root() / "pipeline-workspace/reports" / f"graph-lint-{date.today().isoformat()}.md"
    graph_lint.write_report(result, report)
    for e in result["errors"]:
        print(f"[ERR] {e}")
    for w in result["warnings"]:
        print(f"[warn] {w}")
    print(f"[graph-lint] errors={len(result['errors'])} warnings={len(result['warnings'])} -> {report}")
    if result["errors"]:
        raise SystemExit(2)


def _record_workorder(db, source_id, src_row, staging):
    """据当前 vault 构建 + 落盘 + 记账 work order，返回 (path, wo, output_hash)。
    单一真值，供 workorder（首次）与 reopen（增量重开）共用——保证 reopen 的 registry hash /
    页快照与正常 workorder 完全一致，不漂移。"""
    import state_store
    import workorder
    import json
    import hashlib
    wo = workorder.build_workorder(_vault_dir(), source_id=source_id,
                                   domain=src_row["domain"], staging_dir=staging)
    path = workorder.write_workorder(staging, wo)
    ohash = hashlib.sha256(path.read_bytes()).hexdigest()
    state_store.record_work_order(db, source_id, path=str(path),
                                  registry_hash=wo["registry"]["hash"],
                                  write_scope_json=json.dumps(wo["write_scope"]))
    state_store.record_artifact(db, source_id, kind="workorder", path=str(path), sha256=ohash)
    return path, wo, ohash


def cmd_workorder(args):
    """生成 source 级 ingest work order（spec §9）：windowed → workorder_ready。"""
    import state_store
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
        path, wo, ohash = _record_workorder(db, args.source, src_row, staging)
        state_store.complete_stage(db, args.source, "workorder_ready", output_hash=ohash)
        print(f"[OK] workorder → {path} (registry {wo['registry']['hash'][:12]})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "workorder_ready", error=str(e))
        raise


def cmd_reopen(args):
    """重开一个已收尾来源做增量补充（通用增量发布入口，对任意来源/领域适用）。
    据当前 vault 重建 work order（刷新 registry hash + 页快照，使覆盖保护/registry 校验对当前
    published 状态成立）+ 把状态机从 lint/ingested 重置回 workorder_ready/done。之后照常
    ingest-start → window-start/写页/window-done → ingest-done → lint：lint 只 promote 本轮
    新增/改写的 proposed 页，既有 published 页原样保留（不回滚）。"""
    import state_store
    db = _vault_state_db()
    src_row = state_store.get_source(db, args.source)
    if src_row is None:
        raise SystemExit(f"unknown source: {args.source}; run add-source first")
    staging = _staging_dir(args.source)
    if not (staging / "windows.jsonl").exists():
        raise SystemExit(f"staging 缺 windows.jsonl（{staging}）；预处理产物已清理，"
                         "无法 reopen——先重跑 windows/workorder 再增量")
    try:
        state_store.reopen_source(db, args.source)
    except state_store.InvalidTransition as e:
        raise SystemExit(str(e))
    path, wo, _ohash = _record_workorder(db, args.source, src_row, staging)
    n_assets = _sync_assets(args.source)  # 幂等：确保公式页源图已在 vault，供增量嵌图
    print(f"[OK] reopened '{args.source}' for incremental ingest "
          f"(workorder 重建 → {path}, registry {wo['registry']['hash'][:12]}, synced {n_assets} PNG); "
          "next: ingest-start → window-start/写页/window-done → ingest-done → lint（增量 promote）")


def _page_ranges_for_md(md: str) -> dict:
    """source.md 各 `<!-- page N -->` 页的 char 范围 {page: (start, end)}。
    复用 windowing.page_char_ranges（与 PyMuPDF page block 同一套 marker 扫描真值）。"""
    import windowing
    return windowing.page_char_ranges(md)


def _pages_overlapping_range(page_ranges: dict, start: int, end: int) -> list:
    """与窗口 char 区间 [start,end) 有交叠的页号（含窗起所在页）。"""
    return [page for page, (ps, pe) in sorted(page_ranges.items())
            if not (pe <= start or ps >= end)]


def cmd_show_window(args):
    """打印指定 processing window 的源文本；默认在窗文本前列出本窗覆盖的难页资产头（route B 读图锚点）。

    资产头消除"ingest agent 须自行把 pages.jsonl 难页与页标对上再开图"的漏读风险（不改 windowing，
    显示时即时算本窗覆盖页）。`--plain` 仅打印纯文本（调试用）。"""
    import json
    staging = _staging_dir(args.source)
    md = (staging / "source.md").read_text(encoding="utf-8")
    selected = None
    for line in (staging / "windows.jsonl").read_text(encoding="utf-8").splitlines():
        w = json.loads(line)
        if w["window_id"] == args.window:
            selected = w
            break
    if selected is None:
        raise SystemExit(f"window not found: {args.window}")
    start, end = selected["char_start"], selected["char_end"]
    if selected.get("mode") == "blocks" and not getattr(args, "plain", False):
        # block-aware 窗的结构化头（纯加法；不改下方原窗正文输出语义）。
        hp = selected.get("heading_path", "")
        bids = ",".join(selected.get("block_ids") or [])
        rf = ",".join(selected.get("risk_flags") or [])
        assets = ",".join(selected.get("assets") or [])
        contains = ",".join(selected.get("contains") or [])
        print(f"<!-- window-meta: heading_path={hp} "
              f"pages={selected.get('page_start')}-{selected.get('page_end')} "
              f"contains={contains} block_ids={bids} risk_flags={rf} assets={assets} -->")
    # 默认 ingest 输入只含 risk_flags 标签（最小标记），不含仲裁 reason/audit；--verbose 才打印（debug 用）。
    if getattr(args, "verbose", False):
        dec_path = staging / "arbitration" / "decisions.json"
        if dec_path.exists():
            ps, pe_ = int(selected.get("page_start", 0)), int(selected.get("page_end", 0))
            decs = json.loads(dec_path.read_text(encoding="utf-8")).get("decisions", [])
            rows = [d for d in decs if ps <= int(d.get("page", 0)) <= pe_]
            if rows:
                print("<!-- arbitration (verbose; NOT part of default ingest input) -->")
                for d in rows:
                    print(f"- page={d.get('page')} decision={d.get('decision')} "
                          f"reason={d.get('reason', '')}")
                print("<!-- /arbitration -->")
    if not getattr(args, "plain", False):
        pages = _pages_overlapping_range(_page_ranges_for_md(md), start, end)
        page_meta = {}
        pages_path = staging / "pages.jsonl"
        if pages_path.exists():
            for line in pages_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    obj = json.loads(line)
                    page_meta[int(obj["page"])] = obj
        asset_lines = []
        for page in pages:
            meta = page_meta.get(page)
            if not meta or not meta.get("needs_vision"):
                continue
            png = staging / "assets" / f"p{page:04d}.png"
            tier = meta.get("vision_tier", "?")
            reasons = ",".join(meta.get("needs_vision_reason") or [])
            asset_lines.append(
                f"- page={page} tier={tier} reason={reasons} "
                f"staging={png.as_posix()} vault=![[assets/{args.source}/p{page:04d}.png]]")
        if asset_lines:
            print("<!-- route-b-assets：本窗难页，读图保真（must 必读；nice 至少快速查看；公式写 KaTeX、图嵌原图、表 markdown+源图） -->")
            for ln in asset_lines:
                print(ln)
            print("<!-- /route-b-assets -->")
    print(md[start:end])


def cmd_ingest_start(args):
    """/ingest 开工：取 vault 锁 + stale registry 硬校验 + 推进到 ingesting。
    幂等可恢复：source 已处于 ingesting/running（崩溃/空闲后 resume）时，校验 registry 后
    刷新同源锁；若锁已 stale 则回收重取。锁被**他源**持有才拒（守住跨 agent 互斥）。"""
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

    registry_ok = ingest_guards.registry_fresh(_vault_dir(), wo_row["registry_hash"])
    if held is not None:
        if not registry_ok:
            raise SystemExit("stale registry: disk _registry.yaml != work order hash; re-run workorder")
        if resuming:
            if locks.is_stale(db, scope="vault", ttl_seconds=LOCK_TTL_SECONDS):
                locks.release(db, scope="vault", holder=args.source)
                if not locks.acquire(db, scope="vault", holder=args.source, pid=os.getpid()):
                    held = locks.get(db, scope="vault")
                    raise SystemExit(f"vault lock held by {held['holder'] if held else '?'}")
            else:
                locks.heartbeat(db, scope="vault", holder=args.source)
            print(f"[OK] resumed ingesting '{args.source}'（vault 锁有效）；续 window 循环")
            return
        if locks.is_stale(db, scope="vault", ttl_seconds=LOCK_TTL_SECONDS):
            locks.release(db, scope="vault", holder=args.source)
        else:
            state = f"{src['current_stage']}/{src['current_status']}" if src else "unknown"
            raise SystemExit(f"vault lock held by {args.source}, but source state is {state}")

    if not locks.acquire(db, scope="vault", holder=args.source, pid=os.getpid()):
        held = locks.get(db, scope="vault")
        raise SystemExit(f"vault lock held by {held['holder'] if held else '?'}")
    try:
        if not registry_ok:
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
    _require_vault_lock(db, args.source)
    state_store.complete_stage(db, args.source, "ingesting")
    state_store.start_stage(db, args.source, "ingested")
    state_store.complete_stage(db, args.source, "ingested")
    locks.release(db, scope="vault", holder=args.source)
    print(f"[OK] '{args.source}' ingested (status=proposed); 收尾 lint/promote 见 P6")


def cmd_window_start(args):
    import state_store
    import locks
    db = _vault_state_db()
    _require_vault_lock(db, args.source)
    state_store.start_window(db, args.source, args.window, input_hash=args.hash)
    locks.heartbeat(db, scope="vault", holder=args.source)
    print(f"[OK] window {args.window} running")


def cmd_window_done(args):
    import state_store
    import locks
    import json
    db = _vault_state_db()
    _require_vault_lock(db, args.source)
    # C3：--writes/--proposals 必须是合法 JSON 数组，否则 fail-fast。最常见的坑是 Windows 上
    # 经 `conda run` 调用时双引号被吞，["a.md"] 变成 [a.md]（非法 JSON）——旧行为是静默存损坏值、
    # 收尾 lint 读取时才崩（JSONDecodeError）。这里提前拦截并给出修复指引（改用环境 python 直调）。
    for flag, raw in (("--writes", args.writes), ("--proposals", getattr(args, "proposals", None))):
        if raw in (None, ""):
            continue
        try:
            if not isinstance(json.loads(raw), list):
                raise ValueError("不是 JSON 数组")
        except (ValueError, TypeError) as e:
            raise SystemExit(
                f"{flag} 不是合法 JSON 数组：{raw!r}（{e}）。\n"
                f"Windows 上多半是 `conda run` 吞了双引号——请改用环境 python 直调：\n"
                f'  & "<miniconda>\\envs\\study-kb\\python.exe" scripts\\pipeline.py window-done '
                f"--source {args.source} --window {args.window} {flag} '[\"a.md\",\"b.md\"]'")
    state_store.finish_window(db, args.source, args.window,
                              write_set_json=args.writes, proposal_set_json=args.proposals)
    locks.heartbeat(db, scope="vault", holder=args.source)
    print(f"[OK] window {args.window} finished")


def cmd_window_fail(args):
    import state_store
    db = _vault_state_db()
    _require_vault_lock(db, args.source)
    state_store.fail_window(db, args.source, args.window, error=args.error)
    print(f"[OK] window {args.window} failed: {args.error}")


def cmd_resolve_concept(args):
    """概念归一唯一入口（spec §6）：实时扫描概念页构建 registry，命中合并、未命中新建。不写派生文件。"""
    import concept_store
    db = _vault_state_db()
    # ingest 期建/并概念页是真实 vault 写，与 check-write/snapshot-page 同构：
    # 仅当 --ref-source 正在 ingest 时强制持锁（kb-save 等非 ingest 调用 ref_source 为空，跳过）。
    if args.ref_source and _source_is_running_ingest(db, args.ref_source):
        _require_vault_lock(db, args.ref_source)
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
    if _source_is_running_ingest(db, args.source):
        _require_vault_lock(db, args.source)
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
    if _source_is_running_ingest(db, args.source):
        _require_vault_lock(db, args.source)
    rid = state_store.latest_run_id(db, args.source, "ingesting")
    run_id = f"r{rid}" if rid else "manual"
    manifest = snapshots.take_snapshot(
        _workspace_root() / "pipeline-workspace/snapshots", source_id=args.source,
        run_id=run_id, files=[_vault_dir() / args.path], base_dir=_vault_dir())
    print(f"[OK] snapshot {args.path} -> {manifest}")


def _mineru_risk_violations(source_id, proposed, written):
    """仅当本源 parse_report.selected_backend == 'mineru' 且有风险窗（table/equation/image/
    ocr_low_confidence）时，跑 wiki_gate.lint_risk_traceability；否则返回空（旧 pymupdf/markdown
    源不受新规则影响）。"""
    import json
    import wiki_gate
    staging = _staging_dir(source_id)
    pr = staging / "parse_report.json"
    if not pr.exists():
        return []
    try:
        backend = json.loads(pr.read_text(encoding="utf-8")).get("selected_backend")
    except Exception:
        return []
    if backend != "mineru":
        return []
    risk_block_ids: set = set()
    wf = staging / "windows.jsonl"
    if wf.exists():
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                w = json.loads(line)
                if wiki_gate.RISK_FLAGS & set(w.get("risk_flags") or []):
                    risk_block_ids.update(w.get("block_ids") or [])
    return wiki_gate.lint_risk_traceability(proposed, source_id=source_id,
                                            risk_block_ids=risk_block_ids, written=written)


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
    for s in (wiki_gate.stray_files(vault) if vault.exists() else []):   # C4：杂物软警告（非阻断）
        print(f"[warn] 杂物文件（疑似 Obsidian 点坏链误建，可删）：{s}")
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
    # 有题必有解（软警告非阻断）：自测题块内既无折叠答案也无指向解答的链接 → 提醒补齐
    import page_rules
    for p in proposed:
        for stem in page_rules.unanswered_question_stems(p["body"]):
            print(f"[warn] 自测题缺解答（有题必有解：嵌套折叠答案或链接到解答处）：{p['rel_path']} :: {stem}")
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
    # Spec 2 渐进 risk lint：仅当本源 backend=mineru 且有风险窗时，要求 lesson 可追溯（旧源不受影响）。
    violations += _mineru_risk_violations(args.source, proposed, written_by[args.source])
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
        try:
            _refresh_skill_backlog(db)  # 自动 harvest：把刚记的失败聚进 skill backlog（best-effort）
        except Exception:
            pass  # harvest 绝不打断 lint 收尾
        raise SystemExit(f"lint failed: {len(violations)} violations -> {queue}")
    # 通过：promote + 重建派生 + 日志 + 清快照
    n = wiki_gate.promote(vault, proposed)
    registry, errors, _w = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        state_store.fail_stage(db, args.source, "lint", error="; ".join(errors))
        raise SystemExit("registry corrupt: " + "; ".join(errors))
    concept_store.write_registry(vault, registry)
    concept_store.remove_stale_aliases(vault)
    wiki_gate.write_index(vault)
    # 派生阅读层（publish-isolated，不阻断发布）：发布/registry/aliases/index 已成功，再建知识图谱
    # （graph-data + 力导向 HTML）；任何失败只 warn、保留旧产物，不改 lint 退出码。
    try:
        _rebuild_graph_artifacts(vault)
    except Exception as e:
        print(f"[WARN] 知识图谱重建失败：{e}；已保留旧 graph-data/html，发布不受影响，可手动跑 rebuild-graph")
    try:
        wiki_gate.write_quiz_index(vault)
    except Exception as e:
        print(f"[WARN] 自测题库索引重建失败：{e}；发布不受影响，可手动跑 rebuild-quiz")
    try:
        _props = wiki_gate.collect_propositions(vault)
        wiki_gate.write_propositions_index(vault)
        for d in wiki_gate.duplicate_proposition_names(_props):
            print(f"[warn] 命题重名（名字即锚点，域内应唯一）：{d}")
    except Exception as e:
        print(f"[WARN] 命题总表重建失败：{e}；发布不受影响，可手动跑 rebuild-propositions")
    # 本源历史 lint 失败报告已过时（本轮已通过），清理避免误导；不动其他 source 的报告
    for stale in sorted((vault / "Review-Queue").glob(f"{args.source}-lint-*.md")):
        stale.unlink()
        print(f"[clean] 清理已过时的 lint 失败报告：{stale.relative_to(vault).as_posix()}")
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


def _refresh_skill_backlog(db):
    """零-LLM：扫 review_proposals 按 kind 聚类 → 写 backlog.yaml，返回 backlog 列表。
    供 skill-mine 显式调用 + lint 收尾自动 harvest 复用。"""
    import state_store
    import yaml as _yaml
    proposals = state_store.list_review_proposals(db) if Path(db).exists() else []
    clusters: dict[str, dict] = {}
    for p in proposals:
        c = clusters.setdefault(p["kind"], {"signature": p["kind"], "count": 0,
                                            "sources": [], "sample_reason": p["reason"]})
        c["count"] += 1
        if p["source_id"] not in c["sources"]:
            c["sources"].append(p["source_id"])
    backlog = sorted(clusters.values(), key=lambda c: (-c["count"], c["signature"]))
    out = _workspace_root() / "pipeline-workspace/skill-evolution/backlog.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_yaml.safe_dump({"backlog": backlog}, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return backlog


def cmd_skill_mine(args):
    """skill 自进化·零-LLM：扫已落库的失败信号(review_proposals) → 按 kind 聚类成 backlog.yaml。"""
    backlog = _refresh_skill_backlog(_vault_state_db())
    out = _workspace_root() / "pipeline-workspace/skill-evolution/backlog.yaml"
    print(f"[OK] skill-mine: {len(backlog)} signatures -> {out}")


def _skill_gate_check(base):
    """gate 核心（skill-gate 与 skill-adopt 共用）：候选只许动 skill 两树(白名单) + 过 pytest。
    返回 (ok, reason)。"""
    import subprocess as _sp
    tracked = _sp.run(["git", "diff", "--name-only", base], capture_output=True, text=True, encoding="utf-8")
    if tracked.returncode != 0:
        return False, f"git diff 失败（不在 git 仓？）: {tracked.stderr.strip()}"
    untracked = _sp.run(["git", "ls-files", "--others", "--exclude-standard"],
                        capture_output=True, text=True, encoding="utf-8")
    changed = [ln.strip() for ln in (tracked.stdout + untracked.stdout).splitlines() if ln.strip()]
    allowed = (".claude/skills/", ".agents/skills/")
    outside = sorted(f for f in changed if not f.startswith(allowed))
    if outside:
        return False, (f"gate-integrity: 候选越界改动 {outside}"
                       f"（只许动 .claude/skills 与 .agents/skills，防游戏自己的门）")
    pt = _sp.run([sys.executable, "-m", "pytest", "tests", "-q"])
    if pt.returncode != 0:
        return False, "pytest: 候选未过测试门（含双树对等）"
    return True, "gate-integrity + pytest 全绿"


def cmd_skill_gate(args):
    """skill 自进化·零-LLM 确定性门：候选只许动 skill 两树（gate-integrity，防游戏自己的门）
    + 过 pytest（含 T2 双树对等等全部守卫）。任一不过即 exit 1。"""
    ok, msg = _skill_gate_check(args.base)
    if not ok:
        print(f"[skill-gate] DENY {msg}")
        raise SystemExit(1)
    print(f"[skill-gate] PASS candidate={args.candidate}: {msg}")


def _append_audit(base_dir, row):
    """skill 自进化 audit（append-only，落 gitignored 工作区）。"""
    import json as _json
    from datetime import datetime, timezone
    base_dir.mkdir(parents=True, exist_ok=True)
    row = {**row, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(base_dir / "audit.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(_json.dumps(row, ensure_ascii=False) + "\n")


def cmd_skill_stage(args):
    """skill 自进化·零-LLM：把候选改动(skill 树 diff)登记为待审提案 + audit；线上(已提交状态)不动。"""
    import subprocess as _sp
    diff = _sp.run(["git", "diff", args.base, "--", ".claude/skills", ".agents/skills"],
                   capture_output=True, text=True, encoding="utf-8")
    base_dir = _workspace_root() / "pipeline-workspace/skill-evolution"
    cand_dir = base_dir / "candidates" / args.candidate
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "proposal.diff").write_text(diff.stdout, encoding="utf-8")
    _append_audit(base_dir, {"candidate": args.candidate, "event": "staged"})
    print(f"[skill-stage] candidate={args.candidate} 已登记提案 -> {cand_dir / 'proposal.diff'}"
          f"（线上不动，待人 skill-adopt）")


def cmd_skill_adopt(args):
    """skill 自进化·人触发：重跑 gate 兜底 → 把候选合并进双树(commit) → audit。gate 不过则拒绝、不提交。"""
    import subprocess as _sp
    ok, msg = _skill_gate_check(args.base)
    if not ok:
        print(f"[skill-adopt] DENY: 候选未过 gate，拒绝采纳：{msg}")
        raise SystemExit(1)
    _sp.run(["git", "add", ".claude/skills", ".agents/skills"])
    commit = _sp.run(["git", "commit", "-q", "-m", f"skill-evolve: adopt candidate {args.candidate}"],
                     capture_output=True, text=True, encoding="utf-8")
    if commit.returncode != 0:
        print(f"[skill-adopt] git commit 失败: {commit.stdout}{commit.stderr}")
        raise SystemExit(1)
    sha = _sp.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, encoding="utf-8").stdout.strip()
    _append_audit(_workspace_root() / "pipeline-workspace/skill-evolution",
                  {"candidate": args.candidate, "event": "adopted", "commit": sha})
    print(f"[skill-adopt] candidate={args.candidate} 已采纳并提交双树 commit={sha}")


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
    pfp = subparsers.add_parser("profile", help="逐页 profile + needs_vision 标记")
    pfp.add_argument("--source", required=True, help="source_id")
    winp = subparsers.add_parser("windows", help="生成确定性 processing windows")
    winp.add_argument("--source", required=True, help="source_id")
    winp.add_argument("--dev-bypass", action="store_true",
                      help="跳过双审分歧闸门构窗（dev 用；产物降级，不可用于 strict 验收）")
    scp = subparsers.add_parser("source-convert", help="转成 staging/<source>/ 全 artifact（按 backend 选后端）")
    scp.add_argument("--source", required=True, help="source_id")
    scp.add_argument("--force", action="store_true",
                     help="强行用 PyMuPDF 转扫描件（绕过 scanned_source fail-closed，慎用）")
    scp.add_argument("--backend", choices=["auto", "pymupdf", "mineru"], default="auto",
                     help="选后端：auto（默认）/ pymupdf 强制轻量 / mineru 强制结构化（未装则 fail-closed）")
    scp.add_argument("--mineru-policy", choices=["conservative", "aggressive"], default="conservative",
                     help="auto 路由策略：conservative（默认，密集 born-digital 仍 PyMuPDF）/ aggressive（密集也走 MinerU）")
    saup = subparsers.add_parser("source-audit",
                                 help="PDF 双审：跑 MinerU structural review 复核 PyMuPDF + 写 reconciliation.json")
    saup.add_argument("--source", required=True, help="source_id")
    saup.add_argument("--strict", action="store_true",
                      help="MinerU 复读必需但不可用/失败 → 非零退出（生产/严格验收，不静默回退 PyMuPDF）")
    arst = subparsers.add_parser("arbitration-status",
                                 help="打印双审分歧仲裁队列状态（候选/已决策/pending；skill 据此自动仲裁）")
    arst.add_argument("--source", required=True, help="source_id")
    arap = subparsers.add_parser("arbitration-apply",
                                 help="物化 decisions.json（render 补图+标记 / ignore / needs_human）；须在 windows 前跑")
    arap.add_argument("--source", required=True, help="source_id")
    arrp = subparsers.add_parser("arbitration-resolve",
                                 help="把某 needs_human 页改成 render|ignore（人工/agent 闭环，reason 必填）")
    arrp.add_argument("--source", required=True, help="source_id")
    arrp.add_argument("--page", required=True, type=int, help="needs_human 页号")
    arrp.add_argument("--decision", required=True, choices=["render", "ignore"], help="改判为 render|ignore")
    arrp.add_argument("--reason", required=True, help="裁决理由（必填，记入 audit）")
    pep = subparsers.add_parser("preflight-eval",
                                help="L4：确定性验收 staging 预处理产物（零-LLM，可 CI 化）")
    pep.add_argument("--source", required=True, help="source_id")
    pep.add_argument("--strict", action="store_true", help="任一 high/fail → 非零退出码")
    pep.add_argument("--json", default=None, help="报告输出路径（默认 staging/<src>/preflight_eval.json）")
    subparsers.add_parser("init-vault", help="建 wiki/ 脚手架 + overview/log/purpose 种子（幂等）")
    subparsers.add_parser("apply-obsidian-style",
                          help="落地学习库观感 CSS snippet + merge appearance.json（幂等，纯配置层零内容改动）")
    subparsers.add_parser("rebuild-registry", help="从概念页 frontmatter 重建 _registry.yaml + aliases.md")
    subparsers.add_parser("rebuild-graph",
                          help="重建知识图谱：graph-data + 力导向交互 HTML（零 LLM，手动 fail-hard；点击节点跳 Obsidian）")
    subparsers.add_parser("graph-lint",
                          help="校验 graph-data.generated.json(+HTML)：fail-hard 非零退出，warn-only 不阻断")
    subparsers.add_parser("rebuild-quiz",
                          help="重建自测题库索引 quiz-index.generated.md（零 LLM；published 页 [!question] 题干+回链）")
    subparsers.add_parser("rebuild-propositions",
                          help="重建命题总表 propositions.generated.md（零 LLM；published 页具名命题+回链）")
    wop = subparsers.add_parser("workorder", help="生成 source 级 ingest work order")
    wop.add_argument("--source", required=True)
    rop = subparsers.add_parser("reopen", help="重开已收尾来源做增量补充（重建 workorder + 状态机回 workorder_ready）")
    rop.add_argument("--source", required=True)
    sap = subparsers.add_parser("sync-assets", help="把本源 staging 难页 PNG 同步进 wiki/assets/<src>/")
    sap.add_argument("--source", required=True)
    swp = subparsers.add_parser("show-window", help="打印指定 window 的源文本（默认含难页资产头）")
    swp.add_argument("--source", required=True)
    swp.add_argument("--window", required=True)
    swp.add_argument("--plain", action="store_true", help="只打印窗口文本，不打印 route B 难页资产头（调试用）")
    swp.add_argument("--verbose", action="store_true",
                     help="额外打印本窗页的仲裁裁决/理由（debug 用；默认 ingest 输入不含 reason/audit）")
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
    subparsers.add_parser("skill-mine",
                          help="skill 自进化·零-LLM：扫失败信号(review_proposals) → backlog.yaml")
    sgp = subparsers.add_parser("skill-gate",
                                help="skill 自进化·零-LLM 门：候选只许动 skill 树(gate-integrity)+过 pytest")
    sgp.add_argument("--candidate", required=True)
    sgp.add_argument("--base", default="HEAD", help="diff 基线 ref（默认 HEAD）")
    ssp = subparsers.add_parser("skill-stage",
                                help="skill 自进化·零-LLM：登记候选提案(diff)+audit，线上不动")
    ssp.add_argument("--candidate", required=True)
    ssp.add_argument("--base", default="HEAD", help="diff 基线 ref（默认 HEAD）")
    sadp = subparsers.add_parser("skill-adopt",
                                 help="skill 自进化·人触发：重跑 gate 兜底 + 合并候选进双树(commit)")
    sadp.add_argument("--candidate", required=True)
    sadp.add_argument("--base", default="HEAD", help="diff 基线 ref（默认 HEAD）")

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
        'source-audit': cmd_source_audit,
        'arbitration-status': cmd_arbitration_status,
        'arbitration-apply': cmd_arbitration_apply,
        'arbitration-resolve': cmd_arbitration_resolve,
        'windows': cmd_windows,
        'preflight-eval': cmd_preflight_eval,
        'fail': cmd_fail,
        'init-vault': cmd_init_vault,
        'apply-obsidian-style': cmd_apply_obsidian_style,
        'rebuild-registry': cmd_rebuild_registry,
        'rebuild-graph': cmd_rebuild_graph,
        'rebuild-quiz': cmd_rebuild_quiz,
        'rebuild-propositions': cmd_rebuild_propositions,
        'graph-lint': cmd_graph_lint,
        'workorder': cmd_workorder,
        'reopen': cmd_reopen,
        'sync-assets': cmd_sync_assets,
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
        'skill-mine': cmd_skill_mine,
        'skill-gate': cmd_skill_gate,
        'skill-stage': cmd_skill_stage,
        'skill-adopt': cmd_skill_adopt,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
