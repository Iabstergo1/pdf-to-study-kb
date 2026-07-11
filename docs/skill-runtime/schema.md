# Page types & frontmatter rules (pointer doc; load per command)

- **Runtime templates + purpose-driven page bodies:** only two templates are read at runtime —
  `templates/concept.md` (the `resolve-concept` new-page scaffold) and `templates/overview.md`
  (the `init-vault` seed). Page types `source` / `lesson` / `topic` / `comparison` / `synthesis`
  have **no fixed template**; their bodies are purpose-driven (`wiki/_meta/purpose.md` + source type +
  reader need). Frontmatter rules below still apply to every type (Dataview fields).
- **Two-phase publish:** any page a command writes is `status: proposed`; only the finishing gate
  promotes it to `published` and folds it into `index.generated.md`. `managed_by: pipeline` is the
  precondition for overwrite protection (a human-owned page is never overwritten).
- **No mandatory section titles (D-4):** `REQUIRED_SECTIONS` is empty; body structure is purpose-driven
  (`wiki/_meta/purpose.md` + source type + reader need), and `templates/*` sections are suggested scaffolds,
  not enforced skeletons. All pages must be clean prose (no bare E-IDs; no inline footnote mechanism — D-5;
  provenance lives in frontmatter). **No body H1 that duplicates the filename (`title-duplicate-h1`); no
  source-image embed in a published body (`source-image-embed`); `concept`/`topic`/`comparison` not too short
  (`content-too-short`).**
- **Per-type frontmatter completeness (`scripts/page_rules.py::REQUIRED_FRONTMATTER`, rule `frontmatter-incomplete`):**
  `source` needs `source_id`/`title`/`domain`/`format` (**not** `source_refs` — it *is* the source);
  `topic`/`comparison`/`synthesis`/`overview` **must carry `source_refs`** (derived-page provenance);
  `concept` needs `canonical_id`/`canonical_name`/`domain`; `lesson` needs the common trio (attribution via
  `source`/window write_set).
- **Ownership ≠ accounting:** `source_refs` only decides which source's lint owns a page. The write ledger
  is separate — a proposed `topic`/`comparison`/`synthesis`/`overview` must be in a window's `--writes`
  (ingest) or the session's `candidate_write_set.json` (kb-save), else `unaccounted-write` blocks.
  **kb-save pages additionally carry `save_session: <run_id>`** (content identity — the candidate set only
  records paths; this marker is what stops one session publishing content later rewritten at the same path
  by another session; verified by `lint --source kb-save --session <run_id>`).
- **Render safety is frontmatter-independent and re-checked vault-wide:** callout whitelist + nesting
  (`> > [!type]`), `$…$`/`$$…$$` math delimiters, non-empty question stems — same scan runs on the proposed
  batch and as a transaction-isolated published preflight (`vault-lint` standalone).
- **A concept page's frontmatter is the single source of truth:** `canonical_id` / `canonical_name` /
  `aliases` / `scope` / `domain` / `source_refs` / `page_path`. Derived files
  (`_registry.yaml` / `index.generated.md`) are rebuilt by the finishing CLI and must never be hand-written.
  **`aliases.md` is retired (B2)** — English aliases live only in the concept's `aliases:` frontmatter.
