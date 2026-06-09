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
    entries = []
    for f in files:
        f = Path(f)
        rel = f.relative_to(base).as_posix()
        if f.exists():
            saved = dest / "files" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, saved)
            entries.append({"rel_path": rel, "existed": True, "sha256": _sha256(f),
                            "saved": saved.as_posix()})
        else:
            entries.append({"rel_path": rel, "existed": False, "sha256": None, "saved": None})
    manifest = dest / "manifest.json"
    manifest.write_text(json.dumps(
        {"source_id": source_id, "run_id": run_id, "base_dir": base.as_posix(), "entries": entries},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


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
