"""已发布来源的跨 vault 确定性复用：只读核验 + 不可变映射证据。

本旁路不重新摄取 PDF，也不改写目标知识页。默认计划零写；apply 只新增不可变证据、
canonical source 页和专用 reused/published 终态，派生层由 pipeline 在登记终态前重建。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import yaml

import mdpage
import vault_adoption


ReuseError = vault_adoption.AdoptionError
sha256_file = vault_adoption.sha256_file
_json_bytes = vault_adoption._json_bytes
_resolved_inside = vault_adoption._resolved_inside
_assert_direct_contained = vault_adoption._assert_direct_contained

_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_ORIGIN_TABLES = {
    "sources", "source_stage_runs", "artifacts", "work_orders", "source_locks",
    "review_proposals", "ingest_progress", "window_reads",
}
_TARGET_TABLES = {
    "sources", "source_stage_runs", "artifacts", "work_orders",
    "ingest_progress", "window_reads", "source_locks",
}
_EXPORT_QUERIES = {
    "sources": "SELECT * FROM sources WHERE source_id=? ORDER BY source_id",
    "source_stage_runs": "SELECT * FROM source_stage_runs WHERE source_id=? ORDER BY id",
    "artifacts": "SELECT * FROM artifacts WHERE source_id=? ORDER BY id",
    "work_orders": "SELECT * FROM work_orders WHERE source_id=? ORDER BY source_id",
    "review_proposals": "SELECT * FROM review_proposals WHERE source_id=? ORDER BY id",
    "ingest_progress": "SELECT * FROM ingest_progress WHERE source_id=? ORDER BY id",
    "window_reads": "SELECT * FROM window_reads WHERE source_id=? ORDER BY window_id",
}
_DERIVED = {
    "index.generated.md", "quiz-index.generated.md", "propositions.generated.md",
    "knowledge-graph.generated.html", "graph-data.generated.json", "aliases.md", "log.md",
}
_EXCLUDE_TOP = {".obsidian", "Review-Queue", "_meta", "assets"}


def _direct_root(path: Path, label: str) -> Path:
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ReuseError(f"{label} not found: {raw}") from exc
    lexical = Path(os.path.abspath(str(raw)))
    if (not resolved.is_dir() or raw.is_symlink()
            or os.path.normcase(str(resolved)) != os.path.normcase(str(lexical))):
        raise ReuseError(f"{label} must be a direct, non-redirected directory: {raw} -> {resolved}")
    return resolved


def _direct_file(path: Path, label: str) -> Path:
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ReuseError(f"{label} not found: {raw}") from exc
    lexical = Path(os.path.abspath(str(raw)))
    if (not resolved.is_file() or raw.is_symlink()
            or os.path.normcase(str(resolved)) != os.path.normcase(str(lexical))):
        raise ReuseError(f"{label} must be a direct, non-redirected file: {raw} -> {resolved}")
    return resolved


def _safe_rel(value, label: str) -> str:
    if not isinstance(value, str):
        raise ReuseError(f"{label} must be a vault-relative POSIX Markdown path")
    parts = value.split("/")
    if (not value.endswith(".md") or value.startswith("/") or "\\" in value
            or ":" in value
            or any(part in ("", ".", "..") for part in parts)):
        raise ReuseError(f"{label} must be a safe vault-relative POSIX .md path: {value!r}")
    return value


def _has_source_ref(meta: dict, source: str) -> bool:
    refs = meta.get("source_refs") or []
    return any(isinstance(ref, dict) and ref.get("source") == source for ref in refs)


def _page_entry(path: Path, root: Path, rel: str, **extra) -> dict:
    resolved = _resolved_inside(path, root)
    if resolved is None or path.is_symlink() or not resolved.is_file():
        raise ReuseError(f"page escapes its vault or is not a regular file: {rel}")
    raw = path.read_bytes()
    return {"path": rel, "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), **extra}


def _read_only_db(path: Path, *, label: str) -> sqlite3.Connection:
    resolved = path.resolve()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(resolved) + suffix)
        if os.path.lexists(str(sidecar)):
            raise ReuseError(
                f"{label} SQLite sidecar is forbidden for read-only reuse: {sidecar}")
    with resolved.open("rb") as fh:
        header = fh.read(100)
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise ReuseError(f"{label} has an invalid SQLite header: {resolved}")
    # SQLite header bytes 18/19 are the write/read format versions. 2 means WAL.
    # Reject before sqlite3 opens the file: even mode=ro may create -shm for WAL.
    if header[18] == 2 or header[19] == 2:
        raise ReuseError(
            f"{label} SQLite journal_mode WAL is forbidden; checkpoint and switch "
            "the database to a rollback journal first")
    # WAL has already been rejected, so normal mode=ro cannot create WAL sidecars.
    # Keep SQLite locking/change detection enabled: immutable=1 would ignore a hot
    # rollback journal and could read through a concurrent writer.
    con = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=1.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    # Pin every table read below to one explicit, consistent read transaction.
    con.execute("BEGIN")
    return con


def _origin_state_snapshot(origin_root: Path, source: str, pdf_path: Path,
                           pdf_sha256: str) -> tuple[dict, bytes]:
    db = origin_root / "pipeline-workspace" / "state" / "study-kb.sqlite"
    _assert_direct_contained(db, origin_root, "origin state database")
    if not db.is_file() or db.is_symlink():
        raise ReuseError(f"origin state database not found: {db}")
    try:
        con = _read_only_db(db, label="origin state database")
    except sqlite3.Error as exc:
        raise ReuseError(f"cannot open origin state read-only: {exc}") from exc
    try:
        tables = {row["name"] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(_ORIGIN_TABLES - tables)
        if missing:
            raise ReuseError(f"origin state schema missing tables: {', '.join(missing)}")
        locks = [dict(row) for row in con.execute(
            "SELECT * FROM source_locks ORDER BY scope")]
        if locks:
            raise ReuseError(
                f"origin vault has an active lock; read-only reuse refused: {locks[0]['holder']}")
        exported = {
            "schema_version": 1,
            **{table: [dict(row) for row in con.execute(sql, (source,))]
               for table, sql in _EXPORT_QUERIES.items()},
            "source_locks": locks,
        }
    except sqlite3.Error as exc:
        raise ReuseError(f"cannot inspect origin state: {exc}") from exc
    finally:
        con.close()

    rows = exported["sources"]
    if len(rows) != 1:
        raise ReuseError(f"origin state must contain exactly one source row for {source!r}")
    row = rows[0]
    if (row.get("format"), row.get("current_stage"), row.get("current_status")) != \
            ("pdf", "lint", "published"):
        raise ReuseError(
            f"origin source {source!r} must be pdf lint/published, got "
            f"{row.get('format')}/{row.get('current_stage')}/{row.get('current_status')}")
    lint_rows = [r for r in exported["source_stage_runs"] if r.get("stage") == "lint"]
    if not lint_rows or lint_rows[-1].get("status") != "done":
        raise ReuseError("origin published state lacks a terminal successful lint stage")
    raw_rows = [r for r in exported["artifacts"] if r.get("kind") == "raw_source"]
    if len(raw_rows) != 1:
        raise ReuseError("origin state must contain exactly one raw_source artifact")
    raw = raw_rows[0]
    try:
        artifact_path = _direct_file(Path(raw["path"]), "origin raw_source artifact")
    except (KeyError, TypeError) as exc:
        raise ReuseError("origin raw_source artifact has no valid path") from exc
    if (os.path.normcase(str(artifact_path)) != os.path.normcase(str(pdf_path))
            or str(raw.get("sha256", "")).lower() != pdf_sha256):
        raise ReuseError(
            "origin raw_source artifact does not match the specified PDF path/SHA-256")
    state_bytes = _json_bytes(exported)
    return exported, state_bytes


def _origin_pages(origin_root: Path, source: str, *, expected_domain: str) \
        -> tuple[dict, list[dict], list[dict]]:
    vault = origin_root / "wiki"
    _assert_direct_contained(vault, origin_root, "origin wiki")
    if not vault.is_dir() or vault.is_symlink():
        raise ReuseError(f"origin wiki not found: {vault}")
    source_rel = f"sources/{source}.md"
    source_path = vault / source_rel
    source_entry = _page_entry(source_path, vault, source_rel)
    source_meta, _body = mdpage.read_page(source_path)
    if (source_meta.get("source_id"), source_meta.get("type"),
            source_meta.get("status"), source_meta.get("format"),
            source_meta.get("domain")) != \
            (source, "source", "published", "pdf", expected_domain):
        raise ReuseError(
            f"origin source page must be canonical published pdf source: {source_rel}")

    concepts: list[dict] = []
    topics: list[dict] = []
    canonical_ids: set[str] = set()
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if rel == source_rel or rel in _DERIVED or rel.split("/", 1)[0] in _EXCLUDE_TOP:
            continue
        entry = _page_entry(path, vault, rel)
        try:
            meta, _ = mdpage.read_page(path)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ReuseError(f"cannot read origin page {rel}: {exc}") from exc
        if meta.get("type") not in ("concept", "topic") or not _has_source_ref(meta, source):
            continue
        if meta.get("status") != "published":
            raise ReuseError(f"origin source-owned page is not published: {rel}")
        if meta["type"] == "concept":
            canonical_id = meta.get("canonical_id")
            if not isinstance(canonical_id, str) or not canonical_id.strip():
                raise ReuseError(f"origin concept lacks canonical_id: {rel}")
            if canonical_id in canonical_ids:
                raise ReuseError(f"origin concept canonical_id is duplicated: {canonical_id}")
            canonical_ids.add(canonical_id)
            concepts.append({**entry, "canonical_id": canonical_id})
        else:
            topics.append({**entry, "title": str(meta.get("title") or Path(rel).stem)})
    concepts.sort(key=lambda item: item["path"])
    topics.sort(key=lambda item: item["path"])
    if len(concepts) != 37 or len(topics) != 3:
        raise ReuseError(
            f"origin mysql truth must be exactly 37 concepts + 3 topics; "
            f"got {len(concepts)} concepts + {len(topics)} topics")
    return source_entry, concepts, topics


def _load_mapping(path: Path, source: str, origin_concepts: list[dict]) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReuseError("mapping must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or set(data) != {"version", "source_id", "targets"}:
        raise ReuseError("mapping top-level fields must be exactly version/source_id/targets")
    if data["version"] != 1 or data["source_id"] != source or not isinstance(data["targets"], list):
        raise ReuseError("mapping requires version=1, matching source_id, and a targets list")
    targets: list[dict] = []
    seen_targets: set[str] = set()
    seen_origins: set[str] = set()
    seen_origin_keys: set[str] = set()
    for index, item in enumerate(data["targets"]):
        if not isinstance(item, dict) or set(item) != {"target", "origin_concepts"}:
            raise ReuseError(
                f"mapping targets[{index}] fields must be exactly target/origin_concepts")
        target = _safe_rel(item["target"], f"mapping targets[{index}].target")
        origins = item["origin_concepts"]
        if not isinstance(origins, list):
            raise ReuseError(f"mapping origins for {target} must be a list")
        normalised = [_safe_rel(value, f"mapping origin for {target}") for value in origins]
        origin_keys = [value.casefold() for value in normalised]
        if normalised != sorted(normalised) or len(set(origin_keys)) != len(origin_keys):
            raise ReuseError(f"mapping origin_concepts must be sorted and unique for {target}")
        target_key = target.casefold()
        if target_key in seen_targets:
            raise ReuseError(f"mapping target is duplicated: {target}")
        seen_targets.add(target_key)
        for origin in normalised:
            origin_key = origin.casefold()
            if origin_key in seen_origin_keys:
                raise ReuseError(f"origin concept mapped more than once: {origin}")
            seen_origin_keys.add(origin_key)
            seen_origins.add(origin)
        targets.append({"target": target, "origin_concepts": normalised})
    target_paths = [item["target"] for item in targets]
    if target_paths != sorted(target_paths):
        raise ReuseError("mapping targets must be sorted by target path")
    expected = {item["path"] for item in origin_concepts}
    if seen_origins != expected:
        missing = sorted(expected - seen_origins)
        extra = sorted(seen_origins - expected)
        raise ReuseError(
            "mapping must cover all 37 origin concepts exactly once "
            f"(missing={len(missing)}, extra={len(extra)})")
    zero = [item for item in targets if not item["origin_concepts"]]
    mapped = len(targets) - len(zero)
    if len(targets) != 8 or mapped != 6 or len(zero) != 2:
        raise ReuseError(
            "mapping must contain exactly 8 targets (6 mapped + 2 zero-mapping); "
            f"got {len(targets)} targets ({mapped} mapped + {len(zero)} zero-mapping)")
    return raw, {"version": 1, "source_id": source, "targets": targets}


def _target_pages(vault: Path, source: str, mapping: dict) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    warnings: list[dict] = []
    mapped_paths: set[str] = set()
    for item in mapping["targets"]:
        rel = item["target"]
        path = vault / rel
        if not path.is_file():
            raise ReuseError(f"mapping target does not exist: {rel}")
        entry = _page_entry(path, vault, rel, mapped_origin_count=len(item["origin_concepts"]))
        try:
            meta, _body = mdpage.read_page(path)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ReuseError(f"cannot read mapping target {rel}: {exc}") from exc
        if meta.get("type") != "concept" or meta.get("status") != "published":
            raise ReuseError(f"mapping target must be a published concept page: {rel}")
        attributed = _has_source_ref(meta, source)
        count = len(item["origin_concepts"])
        if count and not attributed:
            raise ReuseError(
                f"mapped-target-missing-source-ref: {rel} must already contain source_refs source={source}")
        if not count and attributed:
            raise ReuseError(
                f"zero-mapping-target-false-attribution: {rel} must not contain source_refs source={source}")
        if count:
            mapped_paths.add(rel)
        entries.append(entry)

    # Mapping 是归因边界：除 37→非空目标外，任何已有 mysql source_ref 都是未声明归因。
    attributed_paths: set[str] = set()
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if rel in _DERIVED or rel == f"sources/{source}.md" \
                or rel.split("/", 1)[0] in _EXCLUDE_TOP:
            continue
        if (path.is_symlink() or not path.is_file()
                or _resolved_inside(path, vault) is None):
            raise ReuseError(
                f"target Markdown page escapes its vault or is not a regular file: {rel}")
        try:
            meta, _body = mdpage.read_page(path)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ReuseError(f"cannot inspect target source_refs on {rel}: {exc}") from exc
        if _has_source_ref(meta, source):
            attributed_paths.add(rel)
    extras = sorted(attributed_paths - mapped_paths)
    if extras:
        raise ReuseError(
            f"unmapped-target-false-attribution: source={source} appears outside mapping: {extras[0]}")
    missing = sorted(mapped_paths - attributed_paths)
    if missing:
        raise ReuseError(
            f"mapped-target-missing-source-ref: source={source} absent from {missing[0]}")
    return entries, warnings


def _load_stored_manifest(evidence_dir: Path) -> tuple[dict, bytes] | None:
    if not evidence_dir.exists():
        return None
    manifest = evidence_dir / "manifest.json"
    if not evidence_dir.is_dir() or evidence_dir.is_symlink() or not manifest.is_file():
        raise ReuseError("reuse evidence exists without immutable manifest.json")
    _assert_direct_contained(manifest, evidence_dir, "reuse evidence manifest")
    raw = manifest.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReuseError("reuse evidence manifest is not valid canonical JSON") from exc
    required = {
        "domain", "format", "mapped_target_count", "mapping_sha256",
        "origin_concepts", "origin_root", "origin_source", "origin_source_page",
        "origin_state_sha256", "origin_topics", "pdf_path", "pdf_sha256", "source_id",
        "target_pages", "title", "version", "zero_mapping_target_count",
    }
    if not isinstance(data, dict) or set(data) != required or raw != _json_bytes(data):
        raise ReuseError("reuse evidence manifest schema/canonical bytes drift")
    if data.get("version") != 1 or data.get("format") != "external-vault-reuse":
        raise ReuseError("reuse evidence manifest version/format drift")
    return data, raw


def _evidence_expected_files(manifest: dict) -> set[str]:
    origin_entries = ([manifest["origin_source_page"]] + manifest["origin_concepts"]
                      + manifest["origin_topics"])
    return ({"manifest.json", "mapping.json", "origin-state.json"}
            | {f"origin-files/{entry['path']}" for entry in origin_entries}
            | {f"target-files/{entry['path']}" for entry in manifest["target_pages"]})


def _validate_evidence(evidence_dir: Path, manifest_bytes: bytes,
                       manifest: dict) -> list[str]:
    if not evidence_dir.exists():
        return []
    try:
        _assert_direct_contained(evidence_dir, evidence_dir.parent, "reuse evidence")
        actual = {p.relative_to(evidence_dir).as_posix()
                  for p in evidence_dir.rglob("*") if p.is_file() or p.is_symlink()}
    except (OSError, ReuseError) as exc:
        return [f"reuse-evidence-corrupt: {exc}"]
    errors: list[str] = []
    if actual != _evidence_expected_files(manifest):
        errors.append("reuse-evidence-corrupt: evidence file set differs from manifest")
    manifest_path = evidence_dir / "manifest.json"
    if (not manifest_path.is_file() or manifest_path.is_symlink()
            or manifest_path.read_bytes() != manifest_bytes):
        errors.append("reuse-evidence-corrupt: manifest bytes differ")
    mapping = evidence_dir / "mapping.json"
    if (not mapping.is_file() or mapping.is_symlink()
            or sha256_file(mapping) != manifest["mapping_sha256"]):
        errors.append("reuse-evidence-corrupt: stored mapping bytes differ")
    origin_state = evidence_dir / "origin-state.json"
    if (not origin_state.is_file() or origin_state.is_symlink()
            or sha256_file(origin_state) != manifest["origin_state_sha256"]):
        errors.append("reuse-evidence-corrupt: stored origin state bytes differ")
    origin_entries = ([manifest["origin_source_page"]] + manifest["origin_concepts"]
                      + manifest["origin_topics"])
    for prefix, entries in (("origin-files", origin_entries),
                            ("target-files", manifest["target_pages"])):
        for entry in entries:
            copied = evidence_dir / prefix / entry["path"]
            try:
                inside = _resolved_inside(copied, evidence_dir) is not None
            except OSError:
                inside = False
            if (not inside or not copied.is_file() or copied.is_symlink()
                    or copied.stat().st_size != entry["size"]
                    or sha256_file(copied) != entry["sha256"]):
                errors.append(
                    f"reuse-evidence-corrupt: stored {prefix} bytes differ: {entry['path']}")
    return errors


def _source_page_bytes(plan: dict) -> bytes:
    manifest_rel = f"pipeline-workspace/reuses/{plan['source']}/manifest.json"
    meta = {
        "domain": plan["domain"],
        "format": "external-vault-reuse",
        "managed_by": "pipeline",
        "origin_root": str(plan["origin_root"]),
        "origin_source": plan["origin_source"],
        "raw_path": str(plan["pdf_path"]),
        "raw_sha256": plan["pdf_sha256"],
        "reuse_manifest": manifest_rel,
        "reuse_manifest_sha256": plan["manifest_sha256"],
        "source_id": plan["source"],
        "status": "published",
        "title": plan["title"],
        "type": "source",
    }
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True)
    mapped = [item for item in plan["mapping"]["targets"] if item["origin_concepts"]]
    links = [
        f"- [[{item['target']}|{Path(item['target']).stem}]]："
        f"主映射 {len(item['origin_concepts'])} 个 origin concept。"
        for item in mapped
    ]
    body = [
        "本页登记的是从另一个只读、已发布 vault 选择性复用现有知识，不代表在本 vault "
        "重新摄取或重写 PDF。",
        "",
        f"原 vault `{plan['origin_root']}` 的 `{plan['origin_source']}` 已核验为 "
        "`lint/published`；PDF SHA-256、原 source 页、37 张 concept、3 张 topic、状态快照和 "
        "37→目标页映射均冻结在不可变 reuse evidence 中。",
        "",
        "映射到本 vault 的目标页：",
        "",
        *links,
        "",
        "另有 2 张目标页被显式登记为零映射，因此没有获得本来源的 source_refs 归因。"
        "本旁路不创建 work order、processing window 或 window read/write ledger。",
        "",
    ]
    return f"---\n{fm}---\n".encode("utf-8") + "\n".join(body).encode("utf-8")


def _target_state_snapshot(db: Path, source: str) -> dict | None:
    """Read target state without allowing SQLite to create WAL sidecars."""
    if not os.path.lexists(str(db)):
        return None
    if not db.is_file() or db.is_symlink():
        raise ReuseError(f"target state database must be a direct regular file: {db}")
    try:
        con = _read_only_db(db, label="target state database")
    except sqlite3.Error as exc:
        raise ReuseError(f"cannot read target state database without writing: {exc}") from exc
    try:
        tables = {row["name"] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(_TARGET_TABLES - tables)
        if missing:
            return {"schema_missing": missing}
        lock = con.execute(
            "SELECT * FROM source_locks WHERE scope='vault'").fetchone()
        src = con.execute(
            "SELECT * FROM sources WHERE source_id=?", (source,)).fetchone()
        stages = con.execute(
            "SELECT stage,status,input_hash,output_hash FROM source_stage_runs "
            "WHERE source_id=? ORDER BY id", (source,)).fetchall()
        artifacts = con.execute(
            "SELECT kind,path,sha256 FROM artifacts WHERE source_id=? ORDER BY id",
            (source,),
        ).fetchall()
        ledgers = {
            table: con.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE source_id=?", (source,)
            ).fetchone()["n"]
            for table in ("work_orders", "ingest_progress", "window_reads")
        }
        return {
            "lock": dict(lock) if lock else None,
            "source": dict(src) if src else None,
            "stages": [dict(row) for row in stages],
            "artifacts": [dict(row) for row in artifacts],
            "ledgers": ledgers,
        }
    except sqlite3.Error as exc:
        raise ReuseError(f"cannot inspect target state database: {exc}") from exc
    finally:
        con.close()


def _target_state_violations(snapshot: dict | None, *, plan: dict) -> list[str]:
    if snapshot is None:
        return []
    if snapshot.get("schema_missing"):
        return [f"reuse-state-conflict: target state schema missing {snapshot['schema_missing']}"]
    source = snapshot["source"]
    stages = [(r["stage"], r["status"], r["input_hash"], r["output_hash"])
              for r in snapshot["stages"]]
    artifacts = [(r["kind"], r["path"], r["sha256"]) for r in snapshot["artifacts"]]
    expected_source = (plan["domain"], "external-vault-reuse", "reused", "published")
    expected_stage = [("reused", "done", plan["manifest_sha256"],
                       plan["manifest_sha256"])]
    expected_artifact = [("reuse_evidence", str(plan["manifest_path"].resolve()),
                          plan["manifest_sha256"])]
    if source is None:
        if stages or artifacts or any(snapshot["ledgers"].values()):
            return ["reuse-state-conflict: orphan stage/artifact/ingest ledger exists"]
        return []
    actual_source = (source["domain"], source["format"],
                     source["current_stage"], source["current_status"])
    if (actual_source != expected_source or stages != expected_stage
            or artifacts != expected_artifact or any(snapshot["ledgers"].values())):
        return ["reuse-state-conflict: existing source is not the exact reused/published state"]
    return []


def build_plan(*, workspace: Path, source: str, title: str, domain: str,
               pdf_path: Path, pdf_sha256: str, origin_root: Path,
               origin_source: str, mapping_path: Path, lock_ttl_seconds: int = 1800,
               allowed_lock_holder: str | None = None) -> dict:
    """只读构建跨 vault 复用计划；不创建目录、数据库、报告或锁。"""
    if not _SOURCE_ID.fullmatch(source) or not _SOURCE_ID.fullmatch(origin_source):
        raise ReuseError("source ids must use only ASCII letters/digits/./_/-")
    if source != origin_source:
        raise ReuseError("reuse-source requires --source and --origin-source to match")
    if not title.strip() or not domain.strip():
        raise ReuseError("title/domain must not be empty")
    if not _SHA256.fullmatch(pdf_sha256.strip()):
        raise ReuseError("PDF sha256 must be exactly 64 hexadecimal characters")

    workspace = _direct_root(Path(workspace), "target workspace")
    vault = workspace / "wiki"
    _assert_direct_contained(vault, workspace, "target wiki")
    if not vault.is_dir() or vault.is_symlink():
        raise ReuseError(f"target wiki not found: {vault}")
    origin_root = _direct_root(Path(origin_root), "origin root")
    if (origin_root == workspace or origin_root in workspace.parents
            or workspace in origin_root.parents):
        raise ReuseError("origin root and target workspace must be disjoint")
    pdf_path = _direct_file(Path(pdf_path), "PDF")
    expected_pdf_sha = pdf_sha256.strip().lower()
    actual_pdf_sha = sha256_file(pdf_path)
    if actual_pdf_sha != expected_pdf_sha:
        raise ReuseError(
            f"PDF sha256 mismatch: expected {expected_pdf_sha}, actual {actual_pdf_sha}")
    mapping_path = _direct_file(Path(mapping_path), "mapping JSON")

    evidence_dir = workspace / "pipeline-workspace" / "reuses" / source
    manifest_path = evidence_dir / "manifest.json"
    source_path = vault / "sources" / f"{source}.md"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    for path, root, label in (
            (evidence_dir, workspace, "reuse evidence"),
            (source_path, vault, "reuse source page"),
            (db, workspace, "target state database")):
        _assert_direct_contained(path, root, label)
    evidence_mapping = evidence_dir / "mapping.json"
    if evidence_dir in mapping_path.parents and mapping_path != evidence_mapping:
        raise ReuseError(
            "mapping JSON inside reuse evidence must be the immutable evidence mapping.json")

    origin_state, origin_state_bytes = _origin_state_snapshot(
        origin_root, origin_source, pdf_path, actual_pdf_sha)
    origin_source_page, origin_concepts, origin_topics = _origin_pages(
        origin_root, origin_source, expected_domain=origin_state["sources"][0]["domain"])
    mapping_bytes, mapping = _load_mapping(mapping_path, source, origin_concepts)
    mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()
    target_pages, warnings = _target_pages(vault, source, mapping)
    mapped_count = sum(bool(item["origin_concepts"]) for item in mapping["targets"])
    zero_count = len(mapping["targets"]) - mapped_count

    stored = _load_stored_manifest(evidence_dir)
    current_manifest = {
        "domain": domain,
        "format": "external-vault-reuse",
        "mapped_target_count": mapped_count,
        "mapping_sha256": mapping_sha,
        "origin_concepts": origin_concepts,
        "origin_root": str(origin_root),
        "origin_source": origin_source,
        "origin_source_page": origin_source_page,
        "origin_state_sha256": hashlib.sha256(origin_state_bytes).hexdigest(),
        "origin_topics": origin_topics,
        "pdf_path": str(pdf_path),
        "pdf_sha256": actual_pdf_sha,
        "source_id": source,
        "target_pages": target_pages,
        "title": title,
        "version": 1,
        "zero_mapping_target_count": zero_count,
    }
    if stored is None:
        manifest_data = current_manifest
        manifest_bytes = _json_bytes(manifest_data)
    else:
        manifest_data, manifest_bytes = stored
        stable_keys = set(current_manifest) - {"target_pages"}
        if any(current_manifest[key] != manifest_data.get(key) for key in stable_keys):
            if (current_manifest["origin_concepts"] != manifest_data.get("origin_concepts")
                    or current_manifest["origin_topics"] != manifest_data.get("origin_topics")
                    or current_manifest["origin_source_page"] != manifest_data.get("origin_source_page")
                    or current_manifest["origin_state_sha256"] != manifest_data.get("origin_state_sha256")):
                raise ReuseError("origin snapshot drift from immutable reuse manifest")
            raise ReuseError("reuse manifest metadata/mapping/PDF drift")
        old_targets = {entry["path"]: entry for entry in manifest_data["target_pages"]}
        new_targets = {entry["path"]: entry for entry in target_pages}
        if (set(old_targets) != set(new_targets)
                or any(old_targets[path].get("mapped_origin_count")
                       != new_targets[path].get("mapped_origin_count") for path in old_targets)):
            raise ReuseError("reuse mapping target set/count drift from immutable manifest")
        changed = sorted(path for path in old_targets
                         if old_targets[path]["sha256"] != new_targets[path]["sha256"])
        if changed:
            warnings.append({
                "rule": "post-reuse-target-live-drift", "path": "wiki/",
                "detail": f"target pages changed after reuse; historical bytes preserved: {len(changed)}",
            })

    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    plan = {
        "workspace": workspace, "vault": vault, "source": source, "title": title,
        "domain": domain, "pdf_path": pdf_path, "pdf_sha256": actual_pdf_sha,
        "origin_root": origin_root, "origin_source": origin_source,
        "origin_state": origin_state, "origin_state_bytes": origin_state_bytes,
        "origin_source_page": origin_source_page, "origin_concepts": origin_concepts,
        "origin_topics": origin_topics, "mapping_path": mapping_path,
        "mapping_bytes": mapping_bytes, "mapping_sha256": mapping_sha, "mapping": mapping,
        "target_pages": target_pages, "mapped_target_count": mapped_count,
        "zero_mapping_target_count": zero_count, "warnings": warnings,
        "evidence_dir": evidence_dir, "manifest_path": manifest_path,
        "manifest_data": manifest_data, "manifest_bytes": manifest_bytes,
        "manifest_sha256": manifest_sha, "source_path": source_path, "db": db,
    }
    plan["source_bytes"] = _source_page_bytes(plan)

    evidence_errors = _validate_evidence(evidence_dir, manifest_bytes, manifest_data)
    if evidence_errors:
        raise ReuseError(evidence_errors[0])
    evidence_verified = stored is not None
    source_verified = False
    if source_path.exists():
        if (not source_path.is_file() or source_path.is_symlink()
                or _resolved_inside(source_path, vault) is None
                or source_path.read_bytes() != plan["source_bytes"]):
            raise ReuseError("reuse source page conflict; refusing to overwrite")
        source_verified = True

    snapshot = _target_state_snapshot(db, source)
    vault_adoption._reject_lock(snapshot, lock_ttl_seconds, allowed_lock_holder)
    state_errors = _target_state_violations(snapshot, plan=plan)
    if state_errors:
        raise ReuseError(state_errors[0])
    state_verified = bool(snapshot and snapshot.get("source"))
    if stored is None and (source_verified or state_verified):
        raise ReuseError("reuse evidence missing for existing source page/state")
    if state_verified and not source_verified:
        raise ReuseError("published reuse state requires its exact canonical source page")
    plan.update({"evidence_verified": evidence_verified,
                 "source_verified": source_verified, "state_verified": state_verified})
    return plan


def validate_output_paths(plan: dict) -> None:
    _assert_direct_contained(plan["vault"], plan["workspace"], "target wiki")
    _assert_direct_contained(plan["evidence_dir"], plan["workspace"], "reuse evidence")
    _assert_direct_contained(plan["source_path"], plan["vault"], "reuse source page")
    _assert_direct_contained(plan["db"], plan["workspace"], "target state database")


def derived_violations(vault: Path) -> list[str]:
    """Read-only proof that every formal derived artifact matches current published pages.

    Graph ``generated_at`` is intentionally preserved from the live canonical artifact while
    every semantic byte is recomputed; rebuilding solely to manufacture a new timestamp would
    violate the exact-repeat byte/mtime no-op contract.
    """
    import concept_store
    import graph_analysis
    import graph_data
    import graph_html
    import graph_lint
    import graph_model
    import wiki_gate

    vault = Path(vault)
    formal = (
        "concepts/_registry.yaml",
        "index.generated.md",
        "graph-data.generated.json",
        "knowledge-graph.generated.html",
        "quiz-index.generated.md",
        "propositions.generated.md",
    )
    raw: dict[str, bytes] = {}
    findings: list[str] = []
    for rel in formal:
        path = vault / rel
        if (not path.is_file() or path.is_symlink()
                or _resolved_inside(path, vault) is None):
            findings.append(f"derived artifact missing/redirected: {rel}")
            continue
        try:
            raw[rel] = path.read_bytes()
        except OSError as exc:
            findings.append(f"derived artifact unreadable: {rel}: {exc}")
    aliases = vault / "aliases.md"
    if os.path.lexists(str(aliases)):
        findings.append("retired derived artifact still exists: aliases.md")

    try:
        registry, errors, _warnings = concept_store.build_registry(
            concept_store.scan_concept_pages(vault))
        if errors:
            findings.append(f"derived registry input invalid: {errors[0]}")
        else:
            expected = yaml.safe_dump(
                {key: registry[key] for key in sorted(registry)},
                allow_unicode=True, sort_keys=True, default_flow_style=False,
            ).encode("utf-8")
            if raw.get("concepts/_registry.yaml") != expected:
                findings.append("derived artifact drift: concepts/_registry.yaml")
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        findings.append(f"derived registry verification failed: {exc}")

    for rel, builder in (
            ("index.generated.md", wiki_gate.build_index),
            ("quiz-index.generated.md", wiki_gate.build_quiz_index),
            ("propositions.generated.md", wiki_gate.build_propositions_index)):
        try:
            expected = builder(vault).encode("utf-8")
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            findings.append(f"derived artifact verification failed: {rel}: {exc}")
        else:
            if raw.get(rel) != expected:
                findings.append(f"derived artifact drift: {rel}")

    graph_rel = "graph-data.generated.json"
    html_rel = "knowledge-graph.generated.html"
    graph = None
    expected_graph = None
    if graph_rel in raw:
        try:
            graph = json.loads(raw[graph_rel].decode("utf-8"))
            generated_at = graph.get("generated_at") if isinstance(graph, dict) else None
            if not isinstance(generated_at, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", generated_at):
                raise ValueError("generated_at is not canonical UTC seconds")
            model = graph_model.build_graph_model(vault)
            analyzed = graph_analysis.analyze_graph(model)
            expected_graph = graph_data.to_graph_data(analyzed)
            expected_graph["generated_at"] = generated_at
            lint = graph_lint.validate_graph_data(graph, vault=vault)
            if lint["errors"]:
                findings.append(f"derived graph structure invalid: {lint['errors'][0]}")
            expected_bytes = (json.dumps(expected_graph, ensure_ascii=False, indent=2)
                              + "\n").encode("utf-8")
            if raw[graph_rel] != expected_bytes:
                findings.append(f"derived artifact drift: {graph_rel}")
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            findings.append(f"derived graph verification failed: {exc}")

    if html_rel in raw:
        try:
            html = raw[html_rel].decode("utf-8")
            html_errors = graph_lint.validate_html(html)
            if html_errors:
                findings.append(f"derived graph HTML invalid: {html_errors[0]}")
            if expected_graph is not None:
                expected_html = graph_html.to_html(
                    expected_graph, vault_root=vault.resolve().as_posix()).encode("utf-8")
                if raw[html_rel] != expected_html:
                    findings.append(f"derived artifact drift: {html_rel}")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            findings.append(f"derived graph HTML verification failed: {exc}")
    return findings


def _verify_live_entry(root: Path, entry: dict, label: str) -> bytes:
    path = root / entry["path"]
    if (not path.is_file() or path.is_symlink() or _resolved_inside(path, root) is None):
        raise ReuseError(f"{label} page drift: {entry['path']}")
    raw = path.read_bytes()
    if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ReuseError(f"{label} page drift: {entry['path']}")
    return raw


def verify_live_inputs(plan: dict) -> None:
    """Revalidate every external truth input without writing either vault."""
    if sha256_file(plan["pdf_path"]) != plan["pdf_sha256"]:
        raise ReuseError("PDF drift after validation")
    if hashlib.sha256(plan["mapping_path"].read_bytes()).hexdigest() != \
            plan["mapping_sha256"]:
        raise ReuseError("mapping drift after validation")
    _state, state_bytes = _origin_state_snapshot(
        plan["origin_root"], plan["origin_source"],
        plan["pdf_path"], plan["pdf_sha256"])
    if state_bytes != plan["origin_state_bytes"]:
        raise ReuseError("origin state drift after validation")
    origin_vault = plan["origin_root"] / "wiki"
    for entry in ([plan["origin_source_page"]] + plan["origin_concepts"]
                  + plan["origin_topics"]):
        _verify_live_entry(origin_vault, entry, "origin")


def write_evidence(plan: dict) -> bool:
    """首次原子写不可变 reuse evidence；已存在且逐字验证时幂等返回 False。"""
    validate_output_paths(plan)
    evidence_dir: Path = plan["evidence_dir"]
    existing = _validate_evidence(
        evidence_dir, plan["manifest_bytes"], plan["manifest_data"])
    if evidence_dir.exists():
        if existing:
            raise ReuseError(existing[0])
        # Recovery/replay still rechecks live PDF, mapping, origin state and pages while
        # the caller holds the target lock; immutable evidence is not a bypass.
        verify_live_inputs(plan)
        return False
    verify_live_inputs(plan)

    parent = evidence_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    _assert_direct_contained(parent, plan["workspace"], "reuse evidence parent")
    temp = parent / f".{plan['source']}.{uuid.uuid4().hex}.tmp"
    temp.mkdir()
    try:
        origin_vault = plan["origin_root"] / "wiki"
        origin_entries = ([plan["origin_source_page"]] + plan["origin_concepts"]
                          + plan["origin_topics"])
        for entry in origin_entries:
            raw = _verify_live_entry(origin_vault, entry, "origin")
            target = temp / "origin-files" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        # First write only: target_pages are the current immutable reuse baseline.
        for entry in plan["manifest_data"]["target_pages"]:
            raw = _verify_live_entry(plan["vault"], entry, "target")
            target = temp / "target-files" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (temp / "mapping.json").write_bytes(plan["mapping_bytes"])
        (temp / "origin-state.json").write_bytes(plan["origin_state_bytes"])
        (temp / "manifest.json").write_bytes(plan["manifest_bytes"])
        # Double-collect before publishing the evidence directory so state/page/PDF
        # changes during the copy cannot produce a Frankenstein snapshot.
        verify_live_inputs(plan)
        errors = _validate_evidence(temp, plan["manifest_bytes"], plan["manifest_data"])
        if errors:
            raise ReuseError(errors[0])
        try:
            temp.rename(evidence_dir)
        except OSError:
            if evidence_dir.exists() and not _validate_evidence(
                    evidence_dir, plan["manifest_bytes"], plan["manifest_data"]):
                return False
            raise
        errors = _validate_evidence(
            evidence_dir, plan["manifest_bytes"], plan["manifest_data"])
        if errors:
            raise ReuseError(errors[0])
        return True
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def write_source_page(plan: dict) -> bool:
    """新增 canonical source 页；绝不覆盖不同字节。"""
    target: Path = plan["source_path"]
    expected: bytes = plan["source_bytes"]
    if target.exists():
        if target.is_file() and not target.is_symlink() and target.read_bytes() == expected:
            return False
        raise ReuseError(f"reuse source page conflict: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_direct_contained(target.parent, plan["vault"], "reuse source page parent")
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(expected)
        if temp.read_bytes() != expected:
            raise ReuseError(f"failed to verify source page bytes: {target}")
        try:
            temp.rename(target)
        except OSError:
            if target.exists() and target.is_file() and target.read_bytes() == expected:
                return False
            raise
        return True
    finally:
        temp.unlink(missing_ok=True)
