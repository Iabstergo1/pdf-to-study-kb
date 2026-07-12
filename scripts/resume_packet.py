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


def _extract_resume_block(digest_text: str) -> str | None:
    """digest 顶部 `## RESUME` 块：从该标题行到下一个 `## `（契约见 write-pages.md U7）。"""
    lines = digest_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^## RESUME\b", ln):
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


def build_resume_packet(*, db_path, staging_dir, repo_root, source_id,
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
    if not digest_file.exists():
        problems.append(f"ingesting 状态但找不到 digest（{digest_file}）——外部记忆缺失，需人工核对")
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

    wins = [json.loads(ln) for ln in windows_file.read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    win_ids = [w["window_id"] for w in wins]
    status_by_id = {r["window_id"]: r["status"]
                    for r in state_store.window_progress(db_path, source_id)}
    for wid, st in status_by_id.items():
        if wid not in set(win_ids):
            problems.append(f"账本窗口 {wid}（{st}）不在 windows.jsonl——状态矛盾，需人工核对")
    finished = [w for w in win_ids if status_by_id.get(w) == "finished"]
    running = [w for w in win_ids if status_by_id.get(w) == "running"]
    failed = [w for w in win_ids if status_by_id.get(w) == "failed"]
    if len(running) > 1:
        problems.append(f"账本同时存在多个 running 窗口：{', '.join(running)}——恢复目标不唯一，需人工核对")
    next_win = next((w for w in wins if status_by_id.get(w["window_id"]) != "finished"), None)

    resume_block = _extract_resume_block(digest_file.read_text(encoding="utf-8"))
    if resume_block is None:
        if re.search(r"(?m)^## DONE\b", digest_file.read_text(encoding="utf-8")):
            problems.append("digest 顶部是 ## DONE（本源已宣告写完）——若账本仍有未完窗口先人工核对；"
                            "若只剩收尾：阶段 E 综合层 → pipeline.py ingest-done → lint，无需 packet")
        else:
            problems.append("digest 缺 ## RESUME 块（恢复锚点缺失，需人工补写后重试）")
    else:
        mentioned = set(_WIN_TOKEN.findall(resume_block))
        unknown = sorted(mentioned - set(win_ids))
        if unknown:
            problems.append(f"RESUME 指向不存在的窗口：{', '.join(unknown)}")
        if next_win is not None and next_win["window_id"] not in mentioned:
            problems.append(
                f"RESUME 未提及账本判定的下一窗 {next_win['window_id']}——digest 过期或状态矛盾"
                f"（RESUME 提及：{', '.join(sorted(mentioned)) or '无'}）；"
                "人工把 digest 顶部 RESUME 更新到真实下一窗后重试")
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
    if next_win is None:
        a("# 所有窗口已完成：先做阶段 E 综合层（更新 overview + 按需 topic/comparison/synthesis，"
          "并进某窗 --writes），再收尾：")
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
        a("# 之后按 windows.jsonl 顺序逐窗推进；每窗后刷新 digest 顶部 ## RESUME")
    a("=== /RESUME_PACKET v1 ===")
    return "\n".join(out)
