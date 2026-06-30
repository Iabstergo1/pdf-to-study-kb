# ingest / phase B+C+D — start + per-window writing (the only LLM stretch; quality is won here)

## Phase B: start (guards enforced by the CLI)

`python scripts/pipeline.py ingest-start --source <src>`: takes the vault lock + validates the stale
registry. If it aborts, regenerate the work order as prompted — **do not bypass it.**

## Phase C prelude: build whole-book understanding first (`chapters.json` = deterministic chapter map / navigation spine)

Before writing windows, read `staging/<src>/chapters.json` — produced **deterministically** by
`source-convert` from the PDF bookmarks (each chapter's `chapter_id` + page range; **the map is drawn by
the CLI and sha256-frozen, not by the LLM**; md sources degrade to a single whole-book chapter). It is the
**whole-book map** of this source:

- **Read the chapter map first** and decide, per chapter, which concepts deserve depth and which get a
  sentence. Use it as shared context for chapter-by-chapter writing. This is the cure for "thin" pages —
  the LLM writes inside the whole book's structure, not from an isolated 2000-token fragment.
- **Organize writing by chapter:** windows remain the **deterministic read & accounting unit**
  (`window-done --writes` unchanged), but advance **by chapter** (the chapter a window's pages fall into);
  finish a chapter's windows before the next. Map window→chapter via the `<!-- page N -->` markers in `source.md`.
- **Resume anchor:** after an interruption, re-read `chapters.json` + the digest `## RESUME` block to locate
  the next unfinished window. The chapter map is deterministically replayable; the LLM never re-draws it.
- The overview "concept map" and topic split **follow the chapter map** (see phase E).

## Phase C: per-window sub-units (rolling digest = external memory for long sources)

For each window in `staging/<src>/windows.jsonl` (ascending `window_id`, **in chapter order**), advance
U1–U7. **Each sub-unit has an output + acceptance + persisted artifact** — not one vague "read and write".

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| **U1 read window** | window_id | window source text + resume check | `window-start` recorded; `digest.md` read | `ingest_progress` | window missing |
| **U2 extract candidates** | window text | candidate concepts / key claims | each cites a source §section | (digest draft) | — |
| **U3 resolve** | candidates | `[merged]`/`[created]` concept page + canonical_id | merge on hit, never create a duplicate | concept frontmatter | registry corrupt |
| **U4 draft** | resolution + window text | `status: proposed` pages (required sections present) | frontmatter valid, no missing section | vault (proposed) | check-write DENY |
| **U5 self-check** | drafted pages | page_rules self-check | **0 violations** before U6 | — | self-check fails → fix, do not account |
| **U6 account** | written pages | `window-done --writes '[...]'` | **every** non-source page in --writes | `ingest_progress` | miss → orphan page |
| **U7 digest** | window highlights | roll `digest.md` (keep last 8 windows in detail + older folded to chapter summaries) + refresh top `## RESUME` block | new concepts / open threads; RESUME points to next window; digest stays bounded | `staging/<src>/digest.md` | — |

Sub-unit command detail:
- U1: `python scripts/pipeline.py window-start --source <src> --window <id> --hash <window sha or char-range string>`;
  `python scripts/pipeline.py show-window --source <src> --window <id>` reads the window. If the top of the
  output shows `<!-- route-b-assets`, each line like
  `- page=26 tier=must reason=formula staging=.../assets/p0026.png vault=![[assets/<src>/p0026.png]]` is
  **visual evidence you must check** — `tier=must` read the image, `tier=nice` at least skim it; when you
  write the matching formula/table/figure, embed the `vault=` `![[assets/<src>/pXXXX.png]]` (formula → KaTeX
  `$$…$$`, figure → embed the source image, table → markdown + source image, per phase D). Read
  `staging/<src>/digest.md` first (skip on the first window). Use `--plain` only when debugging the raw slice.
- U3: `python scripts/pipeline.py resolve-concept --mention "<mention>" --domain <domain> [--alias "<english name>"] --ref-source <src> --ref-sections "<5.2>"`, then edit the page it returns.
- U5: self-check primitives in `scripts/page_rules.py` (see "lint hard rules" below).
- U6: `python scripts/pipeline.py window-done --source <src> --window <id> --writes '["<page>"]'` (on failure use `window-fail --error "<reason>"`).
- U7: refresh the `## RESUME` block at the **top** of `digest.md` each window (the resume anchor; on
  resume say "continue" or run `scripts/resume-ingest.ps1`, both relocate via the RESUME block + `pipeline.py
  next` — a machine-readable anchor for Claude and Codex alike, no session hook). The block runs from
  `## RESUME` to the next `## `, stays terse, and contains at least: **progress** (done windows + next
  window id and its `--hash`), **resume steps** (`ingest-start` is idempotent and reports resumed → per-window
  loop), and a one-line **writing discipline** reminder (concepts via resolve-concept, full-path wikilinks,
  interpreter + `PYTHONUTF8=1`). **Do not dump full window logs into RESUME.** When the whole source is done,
  rename the heading to `## DONE` so resume is not misled.
  - **Rolling digest discipline (prevents context bloat; unattended resume depends on it):** keep only the
    **last 8 windows** in per-window detail; fold older windows into **chapter-level summaries** (one line per
    chapter). After each window, compress the window sliding out of the last-8 range into the chapter
    summary. Goal: digest size grows with chapter count, not window count.

## Phase D: writing discipline (applies to every write)

- **Write guard:** `python scripts/pipeline.py check-write --source <src> --path <vault-rel-path>`. DENY
  (out of scope / not in snapshot / hash changed / `managed_by: human`) → do not write; put the proposed
  change in `wiki/Review-Queue/<page>-proposal.md`.
- **Snapshot before overwriting an existing page:** `python scripts/pipeline.py snapshot-page --source <src> --path <rel-path>`.
- **Every new/edited page is `status: proposed` + `managed_by: pipeline`;** templates in `templates/`, required sections present.
- **Concepts only via resolve-concept** (merge on hit, never create a duplicate); aliases only in the concept page's `aliases:` frontmatter.
- **Never hand-write derived files:** `concepts/_registry.yaml`, `aliases.md`, `index.generated.md` are rebuilt by the finishing CLI.
- **Non-text content is source-image-authoritative (route B, by type):** any content from a `needs_vision`
  hard page (`pages.jsonl` `needs_vision_reason` = formula / formula-borderline / vector-figure / table /
  caption) must embed the page's source image `![[assets/<src>/pXXXX.png]]`, handled **by type**:
  - **Formula page** (formula / formula-borderline): write full KaTeX **and** embed the source image — plain
    text flattens super/subscripts and fractions (a lesson with `$$` and no image is hard-blocked by lint;
    a concept puts the source image in its "Formalization" section).
  - **Figure page** (vector-figure / captioned figure): **do not redraw from text** — embed the original;
    the prose only explains what it says and how to read it (LLM redraws of vector/flow figures are unreliable).
  - **Table page** (table): convert to a markdown table where possible (searchable/linkable) **and** embed
    the source image to verify; complex or borderless tables are source-image-authoritative.
  - Principle: **the LLM is good at understanding, organizing, explaining; faithful reproduction of non-text
    objects belongs to the original pixels.**
- **Link restraint (avoid graph noise):** wikilink only real strong relations (depends-on / generalizes /
  contrasts / specializes); do not build a central "link-everything" hub. Summary pages (`sources/<src>.md`,
  `overview.md`) wikilink only a few core concepts.
- **Depth (do not degrade to a summary):** every concept has at least one worked example or key derivation
  step (not just a definition); a lesson gives actionable detail, not a chapter recap. Vague summary pages are unfinished.
- **写作风格（高信息密度的学术散文，不是模板填空）：** 正文以连贯段落为主、少用机械的要点罗列；句式长短交错、节奏有起伏，用词精确多样，避免公式化、重复化、可预测的措辞；段落之间有清晰的逻辑递进，每段服务一个目的又彼此衔接，读起来像一篇打磨过的短文而非提纲。**必需小节标题保持 verbatim（lint 强制），但小节内不套固定骨架**——依内容自然展开；表格只用在确需结构化对照处（comparison 的对比维度、topic 的各来源贡献），其余以散文铺陈。不同概念/页面之间也要避免千篇一律的同构句式。
- **页面文件名用中文：** topic/comparison/synthesis 新建页的文件名取中文（与页面 `title` 一致），如 `topics/<中文主题名>.md`、`comparisons/<甲> vs <乙>.md`；概念页文件名由 `resolve-concept` 自动取中文 `canonical_name`，无需你指定。wikilink 因此是中文全路径：`[[domains/<domain>/concepts/<中文概念名>|<中文概念名>]]`。`canonical_id` 仍是稳定 ASCII（内部去重键，不影响侧栏/画布显示）。
- **图谱关系标注（Knowledge Graph v2.0，可选增强而非必填）：** 强 wikilink 关系处可在同段/同列表项末尾追加轻量注释 `<!-- graph: confidence=<extracted|inferred|ambiguous> relation=<depends_on|contrasts|related> evidence="<一句话、有来源依据的理由>" -->`，供确定性图谱构建赋权/分簇。**优先写 `confidence`，`relation` 可省略**；v2.0 关系白名单只有 `depends_on`/`contrasts`/`related`，未知值自动降级为 `related`/`ambiguous`（图谱 lint 记 warning）。注释只解释这条边，页面 `source_refs` 仍是证据权威；**不要给弱导航链接加注释**——无注释 wikilink 由图谱按结构信号（共引/同源/类型亲和）+ topic membership 自动赋权，不会因没标注而丢失。图谱构建全程零 LLM、只读页面已有轻量结构信息；图谱导航入口是 `knowledge-graph.generated.html`（力导向交互图，点击节点经 `obsidian://` 跳到对应 Obsidian 笔记；不再生成 Obsidian canvas）。
- Append to `log.md`: `## [YYYY-MM-DD] ingest | <src> | <created/updated pages>` (append-only).

## Lint hard rules cheat-sheet (violating any one blocks publish; recite before each page)

1. **Wikilinks use full vault-relative paths** (not Obsidian basenames): `[[domains/<domain>/concepts/<中文概念名>|<中文概念名>]]`（中文文件名全路径），and the target page must exist.
2. **Every `[^e1]` reference has a `[^e1]:` definition line**; no bare `[E-...]` IDs in the body.
3. **Required section titles match verbatim** (concept 6 / topic 3 / comparison 4 / synthesis 4 / source 6 / overview 3).
4. **A lesson containing `$$` must embed a real source-page PNG** `![[assets/<src>/pXXXX.png]]`; a lesson is not too short after placeholders are removed.
5. **Concept dedup:** only via resolve-concept, merge on hit, never hand-build a duplicate (duplicate `canonical_id` blocks).
6. **Ownership (most-missed):** a page with no `source:` frontmatter (`topics/**`/`comparisons/**`/`synthesis/**`/`overview.md`) **must be in some window's `window-done --writes`**, or it is fail-closed as an orphan.
7. **No bare `|` in table-cell formulas:** use `\lvert S \rvert` for `|S|` (or escape `\|`, or move the formula out of the table) — a bare `|` is read as a column separator and breaks KaTeX (`formula-table-pipe` hard-block).
8. **Synthesis (phase E) mandatory:** after producing concepts you must update overview + build topic/comparison/synthesis as needed (into `--writes`), else lint `L7-synthesis-missing` blocks.
9. **Concept coverage (`concepts-uncovered`):** in a concept-heavy domain (≥6 concepts) **every concept must be收编 by some topic** (topic body full-path wikilink or `related_concepts[]`); any uncovered concept blocks publish (already-published pages are re-checked too).
10. **No unfilled placeholders (`placeholder-unfilled`):** a concept/topic/comparison/overview body must not still contain「（待 /ingest 填写）」—— half-finished pages block publish (already-published pages re-checked). （`lint` 另对 0 字节 / `*.png.md` 杂物页发 `stray` 软警告，不阻断。）

## Callouts & figure width (Obsidian rendering)

- **Callouts** (whitelist — unknown types hard-fail lint): pitfalls → `> [!warning]`, self-test →
  `> [!question]`, worked examples → `> [!example]`, key takeaways → `> [!tip]`. Whitelist:
  `note tip info important warning question example abstract summary quote success todo`. Not required
  to use callouts — just never invent a type outside the whitelist.
- **Figure width**: when embedding a hard-page image, size it with `![[assets/<src>/pNNNN.png|640]]`
  (formula pages narrower, full-page figures wider) so it does not overflow the reading column.

## Precise links & inline markup (Obsidian Flavored Markdown)

Obsidian extends Markdown; use these to make the knowledge web precise and readable — **wikilinks always
stay full vault-relative paths** (hard rule #1); the extensions below only add anchors/display on top (the
link regex stops at `|`/`#`, so they are lint-safe).

- **Targeted wikilinks** (full path + anchor/display):
  - `[[domains/x/concepts/y.md|布雷斯悖论]]` — custom display text: read naturally, link precisely.
  - `[[domains/x/lessons/z.md#某小节标题]]` — link to a specific **section heading** of the target page.
  - `[[domains/x/lessons/z.md#^thm-2]]` — link to a specific **block** (paragraph / formula / theorem).
- **Block IDs** make one line linkable: append `^block-id` to the end of a paragraph, or on its own line
  after a list / quote / `$$…$$`. Use for a key theorem / definition / formula another page should cite
  exactly — `… 故均衡唯一。 ^thm-2` → cite as `[[…/z.md#^thm-2]]`. Keep ids short, ascii-kebab.
- **Highlight** a term with `==…==` only on its **first, defining** occurrence (`==<被定义的术语>==`), to mark
  "this is the term being defined here" — not for decoration.
- **Editorial comments** `%%…%%` are hidden in reading view: only for non-substantive notes to a future
  editor. **Never hide substantive content or an unresolved problem inside `%%…%%`** — open issues go to the
  Review-Queue, not into invisible text the lint cannot see.
- **Mermaid** (` ```mermaid `) is allowed only for an **LLM-authored conceptual diagram** you genuinely
  understand (e.g. a small concept-dependency graph); add `class NodeName internal-link;` to make a node a
  vault link. It is **not** a way to reproduce a source figure — source figures stay image-authoritative (phase D).
