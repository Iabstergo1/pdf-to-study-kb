# Concept resolution protocol (resolve_or_create_concept)

The **single entry point** for every concept create/update. A unique exact name, alias or explicit
`canonical_id` match merges into the existing page (**never create** a duplicate); a miss creates a
skeleton only if both its derived ID and file path are unused. The
**`canonical_id`** is the ASCII-stable dedup key `concept.<domain>.<slug>`; the page **filename/path uses
the (Chinese) `canonical_name`** — `domains/<domain>/concepts/<canonical_name>.md` — **decoupled** from the
canonical_id (the id stays ASCII even when the filename is CJK; do not assume the path mirrors the id).

## Usage (shared by /ingest and /kb-save)

```
python scripts/pipeline.py resolve-concept --mention "<mention in the body>" --domain <domain> \
    [--alias "<alias>" ...] [--ref-source <source_id> --ref-sections "5.2,12.2"]
```

- `[merged] <canonical_id> -> <page path>`: during ingest, the command itself runs the write guard and saves
  the existing page's first baseline **before** merging aliases/source refs. Run `check-write` again before
  editing the body; it is idempotent and reuses that verified baseline. Do not run `snapshot-page` after editing.
- `[created] <canonical_id> -> <page path>`: during ingest the target path is checked against the work order
  before creating any file; a denied domain leaves no skeleton. The skeleton page exists (`status: proposed`); fill the body
  (**purpose-driven structure — no mandatory section titles, D-4**), reaching a usable depth.
- Each call **rescans the concept pages live** to rebuild the in-memory registry, so concepts created
  earlier in the session are immediately matchable by later resolves.
- Both `--mention` and every `--alias` participate in matching. Each term prefers the requested home
  domain over shared; all terms' results must identify at most one concept. Conflicting hits stop before
  snapshots or edits and list the terms and matching page paths. Explicit IDs must belong to that home
  domain or shared, and are not added to aliases. The CLI resolves once, snapshots the selected existing
  page, then merges that same page.
- A derived-ID collision alone is **not** a semantic match. Punctuation can collapse distinct names
  (`C`, `C++`, `C#`) to one slug. Creation refuses the collision without leaving a second page and prints
  the existing page's name and aliases. For a true synonym, use a known existing name/alias and retry;
  for a distinct concept, choose a distinct canonical name. ASCII names take precedence over aliases,
  so `--mention "C++" --alias CPP` still collides with C; `--mention CPP --alias "C++"` distinguishes it.
  Do not invent a different concept merely to bypass a collision. Use the returned path for body edits,
  wikilinks and the window write set; do not infer it from the mention.
- Homonyms across domains (econ `utility` vs cs `utility`) are kept apart by the `concept.<domain>.<slug>`
  namespace and never merge.
- **Application-context routing:** mathematical, statistical and optimization support concepts use the
  source domain for the application actually explained in the source (cosine similarity in LLM retrieval
  → `llm`). Do not create an unapproved statistics/optimization domain by following a disciplinary label.
  Explicitly authorized cross-domain concepts still use their home domain: 研究问题 / 学术论文结构 /
  组合创新 → `research-method` when the actual work order permits it. The work order pre-authorizes writes
  (`workorder.CROSS_DOMAIN_HOME_DOMAINS`, currently `research-method`) at `domains/<home>/concepts/**` **only**
  (never `domains/**`, never their lessons/topics). Read its actual `cross_domain_concept_scope` and
  `write_scope` before resolving. A routing mistake may be corrected from the source's application meaning;
  an explicit denial is never permission to change the work order or rename a concept just to get through.
- Cross-domain promotion (domain → shared) requires human sign-off via the Review-Queue; the command
  never promotes on its own.
- Aliases are written only to the concept page's `aliases:` frontmatter; **`aliases.md` is retired (B2)** —
  Obsidian reads frontmatter `aliases:` natively for search/autocomplete.
