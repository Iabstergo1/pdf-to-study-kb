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
    # newline="\n"：磁盘字节必须与返回的 hash 一致（Windows 默认会写 \r\n，导致 stale 守卫误报）
    out.write_text(text, encoding="utf-8", newline="\n")
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


CONCEPT_BODY = """# {name}

## 一句话

（待 /ingest 填写）

## 直觉

（待 /ingest 填写）

## 形式化

（待 /ingest 填写）

## 各章如何处理

（待 /ingest 填写）

## 与其他概念的关系

（待 /ingest 填写）

## 自测

（待 /ingest 填写：1–3 个自测问题，链接相关 lesson）
"""

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _concept_body(name: str) -> str:
    """概念骨架正文：优先取 templates/concept.md（单一真值）；缺失回退内置常量。
    用 str.replace 而非 format——模板里可能出现其它花括号。"""
    tpl = _TEMPLATES_DIR / "concept.md"
    if tpl.exists():
        _, body = mdpage.read_page(tpl)
        return body.replace("{name}", name)
    return CONCEPT_BODY.format(name=name)


def resolve(mention: str, *, domain: str, registry: dict):
    """mention 命中 canonical（名/别名，先本域后 shared）→ (canonical_id, entry)；未命中 → None。"""
    if mention in registry:  # 直接给 canonical_id
        return mention, registry[mention]
    n = _norm(mention)
    for want in (domain, "shared"):
        for cid in sorted(registry):
            e = registry[cid]
            if e["domain"] != want:
                continue
            if _norm(e["canonical_name"]) == n or any(_norm(a) == n for a in e["aliases"]):
                return cid, e
    return None


def create_concept(vault, *, domain: str, name: str, aliases=(), source_ref=None) -> Path:
    """新建骨架概念页（status: proposed；§8 最小结构）。页已存在则拒绝——必须走 merge。"""
    cid = canonical_id(domain, name, aliases)    # 稳定 ID（spec §6 不变：优先 ASCII 别名，去重键稳定）
    slug = slugify(name)                          # 文件名用中文 canonical_name（侧栏/画布/标签可读），与 cid 解耦
    if domain == "shared":
        rel = Path("concepts") / f"{slug}.md"
    else:
        rel = Path("domains") / domain / "concepts" / f"{slug}.md"
    path = Path(vault) / rel
    if path.exists():
        raise FileExistsError(f"concept page already exists: {rel} (use merge, never duplicate)")
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases),
            "scope": "shared" if domain == "shared" else "domain",
            "domain": domain,
            "source_refs": [source_ref] if source_ref else [],
            "page_path": rel.as_posix(), "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(path, meta, _concept_body(name))
    return path


def merge_concept(vault, page_path: str, *, source_ref=None, new_aliases=()) -> None:
    """merge 进既有页：只累积 frontmatter 的 source_refs/aliases（去重），绝不新建页、不动正文。"""
    path = Path(vault) / page_path
    meta, body = mdpage.read_page(path)
    if new_aliases:
        cur = list(meta.get("aliases") or [])
        known = {_norm(meta.get("canonical_name", ""))} | {_norm(x) for x in cur}
        for a in new_aliases:
            if _norm(a) not in known:
                cur.append(a)
                known.add(_norm(a))
        meta["aliases"] = cur
    if source_ref:
        refs = list(meta.get("source_refs") or [])
        hit = next((r for r in refs if r.get("source") == source_ref.get("source")), None)
        if hit:
            hit["sections"] = list(dict.fromkeys([*hit.get("sections", []),
                                                  *source_ref.get("sections", [])]))
        else:
            refs.append(source_ref)
        meta["source_refs"] = refs
    mdpage.write_page(path, meta, body)


def resolve_or_create_concept(vault, *, mention: str, domain: str, registry: dict,
                              aliases=(), source_ref=None):
    """唯一入口（spec §6）：命中 → merge 既有页，返回 (cid, path, "merged")；
    未命中 → create 骨架页，返回 (cid, path, "created")。新建后调用方需重建 registry。"""
    hit = resolve(mention, domain=domain, registry=registry)
    if hit:
        cid, entry = hit
        merge_concept(vault, entry["page_path"], source_ref=source_ref,
                      new_aliases=[mention, *aliases])
        return cid, Path(vault) / entry["page_path"], "merged"
    path = create_concept(vault, domain=domain, name=mention,
                          aliases=list(aliases), source_ref=source_ref)
    meta, _ = mdpage.read_page(path)
    return meta["canonical_id"], path, "created"
