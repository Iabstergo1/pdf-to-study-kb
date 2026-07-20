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
_DERIVED = {"index.generated.md", "aliases.md", "quiz-index.generated.md",
            "propositions.generated.md"}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
# callout 学习白名单（设宽；不强制必须用 callout，只禁未知类型，防 LLM 乱编导致 Obsidian 不渲染）
CALLOUT_WHITELIST = frozenset({"note", "tip", "info", "important", "warning", "question",
                               "example", "abstract", "summary", "quote", "success", "todo"})
# callout 类型的语法入口只有一个：page_rules.parse_callouts（render_safety_violations 遍历其
# 节点+错误头做白名单检查）。这里不再维护第二套类型正则——两套语法曾双向分裂。
# D-1/G1：发布正文禁嵌源图（`![[assets/...]]`）。源图只作 LLM 阅读证据，不进入发布产物。
_SOURCE_IMG = re.compile(r"!\[\[\s*assets/")
_PLACEHOLDER = re.compile(r"（待 /ingest 填写[^）]*）")
# init-vault 种子模板的尖括号占位行（如「<按领域组织的概念网络：…>」整行占位）。
# 曾两次发生：ingest 声称已重写 overview，实际重写被 lint 失败回滚吃掉、重跑无人复查，
# published 的 overview 始终是未填充种子——门禁对此完全沉默。
_SEED_PLACEHOLDER = re.compile(r"^\s*<[^<>\n]+>\s*$", re.MULTILINE)
# A1/A3：这些类型的页正文里残留占位 = 半成品，阻断（lesson 用 L6 长度代理，不在此列）。
_PLACEHOLDER_TYPES = ("concept", "topic", "comparison", "overview")


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


def _sources_with_synthesis(vault) -> frozenset[str]:
    """已发布综合层页（overview/topic/comparison/synthesis）已覆盖过的 source id 集合。
    供 concepts_without_synthesis 判断"这个来源的阶段 E 义务是否已被满足过"——首次入库
    仍必须在本批带综合层页，但对已有综合层覆盖的来源做窄范围返工（只改既有 concept 的
    局部措辞）不必每次重触综合层，否则会逼着写无实质内容的综合层编辑（历史教训：
    broken-link 规则曾用同一逻辑逼出一整页无来源的"死锁"概念）。"""
    vault = Path(vault)
    out: set[str] = set()
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, _ = mdpage.read_page(f)
        if meta.get("type") not in ("overview", "topic", "comparison", "synthesis") \
                or meta.get("status") != "published":
            continue
        for r in (meta.get("source_refs") or []):
            if isinstance(r, dict) and r.get("source"):
                out.add(r["source"])
    return frozenset(out)


def concepts_without_synthesis(pages: list[dict], *,
                                covered_sources: frozenset[str] = frozenset()) -> int:
    """本批 proposed 里产出了 concept 却没有任何综合层页（overview/topic/comparison/
    synthesis）时，返回仍需追责的 concept 数；否则 0。阶段 E（综合层）是一等产物，跳过它
    多半是漏做；但纯 lesson 的小源（如几行笔记）无综合层属正常，不计入。

    covered_sources：已有已发布综合层页覆盖过的 source id 集合（见 _sources_with_synthesis）。
    一个 concept 只有在"本批没带综合层页"且"它的 source_refs 里有来源不在 covered_sources
    内"时才计入——已经满足过阶段 E 义务的来源，窄范围返工不必每次重触综合层；没有
    source_refs 的 concept（无法判断来源是否已覆盖）保守计入，维持原有阻断。"""
    types = [p.get("meta", {}).get("type") for p in pages]
    n_concept = sum(t == "concept" for t in types)
    has_synth = any(t in ("overview", "topic", "comparison", "synthesis") for t in types)
    if not n_concept or has_synth:
        return 0
    uncovered = 0
    for p in pages:
        if p.get("meta", {}).get("type") != "concept":
            continue
        srcs = {r.get("source") for r in (p.get("meta", {}).get("source_refs") or [])
                if isinstance(r, dict) and r.get("source")}
        if not srcs or not srcs.issubset(covered_sources):
            uncovered += 1
    return uncovered


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


