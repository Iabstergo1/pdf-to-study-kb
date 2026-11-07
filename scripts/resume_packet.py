"""RESUME_PACKET v1：中断 ingest 的结构化恢复包（零 LLM，确定性组装）。

定位：恢复体验加固——把"新会话自己从 chapters/digest/workorder 拼状态"改成"系统喂给一个
带稳定分区的纯文本包"（`resume-ingest.ps1` 原样注入 headless prompt，人也可直接阅读）。
它**不是**新的安全边界：物理保障仍是末端 fail-closed lint + two-phase publish + 快照回滚。
fail-closed：状态或产物矛盾时拒绝输出"看起来能继续"的残缺包，并给出修复指引。

三个正交事实，不混成一个 manifest：
- write boundary：workorder 允许写到哪里（write_scope）；
- completed writes：账本（ingest_progress）里已真实完成的窗口；
- next window context：接下来读哪个窗（账本判定，digest RESUME 须与之一致，否则拒绝出包）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import locks
import ingest_guards
import source_artifacts as sa
import state_store

MARK_START = "<!-- resume-critical:start -->"
MARK_END = "<!-- resume-critical:end -->"
CONTRACT_REL = "skills/ingest/references/write-pages.md"
_WIN_TOKEN = re.compile(r"\bw\d{4}\b")


class ResumePacketError(Exception):
    """fail-closed：列出全部矛盾/缺失，不输出残缺包。"""

    def __init__(self, problems, source_id=""):
        self.problems = list(problems)
        lines = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(
            "resume-packet fail-closed（拒绝输出残缺包）：\n" + lines +
            f"\n修复后重试：python scripts/pipeline.py next --source {source_id} --resume-packet")


def _extract_resume_block(digest_text: str, heading="RESUME") -> str | None:
    """digest 顶部 `## RESUME` 块：从该标题行到下一个 `## `（契约见 write-pages.md U7）。"""
    lines = digest_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^## {heading}\b", ln):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()


def _unchanged_pages(vault: Path, wo: dict) -> bool:
    """缺摘要时，只有完整工单快照与当前可写页面逐一一致才允许首次初始化。"""
    groups = [wo.get("concept_pages_snapshot"), wo.get("other_pages_snapshot")]
    if not vault.is_dir() or any(not isinstance(group, list) for group in groups):
        return False
    expected = {}
    for entry in [*groups[0], *groups[1]]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("sha256"):
            return False
        if ingest_guards.in_write_scope(entry["path"], wo["write_scope"]):
            expected[entry["path"]] = entry["sha256"]
    actual = {p.relative_to(vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in vault.rglob("*.md")
              if ingest_guards.in_write_scope(p.relative_to(vault).as_posix(), wo["write_scope"])}
    return actual == expected and ingest_guards.registry_fresh(vault, wo["registry"]["hash"])


def _window_position(db_path, staging, source_id):
    wins = sa.read_jsonl(staging / "windows.jsonl")
    win_ids = [w["window_id"] for w in wins]
    states = {r["window_id"]: r["status"] for r in state_store.window_progress(db_path, source_id)}
    problems = [f"账本窗口 {wid}（{st}）不在 windows.jsonl——状态矛盾，需人工核对"
                for wid, st in states.items() if wid not in set(win_ids)]
    finished = [w for w in win_ids if states.get(w) == "finished"]
    running = [w for w in win_ids if states.get(w) == "running"]
    failed = [w for w in win_ids if states.get(w) == "failed"]
    if len(running) > 1:
        problems.append(f"账本同时存在多个 running 窗口：{', '.join(running)}——恢复目标不唯一，需人工核对")
    if problems:
        raise ResumePacketError(problems, source_id)
    next_win = next((w for w in wins if states.get(w["window_id"]) != "finished"), None)
    return wins, states, finished, running, failed, next_win


def next_window_info(*, db_path, staging_dir, source_id):
    """紧凑的账本定位信息，供 U7 更新摘要；不验证摘要、不授权恢复。"""
    src = state_store.get_source(db_path, source_id)
    if src is None:
        raise ResumePacketError([f"unknown source: {source_id}"], source_id)
    staging = Path(staging_dir)
    missing = [f"staging 缺 {name}" for name in ("windows.jsonl", "source.md") if not (staging / name).exists()]
    if missing:
        raise ResumePacketError(missing, source_id)
    wins, _, finished, _, _, window = _window_position(db_path, staging, source_id)
    next_window = None
    if window is not None:
        md = (staging / "source.md").read_text(encoding="utf-8")
        digest = hashlib.sha256(md[window["char_start"]:window["char_end"]].encode("utf-8")).hexdigest()[:12]
        next_window = {"window_id": window["window_id"], "hash": f"sha256:{digest}",
                       "page_start": window.get("page_start"), "page_end": window.get("page_end"),
                       "chapter_title": window.get("chapter_title", "")}
    return {"source_id": source_id, "stage": src["current_stage"], "status": src["current_status"],
            "total": len(wins), "finished": len(finished), "next_window": next_window}


def build_resume_packet(*, db_path, staging_dir, repo_root, vault_dir, source_id,
                        lock_ttl_seconds: int = 1800) -> str:
    staging = Path(staging_dir)
    repo_root = Path(repo_root)

    src = state_store.get_source(db_path, source_id)
    if src is None:
        raise ResumePacketError([f"unknown source: {source_id}"], source_id)
    if not (src["current_stage"] == "ingesting" and src["current_status"] == "running"):
        raise ResumePacketError(
            [f"source 不在进行中 ingest（当前 {src['current_stage']}/{src['current_status']}；"
             "packet 只服务 stage=ingesting/status=running 的中断恢复，其余状态按 next 提示走）"],
            source_id)

    problems: list[str] = []
    windows_file = staging / "windows.jsonl"
    source_md = staging / "source.md"
    digest_file = staging / "digest.md"
    for p, label in ((windows_file, "windows.jsonl"), (source_md, "source.md")):
        if not p.exists():
            problems.append(f"staging 缺 {label}（{p}）")
    initializing = not digest_file.exists()
    wo_row = state_store.get_work_order(db_path, source_id)
    wo_file = Path(wo_row["path"]) if wo_row is not None else staging / "workorder.yaml"
    if wo_row is None:
        problems.append("workorder 记录缺失（work_orders 表无此 source；先跑 pipeline.py workorder）")
    if not wo_file.exists():
        problems.append(f"workorder 记录指向的 YAML 缺失（{wo_file}）")

    wo_data = None
    wo_scope = None
    if wo_row is not None and wo_file.exists():
        try:
            wo_data = yaml.safe_load(wo_file.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as e:
            problems.append(f"workorder YAML 无法读取：{e}")
        if isinstance(wo_data, dict):
            try:
                db_scope = json.loads(wo_row["write_scope_json"] or "[]")
            except (TypeError, ValueError) as e:
                problems.append(f"work_orders.write_scope_json 损坏：{e}")
                db_scope = None
            wo_scope = wo_data.get("write_scope")
            if not isinstance(wo_scope, list):
                problems.append("workorder YAML 缺合法 write_scope 列表")
            elif db_scope is not None and wo_scope != db_scope:
                problems.append("workorder YAML write_scope 与 SQLite 镜像不一致——写入边界真值冲突，需重建 workorder")
            yaml_registry = wo_data.get("registry")
            yaml_registry_hash = yaml_registry.get("hash") if isinstance(yaml_registry, dict) else None
            if yaml_registry_hash != wo_row["registry_hash"]:
                problems.append("workorder YAML registry.hash 与 SQLite 镜像不一致——需重建 workorder")
        elif wo_data is not None:
            problems.append("workorder YAML 根节点不是 mapping")

    contract_claude = repo_root / ".claude" / CONTRACT_REL
    contract_codex = repo_root / ".agents" / CONTRACT_REL
    critical = None
    if not contract_claude.exists():
        problems.append(f"写作契约缺失（{contract_claude}）")
    if not contract_codex.exists():
        problems.append(f"写作契约缺失（{contract_codex}）")
    if contract_claude.exists() and contract_codex.exists():
        claude_bytes = contract_claude.read_bytes()
        codex_bytes = contract_codex.read_bytes()
        if claude_bytes != codex_bytes:
            problems.append("双树 write-pages.md 字节不对等——恢复契约真值冲突，先修复 parity")
    if contract_claude.exists():
        text = contract_claude.read_text(encoding="utf-8")
        if MARK_START not in text or MARK_END not in text:
            problems.append(f"写作契约缺 resume-critical 标记块（需含 {MARK_START} … {MARK_END}）")
        else:
            critical = text.split(MARK_START, 1)[1].split(MARK_END, 1)[0].strip()
            if not critical:
                problems.append("resume-critical 标记块为空")
    if problems:
        raise ResumePacketError(problems, source_id)

    wins, status_by_id, finished, running, failed, next_win = _window_position(db_path, staging, source_id)
    win_ids = [w["window_id"] for w in wins]

    if initializing:
        if (status_by_id or state_store.window_read_ids(db_path, source_id)
                or not _unchanged_pages(Path(vault_dir), wo_data)):
            problems.append(f"digest 缺失（{digest_file}），且不能确认尚未开始阅读或写作——需人工核对，不能重建历史摘要")
        if not (staging / "chapters.json").exists():
            problems.append("首次初始化缺 chapters.json，不能建立全书内容路由")
        if next_win is None:
            problems.append("首次初始化缺阅读窗口")
        resume_block = "digest_status=not_created（无阅读/处理记录，可写页面与工单快照一致；需先初始化摘要）"
    else:
        digest_text = digest_file.read_text(encoding="utf-8")
        resume_block = _extract_resume_block(digest_text)
        if resume_block is None and next_win is None:
            resume_block = _extract_resume_block(digest_text, "DONE")
    if not initializing and resume_block is None:
        if re.search(r"(?m)^## DONE\b", digest_file.read_text(encoding="utf-8")):
            problems.append("digest 顶部是 ## DONE（本源已宣告写完），但账本仍有未完窗口，需人工核对")
        else:
            problems.append("digest 缺 ## RESUME 块（恢复锚点缺失，需人工补写后重试）")
    elif not initializing:
        mentioned = set(_WIN_TOKEN.findall(resume_block))
        unknown = sorted(mentioned - set(win_ids))
        if unknown:
            problems.append(f"RESUME 指向不存在的窗口：{', '.join(unknown)}")
        if next_win is not None and next_win["window_id"] not in mentioned:
            current_round = state_store.round_anchor(db_path, source_id)
            reads = state_store.window_reads_in_round(db_path, source_id, current_round)
            completed = [dict(window_id=row["window_id"], round=row["round"],
                              read_in_current_round=row["window_id"] in reads,
                              input_hash=row["input_hash"], write_set_json=row["write_set_json"])
                         for row in state_store.window_states(db_path, source_id)
                         if row["status"] == "finished"]
            problems.append(
                f"RESUME 未提及账本判定的下一窗 {next_win['window_id']}——digest 过期或状态矛盾"
                f"（RESUME 提及：{', '.join(sorted(mentioned)) or '无'}）；"
                "执行入库的智能体须先核对已完成窗口并补齐 digest/RESUME 后重试；"
                "不能只替换窗口编号，无法解释记录时交由人工核对\n"
                f"核对依据（仍拒绝出包，不授权继续写作）：current_round={current_round}; "
                f"completed_windows={json.dumps(completed, ensure_ascii=False)}")
    if problems:
        raise ResumePacketError(problems, source_id)

    md = source_md.read_text(encoding="utf-8")
    lock_row = locks.get(db_path, scope="vault")
    if lock_row is None:
        lock_line = "lock=none（ingest-start 会重取）"
    else:
        stale = locks.is_stale(db_path, scope="vault", ttl_seconds=lock_ttl_seconds)
        lock_line = (f"lock=held holder={lock_row['holder']} "
                     f"heartbeat={lock_row['heartbeat_at']} stale={'yes' if stale else 'no'}")
    contract_bytes = contract_claude.read_bytes()
    contract_hash = hashlib.sha256(contract_bytes).hexdigest()[:12]

    out: list[str] = []
    a = out.append
    a("=== RESUME_PACKET v1 ===")
    a("[source]")
    a(f"source_id={source_id}")
    a(f"domain={src['domain']}")
    a(f"stage={src['current_stage']}")
    a(f"status={src['current_status']}")
    a(f"phase={'initialize' if initializing else 'finish' if next_win is None else 'resume'}")
    a(lock_line)
    a("")
    a("[windows]")
    a(f"total={len(win_ids)}")
    a(f"finished={len(finished)}" + (f" last={','.join(finished[-3:])}" if finished else ""))
    a(f"running={','.join(running) or 'none'}")
    a(f"failed={','.join(failed) or 'none'}")
    nhash = None
    if next_win is None:
        a("next_window=none（全部窗口已完成）")
        rows = state_store.window_states(db_path, source_id)
        current_round = state_store.round_anchor(db_path, source_id)
        written = set()
        for row in rows:
            if row["status"] == "finished" and row.get("round") == current_round:
                try:
                    paths = json.loads(row["write_set_json"] or "[]")
                    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                        raise ValueError("write_set must be a list of paths")
                    written.update(p.replace("\\", "/") for p in paths)
                except (ValueError, TypeError) as error:
                    raise ResumePacketError([f"窗口 {row['window_id']} 写集损坏：{error}"], source_id)
        import mdpage
        vault = staging.parents[2] / "wiki"
        source_pages = []
        for page in sorted(vault.rglob("*.md")):
            meta, _ = mdpage.read_page(page)
            if meta.get("status") in ("proposed", "published") and (
                    meta.get("source_id") == source_id or meta.get("source") == source_id
                    or any(ref.get("source") == source_id for ref in (meta.get("source_refs") or []) if isinstance(ref, dict))):
                source_pages.append(page.relative_to(vault).as_posix())
        a(f"round_written_pages={json.dumps(sorted(written), ensure_ascii=False)}")
        a(f"source_pages={json.dumps(source_pages, ensure_ascii=False)}")
        a("# round_written_pages 只认本轮已完成窗口的账；source_pages 是来源归属，不能代替记账。")
        if finished:
            last = next(row for row in rows
                        if row["window_id"] == finished[-1])
            a(f"last_finished_window={last['window_id']}")
            a(f"last_finished_writes={last['write_set_json'] or '[]'}")
            a(f"last_finished_proposals={last['proposal_set_json'] or '[]'}")
    else:
        nid = next_win["window_id"]
        nhash = hashlib.sha256(
            md[next_win["char_start"]:next_win["char_end"]].encode("utf-8")).hexdigest()[:12]
        a(f"next_window={nid}")
        a(f"next_window_hash=sha256:{nhash}")
        a(f"next_window_meta=heading_path={next_win.get('heading_path', '')}"
          f" pages={next_win.get('page_start', '?')}-{next_win.get('page_end', '?')}"
          f" chapter={next_win.get('chapter_title', '')}")
    a("")
    a("[digest-resume]")
    a(resume_block)
    a("")
    a("[workorder]")
    a(f"path={wo_file}")
    a(f"sha256={hashlib.sha256(wo_file.read_bytes()).hexdigest()[:12]}")
    a(f"registry_hash={wo_row['registry_hash']}")
    a("write_scope:")
    for s in wo_scope:
        a(f"- {s}")
    a("")
    a("[writing-contract]")
    a(f"claude={contract_claude}")
    a(f"codex={contract_codex}")
    a(f"sha256={contract_hash} （双树字节对等）——动笔前必须完整重读全文，[resume-critical] 只是恢复摘要")
    a("")
    a("[resume-critical]")
    a(critical)
    a("")
    a("[next-commands]")
    a("# 环境：study-kb 解释器 + PYTHONUTF8=1")
    a(f"python scripts/pipeline.py ingest-start --source {source_id}   # 幂等：恢复/重取锁")
    if initializing:
        a(f"# 先读取 {staging / 'chapters.json'}、workorder.yaml 与 ingest/references/content-routing.md。")
        a(f"# 由模型按原文建立全书理解和逐章路由表，写入 {digest_file}；")
        a(f"# 在 ## RESUME 如实记录尚无已完成窗口、下一窗 {next_win['window_id']}，然后才执行下面的读窗写作。")
        a("# 这是首次初始化，不是丢失摘要的恢复；不要重新生成工单，也不要声称读过未读的窗口。")
    if next_win is None:
        a("# 所有窗口已完成：先做阶段 E 综合层（更新 overview + 按需 topic/comparison/synthesis，"
          "并进某窗 --writes），再收尾：")
        a("# window-done 会替换写集；保留 last_finished_writes/proposals，追加本次新增路径再记账，不重启该窗。")
        a(f"python scripts/pipeline.py ingest-done --source {source_id}")
        a(f"python scripts/pipeline.py lint --source {source_id}")
    else:
        nid = next_win["window_id"]
        a(f"python scripts/pipeline.py window-start --source {source_id} --window {nid}"
          f" --hash sha256:{nhash}")
        a(f"python scripts/pipeline.py show-window --source {source_id} --window {nid}")
        a("# 读窗+写页（新页先 check-write；改既有页先 snapshot-page）→ page_rules 自检 → 记账：")
        a(f"python scripts/pipeline.py window-done --source {source_id} --window {nid}"
          " --writes '[\"<vault-rel-path>\", ...]'")
        a(f"# 每窗 U6 后用 next --source {source_id} --json 获取下一窗 ID/hash，完成 U7 digest 更新；")
        a("# 仍有窗口时刷新 ## RESUME，全部完成改为 ## DONE，注明综合层/发布待办；再校验恢复包。")
    a("=== /RESUME_PACKET v1 ===")
    return "\n".join(out)
