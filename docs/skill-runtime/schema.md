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
- **A concept page's frontmatter is the single source of truth:** `canonical_id` / `canonical_name` /
  `aliases` / `scope` / `domain` / `source_refs` / `page_path`. Derived files
  (`_registry.yaml` / `index.generated.md`) are rebuilt by the finishing CLI and must never be hand-written.
  **`aliases.md` is retired (B2)** — English aliases live only in the concept's `aliases:` frontmatter.