def is_accounted_write(rel_path: str, accounted: set[str]) -> bool:
    """记账判定（与归属 belongs_to_source **正交**）：本轮写作动作是否进入处理台账——
    窗口 write_set ∪ query-session candidate_write_set（kb-save 的记账通道）。
    source_refs 只解决"哪个来源的 lint 管这页"，不能替代记账（曾出现整本书 66 窗写集
    零非概念页仍全部过门禁——文档契约与实现脱节）。"""
    return rel_path.replace("\\", "/") in accounted


def belongs_to_source(rel_path: str, meta: dict, source_id: str, written: set[str]) -> bool:
    """页面**归属**判定（lint/promote 范围隔离，只回答"哪个来源负责这页"）：
    **frontmatter 归属（source/source_id/source_refs）是最高依据**——归属他源的页即使被
    本源 write_set 记账也不属于本源（曾复现：source_refs=B 的页被 A 的写集认领后 A/B 双真，
    A 可代发 B 的页）。仅当页面没有任何 frontmatter 归属字段时，才回退用本源 window
    write_set 认领（遗留 lesson 等无归属字段页）。`sources/<src>.md` 恒属本源。
    记账检查另走 is_accounted_write——归属命中不等于本轮动作已入台账。"""
    if rel_path == f"sources/{source_id}.md":
        return True
    fm_sources = {s for s in (meta.get("source"), meta.get("source_id")) if s}
    fm_sources |= {r.get("source") for r in (meta.get("source_refs") or [])
                   if isinstance(r, dict) and r.get("source")}
    if fm_sources:
        return source_id in fm_sources
    return rel_path in written


def _link_exists(vault: Path, target: str) -> bool:
    t = target.strip()
    return (vault / t).exists() or (vault / f"{t}.md").exists()


def placeholder_violations(vault, pages: list[dict]) -> list[dict]:
    """A1+A3：占位符残留检查。concept/topic/comparison/overview 正文若仍含"（待 /ingest 填写）"
    = 半成品 → 阻断。覆盖两处：① 本轮 proposed（pages）；② vault 内已 published 的同类页
    （堵"首轮发布后永不复检"的洞——弱模型留的占位曾就这样静默发布）。"""
    vault = Path(vault)
    vs: list[dict] = []
    seen: set[str] = set()
    for p in pages:
        if p["meta"].get("type") in _PLACEHOLDER_TYPES and _PLACEHOLDER.search(p["body"]):
            vs.append({"path": p["rel_path"], "rule": "placeholder-unfilled",
                       "detail": "页面仍含未填写占位「（待 /ingest 填写）」；不许发布半成品（阶段 E 必做：填实正文）"})
            seen.add(p["rel_path"])
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in seen or rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if (meta.get("status") == "published" and meta.get("type") in _PLACEHOLDER_TYPES
                and _PLACEHOLDER.search(body)):
            vs.append({"path": rel, "rule": "placeholder-unfilled",
                       "detail": "已发布页仍含未填写占位「（待 /ingest 填写）」（历史半成品）；请补实后再发布"})
    return vs


