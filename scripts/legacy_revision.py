"""Auditable revisions for pages inherited through ``adopt-vault``.

This module deliberately does not manufacture ingest work orders or mutate the
main state schema.  Authorization lives in an immutable sidecar operation; a
mutable candidate is linted in a complete vault overlay and only then switched
into the live vault under the existing vault lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

import concept_store
import evidence_fs
import graph_analysis
import graph_data
import graph_html
import graph_lint
import graph_model
import locks
import mdpage
import vault_adoption
import wiki_gate


CONTRACT_VERSION = 1
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OPERATION_ID = re.compile(r"[0-9a-f]{20}")
_LOG_ANCHOR = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] revise-adopted \| "
    r"(?P<source>[A-Za-z0-9][A-Za-z0-9._-]*) \| operation "
    r"(?P<operation>[0-9a-f]{20}) post (?P<post>[0-9a-f]{12})$")
_IMMUTABLE_FRONTMATTER = (
    "type", "canonical_id", "canonical_name", "aliases", "scope", "domain",
    "page_path", "managed_by", "source_refs",
)
_DERIVED = (
    "concepts/_registry.yaml",
    "index.generated.md",
    "graph-data.generated.json",
    "knowledge-graph.generated.html",
    "quiz-index.generated.md",
    "propositions.generated.md",
)
_EXCLUDED_TOP = {".obsidian", "Review-Queue", "_meta", "assets"}
_PAGE_REQUIRED_KEYS = {"path", "reason", "evidence", "citation_removals"}
_PAGE_OPTIONAL_KEYS = {"frontmatter_updates"}
_AUTHORIZATION_PAGE_KEYS = {
    "citation_plan", "evidence", "immutable_frontmatter",
    "immutable_frontmatter_sha256", "path", "pre_sha256", "pre_size", "reason",
}
_AUTHORIZATION_PAGE_OPTIONAL_KEYS = {"frontmatter_updates", "immutable_frontmatter_pre"}


class LegacyRevisionError(evidence_fs.EvidenceBoundaryError):
    """The adopted-revision contract was not satisfied."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fault_point(_point: str) -> None:
    """No-op seam used by recovery tests; production callers never configure it."""


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value) -> bytes:
    return evidence_fs.json_bytes(value)


def _load_canonical_json(path: Path, label: str):
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyRevisionError(f"{label} is unreadable or invalid JSON: {path}") from exc
    if raw != _json_bytes(value):
        raise LegacyRevisionError(f"{label} is not canonical JSON: {path}")
    return value, raw


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(raw)
        if tmp.read_bytes() != raw:
            raise LegacyRevisionError(f"failed to verify temporary write: {path}")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _assert_direct_path(path: Path, root: Path, label: str) -> None:
    evidence_fs.assert_direct_contained(
        path, root, label, error=LegacyRevisionError)


