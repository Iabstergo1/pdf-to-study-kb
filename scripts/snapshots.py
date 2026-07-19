"""Pre-ingest 文件快照 + 回滚（默认非 git，spec §3.3）。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def take_snapshot(snap_root, *, source_id: str, run_id: str, files, base_dir) -> Path:
    base = Path(base_dir)
    dest = Path(snap_root) / source_id / run_id
    (dest / "files").mkdir(parents=True, exist_ok=True)
    manifest = dest / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        expected = {"source_id": source_id, "run_id": run_id, "base_dir": base.as_posix()}
        actual = {k: data.get(k) for k in expected}
        if actual != expected:
            raise ValueError(f"snapshot manifest header mismatch: {actual!r} != {expected!r}")
        entries = list(data.get("entries") or [])
    else:
        entries = []
    known = {e.get("rel_path") for e in entries}
    for f in files:
        f = Path(f)
        rel = f.relative_to(base).as_posix()
        # 第一份基线不可覆盖：后续 check-write 可能发生在本轮页已被编辑之后。
        if rel in known:
            continue
        if f.exists():
            saved = dest / "files" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, saved)
            entries.append({"rel_path": rel, "existed": True, "sha256": _sha256(f),
                            "saved": saved.as_posix()})
        else:
            entries.append({"rel_path": rel, "existed": False, "sha256": None, "saved": None})
        known.add(rel)
    payload = {"source_id": source_id, "run_id": run_id,
               "base_dir": base.as_posix(), "entries": entries}
    tmp = manifest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(manifest)
    return manifest


def verify_prewrite_entry(manifest_path, *, source_id: str, run_id: str, base_dir,
                          rel_path: str, expected_sha256: str) -> str | None:
    """核验既有页的写前快照；成功返回 None，失败返回可直接展示的原因。"""
    manifest = Path(manifest_path)
    if not manifest.exists():
        return "manifest missing"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"manifest unreadable: {e}"
    base = Path(base_dir)
    if data.get("source_id") != source_id or data.get("run_id") != run_id or \
            data.get("base_dir") != base.as_posix():
        return "manifest header mismatch"
    matches = [e for e in (data.get("entries") or []) if e.get("rel_path") == rel_path]
    if len(matches) != 1:
        return "entry missing" if not matches else "duplicate entries"
    entry = matches[0]
    if not entry.get("existed") or entry.get("sha256") != expected_sha256:
        return "entry does not match work-order baseline"
    expected_saved = manifest.parent / "files" / rel_path
    saved = Path(entry.get("saved") or "")
    try:
        if saved.resolve() != expected_saved.resolve():
            return "saved path mismatch"
    except OSError as e:
        return f"saved path invalid: {e}"
    if not saved.is_file():
        return "saved file missing"
    if _sha256(saved) != expected_sha256:
        return "saved file hash mismatch"
    return None


def rollback(manifest_path) -> None:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    base = Path(data["base_dir"])
    for e in data["entries"]:
        target = base / e["rel_path"]
        if e["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)  # 目录可能已被删
            shutil.copy2(e["saved"], target)
        elif target.exists():
            target.unlink()


def cleanup(snap_root, *, source_id: str, run_id: str) -> None:
    d = Path(snap_root) / source_id / run_id
    if d.exists():
        shutil.rmtree(d)