def window_evidence_violations(rows: list[dict], read_window_ids, *, scope_paths=None) -> list[dict]:
    """写页窗口的阅读证据（纯函数；rows = 本源 ingest_progress 行，read_window_ids = window_reads）。

    `window-unread-write`：这一窗写了页，却从未 show-window 读过它 → 页的内容无源可依。确定性层
    能强制"跑完流程"，却强制不了"LLM 真读了源"——读窗台账是唯一能机器判定的替代证据（靠预训练
    知识 + chapters.json 章节地图凭空写页时，页面形式全部合规，只有这里露馅）。
    无阈值二值判据（不问覆盖率，只问"写了页的窗读过吗"）。空写集跳窗合法——读后判定无可写内容
    是正常结果，不在此约束。

    刻意不查 `started_at == finished_at` 的"同秒刷窗"：window-start/done 之间并不强制包含写页
    动作（页可先写好再记账），秒级时间戳下合法的快速记账也会同秒，假阳性高；且实测中同秒刷窗
    的窗无一例外也是未读窗，本规则已完全覆盖其检出。

    scope_paths：只查 write_set 与之有交集的窗（本批 proposed 页集合）；None = 全查。
    reopen/reset 都不清 ingest_progress（"一概不动"），上一轮的窗行会永久留在台账里，其页早已
    published——lint 是本批发布门禁，不为历史轮的证据缺口阻断本批（旧源根本没有读窗记录）。"""
    import json as _json
    reads = set(read_window_ids)
    scope = None if scope_paths is None else {str(x).replace("\\", "/") for x in scope_paths}
    vs: list[dict] = []
    for r in rows:
        raw = r.get("write_set_json")
        try:
            writes = _json.loads(raw) if raw else []
        except (ValueError, TypeError):
            writes = []          # 损坏 JSON 由 window-done 的 C3 校验负责，这里不重复报
        if not writes:
            continue
        if scope is not None and not ({str(w).replace("\\", "/") for w in writes} & scope):
            continue             # 历史轮的窗（页已 published，不在本批）
        wid = r.get("window_id")
        if wid not in reads:
            vs.append({"path": f"(window {wid})", "rule": "window-unread-write",
                       "detail": f"窗 {wid} 写了 {len(writes)} 个页却无 show-window 读窗记录；"
                                 "写页前必须读窗——未读窗写出的页没有源依据"})
    return vs


def scan_source_pages(vault) -> list[dict]:
    """vault 内全部 `type: source` 页（published ∪ proposed）→ [{rel_path, meta}]。
    唯一性须跨 status 看：重复的两份无论哪份先发布都会撞图谱 node id。"""
    vault = Path(vault)
    out: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, _body = mdpage.read_page(f)
        if meta.get("type") == "source":
            out.append({"rel_path": rel, "meta": meta})
    return out


def source_page_violations(pages: list[dict]) -> list[dict]:
    """来源台账页的路径规范 + 每 source_id 唯一（纯函数；调用方给 vault 内全部 source 页）。

    台账页只能落 `sources/<source_id>.md`。域下另建同 source_id 的台账页会与顶层那份在
    graph_model._page_id 生成同一个 `source:<id>` 节点 id → rebuild-graph fail-hard；图谱是
    publish-isolated 的，发布不拦，图谱就此静默坏掉。既有 source-page-missing 只查台账页**存在**，
    不查唯一/位置——两份都在时它照样通过。"""
    vs: list[dict] = []
    by_id: dict[str, list[str]] = {}
    for p in pages:
        meta = p["meta"]
        if meta.get("type") != "source":
            continue
        sid = str(meta.get("source_id") or "")
        if not sid:
            continue
        rel = p["rel_path"]
        by_id.setdefault(sid, []).append(rel)
        expect = f"sources/{sid}.md"
        if rel != expect:
            vs.append({"path": rel, "rule": "source-page-misplaced",
                       "detail": f"source 台账页须落 `{expect}`（当前 `{rel}`）；域下不设 sources/，"
                                 "综合层与台账页只落顶层"})
    for sid, rels in sorted(by_id.items()):
        if len(rels) > 1:
            vs.append({"path": sorted(rels)[0], "rule": "source-page-duplicate",
                       "detail": f"source_id `{sid}` 有 {len(rels)} 个台账页（{'、'.join(sorted(rels))}）；"
                                 f"同 source_id 撞图谱 `source:{sid}` 节点 id → rebuild-graph fail-hard，"
                                 f"只保留 `sources/{sid}.md`"})
    return vs


