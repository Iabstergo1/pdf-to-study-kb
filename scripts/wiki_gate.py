"""收尾门禁（spec §10/§11）：proposed 收集、确定性 lint、index 重建、promote。零 LLM。

语义 lint（L4/矛盾/Q2）不在此处——见 /wiki-lint-semantic（P8）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage
import page_rules

_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets"}
_DERIVED = {"index.generated.md", "aliases.md"}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_RULE_BY_TYPE = {"concept": "L2", "topic": "L3", "overview": "L5"}
_PLACEHOLDER = re.compile(r"（待 /ingest 填写[^）]*）")


def collect_proposed(vault) -> list[dict]:
    vault = Path(vault)
    out = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") == "proposed":
            out.append({"rel_path": rel, "meta": meta, "body": body})
    return out


def belongs_to_source(rel_path: str, meta: dict, source_id: str, written: set[str]) -> bool:
    """页面归属判定（lint/promote 范围隔离）：本 source 的 window write_set 优先（覆盖
    topic/synthesis/overview 等无归属字段的页），其次 frontmatter 归属。"""
    if rel_path in written or rel_path == f"sources/{source_id}.md":
        return True
    if meta.get("source") == source_id or meta.get("source_id") == source_id:
        return True
    return any(isinstance(r, dict) and r.get("source") == source_id
               for r in (meta.get("source_refs") or []))


def _link_exists(vault: Path, target: str) -> bool:
    t = target.strip()
    return (vault / t).exists() or (vault / f"{t}.md").exists()


def lint_pages(vault, pages: list[dict]) -> list[dict]:
    """返回违规列表 [{path, rule, detail}]；空列表 = 门禁通过。"""
    vault = Path(vault)
    vs: list[dict] = []

    def hit(path, rule, detail):
        vs.append({"path": path, "rule": rule, "detail": detail})

    for p in pages:
        rel, meta, body = p["rel_path"], p["meta"], p["body"]
        ptype = meta.get("type", "")
        # L1：任何页正文不得有裸 E-ID
        for bare in page_rules.find_bare_evidence_ids(body):
            hit(rel, "L1", f"bare evidence id {bare}")
        # 证据脚注：引用必须有定义
        for fn in sorted(page_rules.missing_footnote_defs(body)):
            hit(rel, "evidence-footnote", f"footnote [^{fn}] has no definition")
        # 必需小节（concept=L2 / topic=L3 / overview=L5 / 其余统称 sections）
        if ptype in page_rules.REQUIRED_SECTIONS:
            for sec in page_rules.missing_sections(body, page_rules.required_sections_for(ptype)):
                hit(rel, _RULE_BY_TYPE.get(ptype, "sections"), f"missing section {sec}")
        # 公式邻接：公式重的 lesson 必须引用源页截图（spec §10）
        if ptype == "lesson" and "$$" in body and "![[" not in body:
            hit(rel, "formula-screenshot", "formula lesson lacks source-page screenshot embed")
        # L6 代理：lesson 去占位后过短 = 疑似空课/封面页产物（精确 L6 需源页映射，见 plan 取舍）
        if ptype == "lesson" and len(_PLACEHOLDER.sub("", body).strip()) < 80:
            hit(rel, "L6-empty-lesson", "lesson body too short (proxy for cover/blank/toc)")
        # 断链
        for target in _WIKILINK.findall(body):
            if target.startswith(("http://", "https://")):
                continue
            if not _link_exists(vault, target):
                hit(rel, "broken-link", f"[[{target}]] not found")
    # 重复 canonical_id（vault 级，阻断）
    _reg, errors, _warn = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    for e in errors:
        hit("concepts/", "duplicate-canonical", e)
    return vs


def _published_pages(vault: Path) -> list[tuple[str, dict]]:
    out = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, _ = mdpage.read_page(f)
        if meta.get("status") == "published":
            out.append((rel, meta))
    return out


def build_index(vault) -> str:
    """index.generated.md：只收录 status: published（spec §3.3），按类型分组、确定性排序。"""
    vault = Path(vault)
    groups: dict[str, list[str]] = {}
    for rel, meta in _published_pages(vault):
        groups.setdefault(meta.get("type", "other"), []).append(
            f"- [[{rel}|{meta.get('title') or meta.get('canonical_name') or rel}]]")
    lines = ["# 内容目录（派生文件：由收尾 CLI 重建，只收录 published，勿手改）", ""]
    for ptype in ["overview", "concept", "topic", "comparison", "synthesis", "lesson", "source", "other"]:
        if ptype in groups:
            lines += [f"## {ptype}", ""] + sorted(groups[ptype]) + [""]
    return "\n".join(lines)


def write_index(vault) -> None:
    (Path(vault) / "index.generated.md").write_text(build_index(vault),
                                                    encoding="utf-8", newline="\n")


def promote(vault, pages: list[dict]) -> int:
    """proposed → published（只动 frontmatter status，不碰正文）。"""
    vault = Path(vault)
    for p in pages:
        meta, body = mdpage.read_page(vault / p["rel_path"])
        meta["status"] = "published"
        mdpage.write_page(vault / p["rel_path"], meta, body)
    return len(pages)
