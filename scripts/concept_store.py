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
    """确定性 slug：纯 ASCII 名转 kebab；含任何非 ASCII 字符（CJK 等）的名保留原字（去空白）。
    中文夹 ASCII 片段（如「生成式AI」「清单20问」）必须整名保留——不得取出局部 ASCII 残片当 slug
    （曾把「生成式AI的科研辅助定位」塌缩成 ai.md）。"""
    s = name.strip()
    if s.isascii():
        ascii_slug = _ASCII_SLUG.sub("-", s.lower()).strip("-")
        if ascii_slug:
            return ascii_slug
    return re.sub(r"\s+", "", s)


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


def remove_stale_aliases(vault) -> None:
    """B2：aliases.md 已废弃（别名只保留在概念页 frontmatter，Obsidian 原生用于搜索/补全）。
    清理旧 vault 可能残留的派生 aliases.md，不再生成。"""
    (Path(vault) / "aliases.md").unlink(missing_ok=True)


# 与 templates/concept.md 同构的回退种子（D-4 之后无强制小节）：散文占位 + 正确嵌套的
# 自测示例。种子必须只教"对的形状"——会话中断恢复后的写作 LLM 会照种子填空。
CONCEPT_BODY = """（待 /ingest 填写：高信息密度的散文正文——开门见山给出定义（被定义术语首次出现用 ==高亮==），
随后由内容自然展开直觉、机制、边界条件与常见误区，相关概念用全路径 wikilink 编入行文。
结构由 purpose.md 与内容决定，**没有强制小节**；装置预算与写作纪律见 ingest 的 write-pages.md。）

> [!question] 自测
> （待 /ingest 填写：情境化题干，写在块内首行、以问号结尾——绝不写进 callout 标题）
> > [!success]- 参考答案
> > （待 /ingest 填写：答案只放这个嵌套折叠块里，绝不明文跟在题干后）
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


def is_alias_hit(mention: str, cid: str, entry: dict) -> bool:
    """mention 命中的是 alias 而非 canonical_name/canonical_id。alias 命中即静默合并，
    若该 alias 是被囤进整体页的独立子概念名（会永久劫持后来者），这是唯一能提示的时刻。"""
    return mention != cid and _norm(mention) != _norm(entry["canonical_name"])


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