# 非 Obsidian 数学分隔符：`\(`/`\[` 在 Obsidian 里不渲染（行内应 $…$，块级 $$…$$）。
# 负向后顾排除 `\\[4pt]` 这类 KaTeX 换行间距；只查开分隔符，行内代码已被 strip 剔除。
_MATH_DELIM = re.compile(r"(?<!\\)\\[\(\[]")


def render_safety_violations(rel: str, body: str) -> list[dict]:
    """已知、确定性、可高置信识别的渲染陷阱（唯一实现）：proposed 批（lint_pages）与
    published preflight（vault_render_safety）共用。覆盖：callout 未知类型 / 块内同级
    callout 头 / 非 Obsidian 数学分隔符 / 空题干。未知渲染陷阱不在此穷举——由
    kb-postmortem 循环发现后立法。纯函数、无 I/O。"""
    import page_rules
    vs: list[dict] = []
    b = page_rules.strip_code_blocks(body)
    nodes, errors = page_rules.parse_callouts(b)
    # 类型白名单直接遍历解析器视野（节点 + 结构错误头）——曾有第二套正则只认 ASCII 开头，
    # Unicode 类型逃逸未知检查、连字符类型对解析器隐身，两套语法双向分裂。
    seen_types: list[str] = [n["type"] for n in nodes] + \
        [e["type"] for e in errors if e["kind"] != "empty-question-stem"]
    for ct in seen_types:
        if ct.lower() not in CALLOUT_WHITELIST:
            vs.append({"path": rel, "rule": "callout-unknown",
                       "detail": f"未知 callout 类型 [!{ct}]"
                                 f"（白名单：{', '.join(sorted(CALLOUT_WHITELIST))}）"})
    for e in errors:
        if e["kind"] == "same-depth-callout-inside-active-block":
            vs.append({"path": rel, "rule": "callout-nested-malformed",
                       "detail": f"callout 块内出现同级 `[!…]` 头，会被 Obsidian 渲染成字面量文本"
                                 f"（答案不再折叠）；嵌套请写 `> > [!type]`，或用真空行结束上一个块：{e['text']}"})
        elif e["kind"] == "empty-question-stem":
            vs.append({"path": rel, "rule": "question-stem-empty",
                       "detail": "自测题既无标题文本也无正文题干行——空题干进不了复习闭环，补题干或删除该块"})
    for line in sorted({b[:m.start()].count("\n") + 1 for m in _MATH_DELIM.finditer(b)}):
        vs.append({"path": rel, "rule": "math-delimiter-nonobsidian",
                   "detail": f"LaTeX 分隔符 `\\(`/`\\[` Obsidian 不渲染（第 {line} 行）——"
                             "行内公式用 $…$，块级用 $$…$$"})
    return vs


def vault_render_safety(vault, statuses: tuple = ("published",)) -> list[dict]:
    """全库渲染安全复检（lint 的 vault preflight 与 vault-lint CLI 共用）：扫描指定状态的
    页面，违规附 `owner`（frontmatter source / source_id / source_refs 首源，缺省
    vault-health）供 Review-Queue 归属与去重。只读、零 LLM。"""
    vault = Path(vault)
    out: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") not in statuses:
            continue
        owner = (meta.get("source") or meta.get("source_id")
                 or next((r.get("source") for r in (meta.get("source_refs") or [])
                          if isinstance(r, dict) and r.get("source")), None) or "vault-health")
        for v in render_safety_violations(rel, body):
            v["owner"] = owner
            out.append(v)
    return out


