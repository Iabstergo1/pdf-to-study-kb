# Concept resolution protocol (resolve_or_create_concept)

The **single entry point** for every concept create/update. On a `canonical_id` hit it merges into the
existing page (**never create** a duplicate); on a miss it creates a skeleton page and registers it. The
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
- `[created] <canonical_id> -> <page path>`: the skeleton page exists (`status: proposed`); fill the body
  (**purpose-driven structure — no mandatory section titles, D-4**), reaching a usable depth.
- Each call **rescans the concept pages live** to rebuild the in-memory registry, so concepts created
  earlier in the session are immediately matchable by later resolves.
- Homonyms across domains (econ `utility` vs cs `utility`) are kept apart by the `concept.<domain>.<slug>`
  namespace and never merge.
- **Home-domain routing (D-3): `--domain` is the concept's *home* domain — where it actually belongs — not
  the source book's domain.** A game-theory source discussing 研究问题 / 学术论文结构 / 组合创新 (research
  methodology) resolves those into `research-method`; statistics → `statistics`, optimization → `optimization`,
  etc. The work order pre-authorizes cross-domain concept writes for the managed home domains
  (`workorder.CROSS_DOMAIN_HOME_DOMAINS`, currently `research-method`) at `domains/<home>/concepts/**` **only**
  (never `domains/**`, never their lessons/topics); an unlisted home domain must be added to that audited
  allowlist before `check-write` will permit it.
- Cross-domain promotion (domain → shared) requires human sign-off via the Review-Queue; the command
  never promotes on its own.
- Aliases are written only to the concept page's `aliases:` frontmatter; **`aliases.md` is retired (B2)** —
  Obsidian reads frontmatter `aliases:` natively for search/autocomplete.
