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
import thresholds  # 门禁阈值单一真值（env 可覆盖）

_EXCLUDE_TOP = {"Review-Queue", "_meta", "assets"}
_DERIVED = {"index.generated.md", "aliases.md"}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
# callout 学习白名单（设宽；不强制必须用 callout，只禁未知类型，防 LLM 乱编导致 Obsidian 不渲染）
CALLOUT_WHITELIST = frozenset({"note", "tip", "info", "important", "warning", "question",
                               "example", "abstract", "summary", "quote", "success", "todo"})
_CALLOUT = re.compile(r"^>\s*\[!([A-Za-z][\w-]*)\]", re.MULTILINE)
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


def concepts_without_synthesis(pages: list[dict]) -> int:
    """软提醒原语（非阻断）：本批 proposed 里产出了 concept 却没有任何综合层页
    （overview/topic/comparison/synthesis）时，返回 concept 数；否则 0。阶段 E（综合层）是
    一等产物，跳过它多半是漏做；但纯 lesson 的小源（如几行笔记）无综合层属正常，故只提醒不阻断。"""
    types = [p.get("meta", {}).get("type") for p in pages]
    n_concept = sum(t == "concept" for t in types)
    has_synth = any(t in ("overview", "topic", "comparison", "synthesis") for t in types)
    return n_concept if (n_concept and not has_synth) else 0


# 概念多的源必须有 topic 主题页做分类层（扁平概念之上的导航）；阈值以下的小源不强制。
# 阈值见 thresholds.TOPIC_THRESHOLD（env 可覆盖）。


def concept_heavy_without_topic(pages: list[dict]) -> int:
    """阻断原语：本批产出 ≥_TOPIC_THRESHOLD 个 concept 却无任何 topic 页时返回 concept 数；否则 0。
    概念去重后是扁平命名空间，分类靠 topic 页（按主题把概念聚起来）——概念多还不分组就发布，
    用户只会看到一堆并列概念、无从导航（llm-wiki 通用模式：topic 页 + 图谱做分类，不靠文件夹）。
    小源（<阈值）只有零散概念、无主题可聚属正常，不强制。"""
    types = [p.get("meta", {}).get("type") for p in pages]
    n_concept = sum(t == "concept" for t in types)
    has_topic = any(t == "topic" for t in types)
    return n_concept if (n_concept >= thresholds.TOPIC_THRESHOLD and not has_topic) else 0


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
        # prose-markup 检查剔除代码块：编程页代码里的 [^...]/[E../[[ 是代码非 wiki 标记
        prose = page_rules.strip_code_blocks(body)
        # L1：任何页正文不得有裸 E-ID
        for bare in page_rules.find_bare_evidence_ids(prose):
            hit(rel, "L1", f"bare evidence id {bare}")
        # 证据脚注：引用必须有定义（引用从散文取，定义仍从全文取——定义行不在代码块）
        for fn in sorted(page_rules.footnote_refs(prose) - page_rules.footnote_defs(body)):
            hit(rel, "evidence-footnote", f"footnote [^{fn}] has no definition")
        # 必需小节（concept=L2 / topic=L3 / overview=L5 / 其余统称 sections）
        if ptype in page_rules.REQUIRED_SECTIONS:
            for sec in page_rules.missing_sections(body, page_rules.required_sections_for(ptype)):
                hit(rel, _RULE_BY_TYPE.get(ptype, "sections"), f"missing section {sec}")
        # 公式邻接：公式重的 lesson 必须引用源页截图（spec §10）
        if ptype == "lesson" and "$$" in body and "![[" not in body:
            hit(rel, "formula-screenshot", "formula lesson lacks source-page screenshot embed")
        # 表格内公式含未转义 `|`：会被当列分隔符撕碎公式 / KaTeX 渲染失败（任意页类型）
        for snip in page_rules.katex_pipe_in_table(body):
            hit(rel, "formula-table-pipe",
                f"公式内未转义的 | 落在表格单元格（用 \\lvert\\rvert 或 \\| 或把公式移出表格）：{snip}")
        # L6 代理：lesson 去占位后过短 = 疑似空课/封面页产物（精确 L6 需源页映射，见 plan 取舍）
        if ptype == "lesson" and len(_PLACEHOLDER.sub("", body).strip()) < thresholds.LESSON_MIN_BODY:
            hit(rel, "L6-empty-lesson", "lesson body too short (proxy for cover/blank/toc)")
        # 断链（从散文取——代码里的 [[ 不是 wikilink）
        for target in _WIKILINK.findall(prose):
            if target.startswith(("http://", "https://")):
                continue
            if not _link_exists(vault, target):
                hit(rel, "broken-link", f"[[{target}]] not found")
        # callout 类型白名单（未知类型 → 阻断，复用现有 lint 通道）
        for ct in _CALLOUT.findall(page_rules.strip_code_blocks(body)):
            if ct.lower() not in CALLOUT_WHITELIST:
                hit(rel, "callout-unknown",
                    f"未知 callout 类型 [!{ct}]（白名单：{', '.join(sorted(CALLOUT_WHITELIST))}）")
    # 综合层缺失（阶段 E 是一等产物，spec §3）：本批产出 concept 却无任何综合层页 → fail-closed
    n_skip = concepts_without_synthesis(pages)
    if n_skip:
        hit("(synthesis-layer)", "L7-synthesis-missing",
            f"本批产出 {n_skip} 个 concept 但无综合层页（overview/topic/comparison/synthesis）；"
            "阶段 E 必做——至少更新 overview，再发布")
    # 分类层缺失：概念多却无 topic 主题页 → fail-closed（扁平概念之上的导航层）
    n_flat = concept_heavy_without_topic(pages)
    if n_flat:
        hit("(topics)", "topics-missing",
            f"本批产出 {n_flat} 个 concept 却无 topic 主题页（≥{thresholds.TOPIC_THRESHOLD} 概念须按主题聚成 topic 页做分类层）；"
            "阶段 E 必做——把概念按主题分组")
    # 重复 canonical_id（vault 级，阻断）
    _reg, errors, _warn = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    for e in errors:
        hit("concepts/", "duplicate-canonical", e)
    return vs


# Spec 2：MinerU 结构化块的风险 flag（lint 据此判风险窗，要求 lesson 可追溯）。
RISK_FLAGS = {"table", "equation", "image", "ocr_low_confidence"}


def lint_risk_traceability(pages: list[dict], *, source_id: str, risk_block_ids: set,
                           written: set) -> list[dict]:
    """Spec 2 渐进 risk lint（仅 mineru 源由 cmd_lint 启用）：本源有风险窗（table/equation/
    image/ocr_low_confidence）时，归属本源的 proposed lesson 页须有可追溯 source_refs
    （某条 ref 的 source==本源 且 block_ids 非空）。risk_block_ids 空 = 无风险窗，不触发；
    不碰旧来源（pymupdf/markdown 源 cmd_lint 不调用此规则）。"""
    vs: list[dict] = []
    if not risk_block_ids:
        return vs
    for p in pages:
        rel, meta = p["rel_path"], p["meta"]
        if meta.get("type") != "lesson":
            continue
        if not belongs_to_source(rel, meta, source_id, written):
            continue
        refs = meta.get("source_refs") or []
        ok = any(isinstance(r, dict) and r.get("source") == source_id and r.get("block_ids")
                 for r in refs)
        if not ok:
            vs.append({"path": rel, "rule": "risk-traceability",
                       "detail": "mineru 风险源（table/equation/image）的 lesson 页缺可追溯 "
                                 "source_refs：须含 {source, window, pages, block_ids}"})
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