def _membership_nodes(vault) -> dict[str, dict]:
    """topic 收编检查的共享输入：vault 内 published ∪ proposed 的 concept/topic 节点
    （proposed 页已在盘上，rglob 可见），喂给 graph_model.topic_membership（唯一实现）。"""
    vault = Path(vault)
    nodes: dict[str, dict] = {}
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") not in ("published", "proposed") or meta.get("type") not in ("concept", "topic"):
            continue
        links = {t.strip().rstrip("\\") for t in _WIKILINK.findall(body)
                 if not t.strip().startswith(("http://", "https://"))}
        nodes[rel] = {"type": meta["type"], "domain": meta.get("domain", "") or "",
                      "canonical_id": meta.get("canonical_id", "") or "",
                      "related_concepts": meta.get("related_concepts") or [], "links": links}
    return nodes


def _concepts_by_domain(nodes: dict[str, dict]) -> dict[str, int]:
    n_by_dom: dict[str, int] = {}
    for n in nodes.values():
        if n["type"] == "concept":
            n_by_dom[n["domain"]] = n_by_dom.get(n["domain"], 0) + 1
    return n_by_dom


def concepts_uncovered_by_topic(vault) -> list[str]:
    """A2：概念全覆盖检查。复用 graph_model.topic_membership（与图谱社区同一套归属逻辑）算出
    未被任何 topic 收编的 concept；仅对 concept-heavy 域（概念数 ≥ TOPIC_THRESHOLD）强制——
    与 topics-missing 阈值一致。返回未覆盖的 concept page_path（排序）。"""
    import graph_model
    nodes = _membership_nodes(vault)
    _membership, unassigned = graph_model.topic_membership(nodes)
    n_by_dom = _concepts_by_domain(nodes)
    out: list[str] = []
    for dom, cps in unassigned.items():
        if n_by_dom.get(dom, 0) >= thresholds.TOPIC_THRESHOLD:
            out.extend(sorted(cps))
    return sorted(out)


def topic_coverage_monopoly(vault) -> list[str]:
    """②软警告（非阻断）：单个 topic 收编了某域内过高比例的概念。A2 只查"链接存在"，
    一页尾部链接倾倒即可便宜满足；收编占比异常是倾倒糊弄的征兆，提示人工复核收编质量
    （按主题拆分/充实正文），不阻断发布。仅对 concept-heavy 域（≥ TOPIC_THRESHOLD）检查。"""
    import graph_model
    nodes = _membership_nodes(vault)
    membership, _unassigned = graph_model.topic_membership(nodes)
    n_by_dom = _concepts_by_domain(nodes)
    out: list[str] = []
    for tp in sorted(membership):
        by_dom: dict[str, int] = {}
        for cp in membership[tp]:
            d = nodes[cp]["domain"]
            by_dom[d] = by_dom.get(d, 0) + 1
        for d, n in sorted(by_dom.items()):
            total = n_by_dom.get(d, 0)
            if total >= thresholds.TOPIC_THRESHOLD and n / total >= thresholds.TOPIC_MONOPOLY_RATIO:
                out.append(f"{tp} 一页收编了域 {d} 的 {n}/{total} 个概念"
                           f"（≥{thresholds.TOPIC_MONOPOLY_RATIO:.0%}）——链接倾倒给不了读者真实的"
                           "导航结构，请按主题拆分 topic 或在正文里实质性收编")
    return out


def stray_files(vault) -> list[str]:
    """C4（非阻断软警告）：列出 Obsidian 点击坏链误建的杂物——0 字节 .md，以及 *.png.md/*.jpg.md
    （图片名却被建成 md 空页）。它们不影响发布，但会污染侧栏/画布，提示用户删除。"""
    vault = Path(vault)
    out: list[str] = []
    # 杂物可能落在任何目录（尤其 assets/ 下的 *.png.md），故扫全库，不套 _EXCLUDE_TOP。
    for f in sorted(vault.rglob("*.md")):
        if f.name.endswith((".png.md", ".jpg.md", ".jpeg.md")) or f.stat().st_size == 0:
            out.append(f.relative_to(vault).as_posix())
    return out


