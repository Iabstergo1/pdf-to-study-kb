#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI（新架构：确定性预处理 + 收尾门禁 + 状态跟踪，零 LLM）

预处理：add-source → profile → source-convert → windows → workorder
/ingest 会话支撑：ingest-start/done、window-start/done/fail、show-window、
                resolve-concept、check-write、snapshot-page
增量重开：reopen（已收尾来源重建 workorder + 状态机回 workorder_ready 做增量补充）
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
    import json
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    # 混入 profiler 版本：确定性启发式升级即失效缓存（对任意来源通用）。
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest() + ":" + source_profile.PROFILER_VERSION
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
    """source-convert：raw → staging/<source>/source.md + 难页 PNG。"""
    import state_store
    import source_convert
    import source_profile
    import hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    # 整本扫描件 fail-closed：route B 不适合让 LLM 临场 OCR 上千整页图；停在 profile，需 OCR route。
    # 少数扫描页混在普通 PDF（比值<0.8）不触发，仍按 route B 处理。--force 可强行渲染（慎用）。
    if not getattr(args, "force", False):
        import json as _json
        pj = _staging_dir(args.source) / "pages.jsonl"
        if pj.exists():
            _pages = [_json.loads(l) for l in pj.read_text(encoding="utf-8").splitlines() if l.strip()]
            if source_profile.is_scanned_source(_pages):
                raise SystemExit(
                    "scanned_source / requires_ocr：本源近乎整本扫描件（≥80% 零文本+图像页），route B 不适用"
                    "——不让 LLM 临场 OCR 上千整页图。预处理停在 profile；请走 OCR route。"
                    "少数扫描页混在普通 PDF 不受影响。确要强行渲染：加 --force。")
    # 版本化缓存键（单一真值，与 dispatcher 同源）：raw sha + PROFILER_VERSION（连带难页 PNG）
    # + ARTIFACT_VERSION（blocks/parse_report 形状）。
    ihash = source_convert.converted_input_hash(raw)
    if not state_store.should_run_stage(db, args.source, "converted", input_hash=ihash):
        print("[skip] converted up-to-date")
        return
    state_store.start_stage(db, args.source, "converted", input_hash=ihash)
    try:
        out = _staging_dir(args.source)
        res = source_convert.convert(raw, out_dir=out, fmt=src_row["format"])
        # pages.jsonl 已由 profile 阶段产出；convert 内部用同一批纯函数复算 needs_vision，结果一致
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.record_artifact(db, args.source, kind="chapters", path=res["chapters_path"], sha256=res["chapters_sha"])
        state_store.record_artifact(db, args.source, kind="blocks", path=res["blocks_path"], sha256=res["blocks_sha"])
        state_store.record_artifact(db, args.source, kind="parse_report", path=res["parse_report_path"], sha256=res["parse_report_sha"])
        n_assets = _sync_assets(args.source)  # 难页 PNG 入 vault（公式嵌图依赖；任意源通用）
        state_store.complete_stage(db, args.source, "converted", output_hash=res["sha256"])
        print(f"[OK] converted → {res['source_md']} (needs_vision pages: {res['needs_vision_pages']}; "
              f"synced {n_assets} PNG → vault assets)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "converted", error=str(e))
        raise


def _sync_assets(source_id: str) -> int:
    """把 staging/<src>/assets 下的难页 PNG 复制进 wiki/assets/<src>/（确定性、幂等）。
    公式 lesson/concept 嵌入 `![[assets/<src>/pXXXX.png]]` 需图在 vault 内才不断链——
    对任意有 needs_vision 页的来源通用（不止某本书）。返回本次复制/更新的文件数。"""
    import shutil
    import hashlib
    staging_assets = _staging_dir(source_id) / "assets"
    if not staging_assets.exists():
        return 0
    dst_dir = _vault_dir() / "assets" / source_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for png in sorted(staging_assets.glob("*.png")):
        dst = dst_dir / png.name
        if (not dst.exists()) or (hashlib.sha256(dst.read_bytes()).hexdigest()
                                  != hashlib.sha256(png.read_bytes()).hexdigest()):
            shutil.copy2(png, dst)
            n += 1
    return n


def cmd_sync_assets(args):
    """把本源 staging 难页 PNG 同步进 vault（供公式页嵌图）。预处理/重开会自动调用，亦可单独跑。"""
    n = _sync_assets(args.source)
    print(f"[OK] synced {n} source-page PNG(s) -> wiki/assets/{args.source}/")


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
    # 有 blocks → 以 blocks.jsonl 为切窗依据（block-aware）；无 → 退回 source.md char 窗。
    if blocks_path.exists():
        basis = blocks_path.read_bytes()
        build = lambda: windowing.build_windows_from_blocks(source_artifacts.read_blocks(blocks_path))
    else:
        basis = source_md.read_text(encoding="utf-8").encode("utf-8")
        build = lambda: windowing.build_windows(source_md.read_text(encoding="utf-8"))
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
    """source.md 各 `<!-- page N -->` 页的 char 范围 {page: (start, end)}（纯函数，显示时即时算）。"""
    import re
    markers = [(int(m.group(1)), m.start()) for m in re.finditer(r"<!-- page (\d+) -->", md)]
    ranges = {}
    for i, (page, start) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else len(md)
        ranges[page] = (start, end)
    return ranges


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
    db = _vault_state_db()
    _require_vault_lock(db, args.source)
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
    tracked = _sp.run(["git", "diff", "--name-only", base], capture_output=True, text=True)
    if tracked.returncode != 0:
        return False, f"git diff 失败（不在 git 仓？）: {tracked.stderr.strip()}"
    untracked = _sp.run(["git", "ls-files", "--others", "--exclude-standard"],
                        capture_output=True, text=True)
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
                   capture_output=True, text=True)
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
                     capture_output=True, text=True)
    if commit.returncode != 0:
        print(f"[skill-adopt] git commit 失败: {commit.stdout}{commit.stderr}")
        raise SystemExit(1)
    sha = _sp.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
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
    for name, help_text in [("profile", "逐页 profile + needs_vision 标记"),
                            ("windows", "生成确定性 processing windows")]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--source", required=True, help="source_id")
    scp = subparsers.add_parser("source-convert", help="转成 staging/<source>/source.md + 难页 PNG")
    scp.add_argument("--source", required=True, help="source_id")
    scp.add_argument("--force", action="store_true",
                     help="强行转换扫描件（绕过 scanned_source fail-closed，慎用）")
    subparsers.add_parser("init-vault", help="建 wiki/ 脚手架 + overview/log/purpose 种子（幂等）")
    subparsers.add_parser("apply-obsidian-style",
                          help="落地学习库观感 CSS snippet + merge appearance.json（幂等，纯配置层零内容改动）")
    subparsers.add_parser("rebuild-registry", help="从概念页 frontmatter 重建 _registry.yaml + aliases.md")
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
        'windows': cmd_windows,
        'fail': cmd_fail,
        'init-vault': cmd_init_vault,
        'apply-obsidian-style': cmd_apply_obsidian_style,
        'rebuild-registry': cmd_rebuild_registry,
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
