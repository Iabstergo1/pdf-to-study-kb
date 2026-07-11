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
- **Content routing（开工一次，逐窗查表）：** after building whole-book understanding, follow
  `references/content-routing.md` — write a per-chapter `## 路由表`（章 → 类型 → 写法取向）into
  `digest.md` before the first window; consult it per window. Routing is **advisory**: when a chapter's
  label doesn't fit the actual content, write what the content needs and log a `[routing-deviation]`
  marker（固定格式见该手册）. `purpose.md` outranks every routing recommendation.
- The overview "concept map" and topic split **follow the chapter map** (see phase E).

## Phase C: per-window sub-units (rolling digest = external memory for long sources)

For each window in `staging/<src>/windows.jsonl` (ascending `window_id`, **in chapter order**), advance
U1–U7. **Each sub-unit has an output + acceptance + persisted artifact** — not one vague "read and write".

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| **U1 read window** | window_id | window source text + resume check | `window-start` recorded; `digest.md` read | `ingest_progress` | window missing |
| **U2 extract candidates** | window text | candidate concepts / key claims | each cites a source §section | (digest draft) | — |
| **U3 resolve** | candidates | `[merged]`/`[created]` concept page + canonical_id | merge on hit, never create a duplicate | concept frontmatter | registry corrupt |
| **U4 draft** | resolution + window text | `status: proposed` pages (purpose-driven structure) | frontmatter complete for the page type; no source-image embed | vault (proposed) | check-write DENY |
| **U5 self-check** | drafted pages | page_rules self-check | **0 violations** before U6 | — | self-check fails → fix, do not account |
| **U6 account** | written pages | `window-done --writes '[...]'` | **every** non-source page in --writes | `ingest_progress` | miss → orphan page |
| **U7 digest** | window highlights | roll `digest.md` (keep last 8 windows in detail + older folded to chapter summaries) + refresh top `## RESUME` block | new concepts / open threads; RESUME points to next window; digest stays bounded | `staging/<src>/digest.md` | — |

Sub-unit command detail:
- U1: `python scripts/pipeline.py window-start --source <src> --window <id> --hash <window sha or char-range string>`;
  `python scripts/pipeline.py show-window --source <src> --window <id>` reads the window. If the top of the
  output shows `<!-- route-b-assets`, each line like
  `- page=26 tier=must reason=formula staging=.../assets/p0026.png` is **visual evidence you must READ** —
  `tier=must` read the image, `tier=nice` at least skim it; then **re-express it natively in the page and never
  embed it** (formula → native KaTeX `$$…$$` with the result stated, table → Markdown/prose, figure → mermaid/prose,
  per phase D; `source-image-embed` hard-blocks any `![[assets/…]]`). Read `staging/<src>/digest.md` first
  (skip on the first window). Use `--plain` only when debugging the raw slice.
- U3: **first decide the concept's home domain — where the concept actually belongs, not the source book's
  domain (D-3).** A game-theory book that discusses 研究问题 / 文献综述 / 学术论文结构 / A-F框架 (research
  methodology) resolves those into `research-method`, not `game-theory`; statistics/econometrics → `statistics`,
  optimization → `optimization`, etc. Then `python scripts/pipeline.py resolve-concept --mention "<mention>"
  --domain <home-domain> [--alias "<english name>"] --ref-source <src> --ref-sections "<5.2>"`, and edit the page
  it returns. The work order pre-authorizes cross-domain concept writes for the managed home domains (`research-method`
  today) at `domains/<home>/concepts/**` only — if `check-write` DENYs an unlisted home domain, that domain must be
  added to the audited allowlist first, don't force it elsewhere. High-value sub-concepts (纳什均衡 / 子博弈精炼纳什
  均衡 / 贝叶斯纳什均衡 / 完美贝叶斯均衡 / 逆向归纳) deserve **their own pages**, not just plaintext mentions.
  **命名与 alias 卫生（aliases 是 resolve-concept 的命中键，写错会长期劫持后来者）：** ①工具级/实例级
  概念**不得抢注通用名**——canonical_name 取它实际的名字（工具命令名/书内术语），通用学科名留给未来
  真正讲该概念的书（例：讲某工具的"二分定位"功能就叫工具命令名，不叫「二分查找」）；②**别的概念的
  名字绝不进本页 aliases**——组成部件/子概念（它们值得自己的页）塞进整体页的 aliases 后，未来对该
  名字的 resolve 会永远命中错误的页并静默合并（`rebuild-registry` 对跨页撞名打 `[warn]`，见警即改）。
