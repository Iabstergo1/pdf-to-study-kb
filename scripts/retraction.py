"""retract-source 文件层（spec：证据先行，删除在后）。

动机（2026-07-17 mysql 事件）：下架动作曾直接清掉三张账本表 + 30 个页面，审计结论的
原始证据随处置消失，事后连"当初判的对不对"都无法复核。本模块把撤库固化为确定性顺序：
分类（只读）→ 导出证据包 → 核验哈希完整 → 才允许精确删除。DB 读写与派生层重建留在
pipeline.py 编排（state_store 导出/清账、五个 rebuild 回调），本模块零 SQL、零兄弟依赖。

分类规则（保守：只删"确定独占本源"的页）：
- managed_by=human → keep_human（覆盖保护同一原则：绝不动人工页，即使独占/在台账）
- type=source 且 source_id 匹配 → delete（含域下错位重复的台账页）
- type=lesson 且 source 字段匹配 → delete
- source_refs 只含本源 → delete；含本源也含他源 → keep_shared（报告，人工去引）
- 无任何 frontmatter 归属、但在本源 write ledger → delete（台账孤儿）
- 其余（他源/无关）→ 不入任何清单
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage

_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets", ".obsidian"}
_DERIVED = {"index.generated.md", "aliases.md", "quiz-index.generated.md",
            "propositions.generated.md", "graph-data.generated.json",
            "knowledge-graph.generated.html"}


def _ref_sources(meta: dict) -> set[str]:
    out: set[str] = set()
    for ref in (meta.get("source_refs") or []):
        if isinstance(ref, dict) and ref.get("source"):
            out.add(str(ref["source"]))
    return out


def classify_pages(vault, source_id: str, *, written_paths=frozenset()) -> dict:
    """全库只读扫描 → {delete, keep_shared, keep_human}，各项为 [{path, type, reason}]。"""
    vault = Path(vault)
    written = {str(p).replace("\\", "/") for p in written_paths}
    res: dict[str, list[dict]] = {"delete": [], "keep_shared": [], "keep_human": []}
    if not vault.exists():
        return res
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, _body = mdpage.read_page(f)
        ptype = str(meta.get("type", ""))
        refs = _ref_sources(meta)
        owns = (
            (ptype == "source" and str(meta.get("source_id", "")) == source_id)
            or (ptype == "lesson" and str(meta.get("source", "")) == source_id)
            or (refs == {source_id})
            or (not refs and not meta.get("source") and ptype != "source" and rel in written)
        )
        shared = source_id in refs and refs != {source_id}
        if not owns and not shared:
            continue
        entry = {"path": rel, "type": ptype}
        if str(meta.get("managed_by", "")) == "human":
            entry["reason"] = "managed_by=human，永不删除；如需撤须人工处理"
            res["keep_human"].append(entry)
        elif shared:
            entry["reason"] = f"source_refs 含他源（{'、'.join(sorted(refs - {source_id}))}），只报告不删；人工去引"
            res["keep_shared"].append(entry)
        else:
            entry["reason"] = "独占本源"
            res["delete"].append(entry)
    return res


def export_evidence(vault, dest, delete_paths, *, db_rows: dict, plan: dict) -> dict:
    """把待删页全文 + DB 账本行 + 撤库计划复制进证据包并核验（fail-closed）。

    结构：dest/pages/<rel>（逐字节副本）、db/<table>.json、plan.json、manifest.json。
    **manifest 覆盖全部 payload**（P1-4：页面 + 每个 DB 导出 + plan 都记 sha256/bytes——
    只核页面时篡改 db/*.json 或 plan 仍会"核验通过"）。manifest 最后写（覆盖其余一切）。
    页面复制后立即重算副本哈希比对，任一不一致 → RuntimeError，绝不在证据不完整时返回成功。
    返回 {pages, tables}。"""
    vault = Path(vault)
    dest = Path(dest)
    (dest / "pages").mkdir(parents=True, exist_ok=True)
    (dest / "db").mkdir(parents=True, exist_ok=True)
    manifest_pages: list[dict] = []
    for rel in delete_paths:
        src = vault / rel
        raw = src.read_bytes()
        out = dest / "pages" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        h_src = hashlib.sha256(raw).hexdigest()
        h_copy = hashlib.sha256(out.read_bytes()).hexdigest()
        if h_src != h_copy:
            raise RuntimeError(f"evidence copy hash mismatch: {rel}（证据不完整，中止，未删除任何页）")
        manifest_pages.append({"path": rel, "sha256": h_src, "bytes": len(raw)})
    files: list[str] = []
    for table, rows in sorted(db_rows.items()):
        (dest / "db" / f"{table}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        files.append(f"db/{table}.json")
    (dest / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append("plan.json")
    manifest_files = []
    for rel in files:
        raw = (dest / rel).read_bytes()
        manifest_files.append({"path": rel, "sha256": hashlib.sha256(raw).hexdigest(),
                               "bytes": len(raw)})
    (dest / "manifest.json").write_text(
        json.dumps({"pages": manifest_pages, "files": manifest_files, "tables": sorted(db_rows)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"pages": len(manifest_pages), "tables": len(db_rows)}


def verify_evidence(dest) -> list[str]:
    """证据包完整性核验（删除前最后一道）：页面 + DB 导出 + plan 全部重算哈希 vs manifest；
    缺失/失配报路径，清单重复报 `dup:<path>`，清单外文件报 `extra:<path>`（P1-4：完整性
    包含"没有多余"——证据目录被塞入未记录文件同样是污染）。空列表 = 完整。"""
    dest = Path(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    bad: list[str] = []
    page_entries = manifest.get("pages", [])
    file_entries = manifest.get("files", [])
    for rel, seen in (("pages", page_entries), ("files", file_entries)):
        paths = [m["path"] for m in seen]
        for dup in sorted({p for p in paths if paths.count(p) > 1}):
            bad.append(f"dup:{dup}")
    for m in page_entries:
        copy = dest / "pages" / m["path"]
        if not copy.exists() or hashlib.sha256(copy.read_bytes()).hexdigest() != m["sha256"]:
            bad.append(m["path"])
    for m in file_entries:
        f = dest / m["path"]
        if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != m["sha256"]:
            bad.append(m["path"])
    listed = {f"pages/{m['path']}" for m in page_entries} | {m["path"] for m in file_entries}
    listed.add("manifest.json")
    for f in sorted(dest.rglob("*")):
        if f.is_file():
            rel = f.relative_to(dest).as_posix()
            if rel not in listed:
                bad.append(f"extra:{rel}")
    return bad


def verify_sources_match(vault, dest) -> list[str]:
    """删除前的源侧对账（P1-3 配套）：vault 里每个待删页当前哈希 vs manifest 导出时哈希。
    失配/已消失 → 报路径——证据导出后源文件发生漂移时，必须在第一项删除前整体中止。"""
    vault = Path(vault)
    dest = Path(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    bad: list[str] = []
    for m in manifest.get("pages", []):
        src = vault / m["path"]
        if not src.exists() or hashlib.sha256(src.read_bytes()).hexdigest() != m["sha256"]:
            bad.append(m["path"])
    return bad


def delete_pages(vault, paths) -> int:
    """精确删除列出的页（只删清单内文件），随后剪掉 domains/ 下因此变空的目录。"""
    vault = Path(vault)
    n = 0
    parents: set[Path] = set()
    for rel in paths:
        f = vault / rel
        if f.exists():
            f.unlink()
            n += 1
            parents.add(f.parent)
    # 只在 domains/ 下剪空目录（顶层 sources/topics 等即使空也保留：init-vault 布局）
    for p in sorted(parents, key=lambda x: len(x.parts), reverse=True):
        cur = p
        while cur != vault and vault in cur.parents and "domains" in cur.relative_to(vault).parts:
            if cur.exists() and not any(cur.iterdir()):
                cur.rmdir()
                cur = cur.parent
            else:
                break
    return n


def append_log(vault, source_id: str, n_pages: int, evidence_rel: str, date_iso: str) -> None:
    """向 wiki/log.md 追加撤库审计行（与 promote 行同格式层级）。"""
    log = Path(vault) / "log.md"
    line = (f"\n## [{date_iso}] retract | {source_id} | removed {n_pages} pages "
            f"(evidence: {evidence_rel})\n")
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
