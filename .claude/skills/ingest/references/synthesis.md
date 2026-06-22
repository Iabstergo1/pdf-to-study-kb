# ingest / phase E — synthesis duties (first-class, not optional)

**Inputs:** the concepts/lessons written from this source's windows + existing vault synthesis pages.
**Outputs:** incrementally updated overview/topic/comparison/synthesis (`status: proposed`).
**Persisted:** vault pages (proposed) + accounted in the matching window's `--writes`.
**Failure stop:** on a contradiction with an existing conclusion, record it under "open questions" — never silently rewrite.

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
  many concepts (~≥6), **group them by topic into `topics/<topic>.md`** (e.g. "information & dynamic games"
  groups signaling / principal-agent / repeated games), each with a core synthesis + a per-source
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
