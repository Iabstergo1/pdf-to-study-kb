"""source 级 work order 生成（spec §9）：写入边界 + registry hash 守卫 + 页面快照。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage

# G3：受管跨域 home-domain 白名单。任何来源都可把「本就属于这些域」的概念页 resolve/merge 到其 home
# 域（概念落 home domain，不落当前来源域）。窄放行——只放行 `domains/{home}/concepts/**`，不开 domains/**、
# 不放行其 lessons/topics。新增 home（statistics/optimization/programming…）须经审计后加入此表。
CROSS_DOMAIN_HOME_DOMAINS = ["research-method"]


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _managed_by(p: Path) -> str:
    meta, _ = mdpage.read_page(p)
    return meta.get("managed_by", "pipeline")


def _read_backend(staging: Path) -> str:
    """从 parse_report.json 读 selected_backend；缺失/解析失败 → "unknown"（不破坏 legacy staging）。"""
    import json
    p = Path(staging) / "parse_report.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("selected_backend", "unknown")
        except Exception:
            return "unknown"
    return "unknown"


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
        # 本域 + shared + 跨域 home 白名单：均纳入快照（跨域 home 概念也吃 hash 覆盖保护，G3）
        if e["domain"] not in (domain, "shared", *CROSS_DOMAIN_HOME_DOMAINS):
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
    # 所有可写的既有综合层页面也必须入 hash 快照。旧实现漏掉 topic/comparison/synthesis，
    # 导致它们虽在 write_scope 内，却无法证明 check-write 发生在编辑之前。
    for dirname in ("topics", "comparisons", "synthesis"):
        d = vault / dirname
        if d.exists():
            candidates += sorted(d.rglob("*.md"))
    seen = set()
    for p in candidates:
        if p.exists():
            rel = p.relative_to(vault).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            other_snap.append({"path": rel, "sha256": _sha256_file(p), "managed_by": _managed_by(p)})

    # G3：跨域概念写入窄放行——每个 ≠ 当前域的 home 追加精确到 concepts/** 一条（不放行其 lessons/topics）
    cross_scope = [f"domains/{h}/concepts/**" for h in CROSS_DOMAIN_HOME_DOMAINS if h != domain]

    # 本域边界与 G3 跨域同样收窄到 concepts/lessons（曾是 `domains/{domain}/**` 宽通配）：综合层
    # （topic/comparison/synthesis）与来源台账页只落顶层，域下另建它们既偏离既有布局，域下的
    # sources/<src>.md 更会与顶层台账页同 source_id，撞 graph_model._page_id 的 "source:<id>"
    # 节点 id 使 rebuild-graph fail-hard（顶层 `sources/{source_id}.md` 那条本就意在"台账页唯一"，
    # 宽通配把它架空了）。
    own_scope = [f"domains/{domain}/concepts/**", f"domains/{domain}/lessons/**"]

    return {
        "source_id": source_id,
        "domain": domain,
        "write_scope": own_scope + ["concepts/**", "topics/**", "comparisons/**",
                                    "synthesis/**", f"sources/{source_id}.md",
                                    "overview.md", "log.md"]
        + cross_scope,
        "cross_domain_concept_scope": cross_scope,   # 显式审计字段：本轮允许写入的跨域 home 概念目录
        "registry": {"path": "concepts/_registry.yaml", "hash": reg_hash,
                     "scope": [f"domain:{domain}", "shared"]},
        "concept_pages_snapshot": concept_snap,
        "other_pages_snapshot": other_snap,
        "source": {"text_md": str(staging / "source.md"),               # 旧键保留（向后兼容）
                   "source_md": str(staging / "source.md"),
                   "blocks_jsonl": str(staging / "blocks.jsonl"),
                   "parse_report_json": str(staging / "parse_report.json"),
                   "chapters_json": str(staging / "chapters.json"),
                   "assets_dir": str(staging / "assets"),
                   "page_images_dir": str(staging / "assets"),           # 旧键保留
                   "processing_windows": str(staging / "windows.jsonl"),
                   "backend": _read_backend(staging)},
        "on_failure": "route_to_review_queue",
    }


def write_workorder(staging_dir, wo: dict) -> Path:
    path = Path(staging_dir) / "workorder.yaml"
    path.write_text(yaml.safe_dump(wo, allow_unicode=True, sort_keys=True,
                                   default_flow_style=False), encoding="utf-8")
    return path