def _safe_rel(value: object) -> str:
    if not isinstance(value, str):
        raise LegacyRevisionError("page path must be a string")
    rel = value.replace("\\", "/")
    parts = rel.split("/")
    if (not rel.endswith(".md") or rel.startswith("/")
            or re.match(r"^[A-Za-z]:", rel)
            or any(part in ("", ".", "..") for part in parts)):
        raise LegacyRevisionError(f"unsafe page path: {value!r}")
    if parts[0] in _EXCLUDED_TOP or rel.startswith("sources/") or rel in {
            "overview.md", "log.md", "index.generated.md", "quiz-index.generated.md",
            "propositions.generated.md"}:
        raise LegacyRevisionError(f"page path is outside the legacy revision surface: {rel}")
    return rel


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise LegacyRevisionError("valid_until must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LegacyRevisionError("valid_until must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LegacyRevisionError("valid_until must include a timezone")
    return parsed.astimezone(timezone.utc)


def _assert_not_expired(valid_until: str) -> None:
    if _now() > _parse_datetime(valid_until):
        raise LegacyRevisionError(f"revision authorization expired at {valid_until}")


def _citation_from_evidence(entry: dict) -> dict:
    citation = dict(entry["citation"])
    citation["supports"] = entry["supports"]
    return citation


def _validate_frontmatter_updates(fu, rel: str) -> dict:
    """校验 page 级受控 frontmatter 声明（解锁 S-05 的受控 aliases 变更）。

    声明的键必须属于 _IMMUTABLE_FRONTMATTER；当前只定义 aliases.remove 语义。
    缺省时请求规范化字典不出现该键（约束 A：request_sha256 向后兼容）。
    """
    if not isinstance(fu, dict) or not fu:
        raise LegacyRevisionError(
            f"frontmatter_updates must be a non-empty mapping: {rel}")
    unknown = sorted(set(fu) - set(_IMMUTABLE_FRONTMATTER))
    if unknown:
        raise LegacyRevisionError(
            f"frontmatter_updates keys must belong to immutable frontmatter: "
            f"{', '.join(unknown)}: {rel}")
    if set(fu) != {"aliases"}:
        raise LegacyRevisionError(
            f"frontmatter_updates only supports aliases for now: {rel}")
    aliases = fu["aliases"]
    if not isinstance(aliases, dict) or set(aliases) != {"remove"}:
        raise LegacyRevisionError(
            f"frontmatter_updates.aliases must declare remove: {rel}")
    removes = aliases["remove"]
    if not isinstance(removes, list) or not removes:
        raise LegacyRevisionError(
            f"frontmatter_updates.aliases.remove must be a non-empty list: {rel}")
    if any(not isinstance(a, str) or not a.strip() for a in removes):
        raise LegacyRevisionError(
            f"frontmatter_updates.aliases.remove entries must be non-empty strings: {rel}")
    seen = set()
    clean_removes = []
    for alias in removes:
        alias = alias.strip()
        if alias in seen:
            raise LegacyRevisionError(
                f"duplicate alias in frontmatter_updates.aliases.remove: {alias}: {rel}")
        seen.add(alias)
        clean_removes.append(alias)
    return {"aliases": {"remove": clean_removes}}


def _validate_request(path: Path, source: str) -> tuple[dict, bytes, str]:
    if not _SOURCE_ID.fullmatch(source):
        raise LegacyRevisionError("invalid source id")
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LegacyRevisionError(f"revision request is unreadable: {path}") from exc
    if not isinstance(data, dict):
        raise LegacyRevisionError("revision request must be a YAML mapping")
    allowed_top = {"version", "source_id", "valid_until", "mode", "pages",
                   "revert_operation"}
    unknown = sorted(set(data) - allowed_top)
    if unknown:
        raise LegacyRevisionError(f"revision request has unknown fields: {unknown}")
    if data.get("version") != CONTRACT_VERSION:
        raise LegacyRevisionError(f"revision request version must be {CONTRACT_VERSION}")
    if data.get("source_id") != source:
        raise LegacyRevisionError("request source_id does not match --source")
    valid_until = data.get("valid_until")
    _parse_datetime(valid_until)
    mode = data.get("mode")
    if mode not in {"edit", "revert"}:
        raise LegacyRevisionError("request mode must be edit or revert")
    revert_operation = data.get("revert_operation")
    if mode == "revert":
        if not isinstance(revert_operation, str) or not _OPERATION_ID.fullmatch(revert_operation):
            raise LegacyRevisionError("revert mode requires a valid revert_operation")
    elif revert_operation is not None:
        raise LegacyRevisionError("revert_operation is only valid in revert mode")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise LegacyRevisionError("revision request pages must be a non-empty list")
    normal_pages = []
    seen = set()
    for page in pages:
        if not isinstance(page, dict):
            raise LegacyRevisionError("each request page must be a mapping")
        missing_page = sorted(_PAGE_REQUIRED_KEYS - set(page))
        unknown_page = sorted(set(page) - _PAGE_REQUIRED_KEYS - _PAGE_OPTIONAL_KEYS)
        if missing_page or unknown_page:
            raise LegacyRevisionError(
                "each request page must contain path/reason/evidence/citation_removals"
                " (optional: frontmatter_updates)"
                + (f"; missing: {', '.join(missing_page)}" if missing_page else "")
                + (f"; unknown: {', '.join(unknown_page)}" if unknown_page else ""))
        rel = _safe_rel(page["path"])
        if rel in seen:
            raise LegacyRevisionError(f"duplicate request page path: {rel}")
        seen.add(rel)
        reason = page["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise LegacyRevisionError(f"page reason must not be empty: {rel}")
        clean_fu = None
        if page.get("frontmatter_updates") is not None:
            clean_fu = _validate_frontmatter_updates(page["frontmatter_updates"], rel)
        evidence = page["evidence"]
        if not isinstance(evidence, list):
            raise LegacyRevisionError(f"page evidence must be a list: {rel}")
        clean_evidence = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"citation", "supports"}:
                raise LegacyRevisionError(f"invalid evidence entry: {rel}")
            citation = item["citation"]
            if not isinstance(citation, dict):
                raise LegacyRevisionError(f"citation must be a mapping: {rel}")
            required = {"source", "title", "accessed_on"}
            if not required.issubset(citation) or set(citation) - (required | {"url", "locator"}):
                raise LegacyRevisionError(f"citation fields invalid: {rel}")
            if any(not isinstance(citation[k], str) or not citation[k].strip()
                   for k in required):
                raise LegacyRevisionError(f"citation fields must not be empty: {rel}")
            if "url" in citation:
                if not isinstance(citation["url"], str) or not citation["url"].strip():
                    raise LegacyRevisionError(f"citation url must not be empty: {rel}")
                if not citation["url"].startswith("https://"):
                    raise LegacyRevisionError(f"citation URL must use https: {rel}")
            try:
                date.fromisoformat(citation["accessed_on"])
            except ValueError as exc:
                raise LegacyRevisionError(f"citation accessed_on must be an ISO date: {rel}") from exc
            if not isinstance(item["supports"], str) or not item["supports"].strip():
                raise LegacyRevisionError(f"evidence supports must not be empty: {rel}")
            clean_evidence.append({"citation": {k: citation[k] for k in sorted(citation)},
                                   "supports": item["supports"].strip()})
        removals = page["citation_removals"]
        if not isinstance(removals, list):
            raise LegacyRevisionError(f"citation_removals must be a list: {rel}")
        clean_removals = []
        removal_seen = set()
        for removal in removals:
            if not isinstance(removal, dict) or set(removal) != {"sha256", "reason"}:
                raise LegacyRevisionError(f"invalid citation removal: {rel}")
            digest = str(removal["sha256"]).lower()
            if digest in removal_seen:
                raise LegacyRevisionError(
                    f"duplicate citation removal SHA-256: {rel}: {digest}")
            if not _SHA256.fullmatch(digest):
                hint = ""
                if not isinstance(removal["sha256"], str):
                    # 未加引号的全数字摘要会被 YAML 解析成整数，前导零随之丢失，
                    # 报"格式非法"会把作者引向摘要本身，而真正要改的是 YAML 引号。
                    hint = (" — YAML parsed this value as "
                            f"{type(removal['sha256']).__name__}, not a string; "
                            "quote the sha256 value so an all-digit digest keeps "
                            "its leading zeros")
                raise LegacyRevisionError(
                    f"invalid citation removal SHA-256: {rel}: {digest!r}{hint}")
            if not isinstance(removal["reason"], str) or not removal["reason"].strip():
                raise LegacyRevisionError(f"citation removal reason must not be empty: {rel}")
            removal_seen.add(digest)
            clean_removals.append({"sha256": digest, "reason": removal["reason"].strip()})
        clean_page = {
            "path": rel, "reason": reason.strip(), "evidence": clean_evidence,
            "citation_removals": sorted(clean_removals, key=lambda x: x["sha256"]),
        }
        if clean_fu is not None:
            clean_page["frontmatter_updates"] = clean_fu
        normal_pages.append(clean_page)
    normal = {"version": CONTRACT_VERSION, "source_id": source,
              "valid_until": valid_until, "mode": mode,
              "pages": sorted(normal_pages, key=lambda x: x["path"])}
    if mode == "revert":
        normal["revert_operation"] = revert_operation
    raw = _json_bytes(normal)
    return normal, raw, _sha_bytes(raw)


def _sources(meta: dict) -> set[str]:
    out = set()
    direct = meta.get("source") or meta.get("source_id")
    if isinstance(direct, str):
        out.add(direct)
    for ref in meta.get("source_refs") or []:
        if isinstance(ref, str):
            out.add(ref.split(":", 1)[0])
        elif isinstance(ref, dict) and isinstance(ref.get("source"), str):
            out.add(ref["source"])
    return out


#: 内容页遍历的顶层排除集。**从 _EXCLUDED_TOP 派生，不手抄第二份**——
#: 先前这里是一份字面量副本，与 pipeline.review_content_pages 各写各的，两处
#: 的 log.md 与 generated 规则实际已经分叉（见 prepush-audit-2026-08-08 F4）。
_CONTENT_EXCLUDED_TOP = _EXCLUDED_TOP | {"concepts", "graph", "sources"}


def _content_page_paths(vault: Path) -> list[str]:
    """内容页遍历——**全项目唯一实现**（`pipeline.review_content_pages` 直接转调本函数）。

    排除顶层 Review-Queue/_meta/concepts/graph/sources/.obsidian/assets、
    `*.generated.md` 与顶层 log.md；overview.md 计入（归属可算、修订面仍由 _safe_rel 排除）。

    两条规则刻意用精确形式而非宽松匹配：`log.md` 只排**顶层**（它是 vault 级台账，
    域内同名页不该被静默吞掉）；generated 认 `.generated.md` **后缀**而非 "generated"
    子串（否则 `topics/generated-notes.md` 这类正当内容页会被无声排除出分母）。
    """
    out = []
    for path in vault.rglob("*.md"):
        rel = path.relative_to(vault).as_posix()
        if rel == "log.md" or rel.endswith(".generated.md"):
            continue
        if rel.split("/", 1)[0] in _CONTENT_EXCLUDED_TOP:
            continue
        out.append(rel)
    return sorted(out)


def _owned_pages(vault: Path, source: str) -> list[str]:
    """射程判据（R-08）：页属于来源 ⟺ 该页 source_refs 含该 source_id。

    source_refs 在 _IMMUTABLE_FRONTMATTER 里、frontmatter_updates 只支持
    aliases.remove，因此没有任何流水线路径能经一次修订扩大未来射程（测试钉死）。
    """
    owned = []
    for rel in _content_page_paths(vault):
        try:
            meta, _body = mdpage.read_page(vault / rel)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if source in _sources(meta):
            owned.append(rel)
    return sorted(owned)


def _scope_digest(vault: Path, source: str) -> str:
    """scope digest：**签发期**该来源拥有的全部页按 (path, pre_sha256) 排序的规范形哈希。

    采纳来源不用它（identity 保持 adoption_manifest_sha256 逐字节不变）。

    **射程说明（别把它当运行期锚）**：本值只在签发时计算并写进 identity，因此参与
    operation_id 派生；恢复/提交路径**不会重算后比对**。它记录的是"签发那一刻该来源
    拥有哪些页、内容如何"，是溯源快照，不是漂移检测器。刻意不做全量重算比对——
    那会让任一无关归属页的正常编辑都炸掉操作，在人工编辑跨越 prepare→commit 的
    真实节奏下过于脆。

    真正的运行期保证由两处提供：`_assert_scope_still_owns`（切换前复验授权页仍属本源）
    与 per-page `pre_sha256` + 完整 overlay lint。
    """
    entries = [(rel, _sha_bytes((vault / rel).read_bytes()))
               for rel in _owned_pages(vault, source)]
    return _sha_bytes(_json_bytes(entries))


def _assert_scope_still_owns(context: dict, authorization: dict) -> None:
    """切换前复验：授权里的每一页**当下仍属于本来源**。

    签发时 `_build_authorization` 查过一次 `rel in scope_paths`，但 prepare→commit 之间
    按设计要跨人工编辑候选的时间；期间某页可能不再带本来源的 `source_refs`（采纳来源
    则是被移出 manifest）。此前无人复验，`scope_digest` 也只是签发期快照（见 `_scope_digest`）。

    只在**尚未越过不可回头点**的阶段调用（prepare 与首次提交），与 `_assert_not_expired`
    的放置一致：`committing` 之后再拒会把半完成的切换锁死，而恢复合同刻意不制造死锁。

    这条不会在没有正当编辑的场景下逼人写东西（核心约束⑦）：页确实不再属于本来源时，
    停下来就是正确结果，出口是从当前 live 新建请求，而不是补内容。
    """
    scope = set(context["scope_paths"])
    lost = sorted(p["path"] for p in authorization["pages"] if p["path"] not in scope)
    if lost:
        kind = context.get("kind")
        why = ("不再属于该 adoption manifest" if kind == "adoption"
               else f"的 source_refs 已不含 {authorization['source_id']}")
        raise LegacyRevisionError(
            f"authorized pages left the source scope since signing ({kind}): "
            f"{lost} {why}；请按当前 live 新建 mode: edit 请求")


def _frontmatter_identity(meta: dict) -> dict:
    return {key: meta.get(key) for key in _IMMUTABLE_FRONTMATTER}


def _registered_source_ids(context) -> set[str]:
    """已登记来源：状态库 sources 表 ∪ vault 里的来源页。

    两处都要看，因为它们记录的是不同的登记方式：走完整 ingest 的来源进状态库；
    只以 vault 内一张来源页登记的（本地资料包、跨库复用等）不进状态库，但对
    "这条 citation 指向的是不是本库真实存在的来源"而言同样有效。

    来源页按 frontmatter 判定（``type: source``），不按文件名——``sources/`` 下允许放
    非来源页，用文件名会把它们静默当成合法 source_id。
    """
    ids = set()
    try:
        import state_store
        for row in state_store.status_rows(context["db"]):
            ids.add(row["source_id"])
    except Exception as exc:
        raise LegacyRevisionError(f"cannot read registered sources: {exc}") from exc
    sources_dir = context["vault"] / "sources"
    if sources_dir.is_dir():
        for page in sorted(sources_dir.glob("*.md")):
            try:
                meta, _body = mdpage.read_page(page)
            except Exception:
                continue
            if meta.get("type") != "source":
                continue
            source_id = meta.get("source_id") or page.stem
            if isinstance(source_id, str) and _SOURCE_ID.fullmatch(source_id):
                ids.add(source_id)
    return ids


def _assert_registered_no_url_sources(context, item: dict, rel: str) -> None:
    """任务 2 语义校验：无 url 的 evidence citation，其 source 必须是已登记 source_id。

    结构校验（url 可选 + https 前缀）在 _validate_request；本函数在 _build_authorization
    执行，因为那里才有 workspace 上下文（状态库 + vault sources 目录）。
    """
    missing = [ev["citation"]["source"] for ev in item["evidence"]
               if "url" not in ev["citation"]]
    if not missing:
        return
    registered = _registered_source_ids(context)
    for source in missing:
        if source not in registered:
            raise LegacyRevisionError(
                f"citation without url must name a registered source_id; "
                f"got {source!r}; registered: {sorted(registered)}: {rel}")


def _artifact_anchor(workspace: Path, source: str, snapshot: dict, *,
                     kind: str, expected_kind: str) -> None:
    """非采纳来源的「产出页定义」防篡改锚：产出 artifact 字节必须与账本 sha 一致。"""
    artifacts = snapshot.get("artifacts") or []
    rows = [a for a in artifacts if a["kind"] == expected_kind]
    if len(rows) != 1:
        raise LegacyRevisionError(
            f"source {source} {expected_kind} artifact missing or duplicated (count={len(rows)})")
    path = Path(rows[0]["path"])
    if not path.is_file() or _sha_bytes(path.read_bytes()) != rows[0]["sha256"]:
        raise LegacyRevisionError(
            f"source {source} {expected_kind} artifact identity drift "
            f"(ledger sha={rows[0]['sha256'][:12]}…, file mismatch): {kind}")


def _window_statuses(db: Path, source: str) -> list[str]:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [str(row[0]) for row in con.execute(
            "SELECT status FROM ingest_progress WHERE source_id=? ORDER BY id",
            (source,)).fetchall()]
    finally:
        con.close()


def _ingest_context(workspace: Path, source: str, snapshot: dict) -> dict:
    """摄取来源（format=pdf）的「已完成、可安全修订」判定（R-08 Q1，实测三源形态）。"""
    workspace = Path(workspace).resolve()
    vault = workspace / "wiki"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    src = snapshot["source"]
    stages = snapshot.get("stages") or []
    ledgers = snapshot.get("ledgers") or {}
    actual_source = (src["domain"], src["format"], src["current_stage"],
                     src["current_status"])
    if actual_source != (src["domain"], "pdf", "lint", "published"):
        raise LegacyRevisionError(
            f"source {source} is not in settled ingest state: expected "
            f"(domain, pdf, lint, published), actual {actual_source}")
    last = stages[-1] if stages else None
    if not last or (last["stage"], last["status"]) != ("lint", "done"):
        raise LegacyRevisionError(
            f"source {source} ingest not settled: last stage run is {last!r}, "
            f"expected ('lint', 'done')")
    if last.get("input_hash") != last.get("output_hash"):
        raise LegacyRevisionError(
            f"source {source} lint stage hashes diverge: input={last.get('input_hash')} "
            f"output={last.get('output_hash')}")
    _artifact_anchor(workspace, source, snapshot, kind="ingest",
                     expected_kind="workorder")
    if ledgers.get("work_orders") != 1:
        raise LegacyRevisionError(
            f"source {source} work_orders count {ledgers.get('work_orders')} != 1 "
            f"(completed work order expected)")
    statuses = _window_statuses(db, source)
    in_progress = sorted({s for s in statuses if s in ("running", "failed")})
    if in_progress:
        raise LegacyRevisionError(
            f"source {source} has in-progress ingest windows: {in_progress}")
    if not statuses:
        raise LegacyRevisionError(
            f"source {source} has no ingest windows (ingest incomplete)")
    if ledgers.get("window_reads") != len(statuses):
        raise LegacyRevisionError(
            f"source {source} window_reads {ledgers.get('window_reads')} != windows "
            f"{len(statuses)}")
    return {"workspace": workspace, "vault": vault, "db": db,
            "snapshot": snapshot, "source": source}


def _reuse_context(workspace: Path, source: str, snapshot: dict) -> dict:
    """复用来源（format=external-vault-reuse）的「已完成、可安全修订」判定。"""
    workspace = Path(workspace).resolve()
    vault = workspace / "wiki"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    src = snapshot["source"]
    stages = snapshot.get("stages") or []
    ledgers = snapshot.get("ledgers") or {}
    actual_source = (src["domain"], src["format"], src["current_stage"],
                     src["current_status"])
    if actual_source != (src["domain"], "external-vault-reuse", "reused", "published"):
        raise LegacyRevisionError(
            f"source {source} is not in settled reuse state: expected "
            f"(domain, external-vault-reuse, reused, published), actual {actual_source}")
    last = stages[-1] if stages else None
    if not last or (last["stage"], last["status"]) != ("reused", "done"):
        raise LegacyRevisionError(
            f"source {source} reuse not settled: last stage run is {last!r}, "
            f"expected ('reused', 'done')")
    if last.get("input_hash") != last.get("output_hash"):
        raise LegacyRevisionError(
            f"source {source} reuse stage hashes diverge: input={last.get('input_hash')} "
            f"output={last.get('output_hash')}")
    _artifact_anchor(workspace, source, snapshot, kind="reuse",
                     expected_kind="reuse_evidence")
    if any(ledgers.values()):
        raise LegacyRevisionError(
            f"source {source} reuse ingest ledgers must remain zero: {ledgers}")
    return {"workspace": workspace, "vault": vault, "db": db,
            "snapshot": snapshot, "source": source}


def _revision_context(workspace: Path, source: str, *,
                      allowed_lock_holder: str | None = None,
                      lock_ttl_seconds: int = 1800) -> dict:
    """按来源种类的修订准入（R-08）。采纳路径保留今天全部断言与报错；其余按种类。"""
    workspace = Path(workspace).resolve()
    vault = workspace / "wiki"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    if not vault.is_dir() or not db.is_file():
        raise LegacyRevisionError("wiki vault or state database is missing")
    evidence_dir = workspace / "pipeline-workspace" / "adoptions" / source
    if (evidence_dir / "manifest.json").is_file():
        context = _adoption_context(workspace, source,
                                    allowed_lock_holder=allowed_lock_holder,
                                    lock_ttl_seconds=lock_ttl_seconds)
        kind = "adoption"
    else:
        snapshot = vault_adoption._state_snapshot(db, source)
        if snapshot and snapshot.get("schema_missing"):
            raise LegacyRevisionError(
                f"source {source} state schema incomplete: "
                f"{', '.join(snapshot['schema_missing'])}")
        evidence_fs.reject_lock(snapshot, lock_ttl_seconds, allowed_lock_holder,
                                command="revise-adopted", error=LegacyRevisionError)
        if not snapshot or not snapshot.get("source"):
            raise LegacyRevisionError(f"source not registered in state: {source}")
        fmt = snapshot["source"].get("format")
        if fmt == "pdf":
            context = _ingest_context(workspace, source, snapshot)
            kind = "ingest"
        elif fmt == "external-vault-reuse":
            context = _reuse_context(workspace, source, snapshot)
            kind = "reuse"
        else:
            raise LegacyRevisionError(
                f"source {source} format {fmt!r} has no revision admission path")
    context["kind"] = kind
    if kind == "adoption":
        # 采纳来源射程保持 manifest 成员判定（今天行为逐字节不变；实证
        # manifest["pages"] == source_refs 归属 128/128，两者当前等价）。
        context["scope_paths"] = [entry["path"] for entry in context["manifest"]["pages"]]
    else:
        context["scope_paths"] = _owned_pages(context["vault"], source)
    context["scope_digest"] = _scope_digest(context["vault"], source)
    return context


def _adoption_context(workspace: Path, source: str,
                      allowed_lock_holder: str | None = None,
                      lock_ttl_seconds: int = 1800) -> dict:
    workspace = Path(workspace).resolve()
    vault = workspace / "wiki"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    evidence_dir = workspace / "pipeline-workspace" / "adoptions" / source
    if not vault.is_dir() or not db.is_file():
        raise LegacyRevisionError("wiki vault or state database is missing")
    stored = vault_adoption._load_stored_manifest(evidence_dir)
    if stored is None:
        raise LegacyRevisionError(f"adoption manifest not found for source {source}")
    manifest, manifest_raw = stored
    if manifest.get("source_id") != source or manifest.get("format") != "legacy-vault":
        raise LegacyRevisionError("adoption manifest source/format mismatch")
    findings = vault_adoption._validate_existing_evidence(
        evidence_dir, manifest_raw, manifest["pages"])
    if findings:
        raise LegacyRevisionError(
            f"adoption evidence invalid: {findings[0]['rule']}: {findings[0]['detail']}")
    snapshot = vault_adoption._state_snapshot(db, source)
    if not snapshot:
        raise LegacyRevisionError(f"adopted source state not found: {source}")
    evidence_fs.reject_lock(snapshot, lock_ttl_seconds, allowed_lock_holder,
                            command="revise-adopted", error=LegacyRevisionError)
    manifest_sha = _sha_bytes(manifest_raw)
    src = snapshot.get("source") or {}
    actual_source = (src.get("domain"), src.get("format"), src.get("current_stage"),
                     src.get("current_status"))
    expected_source = (manifest["domain"], "legacy-vault", "adopted", "published")
    stages = [(r["stage"], r["status"], r["input_hash"], r["output_hash"])
              for r in snapshot.get("stages") or []]
    artifacts = [(r["kind"], r["path"], r["sha256"])
                 for r in snapshot.get("artifacts") or []]
    expected_artifact = ("adoption_evidence", str((evidence_dir / "manifest.json").resolve()),
                         manifest_sha)
    if actual_source != expected_source:
        raise LegacyRevisionError("source is not exact legacy-vault adopted/published state")
    if stages != [("adopted", "done", manifest_sha, manifest_sha)]:
        raise LegacyRevisionError("adopted source stage ledger drift")
    if artifacts != [expected_artifact]:
        raise LegacyRevisionError("adopted source artifact ledger drift")
    if snapshot.get("ledgers") != {"work_orders": 0, "ingest_progress": 0,
                                    "window_reads": 0}:
        raise LegacyRevisionError("adopted source ingest ledgers must remain zero")
    source_page = vault / "sources" / f"{source}.md"
    if not source_page.is_file():
        raise LegacyRevisionError("adopted source page is missing")
    source_meta, _ = mdpage.read_page(source_page)
    if (source_meta.get("adoption_manifest_sha256") != manifest_sha
            or source_meta.get("format") != "legacy-vault"):
        raise LegacyRevisionError("adopted source page evidence identity drift")
    return {"workspace": workspace, "vault": vault, "db": db,
            "evidence_dir": evidence_dir, "manifest": manifest,
            "manifest_bytes": manifest_raw, "manifest_sha256": manifest_sha,
            "snapshot": snapshot}


def _candidate_citations(page_request: dict) -> list[dict]:
    return [_citation_from_evidence(item) for item in page_request["evidence"]]


def _citation_hash(value) -> str:
    return _sha_bytes(_json_bytes(value))


def _build_authorization(context: dict, request: dict, request_sha: str) -> tuple[dict, dict]:
    vault = context["vault"]
    scope_paths = set(context["scope_paths"])
    revert_op = None
    if request["mode"] == "revert":
        revert_id = request["revert_operation"]
        revert_op = _operation_root(context["workspace"], request["source_id"]) / revert_id
        prior = _verify_operation(revert_op, context["workspace"], allow_live_drift=True)
        if prior["phase"] != "completed":
            raise LegacyRevisionError("revert_operation must name a completed operation")
        prior_paths = {p["path"] for p in prior["authorization"]["pages"]}
        if prior_paths != {p["path"] for p in request["pages"]}:
            raise LegacyRevisionError("revert page set must equal the completed operation page set")
        prior_pages = {p["path"]: p for p in prior["authorization"]["pages"]}
    else:
        prior_pages = None
    pages = []
    initial_candidates = {}
    for item in request["pages"]:
        rel = item["path"]
        if rel not in scope_paths:
            if context.get("kind") == "adoption":
                raise LegacyRevisionError(f"target is not in adoption manifest: {rel}")
            raise LegacyRevisionError(
                f"target is not owned by source {request['source_id']} "
                f"(source_refs lacks {request['source_id']}): {rel}")
        path = vault / rel
        if not path.is_file() or path.is_symlink() or evidence_fs.resolved_inside(path, vault) is None:
            raise LegacyRevisionError(f"target is missing or redirected: {rel}")
        raw = path.read_bytes()
        meta, _body = mdpage.read_page(path)
        if meta.get("managed_by") == "human":
            raise LegacyRevisionError(f"managed_by: human page is never writable: {rel}")
        if meta.get("managed_by") != "pipeline" or meta.get("status") != "published":
            raise LegacyRevisionError(f"target must be pipeline-managed and published: {rel}")
        if request["source_id"] not in _sources(meta):
            raise LegacyRevisionError(f"target no longer cites adopted source {request['source_id']}: {rel}")
        actual_citations = list(meta.get("citations") or [])
        actual_citation_hashes = {
            _citation_hash(citation): citation for citation in actual_citations}
        requested_removal_hashes = {
            removal["sha256"] for removal in item["citation_removals"]}
        unknown_removal_hashes = sorted(requested_removal_hashes - actual_citation_hashes.keys())
        if unknown_removal_hashes:
            actual_details = "; ".join(
                f"{citation_hash} (source={citation.get('source', '<missing>')!r}, "
                f"url={citation.get('url', '<missing>')!r})"
                for citation_hash, citation in sorted(actual_citation_hashes.items())
            ) or "<none>"
            raise LegacyRevisionError(
                f"citation_removals are not present on current target: {rel}; "
                f"unknown={unknown_removal_hashes}; actual citations: {actual_details}")
        if request["mode"] == "revert":
            prior_post = revert_op / "post" / "files" / rel
            prior_pre = revert_op / "pre" / "files" / rel
            if not prior_post.is_file() or raw != prior_post.read_bytes():
                raise LegacyRevisionError(
                    f"revert-not-applicable-after-live-drift: {rel}")
            old_meta, old_body = mdpage.read_page(prior_pre)
            old_meta["status"] = "proposed"
            fm = yaml.safe_dump(old_meta, allow_unicode=True, sort_keys=True,
                                default_flow_style=False)
            initial_candidates[rel] = f"---\n{fm}---\n{old_body}".encode("utf-8")
        else:
            initial_candidates[rel] = raw
        pre_identity = _frontmatter_identity(meta)
        fu = item.get("frontmatter_updates")
        expected_identity = pre_identity
        page_immutable_pre = None
        page_fu = None
        if request["mode"] == "revert":
            if fu is not None:
                raise LegacyRevisionError(
                    f"revert request must not declare frontmatter_updates: {rel}")
            prior_page = (prior_pages or {}).get(rel)
            if prior_page and prior_page.get("frontmatter_updates"):
                # 回滚撤销先前的受控声明：期望态 = 先前 pre 快照的 identity（恢复 pre 态 aliases）
                expected_identity = _frontmatter_identity(old_meta)
                page_immutable_pre = pre_identity
                page_fu = prior_page["frontmatter_updates"]
        elif fu is not None:
            aliases = list(pre_identity.get("aliases") or [])
            for alias in fu["aliases"]["remove"]:
                if alias not in aliases:
                    raise LegacyRevisionError(
                        f"frontmatter_updates removes absent alias {alias!r}: {rel}")
            expected_identity = dict(pre_identity)
            expected_identity["aliases"] = [
                a for a in aliases if a not in set(fu["aliases"]["remove"])]
            page_immutable_pre = pre_identity
            page_fu = fu
        _assert_registered_no_url_sources(context, item, rel)
        page_entry = {
            "path": rel,
            "pre_sha256": _sha_bytes(raw),
            "pre_size": len(raw),
            "reason": item["reason"],
            "evidence": item["evidence"],
            "citation_plan": {
                "add": _candidate_citations(item),
                "remove_sha256": [x["sha256"] for x in item["citation_removals"]],
                "removals": item["citation_removals"],
            },
            "immutable_frontmatter": expected_identity,
            "immutable_frontmatter_sha256": _sha_bytes(
                _json_bytes(expected_identity)),
        }
        if page_immutable_pre is not None:
            page_entry["immutable_frontmatter_pre"] = page_immutable_pre
        if page_fu is not None:
            page_entry["frontmatter_updates"] = page_fu
        pages.append(page_entry)
    identity = {
        "contract_version": CONTRACT_VERSION,
        "source_id": request["source_id"],
        "request_sha256": request_sha,
        "valid_until": request["valid_until"],
        "mode": request["mode"],
        "revert_operation": request.get("revert_operation"),
        "pages": pages,
    }
    if context["kind"] == "adoption":
        identity["adoption_manifest_sha256"] = context["manifest_sha256"]
    else:
        identity["scope_digest"] = context["scope_digest"]
    operation_id = _sha_bytes(_json_bytes(identity))[:20]
    authorization = dict(identity)
    authorization["operation_id"] = operation_id
    return authorization, initial_candidates


def _operation_root(workspace: Path, source: str) -> Path:
    return Path(workspace) / "pipeline-workspace" / "legacy-revisions" / source


def _phase_from_events(events: list[dict]) -> str:
    if not events:
        return "none"
    return events[-1]["event"]


def _events(op: Path) -> tuple[list[dict], list[bytes]]:
    event_dir = op / "events"
    if not event_dir.exists():
        return [], []
    files = sorted(event_dir.glob("*.json"))
    events, raws = [], []
    for index, path in enumerate(files, 1):
        if path.name != f"{index:04d}-{path.stem.split('-', 1)[-1]}.json":
            raise LegacyRevisionError(f"legacy revision event numbering drift: {path}")
        event, raw = _load_canonical_json(path, "legacy revision event")
        expected_prev = _sha_bytes(raws[-1]) if raws else None
        if event.get("sequence") != index or event.get("previous_event_sha256") != expected_prev:
            raise LegacyRevisionError(f"legacy revision event chain drift: {path}")
        if event.get("event") != path.stem.split("-", 1)[1]:
            raise LegacyRevisionError(f"legacy revision event filename/type drift: {path}")
        events.append(event)
        raws.append(raw)
    sequence = [e.get("event") for e in events]
    legal = (
        ["prepared"], ["prepared", "aborted"],
        ["prepared", "committing"], ["prepared", "committing", "completed"],
        ["prepared", "committing", "recovery_requested"],
        ["prepared", "committing", "recovery_requested", "completed"],
        ["prepared", "committing", "rollback_requested"],
        ["prepared", "committing", "rollback_requested", "rolled_back"],
        ["prepared", "committing", "recovery_requested", "rollback_requested"],
        ["prepared", "committing", "recovery_requested", "rollback_requested",
         "rolled_back"],
    )
    if sequence not in legal:
        raise LegacyRevisionError(f"illegal legacy revision event sequence: {sequence}")
    return events, raws


def _write_event(op: Path, event_type: str, extra: dict | None = None, *,
                 operation_id: str | None = None) -> dict:
    events, raws = _events(op)
    sequence = len(events) + 1
    event = {
        "contract_version": CONTRACT_VERSION,
        "event": event_type,
        "operation_id": operation_id or op.name,
        "previous_event_sha256": _sha_bytes(raws[-1]) if raws else None,
        "recorded_at": _now().isoformat(timespec="seconds"),
        "sequence": sequence,
    }
    event.update(extra or {})
    _atomic_write(op / "events" / f"{sequence:04d}-{event_type}.json", _json_bytes(event))
    return event


def _manifest(kind: str, operation_id: str, files: dict[str, bytes]) -> dict:
    return {"contract_version": CONTRACT_VERSION, "kind": kind,
            "operation_id": operation_id,
            "entries": [{"path": rel, "sha256": _sha_bytes(raw), "size": len(raw)}
                        for rel, raw in sorted(files.items())]}


def _write_file_set(root: Path, kind: str, operation_id: str,
                    files: dict[str, bytes]) -> bytes:
    for rel, raw in sorted(files.items()):
        target = root / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    manifest_raw = _json_bytes(_manifest(kind, operation_id, files))
    (root / "manifest.json").write_bytes(manifest_raw)
    return manifest_raw


def _verify_file_set(root: Path, kind: str, operation_id: str) -> tuple[dict, dict[str, bytes]]:
    manifest, _raw = _load_canonical_json(root / "manifest.json", f"{kind} manifest")
    if (manifest.get("contract_version") != CONTRACT_VERSION
            or manifest.get("kind") != kind
            or manifest.get("operation_id") != operation_id
            or not isinstance(manifest.get("entries"), list)):
        raise LegacyRevisionError(f"{kind} manifest schema drift")
    files = {}
    paths = []
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise LegacyRevisionError(f"{kind} manifest entry schema drift")
        rel = entry["path"]
        if rel == "log.md" or rel in _DERIVED:
            safe = rel
        else:
            safe = _safe_rel(rel)
        path = root / "files" / safe
        if not path.is_file() or path.is_symlink():
            raise LegacyRevisionError(f"{kind} evidence file missing: {safe}")
        raw = path.read_bytes()
        if len(raw) != entry["size"] or _sha_bytes(raw) != entry["sha256"]:
            raise LegacyRevisionError(f"{kind} evidence file hash mismatch: {safe}")
        paths.append(safe)
        files[safe] = raw
    if paths != sorted(set(paths)):
        raise LegacyRevisionError(f"{kind} manifest path order/uniqueness drift")
    actual = {p.relative_to(root / "files").as_posix()
              for p in (root / "files").rglob("*") if p.is_file()}
    if actual != set(paths):
        raise LegacyRevisionError(f"{kind} evidence file set mismatch")
    return manifest, files


def _prepare(op: Path, authorization: dict, candidates: dict[str, bytes], vault: Path) -> None:
    parent = op.parent
    parent.mkdir(parents=True, exist_ok=True)
    _assert_direct_path(parent, vault.parent, "legacy revision operation parent")
    temp = parent / f".{op.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.mkdir()
        auth_raw = _json_bytes(authorization)
        (temp / "authorization.json").write_bytes(auth_raw)
        pre = {page["path"]: (vault / page["path"]).read_bytes()
               for page in authorization["pages"]}
        _write_file_set(temp / "pre", "pre", op.name, pre)
        for rel, raw in sorted(candidates.items()):
            target = temp / "candidate" / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        _write_event(temp, "prepared", {
            "authorization_sha256": _sha_bytes(auth_raw),
            "pre_manifest_sha256": _sha_bytes((temp / "pre" / "manifest.json").read_bytes()),
        }, operation_id=op.name)
        temp.rename(op)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _candidate_files(op: Path, authorization: dict) -> dict[str, bytes]:
    root = op / "candidate" / "files"
    _assert_direct_path(root, op, "legacy revision candidate root")
    expected = {p["path"] for p in authorization["pages"]}
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != expected:
        raise LegacyRevisionError(
            f"candidate-set-mismatch: expected={sorted(expected)} actual={sorted(actual)}")
    files = {}
    for rel in sorted(expected):
        path = root / rel
        if path.is_symlink() or evidence_fs.resolved_inside(path, root) is None:
            raise LegacyRevisionError(f"candidate path is redirected: {rel}")
        files[rel] = path.read_bytes()
    return files


def _validate_candidates(op: Path, authorization: dict) -> tuple[dict[str, bytes], list[dict]]:
    _pre_manifest, pre = _verify_file_set(op / "pre", "pre", op.name)
    candidates = _candidate_files(op, authorization)
    pages = []
    for page in authorization["pages"]:
        rel = page["path"]
        raw = candidates[rel]
        if raw == pre[rel]:
            raise LegacyRevisionError(f"candidate page was not edited: {rel}")
        try:
            meta, body = mdpage.read_page(op / "candidate" / "files" / rel)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise LegacyRevisionError(f"candidate page is invalid markdown/YAML: {rel}") from exc
        if meta.get("status") != "proposed":
            raise LegacyRevisionError(f"candidate status must be proposed: {rel}")
        identity = _frontmatter_identity(meta)
        expected_identity = page["immutable_frontmatter"]
        if identity != expected_identity:
            declared = page.get("frontmatter_updates")
            pre_identity = page.get("immutable_frontmatter_pre")
            if declared is not None and pre_identity is not None and identity == pre_identity:
                raise LegacyRevisionError(
                    f"declared frontmatter update not applied: {rel}")
            changed = [key for key in _IMMUTABLE_FRONTMATTER
                       if identity.get(key) != expected_identity.get(key)]
            raise LegacyRevisionError(f"immutable frontmatter changed ({', '.join(changed)}): {rel}")
        pre_meta, _ = mdpage.read_page(op / "pre" / "files" / rel)
        old = list(pre_meta.get("citations") or [])
        new = list(meta.get("citations") or [])
        old_hashes = {_citation_hash(x): x for x in old}
        new_hashes = {_citation_hash(x): x for x in new}
        removed = set(old_hashes) - set(new_hashes)
        added = set(new_hashes) - set(old_hashes)
        allowed_removed = set(page["citation_plan"]["remove_sha256"])
        required_add = {_citation_hash(x) for x in page["citation_plan"]["add"]}
        if removed != allowed_removed:
            raise LegacyRevisionError(f"citation removal does not match authorization: {rel}")
        if added != required_add or not required_add.issubset(new_hashes):
            raise LegacyRevisionError(f"citation additions/evidence do not match authorization: {rel}")
        pages.append({"rel_path": rel, "meta": meta, "body": body})
    return candidates, pages


def _rebuild_derived(overlay: Path, final_vault: Path) -> None:
    registry, errors, _warnings = concept_store.build_registry(
        concept_store.scan_concept_pages(overlay))
    if errors:
        raise LegacyRevisionError("registry invalid in revision overlay: " + "; ".join(errors))
    concept_store.write_registry(overlay, registry)
    concept_store.remove_stale_aliases(overlay)
    wiki_gate.write_index(overlay)
    model = graph_model.build_graph_model(overlay)
    analyzed = graph_analysis.analyze_graph(model)
    data = graph_data.to_graph_data(analyzed)
    result = graph_lint.validate_graph_data(data, vault=overlay)
    if result["errors"]:
        raise LegacyRevisionError("graph invalid in revision overlay: " + result["errors"][0])
    (overlay / graph_data.GRAPH_DATA_FILE).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (overlay / graph_html.HTML_FILE).write_text(
        graph_html.to_html(data, vault_root=final_vault.resolve().as_posix()),
        encoding="utf-8", newline="\n")
    wiki_gate.write_quiz_index(overlay)
    props = wiki_gate.collect_propositions(overlay)
    duplicates = wiki_gate.duplicate_proposition_names(props)
    if duplicates:
        raise LegacyRevisionError("duplicate proposition names in revision overlay: " + duplicates[0])
    wiki_gate.write_propositions_index(overlay)


def _log_line(source: str, operation_id: str, post_sha: str) -> str:
    return wiki_gate.log_line(
        "revise-adopted", source,
        f"operation {operation_id} post {post_sha[:12]}", date.today().isoformat())


def _build_transition(op: Path, authorization: dict, context: dict) -> dict:
    existing = op / "transition.json"
    if existing.exists():
        transition, _raw, _switch, _post = _verify_transition(op, authorization)
        return transition
    candidates, pages = _validate_candidates(op, authorization)
    vault = context["vault"]
    for page in authorization["pages"]:
        live = vault / page["path"]
        if not live.is_file() or _sha_bytes(live.read_bytes()) != page["pre_sha256"]:
            raise LegacyRevisionError(f"live pre SHA drift: {page['path']}")
    with tempfile.TemporaryDirectory(prefix="legacy-revision-overlay-") as td:
        overlay = Path(td) / "wiki"
        shutil.copytree(vault, overlay)
        for rel, raw in candidates.items():
            (overlay / rel).write_bytes(raw)
        violations = wiki_gate.lint_pages(overlay, pages, phase_e=False)
        candidate_paths = set(candidates)
        violations.extend(v for v in wiki_gate.vault_render_safety(
            overlay, statuses=("published", "proposed")) if v["path"] not in candidate_paths)
        if violations:
            first = violations[0]
            raise LegacyRevisionError(
                f"candidate lint failed: {first['rule']} {first['path']}: {first['detail']}")
        wiki_gate.promote(overlay, pages)
        _rebuild_derived(overlay, vault)
        post = {rel: (overlay / rel).read_bytes()
                for rel in sorted(candidate_paths | set(_DERIVED))}
    switch_pre = {}
    for rel in sorted(set(post) | {"log.md"}):
        path = vault / rel
        switch_pre[rel] = path.read_bytes() if path.exists() else b""
    post_manifest_raw = _json_bytes(_manifest("post", op.name, post))
    post_sha = _sha_bytes(post_manifest_raw)
    log_line = _log_line(authorization["source_id"], op.name, post_sha)
    log_post = switch_pre["log.md"] + log_line.encode("utf-8")
    transition = {
        "contract_version": CONTRACT_VERSION,
        "operation_id": op.name,
        "authorization_sha256": _sha_bytes((op / "authorization.json").read_bytes()),
        "candidate_sha256": {rel: _sha_bytes(raw) for rel, raw in sorted(candidates.items())},
        "entries": [{
            "path": rel,
            "pre_sha256": _sha_bytes(switch_pre[rel]),
            "post_sha256": _sha_bytes(post[rel]),
            "pre_size": len(switch_pre[rel]),
            "post_size": len(post[rel]),
        } for rel in sorted(post)],
        "log": {"line": log_line, "line_sha256": _sha_bytes(log_line.encode("utf-8")),
                "pre_sha256": _sha_bytes(switch_pre["log.md"]),
                "post_sha256": _sha_bytes(log_post),
                "pre_size": len(switch_pre["log.md"]), "post_size": len(log_post)},
        "post_manifest_sha256": post_sha,
    }
    temp = op / f".transition-{uuid.uuid4().hex}"
    try:
        temp.mkdir()
        _write_file_set(temp / "switch-pre", "switch-pre", op.name, switch_pre)
        _write_file_set(temp / "post", "post", op.name, post)
        (temp / "transition.json").write_bytes(_json_bytes(transition))
        for name in ("switch-pre", "post"):
            (temp / name).rename(op / name)
        (temp / "transition.json").replace(op / "transition.json")
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    _fault_point("transition")
    return transition


def _expected_live_manifest(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyRevisionError("--expect-live-manifest must be readable JSON") from exc
    if not isinstance(data, dict) or any(not isinstance(k, str) or not _SHA256.fullmatch(str(v))
                                         for k, v in data.items()):
        raise LegacyRevisionError("--expect-live-manifest must map paths to SHA-256 strings")
    return {k.replace("\\", "/"): str(v) for k, v in data.items()}


def _unknown_live_bytes(op: Path, authorization: dict, vault: Path) -> dict[str, str]:
    transition, _raw, switch, post = _verify_transition(op, authorization)
    unknown = {}
    for entry in transition["entries"]:
        rel = entry["path"]
        path = vault / rel
        current = path.read_bytes() if path.exists() else b""
        if current not in (switch[rel], post[rel]):
            unknown[rel] = _sha_bytes(current)
    log_path = vault / "log.md"
    current_log = log_path.read_bytes() if log_path.exists() else b""
    pre_log = switch["log.md"]
    post_log = pre_log + transition["log"]["line"].encode("utf-8")
    if current_log not in (pre_log, post_log):
        unknown["log.md"] = _sha_bytes(current_log)
    return unknown


def _replace_controlled(vault: Path, rel: str, before: bytes, after: bytes,
                        expected_unknown: dict[str, str] | None, conflicts: Path) -> None:
    path = vault / rel
    if path.exists():
        if path.is_symlink() or evidence_fs.resolved_inside(path, vault) is None:
            raise LegacyRevisionError(f"controlled live path is redirected outside vault: {rel}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _assert_direct_path(path.parent, vault, f"controlled live parent {rel}")
    current = path.read_bytes() if path.exists() else b""
    if current == after:
        return
    if current != before:
        expected = (expected_unknown or {}).get(rel)
        if expected != _sha_bytes(current):
            raise LegacyRevisionError(
                f"controlled live file is neither pre nor post: {rel}; provide --expect-live-manifest")
        conflict = conflicts / rel
        conflict.parent.mkdir(parents=True, exist_ok=True)
        if conflict.exists() and conflict.read_bytes() != current:
            raise LegacyRevisionError(f"conflict archive collision: {rel}")
        if not conflict.exists():
            conflict.write_bytes(current)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    temp.write_bytes(after)
    os.replace(temp, path)


def _forward(op: Path, authorization: dict, context: dict, *,
             expected_unknown: dict[str, str] | None = None) -> None:
    transition, transition_raw, switch, post = _verify_transition(op, authorization)
    events, _ = _events(op)
    if _phase_from_events(events) == "prepared":
        _write_event(op, "committing", {
            "transition_sha256": _sha_bytes(transition_raw),
            "post_manifest_sha256": transition["post_manifest_sha256"],
        })
        _fault_point("committing")
    vault = context["vault"]
    for index, entry in enumerate(transition["entries"]):
        rel = entry["path"]
        _replace_controlled(vault, rel, switch[rel], post[rel], expected_unknown,
                            op / "conflicts" / "files")
        _fault_point(f"switch:{index}:{rel}")
    log_before = switch["log.md"]
    log_after = log_before + transition["log"]["line"].encode("utf-8")
    _replace_controlled(vault, "log.md", log_before, log_after, expected_unknown,
                        op / "conflicts" / "files")
    _fault_point("log")
    for rel, raw in post.items():
        if not (vault / rel).is_file() or (vault / rel).read_bytes() != raw:
            raise LegacyRevisionError(f"post switch verification failed: {rel}")
    if (vault / "log.md").read_bytes() != log_after:
        raise LegacyRevisionError("post switch log verification failed")
    derived = wiki_gate.derived_violations(vault)
    if derived:
        raise LegacyRevisionError(f"post switch derived verification failed: {derived[0]}")
    _verify_switch_ledgers(context, authorization)
    _write_event(op, "completed", {
        "log_line": transition["log"]["line"],
        "log_line_sha256": transition["log"]["line_sha256"],
        "post_manifest_sha256": transition["post_manifest_sha256"],
    })
    _fault_point("completed")


def _verify_switch_ledgers(context: dict, authorization: dict) -> None:
    """切换期的来源台账复核（按来源种类，R-08 补正）。

    采纳/复用：三个 ingest 台账必须全零（采纳今天的行为逐字节不变）；
    摄取：work_orders 恒为 1、无 running/failed 窗口、window_reads 与窗口数一致——
    即操作期间台账仍保持「已完成、可安全修订」的落定形态（操作持 vault 锁，
    台账变化只能来自锁外改库，检测到即拒）。
    """
    source = authorization["source_id"]
    con = sqlite3.connect(context["db"].resolve().as_uri() + "?mode=ro", uri=True)
    try:
        counts = {}
        for table in ("work_orders", "ingest_progress", "window_reads"):
            counts[table] = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_id=?",
                (source,)).fetchone()[0]
        kind = context.get("kind")
        if kind in ("adoption", "reuse"):
            bad = {t: c for t, c in counts.items() if c}
            if bad:
                raise LegacyRevisionError(
                    f"{kind} source ingest ledger changed during switch: {bad}")
        elif kind == "ingest":
            if counts["work_orders"] != 1:
                raise LegacyRevisionError(
                    f"ingest source work_orders changed during switch: "
                    f"{counts['work_orders']}")
            statuses = [str(r[0]) for r in con.execute(
                "SELECT status FROM ingest_progress WHERE source_id=? ORDER BY id",
                (source,)).fetchall()]
            in_progress = sorted({s for s in statuses if s in ("running", "failed")})
            if in_progress:
                raise LegacyRevisionError(
                    f"ingest source windows changed during switch: {in_progress}")
            if counts["window_reads"] != len(statuses):
                raise LegacyRevisionError(
                    f"ingest source window_reads changed during switch: "
                    f"{counts['window_reads']} != {len(statuses)}")
        else:
            raise LegacyRevisionError(f"unknown revision kind during switch: {kind}")
    finally:
        con.close()


def _rollback(op: Path, authorization: dict, context: dict,
              expected_unknown: dict[str, str] | None = None) -> None:
    transition, _transition_raw, switch, post = _verify_transition(op, authorization)
    events, _ = _events(op)
    if _phase_from_events(events) in {"committing", "recovery_requested"}:
        _write_event(op, "rollback_requested")
    vault = context["vault"]
    for entry in reversed(transition["entries"]):
        rel = entry["path"]
        _replace_controlled(vault, rel, post[rel], switch[rel], expected_unknown,
                            op / "conflicts" / "files")
    log_pre = switch["log.md"]
    log_post = log_pre + transition["log"]["line"].encode("utf-8")
    _replace_controlled(vault, "log.md", log_post, log_pre, expected_unknown,
                        op / "conflicts" / "files")
    _write_event(op, "rolled_back")


def _find_request_operation(root: Path, request_sha: str) -> Path | None:
    if not root.exists():
        return None
    _assert_direct_path(root, root.parents[2], "legacy revision source root")
    unexpected = [p.name for p in root.iterdir()
                  if not p.is_dir() or not _OPERATION_ID.fullmatch(p.name)]
    if unexpected:
        raise LegacyRevisionError(f"unexpected legacy revision operation path(s): {sorted(unexpected)}")
    matches = []
    unfinished = []
    for op in sorted(p for p in root.iterdir() if p.is_dir() and _OPERATION_ID.fullmatch(p.name)):
        auth, _ = _load_canonical_json(op / "authorization.json", "authorization")
        events, _ = _events(op)
        phase = _phase_from_events(events)
        if phase in {"prepared", "committing", "recovery_requested", "rollback_requested"}:
            unfinished.append(op)
        if auth.get("request_sha256") == request_sha:
            matches.append(op)
    if len(matches) > 1:
        raise LegacyRevisionError("operation collision: multiple operations match this request")
    if unfinished and (not matches or any(op not in matches for op in unfinished)):
        raise LegacyRevisionError(
            f"another unfinished legacy revision exists: {unfinished[0].name}")
    return matches[0] if matches else None


def _verify_authorization(op: Path, workspace: Path) -> tuple[dict, bytes]:
    auth, raw = _load_canonical_json(op / "authorization.json", "authorization")
    if "adoption_manifest_sha256" in auth:
        anchor = "adoption_manifest_sha256"
    elif "scope_digest" in auth:
        anchor = "scope_digest"
    else:
        raise LegacyRevisionError(f"authorization schema drift: {op}")
    expected_keys = {
        anchor, "contract_version", "mode", "operation_id", "pages",
        "request_sha256", "revert_operation", "source_id", "valid_until",
    }
    if not isinstance(auth, dict) or set(auth) != expected_keys:
        raise LegacyRevisionError(f"authorization schema drift: {op}")
    if (auth["contract_version"] != CONTRACT_VERSION
            or auth["operation_id"] != op.name
            or auth["source_id"] != op.parent.name
            or auth["mode"] not in {"edit", "revert"}
            or not _SHA256.fullmatch(str(auth["request_sha256"]))
            or not _SHA256.fullmatch(str(auth[anchor]))):
        raise LegacyRevisionError(f"authorization identity drift: {op}")
    _parse_datetime(auth["valid_until"])
    if auth["mode"] == "revert":
        if not isinstance(auth["revert_operation"], str) or not _OPERATION_ID.fullmatch(
                auth["revert_operation"]):
            raise LegacyRevisionError(f"authorization revert identity drift: {op}")
    elif auth["revert_operation"] is not None:
        raise LegacyRevisionError(f"edit authorization unexpectedly names revert operation: {op}")
    if not isinstance(auth["pages"], list) or not auth["pages"]:
        raise LegacyRevisionError(f"authorization pages invalid: {op}")
    paths = []
    for page in auth["pages"]:
        if (not isinstance(page, dict)
                or set(page) not in (_AUTHORIZATION_PAGE_KEYS,
                                     _AUTHORIZATION_PAGE_KEYS | _AUTHORIZATION_PAGE_OPTIONAL_KEYS)):
            raise LegacyRevisionError(f"authorization page schema drift: {op}")
        if page.get("frontmatter_updates") is not None and not isinstance(
                page["frontmatter_updates"], dict):
            raise LegacyRevisionError(f"authorization frontmatter_updates schema drift: {op}")
        if page.get("immutable_frontmatter_pre") is not None and not isinstance(
                page["immutable_frontmatter_pre"], dict):
            raise LegacyRevisionError(f"authorization immutable_frontmatter_pre schema drift: {op}")
        rel = _safe_rel(page["path"])
        if (not _SHA256.fullmatch(str(page["pre_sha256"]))
                or not isinstance(page["pre_size"], int) or page["pre_size"] < 0
                or page["immutable_frontmatter_sha256"] != _sha_bytes(
                    _json_bytes(page["immutable_frontmatter"]))):
            raise LegacyRevisionError(f"authorization page identity drift: {rel}")
        paths.append(rel)
    if paths != sorted(set(paths)):
        raise LegacyRevisionError(f"authorization page order/uniqueness drift: {op}")
    identity = {key: auth[key] for key in expected_keys if key != "operation_id"}
    if _sha_bytes(_json_bytes(identity))[:20] != op.name:
        raise LegacyRevisionError(f"authorization operation id is not derived from its identity: {op}")
    if anchor == "adoption_manifest_sha256":
        manifest_path = (Path(workspace) / "pipeline-workspace" / "adoptions" /
                         auth["source_id"] / "manifest.json")
        _manifest, manifest_raw = _load_canonical_json(manifest_path, "adoption manifest")
        if _sha_bytes(manifest_raw) != auth["adoption_manifest_sha256"]:
            raise LegacyRevisionError(f"authorization adoption manifest identity drift: {op}")
    else:
        # 非采纳来源的「页集合定义」锚 = 产出页 artifact（workorder / reuse_evidence）
        # 字节与账本一致；scope_digest 本身已由 operation_id 派生校验覆盖。
        db = Path(workspace) / "pipeline-workspace" / "state" / "study-kb.sqlite"
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            src = con.execute(
                "SELECT format FROM sources WHERE source_id=?",
                (auth["source_id"],)).fetchone()
            fmt = str(src[0]) if src else None
            expected_kind = {"pdf": "workorder",
                             "external-vault-reuse": "reuse_evidence"}.get(fmt)
            if expected_kind is None:
                raise LegacyRevisionError(
                    f"authorization definition anchor kind unknown: {op}")
            rows = con.execute(
                "SELECT path,sha256 FROM artifacts WHERE source_id=? AND kind=? "
                "ORDER BY id", (auth["source_id"], expected_kind)).fetchall()
        finally:
            con.close()
        if len(rows) != 1:
            raise LegacyRevisionError(
                f"authorization {expected_kind} definition anchor drift: {op}")
        artifact_path = Path(rows[0][0])
        if (not artifact_path.is_file()
                or _sha_bytes(artifact_path.read_bytes()) != rows[0][1]):
            raise LegacyRevisionError(
                f"authorization {expected_kind} definition anchor identity drift: {op}")
    return auth, raw


def emit_removal_sha(workspace, page_rel: str, source: str | None = None) -> list[dict]:
    """只读导出目标页每条 citation 的规范 SHA-256 与可读摘要（供请求作者机械复制）。

    零写入：不建 operation、不动 live、不需要授权。哈希口径与 _citation_hash 一致
    （canonical JSON：ensure_ascii=False, indent=2, sort_keys=True + 换行）。

    给了 ``source`` 就校验该页确实属于它（判据与 `_build_authorization` 同一条：
    页的 `source_refs` 含该 source_id）。此前 CLI 的 ``--source`` 是必填却完全不参与，
    可以拿任意来源 id 导出库里任意页，读命令日志的人会误以为导出被限定在该来源内。
    刻意**不**走 `_revision_context`：那是重量级的落定态准入，而这只是个只读抄写器，
    不该因为来源暂时不可修订就拒绝打印哈希。
    """
    vault = Path(workspace) / "wiki"
    rel = _safe_rel(page_rel)
    path = vault / rel
    if not path.is_file() or path.is_symlink() or evidence_fs.resolved_inside(path, vault) is None:
        raise LegacyRevisionError(f"page missing or redirected: {rel}")
    meta, _body = mdpage.read_page(path)
    if source is not None and source not in _sources(meta):
        raise LegacyRevisionError(
            f"page does not belong to source {source!r} (source_refs lacks it): {rel}")
    out = []
    for citation in meta.get("citations") or []:
        out.append({
            "sha256": _citation_hash(citation),
            "source": citation.get("source"),
            "title": citation.get("title"),
            "url": citation.get("url"),
            "sections": citation.get("sections"),
        })
    return out


def _verify_transition(op: Path, auth: dict) -> tuple[dict, bytes, dict[str, bytes],
                                                       dict[str, bytes]]:
    transition, transition_raw = _load_canonical_json(
        op / "transition.json", "legacy revision transition")
    expected_keys = {
        "authorization_sha256", "candidate_sha256", "contract_version", "entries",
        "log", "operation_id", "post_manifest_sha256",
    }
    if (not isinstance(transition, dict) or set(transition) != expected_keys
            or transition["contract_version"] != CONTRACT_VERSION
            or transition["operation_id"] != op.name
            or transition["authorization_sha256"] != _sha_bytes(
                (op / "authorization.json").read_bytes())):
        raise LegacyRevisionError(f"transition schema/authorization drift: {op}")
    switch_manifest, switch = _verify_file_set(op / "switch-pre", "switch-pre", op.name)
    post_manifest, post = _verify_file_set(op / "post", "post", op.name)
    post_manifest_raw = (op / "post" / "manifest.json").read_bytes()
    if transition["post_manifest_sha256"] != _sha_bytes(post_manifest_raw):
        raise LegacyRevisionError(f"transition/post manifest identity drift: {op}")
    page_paths = {page["path"] for page in auth["pages"]}
    expected_post = page_paths | set(_DERIVED)
    if set(post) != expected_post or set(switch) != expected_post | {"log.md"}:
        raise LegacyRevisionError(f"transition controlled file set drift: {op}")
    candidates = _candidate_files(op, auth)
    expected_candidate = {rel: _sha_bytes(raw) for rel, raw in sorted(candidates.items())}
    if transition["candidate_sha256"] != expected_candidate:
        raise LegacyRevisionError(f"frozen candidate bytes drift: {op}")
    expected_entries = [{
        "path": rel,
        "pre_sha256": _sha_bytes(switch[rel]),
        "post_sha256": _sha_bytes(post[rel]),
        "pre_size": len(switch[rel]),
        "post_size": len(post[rel]),
    } for rel in sorted(post)]
    if transition["entries"] != expected_entries:
        raise LegacyRevisionError(f"transition entry bytes drift: {op}")
    log = transition["log"]
    if not isinstance(log, dict) or set(log) != {
            "line", "line_sha256", "post_sha256", "post_size", "pre_sha256", "pre_size"}:
        raise LegacyRevisionError(f"transition log schema drift: {op}")
    line = log["line"]
    log_post = switch["log.md"] + (line.encode("utf-8") if isinstance(line, str) else b"")
    match = _LOG_ANCHOR.fullmatch(line.strip("\n")) if isinstance(line, str) else None
    if (not match or match.group("source") != auth["source_id"]
            or match.group("operation") != op.name
            or match.group("post") != transition["post_manifest_sha256"][:12]
            or log["line_sha256"] != _sha_bytes(line.encode("utf-8"))
            or log["pre_sha256"] != _sha_bytes(switch["log.md"])
            or log["pre_size"] != len(switch["log.md"])
            or log["post_sha256"] != _sha_bytes(log_post)
            or log["post_size"] != len(log_post)):
        raise LegacyRevisionError(f"transition log identity drift: {op}")
    return transition, transition_raw, switch, post


def _verify_operation(op: Path, workspace: Path, *, allow_live_drift: bool) -> dict:
    auth, auth_raw = _verify_authorization(op, workspace)
    events, raws = _events(op)
    if not events or events[0].get("authorization_sha256") != _sha_bytes(auth_raw):
        raise LegacyRevisionError(f"prepared event authorization identity drift: {op}")
    if any(event.get("operation_id") != op.name for event in events):
        raise LegacyRevisionError(f"event/operation identity drift: {op}")
    pre_manifest, pre = _verify_file_set(op / "pre", "pre", op.name)
    expected_pre = {page["path"]: (page["pre_size"], page["pre_sha256"])
                    for page in auth["pages"]}
    if ({rel: (len(raw), _sha_bytes(raw)) for rel, raw in pre.items()} != expected_pre
            or events[0].get("pre_manifest_sha256") != _sha_bytes(
                (op / "pre" / "manifest.json").read_bytes())):
        raise LegacyRevisionError(f"authorization/pre evidence identity drift: {op}")
    candidate_paths = {p.relative_to(op / "candidate" / "files").as_posix()
                       for p in (op / "candidate" / "files").rglob("*") if p.is_file()}
    if candidate_paths != {p["path"] for p in auth["pages"]}:
        raise LegacyRevisionError(f"candidate-set-mismatch: {op}")
    phase = _phase_from_events(events)
    if phase in {"committing", "recovery_requested", "rollback_requested",
                 "completed", "rolled_back"}:
        transition, transition_raw, switch, post = _verify_transition(op, auth)
        committing = events[1]
        if (committing.get("transition_sha256") != _sha_bytes(transition_raw)
                or committing.get("post_manifest_sha256") != transition["post_manifest_sha256"]):
            raise LegacyRevisionError(f"committing event transition identity drift: {op}")
        if phase == "completed":
            completed = events[-1]
            if completed.get("post_manifest_sha256") != transition["post_manifest_sha256"]:
                raise LegacyRevisionError(f"completed event post identity drift: {op}")
            line = completed.get("log_line")
            if (not isinstance(line, str)
                    or completed.get("log_line_sha256") != _sha_bytes(line.encode("utf-8"))):
                raise LegacyRevisionError(f"completed log anchor identity drift: {op}")
            log = Path(workspace) / "wiki" / "log.md"
            count = log.read_text(encoding="utf-8").count(line) if log.is_file() else 0
            if count != 1:
                raise LegacyRevisionError(f"completed operation log anchor count is {count}: {op.name}")
            if not allow_live_drift:
                for page in auth["pages"]:
                    live = Path(workspace) / "wiki" / page["path"]
                    if not live.is_file() or live.read_bytes() != post[page["path"]]:
                        raise LegacyRevisionError(f"post-legacy-revision-live-drift: {page['path']}")
        elif phase == "rolled_back" and not allow_live_drift:
            for page in auth["pages"]:
                live = Path(workspace) / "wiki" / page["path"]
                if not live.is_file() or live.read_bytes() != switch[page["path"]]:
                    raise LegacyRevisionError(f"post-legacy-revision-rollback-live-drift: {page['path']}")
    return {"phase": phase, "authorization": auth, "events": events}


def evidence_findings(workspace: Path) -> list[dict]:
    """Return hard/warning findings for every sidecar operation and log anchor."""
    workspace = Path(workspace)
    root = workspace / "pipeline-workspace" / "legacy-revisions"
    vault = workspace / "wiki"
    findings = []
    operations = {}
    if root.exists():
        try:
            _assert_direct_path(root, workspace, "legacy revision evidence root")
        except LegacyRevisionError as exc:
            return [{"severity": "error", "rule": "legacy-revision-evidence-invalid",
                     "path": str(root), "detail": str(exc)}]
        for source_dir in sorted(root.iterdir()):
            if (not source_dir.is_dir() or source_dir.is_symlink()
                    or evidence_fs.resolved_inside(source_dir, root) is None
                    or not _SOURCE_ID.fullmatch(source_dir.name)):
                findings.append({"severity": "error", "rule": "legacy-revision-orphan-path",
                                 "path": str(source_dir), "detail": "unexpected source directory"})
                continue
            for op in sorted(source_dir.iterdir()):
                if (not op.is_dir() or op.is_symlink()
                        or evidence_fs.resolved_inside(op, source_dir) is None
                        or not _OPERATION_ID.fullmatch(op.name)):
                    findings.append({"severity": "error", "rule": "legacy-revision-orphan-path",
                                     "path": str(op), "detail": "unexpected operation directory"})
                    continue
                operations[(source_dir.name, op.name)] = op
                try:
                    verified = _verify_operation(op, workspace, allow_live_drift=True)
                    phase = verified["phase"]
                    if phase in {"committing", "recovery_requested", "rollback_requested"}:
                        findings.append({
                            "severity": "error", "rule": "legacy-revision-committing",
                            "path": str(op),
                            "detail": "unfinished committing operation; recover it before ordinary lint",
                        })
                    elif phase == "completed":
                        _post_manifest, post = _verify_file_set(op / "post", "post", op.name)
                        drift = [p["path"] for p in verified["authorization"]["pages"]
                                 if not (vault / p["path"]).is_file()
                                 or (vault / p["path"]).read_bytes() != post[p["path"]]]
                        if drift:
                            findings.append({
                                "severity": "warning",
                                "rule": "post-legacy-revision-live-drift", "path": drift[0],
                                "detail": f"completed history remains valid; live target drifted ({len(drift)} page(s))",
                            })
                except (LegacyRevisionError, OSError, UnicodeError, yaml.YAMLError) as exc:
                    findings.append({"severity": "error", "rule": "legacy-revision-evidence-invalid",
                                     "path": str(op), "detail": str(exc)})
    anchors = []
    log = vault / "log.md"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            match = _LOG_ANCHOR.fullmatch(line)
            if match:
                anchors.append((match.group("source"), match.group("operation"), line))
    for source, operation, _line in anchors:
        if (source, operation) not in operations:
            findings.append({
                "severity": "error", "rule": "legacy-revision-orphan-log-anchor",
                "path": "wiki/log.md",
                "detail": f"orphan revise-adopted log anchor has no operation directory: {source}/{operation}",
            })
    return findings


def _locked_run(*, workspace: Path, source: str, request: dict, request_sha: str,
                request_path: Path, apply: bool, abort: bool, recover: str | None,
                expect_live_manifest: Path | None, lock_ttl_seconds: int) -> dict:
    context = _revision_context(workspace, source, lock_ttl_seconds=lock_ttl_seconds)
    root = _operation_root(workspace, source)
    op = _find_request_operation(root, request_sha)
    hard_findings = [finding for finding in evidence_findings(workspace)
                     if finding["severity"] == "error"
                     and not (finding["rule"] == "legacy-revision-committing"
                              and op is not None and Path(finding["path"]) == op)]
    if hard_findings:
        finding = hard_findings[0]
        raise LegacyRevisionError(
            f"{finding['rule']} {finding['path']}: {finding['detail']}")
    if op is None:
        _assert_not_expired(request["valid_until"])
        authorization, candidates = _build_authorization(context, request, request_sha)
        op = root / authorization["operation_id"]
        if not apply:
            return {"phase": "planned", "operation_id": op.name,
                    "pages": [p["path"] for p in authorization["pages"]],
                    "message": "authorization plan verified", "dry_run": True,
                    "warnings": []}
    else:
        authorization, _ = _load_canonical_json(op / "authorization.json", "authorization")
        candidates = None
    verified = _verify_operation(op, workspace, allow_live_drift=True) if op.exists() else None
    phase = verified["phase"] if verified else "none"
    if phase in {"completed", "aborted", "rolled_back"}:
        return {"phase": phase, "operation_id": op.name,
                "pages": [p["path"] for p in authorization["pages"]],
                "message": f"{phase} fully verified", "dry_run": not apply,
                "warnings": []}
    if apply and phase == "prepared" and not abort:
        _assert_not_expired(authorization["valid_until"])
        try:
            _validate_candidates(op, authorization)
        except LegacyRevisionError as exc:
            if "was not edited" not in str(exc):
                raise
            return {"phase": "prepared", "operation_id": op.name,
                    "pages": [p["path"] for p in authorization["pages"]],
                    "message": "prepared; waiting for candidate edits",
                    "dry_run": False, "warnings": []}
    if not apply:
        if phase == "prepared":
            _assert_not_expired(authorization["valid_until"])
            try:
                _validate_candidates(op, authorization)
                message = "prepared candidate verified and ready to commit"
            except LegacyRevisionError as exc:
                if "was not edited" not in str(exc):
                    raise
                message = "prepared; waiting for candidate edits"
            return {"phase": "prepared", "operation_id": op.name,
                    "pages": [p["path"] for p in authorization["pages"]],
                    "message": message, "dry_run": True, "warnings": []}
        return {"phase": phase, "operation_id": op.name,
                "pages": [p["path"] for p in authorization["pages"]],
                "message": f"{phase} requires --apply recovery", "dry_run": True,
                "warnings": []}
    db = context["db"]
    holder = f"revise-adopted:{source}:{os.getpid()}"
    if not locks.acquire(db, scope="vault", holder=holder, pid=os.getpid()):
        current = locks.get(db, scope="vault")
        raise LegacyRevisionError(
            f"active vault lock held by {current['holder'] if current else 'unknown'}")
    try:
        context = _revision_context(workspace, source, allowed_lock_holder=holder,
                                    lock_ttl_seconds=lock_ttl_seconds)
        if phase == "none":
            _assert_not_expired(authorization["valid_until"])
            _assert_scope_still_owns(context, authorization)
            _prepare(op, authorization, candidates, context["vault"])
            _fault_point("prepared")
            return {"phase": "prepared", "operation_id": op.name,
                    "pages": [p["path"] for p in authorization["pages"]],
                    "message": "prepared; edit sidecar candidate files", "dry_run": False,
                    "warnings": []}
        if abort:
            if phase != "prepared":
                raise LegacyRevisionError("--abort is only valid for a prepared operation")
            _write_event(op, "aborted")
            return {"phase": "aborted", "operation_id": op.name,
                    "pages": [p["path"] for p in authorization["pages"]],
                    "message": "prepared operation aborted; live vault unchanged",
                    "dry_run": False, "warnings": []}
        if phase == "prepared":
            _assert_not_expired(authorization["valid_until"])
            _assert_scope_still_owns(context, authorization)
            _build_transition(op, authorization, context)
            _forward(op, authorization, context,
                     expected_unknown=_expected_live_manifest(expect_live_manifest))
        elif phase in {"committing", "recovery_requested", "rollback_requested"}:
            supplied = _expected_live_manifest(expect_live_manifest)
            direction = recover
            expected = supplied
            if phase == "committing" and recover is not None:
                unknown = _unknown_live_bytes(op, authorization, context["vault"])
                if unknown and supplied is None:
                    raise LegacyRevisionError(
                        "controlled live bytes are neither pre nor post; "
                        "provide --expect-live-manifest before freezing recovery")
                if supplied is not None and supplied != unknown:
                    raise LegacyRevisionError(
                        f"expected live manifest does not exactly match unknown bytes: "
                        f"expected={unknown} supplied={supplied}")
                recovery_event = _write_event(op, "recovery_requested", {
                    "direction": recover,
                    "expected_live_sha256": supplied or {},
                })
                direction = recovery_event["direction"]
                expected = recovery_event["expected_live_sha256"]
            elif phase == "recovery_requested":
                events, _ = _events(op)
                recovery_event = events[-1]
                direction = recovery_event.get("direction")
                frozen_expected = recovery_event.get("expected_live_sha256")
                if direction not in {"forward", "rollback"} or not isinstance(
                        frozen_expected, dict):
                    raise LegacyRevisionError("recovery_requested event schema drift")
                if recover is not None and recover != direction:
                    raise LegacyRevisionError("recovery direction differs from frozen event")
                if supplied is not None and supplied != frozen_expected:
                    raise LegacyRevisionError("expected live manifest differs from frozen recovery event")
                expected = frozen_expected
            if direction == "rollback" or phase == "rollback_requested":
                _rollback(op, authorization, context, expected)
            else:
                _forward(op, authorization, context, expected_unknown=expected)
        result = _verify_operation(op, workspace, allow_live_drift=False)
        return {"phase": result["phase"], "operation_id": op.name,
                "pages": [p["path"] for p in authorization["pages"]],
                "message": f"{result['phase']} fully verified", "dry_run": False,
                "warnings": []}
    finally:
        locks.release(db, scope="vault", holder=holder)


def run(*, workspace: Path, source: str, request_path: Path, apply: bool = False,
        abort: bool = False, recover: str | None = None,
        expect_live_manifest: Path | None = None,
        lock_ttl_seconds: int = 1800) -> dict:
    """Plan, prepare, finalize, or recover one deterministic revision operation."""
    if abort and recover:
        raise LegacyRevisionError("--abort and --recover are mutually exclusive")
    if recover not in {None, "forward", "rollback"}:
        raise LegacyRevisionError("--recover must be forward or rollback")
    if (abort or recover or expect_live_manifest) and not apply:
        raise LegacyRevisionError("--abort/--recover/--expect-live-manifest require --apply")
    request, _request_raw, request_sha = _validate_request(Path(request_path), source)
    return _locked_run(
        workspace=Path(workspace).resolve(), source=source, request=request,
        request_sha=request_sha, request_path=Path(request_path), apply=apply,
        abort=abort, recover=recover, expect_live_manifest=expect_live_manifest,
        lock_ttl_seconds=lock_ttl_seconds)