def lint_pages(vault, pages: list[dict], *, phase_e: bool = True) -> list[dict]:
    """返回违规列表 [{path, rule, detail}]；空列表 = 门禁通过。
    phase_e=False（kb-save 会话模式）：跳过 ingest 阶段 E 的概念批义务
    （L7-synthesis-missing / topics-missing / overview-seed）——kb-save 的准入门禁
    已保证产出为综合层形态，且不背 overview 维护义务；A2 概念收编等 vault 级
    不变量照常检查。"""
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
        # D-4：正文小节标题不再强制（REQUIRED_SECTIONS 已清空，结构交写作 LLM + purpose 组织）
        # D-1/G1：发布正文禁嵌源图（对本轮 proposed 批也拦，status 无关）——源图只作 LLM 阅读证据
        if _SOURCE_IMG.search(prose):
            hit(rel, "source-image-embed",
                "发布正文禁嵌源图 `![[assets/…]]`；源图只作阅读证据，公式→原生 KaTeX、表格→Markdown、图→mermaid/散文重建")
        # G2：frontmatter 完整性（按页型；非 source 内容页必带 source_refs，吸收 D3 派生页溯源）
        for k in page_rules.missing_frontmatter(meta, ptype):
            hit(rel, "frontmatter-incomplete",
                f"缺 frontmatter 字段 `{k}`（{ptype} 页必备；source_refs 缺失=溯源断裂）")
        # B1：正文首行与文件名同名的一级标题 → Obsidian 内联标题重复渲染，阻断
        if page_rules.leading_h1_duplicates_filename(body, rel):
            hit(rel, "title-duplicate-h1",
                "正文首行是与文件名同名的 `# 标题`；Obsidian 已用文件名做内联标题，删掉正文这行 H1")
        # 表格内公式含未转义 `|`：会被当列分隔符撕碎公式 / KaTeX 渲染失败（任意页类型）
        for snip in page_rules.katex_pipe_in_table(body):
            hit(rel, "formula-table-pipe",
                f"公式内未转义的 | 落在表格单元格（用 \\lvert\\rvert 或 \\| 或把公式移出表格）：{snip}")
        # 表格行内裸竖线 wikilink：过得了链接解析、但 Obsidian 渲染会把 | 当列分隔符撕碎表格
        # （曾整改反了方向：把正确的 \| 转义"改回"裸写法骗过 lint 却弄坏渲染）
        for snip in page_rules.bare_pipe_wikilink_in_table(body):
            hit(rel, "table-wikilink-pipe",
                f"表格行内 wikilink 的别名竖线未转义（转义为 [[path\\|alias]]，或把链接移出表格放散文）：{snip}")
        # L6 代理：lesson 去占位后过短 = 疑似空课/封面页产物（精确 L6 需源页映射，见 plan 取舍）
        if ptype == "lesson" and len(_PLACEHOLDER.sub("", body).strip()) < thresholds.LESSON_MIN_BODY:
            hit(rel, "L6-empty-lesson", "lesson body too short (proxy for cover/blank/toc)")
        # P2：concept/topic/comparison 正文过短 → 残次页（放开小节后由篇幅底线兜底；讲透优先、不设上限）
        if ptype in ("concept", "topic", "comparison") and \
                len(_PLACEHOLDER.sub("", body).strip()) < thresholds.CONTENT_MIN_BODY:
            hit(rel, "content-too-short",
                f"{ptype} 正文过短（去占位后 <{thresholds.CONTENT_MIN_BODY} 字），疑似残次页；把概念讲透，篇幅不设上限")
        # 断链（从散文取——代码里的 [[ 不是 wikilink）。表格单元格内 Obsidian 标准写法是
        # 转义别名竖线 [[path\|alias]]（裸 | 会被当列分隔符撕碎表格）——剥掉目标尾部的转义反斜杠。
        for target in _WIKILINK.findall(prose):
            target = target.rstrip("\\")
            if target.startswith(("http://", "https://")):
                continue
            if not _link_exists(vault, target):
                hit(rel, "broken-link", f"[[{target}]] not found")
        # 渲染安全（callout 类型/嵌套/数学分隔符/空题干）：与 published preflight 同一实现
        vs += render_safety_violations(rel, body)
        # 防复辟（窄规则，不重新引入强制小节）：已废除的概念页模板骨架成套复活 → 阻断
        if ptype == "concept":
            legacy = page_rules.legacy_scaffold_headings(page_rules.strip_code_blocks(body))
            if legacy:
                hit(rel, "legacy-concept-scaffold",
                    f"已废除的模板骨架成套复活（命中旧标题：{'、'.join(legacy)}）——正文应按内容"
                    "散文组织（D-4 无强制小节；单个自然标题合法，成套旧骨架阻断）")
    # ── 阶段 E 概念批义务（ingest 专属；kb-save 会话模式 phase_e=False 跳过）──
    if phase_e:
        # 综合层缺失（阶段 E 是一等产物，spec §3）：本批产出 concept 却无任何综合层页 → fail-closed，
        # 除非该 concept 的来源已有已发布综合层页覆盖过（窄返工轮不必每次重触综合层）
        n_skip = concepts_without_synthesis(pages, covered_sources=_sources_with_synthesis(vault))
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
    # A1+A3：占位符残留（半成品）→ 阻断（含已 published 同类页复检）
    vs += placeholder_violations(vault, pages)
    # overview 种子复检：本批产出 concept（真实内容源）时，vault 入口页不得仍是未填充的
    # init-vault 种子（尖括号占位/待填写占位）。防"重写被回滚吃掉后无人复查"再次静默发布。
    if phase_e and any(p["meta"].get("type") == "concept" for p in pages):
        ov = vault / "overview.md"
        if ov.exists():
            _m, ov_body = mdpage.read_page(ov)
            if _PLACEHOLDER.search(ov_body) or _SEED_PLACEHOLDER.search(ov_body):
                hit("overview.md", "overview-seed",
                    "本批产出 concept 但 overview.md 仍是未填充的种子骨架（含占位符）；"
                    "阶段 E 必做——重写 overview（注意：lint 失败回滚会连 overview 的就地编辑一起还原，修复重跑前须重新应用）")
    # A2：概念未被任何 topic 收编（画布会落入"未分类"）→ concept-heavy 域 fail-closed
    for cp in concepts_uncovered_by_topic(vault):
        hit(cp, "concepts-uncovered",
            "概念未被任何 topic 收编（画布将落入「未分类」）；阶段 E 必做——"
            "归入某 topic 的 related_concepts 或正文 full-path wikilink")
    # 来源台账页唯一性/位置（vault 级，阻断）：域下另建的同 source_id 台账页撞图谱 `source:<id>`
    # 节点 id → rebuild-graph fail-hard；图谱 publish-isolated 不阻断发布，故必须在这里拦。
    vs += source_page_violations(scan_source_pages(vault))
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
        # B3：显示名优先 title/canonical_name，回退 basename（不显示完整路径；链接目标仍是全路径 rel）
        display = meta.get("title") or meta.get("canonical_name") or Path(rel).stem
        groups.setdefault(meta.get("type", "other"), []).append(f"- [[{rel}|{display}]]")
    lines = ["# 内容目录（派生文件：由收尾 CLI 重建，只收录 published，勿手改）", ""]
    for ptype in ["overview", "concept", "topic", "comparison", "synthesis", "lesson", "source", "other"]:
        if ptype in groups:
            lines += [f"## {ptype}", ""] + sorted(groups[ptype]) + [""]
    return "\n".join(lines)


