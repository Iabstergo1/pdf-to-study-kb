"""跨域提升（spec §6/§13）：候选检测（绝不自动提升）+ 人工确认后的机械提升。零 LLM。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage


def find_candidates(registry: dict) -> list[dict]:
    """同一规范名/别名出现在 ≥2 个不同 domain（shared 除外）→ 提升候选。只检测，不改盘。"""
    by_term: dict[str, dict[str, str]] = {}  # norm term -> {domain: cid}
    for cid in sorted(registry):
        e = registry[cid]
        if e["domain"] == "shared":
            continue
        for term in [e["canonical_name"], *e["aliases"]]:
            by_term.setdefault(concept_store._norm(term), {}).setdefault(e["domain"], cid)
    out = []
    for term in sorted(by_term):
        hits = by_term[term]
        if len(hits) >= 2:
            out.append({"term": term, "domains": sorted(hits),
                        "canonical_ids": sorted(set(hits.values()))})
    return out


def promote_to_shared(vault, canonical_id: str) -> tuple[str, str]:
    """把一个 domain 概念页机械提升为 shared：移动 + frontmatter 改写 + 全 vault 链接重写。
    目标冲突（页文件已存在 / shared 命名空间撞 id）→ 中止且不动盘。"""
    vault = Path(vault)
    registry, errors, _ = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        raise ValueError("corrupt concept pages: " + "; ".join(errors))
    if canonical_id not in registry:
        raise KeyError(f"unknown canonical_id: {canonical_id}")
    entry = registry[canonical_id]
    if entry["domain"] == "shared":
        raise ValueError(f"{canonical_id} already shared")
    slug = canonical_id.rsplit(".", 1)[1]
    new_cid = f"concept.shared.{slug}"
    new_rel = f"concepts/{slug}.md"
    if new_cid in registry or (vault / new_rel).exists():
        raise FileExistsError(f"target exists: {new_cid} / {new_rel}")
    old_rel = entry["page_path"]
    meta, body = mdpage.read_page(vault / old_rel)
    meta.update({"canonical_id": new_cid, "scope": "shared", "domain": "shared",
                 "page_path": new_rel})
    mdpage.write_page(vault / new_rel, meta, body)
    (vault / old_rel).unlink()
    # 全 vault 链接重写：旧页路径（带/不带 .md）→ 新路径
    old_noext = old_rel[:-3]
    new_noext = new_rel[:-3]
    for f in sorted(vault.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        if old_rel in text or old_noext in text:
            f.write_text(text.replace(old_rel, new_rel).replace(old_noext, new_noext),
                         encoding="utf-8", newline="\n")
    return new_cid, new_rel
