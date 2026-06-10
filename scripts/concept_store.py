"""Canonical 概念模型（spec §6）：slug/canonical_id、registry 重建、resolve_or_create_concept。

真值在概念页 frontmatter；concepts/_registry.yaml 与 aliases.md 为派生（本模块重建，/ingest 不写）。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage

_ASCII_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_OK = re.compile(r"[a-z0-9][a-z0-9-]*")


def slugify(name: str) -> str:
    """确定性 slug：ASCII 名转 kebab；纯 CJK 名保留原字（去空白）。"""
    ascii_slug = _ASCII_SLUG.sub("-", name.strip().lower()).strip("-")
    if ascii_slug:
        return ascii_slug
    return re.sub(r"\s+", "", name.strip())


def canonical_id(domain: str, name: str, aliases=()) -> str:
    """concept.<domain>.<slug>；slug 依次试 name、各 alias，取第一个纯 ASCII 的（spec §6 示例规则）。"""
    for cand in (name, *aliases):
        s = slugify(cand)
        if _SLUG_OK.fullmatch(s):
            return f"concept.{domain}.{s}"
    return f"concept.{domain}.{slugify(name)}"


def _norm(term: str) -> str:
    return re.sub(r"\s+", " ", str(term).strip()).lower()


def _concept_dirs(vault: Path):
    yield vault / "concepts"
    domains = vault / "domains"
    if domains.exists():
        for d in sorted(p for p in domains.iterdir() if p.is_dir()):
            yield d / "concepts"


def scan_concept_pages(vault) -> list[dict]:
    """扫描全部概念页（顶层 shared + 各 domain），page_path 以实际位置为准。"""
    vault = Path(vault)
    metas = []
    for cdir in _concept_dirs(vault):
        if not cdir.exists():
            continue
        for f in sorted(cdir.glob("*.md")):
            meta, _ = mdpage.read_page(f)
            if meta.get("type") == "concept":
                meta["page_path"] = f.relative_to(vault).as_posix()
                metas.append(meta)
    return metas


def build_registry(metas: list[dict]) -> tuple[dict, list[str], list[str]]:
    """返回 (registry, errors, warnings)。registry: canonical_id → 条目。
    duplicate/missing canonical_id 是结构性损坏（errors，调用方拒绝写盘）；
    同域名/别名碰撞是重复概念征兆（warnings，阻断属 P6 门禁）。"""
    reg: dict = {}
    errors: list[str] = []
    warnings: list[str] = []
    seen_terms: dict = {}
    for m in metas:
        cid = m.get("canonical_id")
        if not cid:
            errors.append(f"missing canonical_id: {m.get('page_path')}")
            continue
        if cid in reg:
            errors.append(f"duplicate canonical_id: {cid} ({reg[cid]['page_path']} vs {m['page_path']})")
            continue
        reg[cid] = {"canonical_name": m.get("canonical_name", ""),
                    "aliases": list(m.get("aliases") or []),
                    "scope": m.get("scope", "domain"),
                    "domain": m.get("domain", ""),
                    "page_path": m["page_path"]}
        for term in [reg[cid]["canonical_name"], *reg[cid]["aliases"]]:
            key = (reg[cid]["domain"], _norm(term))
            if key in seen_terms and seen_terms[key] != cid:
                warnings.append(f"alias collision in {key[0]}: '{term}' -> {seen_terms[key]} and {cid}")
            seen_terms[key] = cid
    return reg, errors, warnings


def write_registry(vault, registry: dict) -> str:
    """写 concepts/_registry.yaml（key 排序，字节级确定），返回 sha256（P4 work order 用）。"""
    vault = Path(vault)
    out = vault / "concepts" / "_registry.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({k: registry[k] for k in sorted(registry)},
                          allow_unicode=True, sort_keys=True, default_flow_style=False)
    out.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_aliases(vault, registry: dict) -> None:
    """派生 aliases.md：别名 → 概念页（人读视图；/ingest 不写此文件）。"""
    rows = set()
    for cid in sorted(registry):
        e = registry[cid]
        for term in [e["canonical_name"], *e["aliases"]]:
            rows.add(f"- {term} → [[{e['page_path']}|{e['canonical_name']}]] (`{cid}`)")
    lines = ["# 别名索引（派生文件，由 rebuild-registry 重建，勿手改）", ""] + sorted(rows)
    (Path(vault) / "aliases.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