def write_index(vault) -> None:
    (Path(vault) / "index.generated.md").write_text(build_index(vault),
                                                    encoding="utf-8", newline="\n")


def build_quiz_index(vault) -> str:
    """quiz-index.generated.md：全库自测题索引（派生阅读层，零 LLM）。只收 published 页
    [!question] 的题干 + 回链原页，按 domain（无 domain 按顶层目录）分组——**不带答案**：
    答案在原页折叠块里，索引的价值就是给复习一个入口、把读者带回原页。"""
    vault = Path(vault)
    groups: dict[str, list[str]] = {}
    total = 0
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published":
            continue
        stems = page_rules.extract_question_stems(body)
        if not stems:
            continue
        display = meta.get("title") or meta.get("canonical_name") or Path(rel).stem
        items = groups.setdefault(str(meta.get("domain") or rel.split("/")[0]), [])
        for stem in stems:
            items.append(f"- {stem} → [[{rel}|{display}]]")
            total += 1
    lines = ["# 自测题库（派生文件：由收尾 CLI 重建，只收录 published 页的自测题，勿手改）", "",
             f"共 {total} 题。先自己作答，再点链接回原页展开折叠的参考答案核对。", ""]
    for g in sorted(groups):
        lines += [f"## {g}", ""] + groups[g] + [""]
    return "\n".join(lines)


