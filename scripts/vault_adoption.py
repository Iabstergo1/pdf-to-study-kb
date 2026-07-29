"""既有 Obsidian vault 的确定性接管：只读规划 + 不可变证据落盘。

接管不重新摄取或改写知识页。默认规划必须零写；apply 只新增证据目录、一个 canonical
source 台账页和专用终态，随后由 pipeline 编排派生层重建。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import mdpage
import wiki_gate


class AdoptionError(Exception):
    pass


_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_EXCLUDE_TOP = {".obsidian", "Review-Queue", "_meta", "assets"}
_EXCLUDE_FILES = {"log.md", "index.generated.md", "aliases.md",
                  "quiz-index.generated.md", "propositions.generated.md"}
_STATE_TABLES = {"sources", "source_stage_runs", "artifacts", "work_orders",
                 "ingest_progress", "window_reads", "source_locks"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _violation(path: str, rule: str, detail: str) -> dict:
    return {"path": path, "rule": rule, "detail": detail}


def _resolved_inside(path: Path, root: Path) -> Path | None:
    """返回 strict-resolved path；父目录 symlink/junction 逃出 root 时返回 None。"""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    root = root.resolve(strict=True)
    return resolved if resolved != root and root in resolved.parents else None


def _assert_direct_contained(path: Path, root: Path, label: str) -> None:
    """拒绝输出路径或最近既有祖先经 symlink/junction 重定向到锚点外（或别处）。"""
    root = root.resolve(strict=True)
    candidate = Path(path)
    while not candidate.exists():
        if candidate.is_symlink():
            raise AdoptionError(f"{label} uses a symlink outside its direct workspace path: {candidate}")
        if candidate == root or candidate.parent == candidate:
            break
        candidate = candidate.parent
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AdoptionError(f"cannot resolve {label}: {candidate}") from exc
    if resolved != root and root not in resolved.parents:
        raise AdoptionError(f"{label} escapes workspace boundary: {candidate} -> {resolved}")
    lexical = Path(os.path.abspath(str(candidate)))
    if candidate.is_symlink() or os.path.normcase(str(resolved)) != os.path.normcase(str(lexical)):
        raise AdoptionError(f"{label} uses a redirected symlink/junction path: {candidate} -> {resolved}")


def _validate_args(source: str, title: str, domain: str, archive: Path,
                   expected_sha256: str) -> tuple[Path, str]:
    if not _SOURCE_ID.fullmatch(source):
        raise AdoptionError("source_id must start with an ASCII letter/digit and contain only A-Z, a-z, 0-9, ., _ or -")
    if not title.strip():
        raise AdoptionError("title must not be empty")
    if not domain.strip():
        raise AdoptionError("domain must not be empty")
    if not _SHA256.fullmatch(expected_sha256.strip()):
        raise AdoptionError("baseline sha256 must be exactly 64 hexadecimal characters")
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise AdoptionError(f"baseline archive not found: {archive}")
    actual = sha256_file(archive)
    expected = expected_sha256.strip().lower()
    if actual != expected:
        raise AdoptionError(
            f"baseline archive sha256 mismatch: expected {expected}, actual {actual}")
    return archive, actual


def _normalise_zip_member(name: str) -> str:
    raw = name.replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if raw.startswith("/") or any(part == ".." for part in parts):
        raise AdoptionError(f"baseline archive contains unsafe member path: {name!r}")
    return "/".join(parts)


def _is_knowledge_rel(rel: str, source_rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return (rel.endswith(".md") and rel != source_rel and rel not in _EXCLUDE_FILES
            and top not in _EXCLUDE_TOP)


def _archive_page_violations(archive: Path, pages: list[dict], *,
                             source_rel: str) -> list[dict]:
    """逐页核验 ZIP 的 wiki/<rel> 与 live page；额外成员允许但重复成员拒绝。"""
    try:
        zf = zipfile.ZipFile(archive, "r")
    except zipfile.BadZipFile as exc:
        raise AdoptionError("baseline archive must be a valid ZIP file") from exc
    try:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            name = _normalise_zip_member(info.filename)
            if name in members:
                raise AdoptionError(f"baseline archive contains duplicate entry: {name}")
            members[name] = info
        violations: list[dict] = []
        for page in pages:
            member_name = f"wiki/{page['path']}"
            info = members.get(member_name)
            if info is None or info.is_dir():
                violations.append(_violation(
                    page["path"], "baseline-archive-page-missing",
                    f"baseline drift: ZIP member `{member_name}` is missing"))
                continue
            if info.file_size != page["size"]:
                violations.append(_violation(
                    page["path"], "baseline-archive-page-mismatch",
                    f"baseline drift: ZIP member `{member_name}` bytes differ from live wiki page"))
                continue
            h = hashlib.sha256()
            try:
                with zf.open(info, "r") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise AdoptionError(
                    f"cannot verify baseline ZIP member {member_name}: {exc}") from exc
            if h.hexdigest() != page["sha256"]:
                violations.append(_violation(
                    page["path"], "baseline-archive-page-mismatch",
                    f"baseline drift: ZIP member `{member_name}` SHA-256 differs from live wiki page"))
        expected_names = {f"wiki/{page['path']}" for page in pages}
        for name, info in sorted(members.items()):
            if info.is_dir() or not name.startswith("wiki/"):
                continue
            rel = name[5:]
            if _is_knowledge_rel(rel, source_rel) and name not in expected_names:
                violations.append(_violation(
                    rel, "baseline-archive-page-extra",
                    f"baseline drift: ZIP has additional adoptable knowledge page `{name}`"))
        return violations
    finally:
        zf.close()


def _read_only_db(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _state_snapshot(db: Path, source: str) -> dict | None:
    if not db.exists():
        return None
    try:
        con = _read_only_db(db)
    except sqlite3.Error as exc:
        raise AdoptionError(f"cannot read state database without writing: {exc}") from exc
    try:
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = sorted(_STATE_TABLES - tables)
        if missing:
            return {"schema_missing": missing}
        lock = con.execute("SELECT * FROM source_locks WHERE scope='vault'").fetchone()
        src = con.execute("SELECT * FROM sources WHERE source_id=?", (source,)).fetchone()
        stages = con.execute(
            "SELECT stage,status,input_hash,output_hash FROM source_stage_runs "
            "WHERE source_id=? ORDER BY id",
            (source,),
        ).fetchall()
        artifacts = con.execute(
            "SELECT kind,path,sha256 FROM artifacts WHERE source_id=? ORDER BY id", (source,)
        ).fetchall()
        ledgers = {
            table: con.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE source_id=?", (source,)
            ).fetchone()["n"]
            for table in ("work_orders", "ingest_progress", "window_reads")
        }
        return {"lock": dict(lock) if lock else None,
                "source": dict(src) if src else None,
                "stages": [dict(r) for r in stages],
                "artifacts": [dict(r) for r in artifacts],
                "ledgers": ledgers}
    except sqlite3.Error as exc:
        raise AdoptionError(f"cannot inspect state database: {exc}") from exc
    finally:
        con.close()


def _reject_lock(snapshot: dict | None, ttl_seconds: int,
                 allowed_holder: str | None = None) -> None:
    if not snapshot or not snapshot.get("lock"):
        return
    lock = snapshot["lock"]
    if allowed_holder is not None and lock["holder"] == allowed_holder:
        return
    try:
        heartbeat = datetime.fromisoformat(lock["heartbeat_at"])
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    except (TypeError, ValueError):
        age = 0
    if age <= ttl_seconds:
        raise AdoptionError(
            f"active vault lock held by {lock['holder']} since {lock['started_at']}")
    raise AdoptionError(
        f"stale vault lock held by {lock['holder']}; run unlock before adopt-vault")


def _knowledge_files(vault: Path, source_rel: str) -> list[Path]:
    out: list[Path] = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        top = rel.split("/", 1)[0]
        if rel == source_rel or rel in _EXCLUDE_FILES or top in _EXCLUDE_TOP:
            continue
        out.append(path)
    return out


def _source_page_bytes(*, source: str, title: str, domain: str, archive: Path,
                       archive_sha256: str, manifest_rel: str,
                       manifest_sha256: str) -> bytes:
    meta = {
        "adoption_manifest": manifest_rel,
        "adoption_manifest_sha256": manifest_sha256,
        "baseline_archive": str(archive),
        "baseline_sha256": archive_sha256,
        "domain": domain,
        "format": "legacy-vault",
        "managed_by": "pipeline",
        "source_id": source,
        "status": "published",
        "title": title,
        "type": "source",
    }
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True, default_flow_style=False)
    body = (
        "本页登记的是对既有 Obsidian 知识库基线的整体接管，不代表一次外部文档摄取。"
        "接管证据保存了纳管时每张知识页的原始字节与 SHA-256，可据此审计后续漂移。\n\n"
        f"基线归档 SHA-256：`{archive_sha256}`；证据 manifest SHA-256："
        f"`{manifest_sha256}`。\n\n"
        "该来源采用 `legacy-vault` 格式；没有 processing window、block 或 window read/write ledger。"
        "既有知识页不会由 adopt-vault 自动改写。\n"
    )
    return f"---\n{fm}---\n{body}".encode("utf-8")


def _validate_existing_evidence(evidence_dir: Path, expected_manifest: bytes,
                                pages: list[dict]) -> list[dict]:
    if not evidence_dir.exists():
        return []
    manifest = evidence_dir / "manifest.json"
    if not evidence_dir.is_dir() or not manifest.is_file():
        return [_violation(str(evidence_dir), "adoption-evidence-incomplete",
                           "adoption evidence path exists but has no immutable manifest.json")]
    try:
        _assert_direct_contained(manifest, evidence_dir, "adoption evidence manifest")
        files_dir = evidence_dir / "files"
        if files_dir.exists():
            _assert_direct_contained(files_dir, evidence_dir, "adoption evidence files")
    except AdoptionError as exc:
        return [_violation("manifest.json", "adoption-evidence-corrupt", str(exc))]
    actual_manifest = manifest.read_bytes()
    if actual_manifest != expected_manifest:
        return [_violation("manifest.json", "baseline-drift",
                           "baseline drift: current legacy page bytes or adoption metadata no longer match the immutable manifest")]
    violations: list[dict] = []
    expected_files = {p["path"] for p in pages}
    actual_files = {
        p.relative_to(evidence_dir).as_posix()
        for p in evidence_dir.rglob("*") if p.is_file() or p.is_symlink()
    }
    expected_evidence_files = {"manifest.json"} | {f"files/{p}" for p in expected_files}
    if actual_files != expected_evidence_files:
        violations.append(_violation("files/", "adoption-evidence-corrupt",
                                     "evidence file set differs from manifest"))
    for entry in pages:
        copied = evidence_dir / "files" / entry["path"]
        try:
            _assert_direct_contained(copied, evidence_dir, "adoption evidence page")
            contained = _resolved_inside(copied, evidence_dir) is not None
        except AdoptionError:
            contained = False
        if (not contained or not copied.is_file() or copied.is_symlink()
                or copied.stat().st_size != entry["size"]
                or sha256_file(copied) != entry["sha256"]):
            violations.append(_violation(entry["path"], "adoption-evidence-corrupt",
                                         "stored page bytes do not match immutable manifest"))
    return violations


def _state_violations(snapshot: dict | None, *, source: str, domain: str,
                      manifest_path: Path, manifest_sha256: str) -> list[dict]:
    if snapshot is None:
        return []
    if snapshot.get("schema_missing"):
        return [_violation(str(manifest_path), "state-schema-incomplete",
                           f"state database lacks tables: {', '.join(snapshot['schema_missing'])}")]
    src = snapshot["source"]
    stages = [(r["stage"], r["status"], r["input_hash"], r["output_hash"])
              for r in snapshot["stages"]]
    artifacts = [(r["kind"], r["path"], r["sha256"]) for r in snapshot["artifacts"]]
    ledgers = snapshot["ledgers"]
    if src is None:
        if stages or artifacts or any(ledgers.values()):
            return [_violation(source, "adoption-state-conflict",
                               "orphan stage/artifact/ingest ledger exists for unknown source")]
        return []
    expected_src = (domain, "legacy-vault", "adopted", "published")
    actual_src = (src["domain"], src["format"], src["current_stage"], src["current_status"])
    expected_stage = [("adopted", "done", manifest_sha256, manifest_sha256)]
    expected_artifact = [("adoption_evidence", str(manifest_path.resolve()), manifest_sha256)]
    if (actual_src != expected_src or stages != expected_stage
            or artifacts != expected_artifact or any(ledgers.values())):
        return [_violation(source, "adoption-state-conflict",
                           "existing source is not the exact adopted/published immutable-baseline state")]
    return []


def _load_stored_manifest(evidence_dir: Path) -> tuple[dict, bytes] | None:
    if not evidence_dir.exists():
        return None
    manifest_path = evidence_dir / "manifest.json"
    if not evidence_dir.is_dir() or not manifest_path.is_file():
        raise AdoptionError("adoption evidence exists without immutable manifest.json")
    _assert_direct_contained(manifest_path, evidence_dir, "adoption evidence manifest")
    raw = manifest_path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("adoption evidence manifest is not valid canonical JSON") from exc
    required = {"baseline_archive", "baseline_sha256", "domain", "format", "pages",
                "source_id", "title", "version"}
    if not isinstance(data, dict) or set(data) != required or raw != _json_bytes(data):
        raise AdoptionError("adoption evidence manifest schema/canonical bytes drift")
    pages = data.get("pages")
    if not isinstance(pages, list):
        raise AdoptionError("adoption evidence manifest pages must be a list")
    seen: set[str] = set()
    for entry in pages:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise AdoptionError("adoption evidence manifest has an invalid page entry")
        rel, size, digest = entry["path"], entry["size"], entry["sha256"]
        if (not isinstance(rel, str) or not rel.endswith(".md") or rel.startswith("/")
                or "\\" in rel or any(p in ("", ".", "..") for p in rel.split("/"))
                or rel in seen or not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise AdoptionError("adoption evidence manifest has an unsafe/duplicate page entry")
        seen.add(rel)
    if [p["path"] for p in pages] != sorted(seen):
        raise AdoptionError("adoption evidence manifest page order drift")
    return data, raw


def _manifest_metadata_violations(data: dict, *, source: str, title: str, domain: str,
                                  archive: Path, archive_sha256: str) -> list[dict]:
    expected = {"version": 1, "source_id": source, "title": title, "domain": domain,
                "format": "legacy-vault", "baseline_archive": str(archive),
                "baseline_sha256": archive_sha256}
    mismatches = [key for key, value in expected.items() if data.get(key) != value]
    return [] if not mismatches else [_violation(
        "manifest.json", "adoption-manifest-metadata-drift",
        f"immutable adoption manifest differs for: {', '.join(mismatches)}")]


def build_plan(*, workspace: Path, source: str, title: str, domain: str,
               baseline_archive: Path, baseline_sha256: str,
               lock_ttl_seconds: int = 1800,
               allowed_lock_holder: str | None = None) -> dict:
    """只读构建完整接管计划；不创建目录、数据库或报告。"""
    workspace = Path(workspace).resolve()
    vault = workspace / "wiki"
    if not vault.is_dir():
        raise AdoptionError(f"wiki vault not found: {vault}")
    _assert_direct_contained(vault, workspace, "wiki vault")
    archive, archive_sha256 = _validate_args(
        source, title, domain, Path(baseline_archive), baseline_sha256)
    source_rel = f"sources/{source}.md"
    manifest_rel = f"pipeline-workspace/adoptions/{source}/manifest.json"
    evidence_dir = workspace / "pipeline-workspace" / "adoptions" / source
    manifest_path = evidence_dir / "manifest.json"
    db = workspace / "pipeline-workspace" / "state" / "study-kb.sqlite"
    _assert_direct_contained(evidence_dir, workspace, "adoption evidence")
    _assert_direct_contained(db, workspace, "state database")
    _assert_direct_contained(vault / source_rel, vault, "adoption source page")
    snapshot = _state_snapshot(db, source)
    _reject_lock(snapshot, lock_ttl_seconds, allowed_lock_holder)
    stored = _load_stored_manifest(evidence_dir)

    live_pages: list[dict] = []
    lint_pages: list[dict] = []
    violations: list[dict] = []
    warnings: list[dict] = []
    vault_resolved = vault.resolve(strict=True)
    for path in _knowledge_files(vault, source_rel):
        rel = path.relative_to(vault).as_posix()
        resolved = _resolved_inside(path, vault_resolved)
        if resolved is None or path.is_symlink() or not resolved.is_file():
            violations.append(_violation(
                rel, "legacy-page-outside-vault",
                "legacy knowledge page must resolve to a regular file inside the vault; "
                "parent symlink/junction escape is forbidden"))
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            violations.append(_violation(rel, "legacy-page-unreadable", str(exc)))
            continue
        live_pages.append({"path": rel, "size": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest()})
        if stored is not None:
            continue
        try:
            meta, body = mdpage.read_page(path)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            violations.append(_violation(rel, "legacy-page-unreadable", str(exc)))
            continue
        lint_pages.append({"rel_path": rel, "meta": meta, "body": body})
        if meta.get("status") != "published":
            violations.append(_violation(
                rel, "legacy-page-not-published",
                f"legacy adoption accepts published knowledge pages only (got {meta.get('status')!r})"))

    if stored is None:
        pages = live_pages
        if not pages:
            violations.append(_violation("wiki/", "legacy-pages-empty",
                                         "no legacy knowledge pages found to adopt"))
        # Adoption 冻结既有字节，不给 legacy 内容签质量证书；现行 gate 债务全部显式告警，
        # 后续由 vault-lint/graph-lint 正式治理。可读性、published status、边界与证据完整性仍硬拦。
        warnings.extend(wiki_gate.lint_pages(vault, lint_pages, phase_e=False))
        manifest_data = {
            "baseline_archive": str(archive),
            "baseline_sha256": archive_sha256,
            "domain": domain,
            "format": "legacy-vault",
            "pages": pages,
            "source_id": source,
            "title": title,
            "version": 1,
        }
        manifest_bytes = _json_bytes(manifest_data)
    else:
        manifest_data, manifest_bytes = stored
        pages = manifest_data["pages"]
        violations.extend(_manifest_metadata_violations(
            manifest_data, source=source, title=title, domain=domain,
            archive=archive, archive_sha256=archive_sha256))
        baseline = {p["path"]: p for p in pages}
        current = {p["path"]: p for p in live_pages}
        added = sorted(current.keys() - baseline.keys())
        removed = sorted(baseline.keys() - current.keys())
        modified = sorted(path for path in current.keys() & baseline.keys()
                          if current[path]["sha256"] != baseline[path]["sha256"])
        if added or removed or modified:
            warnings.append(_violation(
                "wiki/", "post-adoption-live-drift",
                f"post-adoption live drift (historical evidence unchanged): "
                f"added={len(added)} removed={len(removed)} modified={len(modified)}"))

    violations.extend(_archive_page_violations(
        archive, pages, source_rel=source_rel))
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    source_bytes = _source_page_bytes(
        source=source, title=title, domain=domain, archive=archive,
        archive_sha256=archive_sha256, manifest_rel=manifest_rel,
        manifest_sha256=manifest_sha256)

    source_path = vault / source_rel
    source_verified = False
    if source_path.exists():
        if stored is None:
            violations.append(_violation(
                source_rel, "source-page-conflict",
                "canonical adoption source page exists without immutable adoption evidence"))
        elif (not source_path.is_file() or source_path.is_symlink()
                or _resolved_inside(source_path, vault_resolved) is None
                or source_path.read_bytes() != source_bytes):
            violations.append(_violation(source_rel, "source-page-conflict",
                                         "canonical adoption source page exists with different bytes; it will not be overwritten"))
        else:
            source_verified = True
    evidence_findings = _validate_existing_evidence(evidence_dir, manifest_bytes, pages)
    evidence_verified = stored is not None and not evidence_findings
    violations.extend(evidence_findings)
    state_findings = _state_violations(
        snapshot, source=source, domain=domain, manifest_path=manifest_path,
        manifest_sha256=manifest_sha256)
    state_verified = bool(snapshot and not state_findings and snapshot.get("source"))
    violations.extend(state_findings)
    if stored is None and snapshot and snapshot.get("source"):
        violations.append(_violation(
            str(evidence_dir), "adoption-evidence-missing",
            "published adoption state exists without immutable evidence"))
    if state_verified and not source_verified:
        violations.append(_violation(
            source_rel, "source-page-missing-after-publish",
            "published adoption state requires its exact canonical source page"))
    return {
        "workspace": workspace,
        "vault": vault,
        "source": source,
        "title": title,
        "domain": domain,
        "archive": archive,
        "archive_sha256": archive_sha256,
        "pages": pages,
        "violations": violations,
        "warnings": warnings,
        "evidence_verified": evidence_verified,
        "source_verified": source_verified,
        "state_verified": state_verified,
        "historical_evidence": stored is not None,
        "manifest_data": manifest_data,
        "manifest_bytes": manifest_bytes,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha256,
        "evidence_dir": evidence_dir,
        "source_path": source_path,
        "source_bytes": source_bytes,
    }


def _verify_live_pages(plan: dict) -> None:
    for entry in plan["pages"]:
        source_path = plan["vault"] / entry["path"]
        resolved = _resolved_inside(source_path, plan["vault"])
        if (resolved is None or not resolved.is_file() or source_path.is_symlink()):
            raise AdoptionError(f"baseline drift while verifying {entry['path']}")
        raw = source_path.read_bytes()
        if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise AdoptionError(f"baseline drift while verifying {entry['path']}")


def validate_output_paths(plan: dict) -> None:
    _assert_direct_contained(plan["vault"], plan["workspace"], "wiki vault")
    _assert_direct_contained(plan["evidence_dir"], plan["workspace"], "adoption evidence")
    _assert_direct_contained(
        plan["workspace"] / "pipeline-workspace" / "state" / "study-kb.sqlite",
        plan["workspace"], "state database")
    _assert_direct_contained(plan["source_path"], plan["vault"], "adoption source page")


def write_evidence(plan: dict) -> bool:
    """首次原子落不可变证据目录；已存在且逐字通过核验时幂等返回 False。"""
    validate_output_paths(plan)
    evidence_dir: Path = plan["evidence_dir"]
    if sha256_file(plan["archive"]) != plan["archive_sha256"]:
        raise AdoptionError("baseline archive drift after validation")
    current = _validate_existing_evidence(
        evidence_dir, plan["manifest_bytes"], plan["pages"])
    if evidence_dir.exists():
        if current:
            raise AdoptionError(current[0]["detail"])
        return False
    _verify_live_pages(plan)
    parent = evidence_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    _assert_direct_contained(parent, plan["workspace"], "adoption evidence parent")
    temp_dir = parent / f".{plan['source']}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir()
    try:
        for entry in plan["pages"]:
            source_path = plan["vault"] / entry["path"]
            if (not source_path.is_file() or source_path.is_symlink()
                    or _resolved_inside(source_path, plan["vault"]) is None):
                raise AdoptionError(f"baseline drift while copying {entry['path']}")
            raw = source_path.read_bytes()
            if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise AdoptionError(f"baseline drift while copying {entry['path']}")
            target = temp_dir / "files" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if sha256_file(target) != entry["sha256"]:
                raise AdoptionError(f"failed to verify copied page bytes: {entry['path']}")
        (temp_dir / "manifest.json").write_bytes(plan["manifest_bytes"])
        temp_errors = _validate_existing_evidence(
            temp_dir, plan["manifest_bytes"], plan["pages"])
        if temp_errors:
            raise AdoptionError(temp_errors[0]["detail"])
        try:
            temp_dir.rename(evidence_dir)
        except OSError:
            if evidence_dir.exists() and not _validate_existing_evidence(
                    evidence_dir, plan["manifest_bytes"], plan["pages"]):
                return False
            raise
        final_errors = _validate_existing_evidence(
            evidence_dir, plan["manifest_bytes"], plan["pages"])
        if final_errors:
            raise AdoptionError(final_errors[0]["detail"])
        return True
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def write_source_page(plan: dict) -> bool:
    """新增 canonical source 台账页；绝不覆盖不同内容的既有页。"""
    target: Path = plan["source_path"]
    expected: bytes = plan["source_bytes"]
    if target.exists():
        if target.is_file() and not target.is_symlink() and target.read_bytes() == expected:
            return False
        raise AdoptionError(f"source page conflict; refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_direct_contained(target.parent, plan["vault"], "adoption source page parent")
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(expected)
        if temp.read_bytes() != expected:
            raise AdoptionError(f"failed to verify source page bytes: {target}")
        try:
            temp.rename(target)
        except OSError:
            if target.exists() and target.is_file() and target.read_bytes() == expected:
                return False
            raise
        return True
    finally:
        temp.unlink(missing_ok=True)
