# ingest / phase E — synthesis duties (first-class, not optional)

**Inputs:** the concepts/lessons written from this source's windows + existing vault synthesis pages.
**Outputs:** incrementally updated overview/topic/comparison/synthesis (`status: proposed`).
**Persisted:** vault pages (proposed) + accounted in the matching window's `--writes`.
**Failure stop:** on a contradiction with an existing conclusion, record it under "open questions" — never silently rewrite.

> **文件名与写作风格：** 新建 topic/comparison/synthesis 一律用**中文文件名**（与页面 `title` 一致，如 `topics/<中文主题名>.md`、`comparisons/<甲> vs <乙>.md`），概念页文件名由 `resolve-concept` 自动取中文 `canonical_name`。正文走**高信息密度的学术散文**：句式长短交错、用词多样、段落有逻辑递进，不套固定模板（详见 `write-pages.md`「写作风格」）。必需小节标题仍保持 verbatim，表格只用于「对比维度 / 各来源贡献」等确需结构化对照处。

> **图谱关系（Knowledge Graph v2.0，可选）：** topic 正常 wikilink 其成员概念即可——图谱据 topic membership 自动派生归属，无需手标；comparison 页在正文确实解释了对比时，可对被比较概念加 `<!-- graph: relation=contrasts confidence=inferred evidence="…" -->`；synthesis 页除非关系有来源依据，否则不加宽泛 graph 注释。关系白名单 `depends_on`/`contrasts`/`related`，详见 `write-pages.md`「图谱关系标注」。

- **overview.md updates on every source:** hang this source's new concepts on the "concept map" (and put a
  **topic navigation** block at its top: wikilinks to this source's topic pages, making overview the
  topic→concept entry point). The **concept map and topic navigation follow `chapters.json` (organized by
  chapter)**, forming a stable overview→chapter→topic→concept **navigation spine** — the chapter map is
  frozen deterministically by `source-convert` from the PDF bookmarks, not re-ordered by the LLM. Adjust the
  "recommended learning path" (in chapter order) and the "model family comparison". overview is a living
  synthesis and **must not degrade into a chapter list** (L5 blocks it — it navigates by chapter to
  concepts/topics, it does not just list chapter titles). Before updating: `check-write` + `snapshot-page`
  (it is an existing published seed), set `status: proposed`, and put it in `--writes`.
- **topic (mandatory for concept-heavy sources, else lint `topics-missing` blocks):** when a source yields
  many concepts (~≥6), **group them by topic into `topics/<中文主题名>.md`** (把同一主题下的若干相关概念聚成一组），each with a core synthesis + a per-source
  contribution table + open questions, wikilinking the related concepts — the categorization/navigation
  layer above flat concepts.
- **comparison:** when 2+ comparable models/methods appear, create/update a `comparisons/` page (conclusion / dimensions / when-to-use / related concepts).
- **synthesis:** when a cross-source insight emerges that no single source gives, write a `synthesis/` page.
- **lessons follow the source TOC:** a lesson per source chapter is a linear secondary layer; concepts/topics are the primary organization.
- The finishing CLI rebuilds only derived files (index/registry/aliases) — it **does not rewrite** the above synthesis content; you own it.

**Acceptance:** overview has its three synthesis sections (concept map / recommended learning path / model
family comparison), not a bare link list; **a concept-heavy source has at least one topic page** (else
`topics-missing` blocks) with a per-source contribution table; comparison has its four sections; every
synthesis page is in `--writes`.