def write_quiz_index(vault) -> None:
    (Path(vault) / "quiz-index.generated.md").write_text(build_quiz_index(vault),
                                                         encoding="utf-8", newline="\n")


def collect_propositions(vault) -> list[dict]:
    """全库 published 页的具名命题（`**命题（名）**：结论`）：
    [{name, statement, rel, display, domain}]，rglob 排序确定性。"""
    vault = Path(vault)
    out: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in _DERIVED or rel.split("/")[0] in _EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published":
            continue
        props = page_rules.extract_propositions(body)
        if not props:
            continue
        display = meta.get("title") or meta.get("canonical_name") or Path(rel).stem
        dom = str(meta.get("domain") or rel.split("/")[0])
        for name, stmt in props:
            out.append({"name": name, "statement": stmt, "rel": rel,
                        "display": display, "domain": dom})
    return out


def duplicate_proposition_names(props: list[dict]) -> list[str]:
    """域内重名的命题名（名字即锚点，重名 = 引用歧义；软警告用）。"""
    seen: dict[tuple, int] = {}
    for p in props:
        seen[(p["domain"], p["name"])] = seen.get((p["domain"], p["name"]), 0) + 1
    return sorted(f"{d}/命题（{n}）" for (d, n), c in seen.items() if c > 1)


def build_propositions_index(vault) -> str:
    """propositions.generated.md：全库命题总表（派生阅读层，零 LLM）——这个库到底断言了哪些事。
    名字即锚点（v1 不编号）；只列结论句 + 回链，完整论证在原页。"""
    props = collect_propositions(vault)
    lines = ["# 命题总表（派生文件：由收尾 CLI 重建，只收录 published 页的具名命题，勿手改）", "",
             f"共 {len(props)} 条。引用写法：命题（名字）；点击回链查看完整论证。", ""]
    groups: dict[str, list[str]] = {}
    for p in props:
        groups.setdefault(p["domain"], []).append(
            f"- **{p['name']}** — {p['statement']}（出自 [[{p['rel']}|{p['display']}]]）")
    for g in sorted(groups):
        lines += [f"## {g}", ""] + groups[g] + [""]
    return "\n".join(lines)


def write_propositions_index(vault) -> None:
    (Path(vault) / "propositions.generated.md").write_text(build_propositions_index(vault),
                                                           encoding="utf-8", newline="\n")


def promote(vault, pages: list[dict]) -> int:
    """proposed → published（只动 frontmatter status，不碰正文）。"""
    vault = Path(vault)
    for p in pages:
        meta, body = mdpage.read_page(vault / p["rel_path"])
        meta["status"] = "published"
        mdpage.write_page(vault / p["rel_path"], meta, body)
    return len(pages)
