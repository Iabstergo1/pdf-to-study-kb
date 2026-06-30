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

- `[merged] <canonical_id> -> <page path>`: edit that page to fill/extend the body (run check-write +
  snapshot-page first).
- `[created] <canonical_id> -> <page path>`: the skeleton page exists (`status: proposed`); fill the
  required sections + a self-test.
- Each call **rescans the concept pages live** to rebuild the in-memory registry, so concepts created
  earlier in the session are immediately matchable by later resolves.
- Homonyms across domains (econ `utility` vs cs `utility`) are kept apart by the `concept.<domain>.<slug>`
  namespace and never merge.
- Cross-domain promotion (domain → shared) requires human sign-off via the Review-Queue; the command
  never promotes on its own.
- Aliases are written only to the concept page's `aliases:` frontmatter; `aliases.md` is a derived view
  and must not be hand-written.