- U5: self-check primitives in `scripts/page_rules.py` (see "lint hard rules" below). **Also verify every
  `[[full-path]]` wikilink target in this window's pages actually exists on disk (or is written in this same
  window)** — CJK long filenames are easy to mistype, and linking a page you *plan* to write later is a
  recurring way `broken-link` violations get introduced. If the target is missing: create it via
  `resolve-concept` now, or rephrase as plain text — never account a page with a dangling link.
- U6: `python scripts/pipeline.py window-done --source <src> --window <id> --writes '["<page>"]'` (on failure use `window-fail --error "<reason>"`). If the shell strips quotes from the JSON (Windows `conda run` gotcha), write the array to a UTF-8 file and pass `--writes-file <path.json>` instead.
- U7: refresh the `## RESUME` block at the **top** of `digest.md` each window (the resume anchor; on
  resume say "continue" or run `scripts/resume-ingest.ps1`, both relocate via the RESUME block + `pipeline.py
  next` — a machine-readable anchor for Claude and Codex alike, no session hook). The block runs from
  `## RESUME` to the next `## `, stays terse, and contains at least: **progress** (done windows + next
  window id and its `--hash`), **resume steps** (`ingest-start` is idempotent and reports resumed →
  **re-read THIS file (write-pages.md) before writing any page** — an interrupted session has lost the
  writing contracts; the seed scaffold in a fresh page shows the right shape but never overrides this file →
  per-window loop), and a one-line **writing discipline** reminder (concepts via resolve-concept, full-path
  wikilinks, 自测题干在块内首行/答案只进嵌套折叠 `> > [!success]-`, interpreter + `PYTHONUTF8=1`).
  **Do not dump full window logs into RESUME.** When the whole source is done,
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
- **Every new/edited page is `status: proposed` + `managed_by: pipeline`;** templates in `templates/` are suggested scaffolds — structure is purpose-driven, per-type frontmatter must be complete.
- **Concepts only via resolve-concept** (merge on hit, never create a duplicate); aliases only in the concept page's `aliases:` frontmatter.
- **Never hand-write derived files:** `concepts/_registry.yaml`, `index.generated.md` are rebuilt by the finishing CLI (`aliases.md` is retired — aliases live in concept frontmatter).
- **Non-text content: source images are LLM INPUT evidence, never published output (D-1).** For any
  `needs_vision` hard page (`pages.jsonl` `needs_vision_reason` = formula / formula-borderline / vector-figure /
  table / caption), **read the source image via `show-window` to understand it, then re-express it natively —
  never embed `![[assets/…]]` in a published page** (lint `source-image-embed` hard-blocks it, on the proposed
  batch too, so it can't slip through before promote):
  - **Formula page** (formula / formula-borderline): write full native KaTeX **and state the result, variable
    meanings, conditions and economic reading** — a model page must reach a usable answer (e.g. Cournot
    q\*=(a-c)/3b; Bertrand p=c under homogeneous goods / no capacity limit / tie-splitting), not stop at "take
    the FOC and solve the system".
  - **Table page** (table): convert to a Markdown table where it structures reliably (searchable / linkable);
    where it can't, describe the key comparison in prose — **do not embed the scan to pad the page**.
  - **Figure / flow page** (vector-figure / captioned figure): rebuild as mermaid or prose when you genuinely
    understand it; if you cannot reproduce it reliably, **do not fake it** — route it to the Review-Queue or
    mark it for a human figure.
  - Principle: **the LLM understands / organizes / explains; the source image is evidence it reads, not pixels
    it ships.** If a page can't be made clear without the scan, the writing isn't finished — that is never a
    license to embed.
- **Link restraint (avoid graph noise):** wikilink only real strong relations (depends-on / generalizes /
  contrasts / specializes); do not build a central "link-everything" hub. Summary pages (`sources/<src>.md`,
  `overview.md`) wikilink only a few core concepts.
- **Depth — 讲透优先，篇幅不设上限（多多益善）：** every concept carries at least one worked example or key
  derivation step, not just a definition; **hub concepts (e.g. 均衡 / 信息结构) get visibly more depth than
  supporting ones — weight by learning importance, do not write every page to the same length.** A model page
  reaches a usable result (assumptions → best-response → equilibrium solution → boundary conditions →
  interpretation). Vague summary pages are unfinished.
- **Learning loop & source-page integrity:** a self-test must ship with answers / hints / back-links —
  never questions with no resolution (`lint` prints a non-blocking `[warn]` for unanswered questions).
  **自测题的标准形状是收割契约，不是排版偏好** —— 零 LLM 收割器按固定位置取内容进派生层
  `quiz-index.generated.md`（只收题干+回链、不收答案），写错位置会把答案当题干收进复习索引：

  ```markdown
  > [!question] 自测
  > <题干写在块内首行，以问号结尾>
  > > [!success]- 参考答案
  > > <答案只放嵌套折叠块里，读者点开才可见>
  ```

  三条硬位置：**标题只放「自测」类短语（题干绝不写进 callout 标题）**；题干做块内首个正文行；
  答案只进嵌套折叠 `> > [!success]-`（或以 wikilink 指向解答小节），**绝不明文跟在题干后**——
  答案可见会同时毁掉「先猜再看」装置与 quiz 索引（`lint` 对题干疑似写进标题打非阻断 `[warn]`）。
- **装置预算（先于一切装置的硬约束）：** 正文默认**零装置**——散文必须先独立成立；除页尾自测与推导
  折叠（减重手段，不计预算）外，其余装置（案例解剖/定位段/具名命题/误区 warning/图）**一页至多启用
  一种**。负例：「参与者」这类基础概念页，零装置就是正确答案。**协议里提到某装置 ≠ 每页都要用**——
  按内容判断，宁缺毋滥。
- **阅读兴趣设计（偏好而非门禁——按内容自然使用，绝不为凑格式硬塞题型）：** ① 对**反直觉结论**优先
  "先猜再看"：先抛 `> [!question]` 让读者预测（"两家企业打价格战，要几家才能把价格压到成本？"），再在
  折叠答案或紧随的正文里揭示——预测式学习的记忆效果远强于直接阅读。② 高价值概念可用"找错题"：
  `> [!example]` 给一段故意埋**一处**错误的推导，`> [!success]-` 折叠揭示错在哪。③ `> [!warning]` 只
  服务**真实**的常见误区（高混淆概念对，如 完全信息 vs 完美信息），不做装饰性 callout——每页都有
  warning 会让读者疲劳；预判式质疑（"且慢——"式自问自答）作为误区 warning 的**变体**在认知冲突点使用，
  不单独成装置。④ 概念页结尾的"下一步"链接，只在存在**强因果/对比/递进**关系时写成悬念钩子
  （"古诺假设同时行动——如果一家能抢先呢？→ 斯塔克尔伯格模型"），普通相关链接保持平实；切忌每页
  "下集预告"式营销腔。⑤ 高价值 topic 页**可选**在结尾配一道跨概念综合题（含折叠解答路径），仅在确有
  把握写出高质量综合题时使用——空泛大题不如不出。
- **推导折叠（唯一让页面变轻的装置，鼓励使用）：** 结论与直觉留在正文，多步推导/证明折叠进
  `> [!abstract]- 完整推导`；**折叠块上方必须有可见的结论句**——读者不点开也能带走结果。复习时
  30 秒扫完结论层即走，严格性一寸不丢。**触发判断（防长期漏用）：** 写到连续 ≥3 步代数/证明变形
  还没停手时，先问"这段能不能折叠"——上面「装置预算」的零装置/宁缺毋滥框架**不适用于推导折叠**
  （它已明确不计预算、鼓励用）；理论型章节尤其要对照路由表检查，防止"默认克制"的惯性盖过
  "鼓励使用"的措辞，导致该折叠的长推导没折叠。
- **案例解剖（限 topic 页与少数枢纽概念）：** 一小段现实叙述，然后 `==高亮==` 关键要素并逐一 wikilink
  回概念页（"==两家平台同时公布补贴率== → [[…|静态博弈]]"）——训练"在真实场景里认出模型"，判例解剖 /
  代码走查的同款母题。
- **定位段（仅深层级/易迷路概念）：** 页首一行斜体交代坐标（承接谁 / 解决什么 / 通向哪），如均衡族
  第三层以下的概念（完美贝叶斯均衡）；基础概念不需要，**禁止全库套用同一句式**（八股化即失败）。
- **具名命题：** 库内**承重结论**升格为 `**命题（先发优势）**：一句话结论。`——短名 2–8 字、域内唯一、
  每本书 5–15 条量级（不是每页都有）；跨页引用写名字（"由命题（先发优势）…"），**v1 不做数字编号**
  （名字即锚点）。收尾 CLI 零 LLM 收割全库命题成 `propositions.generated.md`（结论句+回链）。 On `sources/<src>.md`, "精彩摘录" means **real quotations with a page/§ ref**; if you can only
  paraphrase, rename the section (e.g. 核心论点) rather than passing off a summary as a quote. `overview.md`
  learning routes state **what the reader can do after each leg** (e.g. "judge whether a problem is Cournot or
  Bertrand", "write a minimal model setup"), not just the reading order.
- **写作风格（高信息密度的学术散文，不是模板填空）：** 正文以连贯段落为主、少用机械的要点罗列；句式长短交错、
  节奏有起伏，用词精确多样，避免公式化、重复化、可预测的措辞；段落之间有清晰的逻辑递进，读起来像一篇打磨过的
  短文而非提纲。**正文结构由 `purpose.md` + 来源类型 + 读者需求自然决定——不再有强制的逐字小节标题（D-4）；
  `templates/*` 的小节只是建议性脚手架，可增删。** 表格只用在确需结构化对照处（comparison 的对比维度、topic
  的各来源贡献），其余以散文铺陈。不同概念/页面之间也要避免千篇一律的同构句式。
- **页面文件名用中文：** topic/comparison/synthesis 新建页的文件名取中文（与页面 `title` 一致），如 `topics/<中文主题名>.md`、`comparisons/<甲> vs <乙>.md`；概念页文件名由 `resolve-concept` 自动取中文 `canonical_name`，无需你指定。wikilink 因此是中文全路径：`[[domains/<domain>/concepts/<中文概念名>|<中文概念名>]]`。`canonical_id` 仍是稳定 ASCII（内部去重键，不影响侧栏/画布显示）。
- **图谱关系标注（Knowledge Graph v2.0，可选增强而非必填）：** 强 wikilink 关系处可在同段/同列表项末尾追加轻量注释 `<!-- graph: confidence=<extracted|inferred|ambiguous> relation=<depends_on|contrasts|related> evidence="<一句话、有来源依据的理由>" -->`，供确定性图谱构建赋权/分簇。**优先写 `confidence`，`relation` 可省略**；v2.0 关系白名单只有 `depends_on`/`contrasts`/`related`，未知值自动降级为 `related`/`ambiguous`（图谱 lint 记 warning）。注释只解释这条边，页面 `source_refs` 仍是证据权威；**不要给弱导航链接加注释**——无注释 wikilink 由图谱按结构信号（共引/同源/类型亲和）+ topic membership 自动赋权，不会因没标注而丢失。图谱构建全程零 LLM、只读页面已有轻量结构信息；图谱导航入口是 `knowledge-graph.generated.html`（力导向交互图，点击节点经 `obsidian://` 跳到对应 Obsidian 笔记；不再生成 Obsidian canvas）。
- Append to `log.md`: `## [YYYY-MM-DD] ingest | <src> | <created/updated pages>` (append-only).

## Lint hard rules cheat-sheet (violating any one blocks publish; recite before each page)

1. **Wikilinks use full vault-relative paths** (not Obsidian basenames): `[[domains/<domain>/concepts/<中文概念名>|<中文概念名>]]`（中文文件名全路径），and the target page must exist.
2. **Provenance lives in frontmatter `source_refs`, not in the body:** no inline footnote mechanism (`[^e1]`) and no bare `[E-...]` IDs — write immersive prose; the reader shouldn't sense the original document (D-5).
3. **No mandatory section titles (D-4).** Structure is purpose-driven. Instead the machine checks **per-type frontmatter completeness (`frontmatter-incomplete`)**: `source` needs `source_id/title/domain/format` (**not** `source_refs`); `topic`/`comparison`/`synthesis`/`overview` **must carry `source_refs`**; `concept` needs `canonical_id/canonical_name/domain`.
4. **Published body embeds NO source image (`source-image-embed`, blocks the proposed batch too):** re-express formulas as native KaTeX (with the result), tables as Markdown/prose, figures as mermaid/prose — the source image is read-only evidence. A `concept`/`topic`/`comparison` that is too short blocks as `content-too-short`; a lesson too short blocks as `L6-empty-lesson`.
5. **No body H1 that duplicates the filename (`title-duplicate-h1`):** Obsidian already shows the filename as the inline title — start the body with prose, not `# <same title>`.
6. **Concept dedup:** only via resolve-concept, merge on hit, never hand-build a duplicate (duplicate `canonical_id` blocks). Concepts resolve to their **home domain** (methodology → `research-method`, not the source's domain); aliases live only in the concept's `aliases:` frontmatter (`aliases.md` is retired).
7. **Ownership ≠ accounting (most-missed):** `source_refs` only decides **which source's lint owns a page**;
   it never substitutes for the write ledger. Every proposed `topic`/`comparison`/`synthesis`/`overview`
   page **must be in some window's `window-done --writes`** (kb-save pages ride the query-session
   `candidate_write_set.json` instead), or lint fail-closes it as `unaccounted-write`; a page with no
   frontmatter attribution at all is fail-closed as an orphan.
8. **No bare `|` in table-cell formulas:** use `\lvert S \rvert` for `|S|` (or escape `\|`, or move the formula out of the table) — a bare `|` is read as a column separator and breaks KaTeX (`formula-table-pipe` hard-block). **表格单元格内带别名的 wikilink 同理：必须转义为 `[[path\|alias]]`（Obsidian 标准写法，lint 认可）——裸 `|` 会撕碎表格列；误把转义"改回"裸竖线曾同时骗过 lint 并弄坏渲染。最稳妥：链接放表格外的散文，单元格保留纯文本。**
9. **Synthesis (phase E) mandatory:** after producing concepts you must update overview + build topic/comparison/synthesis as needed (into `--writes`), else lint `L7-synthesis-missing` blocks.
10. **Concept coverage (`concepts-uncovered`):** in a concept-heavy domain (≥6 concepts) **every concept must be收编 by some topic** (topic body full-path wikilink or `related_concepts[]`); any uncovered concept blocks publish (already-published pages are re-checked too).
11. **No unfilled placeholders (`placeholder-unfilled`):** a concept/topic/comparison/overview body must not still contain「（待 /ingest 填写）」—— half-finished pages block publish (already-published pages re-checked). （`lint` 另对 0 字节 / `*.png.md` 杂物页发 `stray` 软警告，不阻断。）

## Callouts (Obsidian rendering)

- **Callouts** (whitelist — unknown types hard-fail lint): pitfalls → `> [!warning]`, self-test →
  `> [!question]`, worked examples → `> [!example]`, key takeaways → `> [!tip]`. Whitelist:
  `note tip info important warning question example abstract summary quote success todo`. Not required
  to use callouts — just never invent a type outside the whitelist. **Nesting is one extra `>` per level
  (`> > [!type]`)** — a same-depth `[!type]` head inside an open block renders as literal text and
  hard-fails as `callout-nested-malformed`; an empty question stem hard-fails as `question-stem-empty`;
  inline math is `$…$` and display math `$$…$$` — LaTeX `\(…\)`/`\[…\]` do not render in Obsidian and
  hard-fail as `math-delimiter-nonobsidian`.
- **No source-image embeds (D-1):** published pages never contain `![[assets/<src>/pNNNN.png]]`. Source images
  are evidence you read via `show-window`; render knowledge natively (KaTeX / Markdown table / mermaid / prose).

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
- **Mermaid** (` ```mermaid `) is for an **LLM-authored conceptual diagram** you genuinely understand (e.g. a
  small concept-dependency graph); add `class NodeName internal-link;` to make a node a vault link. Under D-1 it
  is also the preferred way to **rebuild** a source figure/flow you understand — but if you can't reproduce it
  reliably, route to the Review-Queue rather than fake it (never embed the source scan).
