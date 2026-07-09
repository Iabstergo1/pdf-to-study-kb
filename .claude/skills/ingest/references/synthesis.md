# ingest / phase E — synthesis duties (first-class, not optional)

**Inputs:** the concepts/lessons written from this source's windows + existing vault synthesis pages.
**Outputs:** incrementally updated overview/topic/comparison/synthesis (`status: proposed`).
**Persisted:** vault pages (proposed) + accounted in the matching window's `--writes`.
**Failure stop:** on a contradiction with an existing conclusion, record it under "open questions" — never silently rewrite.

> **文件名与写作风格：** 新建 topic/comparison/synthesis 一律用**中文文件名**（与页面 `title` 一致，如 `topics/<中文主题名>.md`、`comparisons/<甲> vs <乙>.md`），概念页文件名由 `resolve-concept` 自动取中文 `canonical_name`。正文走**高信息密度的学术散文**：句式长短交错、用词多样、段落有逻辑递进，不套固定模板（详见 `write-pages.md`「写作风格」）。**结构由 `purpose.md` + 内容自然决定，不再有强制的逐字小节标题（D-4）**；表格只用于「对比维度 / 各来源贡献」等确需结构化对照处。综合页（topic/comparison/synthesis/overview）**必须带 `source_refs` 溯源**（G2）。

> **图谱关系（Knowledge Graph v2.0，可选）：** topic 正常 wikilink 其成员概念即可——图谱据 topic membership 自动派生归属，无需手标；comparison 页在正文确实解释了对比时，可对被比较概念加 `<!-- graph: relation=contrasts confidence=inferred evidence="…" -->`；synthesis 页除非关系有来源依据，否则不加宽泛 graph 注释。关系白名单 `depends_on`/`contrasts`/`related`，详见 `write-pages.md`「图谱关系标注」。

- **overview.md updates on every source:** hang this source's new concepts on the "concept map" (and put a
  **topic navigation** block at its top: wikilinks to this source's topic pages, making overview the
  topic→concept entry point). The **concept map and topic navigation are organized concept/topic-first**,
  forming a stable overview→topic→concept **navigation spine**; `chapters.json` (frozen deterministically by
  `source-convert` from the PDF bookmarks) is a **reading aid the LLM may consult, not the organizing order** —
  the wiki is not shaped by the source TOC. Adjust the "recommended learning path" (with per-leg outcomes) and
  the "model family comparison". overview is a living
  synthesis and **must not degrade into a chapter list** — it navigates by concept/topic (chapters.json is a
  navigation aid, not the organizing spine) and its learning routes state **what the reader can do after each
  leg**, not just the reading order. Before updating: `check-write` + `snapshot-page` (it is an existing
  published seed), set `status: proposed`, add `source_refs`, and put it in `--writes`.
- **topic (mandatory for concept-heavy sources, else lint `topics-missing` blocks):** when a source yields
  many concepts (~≥6), **group them by topic into `topics/<中文主题名>.md`** (把同一主题下的若干相关概念聚成一组），each with a core synthesis + a per-source
  contribution table + open questions, wikilinking the related concepts — the categorization/navigation
  layer above flat concepts. **Build the grouping from the full enumerated concept list, never from memory:**
  list every concept page this source actually wrote (glob `domains/*/concepts/` + the digest's cross-book
  merge list), then tick each one off against some topic's body wikilinks or `related_concepts[]` — on a
  100-concept book, memory-based grouping left 6 concepts uncovered and blocked publish (`concepts-uncovered`).
  Any leftover concept: assign it to an existing topic or create the missing topic before `ingest-done`.
- **comparison:** when 2+ comparable models/methods appear, create/update a `comparisons/` page (conclusion / dimensions / when-to-use / related concepts).
- **synthesis:** when a cross-source insight emerges that no single source gives, write a `synthesis/` page.
- **lessons are optional and downgraded (D-2):** only for a continuous teaching/example/exercise stretch that
  won't sink into concepts; **theme-named, never `第X章`, never a per-chapter recap, never "本章/本书" narration.**
  Concepts/topics/comparisons are the primary organization; prefer them.
- The finishing CLI rebuilds only derived files (`index`/`_registry` — `aliases.md` is retired) — it **does not rewrite** the above synthesis content; you own it.

**Acceptance:** overview covers the concept map / recommended learning path (with per-leg outcomes) / model
family comparison (structure is purpose-driven, not fixed section titles), not a bare link list, and carries
`source_refs`; **a concept-heavy source has at least one topic page** (else `topics-missing` blocks) with a
per-source contribution table and `source_refs`; comparison states conclusion / dimensions / when-to-use /
related concepts; every synthesis page is in `--writes`.
