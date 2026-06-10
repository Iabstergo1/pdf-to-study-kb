"""source 级 work order 生成（spec §9）：写入边界 + registry hash 守卫 + 页面快照。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _managed_by(p: Path) -> str:
    meta, _ = mdpage.read_page(p)
    return meta.get("managed_by", "pipeline")


def build_workorder(vault, *, source_id: str, domain: str, staging_dir) -> dict:
    vault = Path(vault)
    staging = Path(staging_dir)
    # registry 重建保证新鲜（vault 可能尚无概念页 → 空 registry，hash 仍确定）
    metas = concept_store.scan_concept_pages(vault) if vault.exists() else []
    registry, errors, _warnings = concept_store.build_registry(metas)
    if errors:
        raise ValueError("corrupt concept pages: " + "; ".join(errors))
    reg_hash = concept_store.write_registry(vault, registry)

    concept_snap = []
    for cid in sorted(registry):
        e = registry[cid]
        if e["domain"] not in (domain, "shared"):
            continue
        page = vault / e["page_path"]
        concept_snap.append({"canonical_id": cid, "path": e["page_path"],
                             "sha256": _sha256_file(page), "managed_by": _managed_by(page)})

    other_snap = []
    fixed = [f"sources/{source_id}.md", "overview.md", "log.md"]
    lessons_dir = vault / "domains" / domain / "lessons"
    candidates = [vault / rel for rel in fixed]
    if lessons_dir.exists():
        candidates += sorted(lessons_dir.glob("*.md"))
    for p in candidates:
        if p.exists():
            rel = p.relative_to(vault).as_posix()
            other_snap.append({"path": rel, "sha256": _sha256_file(p), "managed_by": _managed_by(p)})

    return {
        "source_id": source_id,
        "domain": domain,
        "write_scope": [f"domains/{domain}/**", "concepts/**", "topics/**", "comparisons/**",
                        "synthesis/**", f"sources/{source_id}.md", "overview.md", "log.md"],
        "registry": {"path": "concepts/_registry.yaml", "hash": reg_hash,
                     "scope": [f"domain:{domain}", "shared"]},
        "concept_pages_snapshot": concept_snap,
        "other_pages_snapshot": other_snap,
        "source": {"text_md": str(staging / "source.md"),
                   "page_images_dir": str(staging / "assets"),
                   "processing_windows": str(staging / "windows.jsonl")},
        "on_failure": "route_to_review_queue",
    }


def write_workorder(staging_dir, wo: dict) -> Path:
    path = Path(staging_dir) / "workorder.yaml"
    path.write_text(yaml.safe_dump(wo, allow_unicode=True, sort_keys=True,
                                   default_flow_style=False), encoding="utf-8")
    return path
