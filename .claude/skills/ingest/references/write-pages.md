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
- Append to `log.md`: `## [YYYY-MM-DD] ingest | <src> | <created/updated pages>` (append-only).

## Lint hard rules cheat-sheet (violating any one blocks publish; recite before each page)

1. **Wikilinks use full vault-relative paths** (not Obsidian basenames): `[[domains/game-theory/concepts/cournot-model|Cournot model]]`, and the target page must exist.
2. **Every `[^e1]` reference has a `[^e1]:` definition line**; no bare `[E-...]` IDs in the body.
3. **Required section titles match verbatim** (concept 6 / topic 3 / comparison 4 / synthesis 4 / source 6 / overview 3).
4. **A lesson containing `$$` must embed a real source-page PNG** `![[assets/<src>/pXXXX.png]]`; a lesson is not too short after placeholders are removed.
5. **Concept dedup:** only via resolve-concept, merge on hit, never hand-build a duplicate (duplicate `canonical_id` blocks).
6. **Ownership (most-missed):** a page with no `source:` frontmatter (`topics/**`/`comparisons/**`/`synthesis/**`/`overview.md`) **must be in some window's `window-done --writes`**, or it is fail-closed as an orphan.
7. **No bare `|` in table-cell formulas:** use `\lvert S \rvert` for `|S|` (or escape `\|`, or move the formula out of the table) — a bare `|` is read as a column separator and breaks KaTeX (`formula-table-pipe` hard-block).
8. **Synthesis (phase E) mandatory:** after producing concepts you must update overview + build topic/comparison/synthesis as needed (into `--writes`), else lint `L7-synthesis-missing` blocks.

## Callouts & figure width (Obsidian rendering)

- **Callouts** (whitelist — unknown types hard-fail lint): pitfalls → `> [!warning]`, self-test →
  `> [!question]`, worked examples → `> [!example]`, key takeaways → `> [!tip]`. Whitelist:
  `note tip info important warning question example abstract summary quote success todo`. Not required
  to use callouts — just never invent a type outside the whitelist.
- **Figure width**: when embedding a hard-page image, size it with `![[assets/<src>/pNNNN.png|640]]`
  (formula pages narrower, full-page figures wider) so it does not overflow the reading column.
