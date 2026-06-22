# Page types & frontmatter rules (pointer doc; load per command)

- **Six page templates (write-page format contract):** `templates/source.md` / `lesson.md` /
  `concept.md` / `topic.md` / `comparison.md` / `synthesis.md`. All frontmatter carries Dataview fields.
- **Two-phase publish:** any page a command writes is `status: proposed`; only the finishing gate
  promotes it to `published` and folds it into `index.generated.md`. `managed_by: pipeline` is the
  precondition for overwrite protection (a human-owned page is never overwritten).
- **Required sections:** authoritative in `scripts/page_rules.py::REQUIRED_SECTIONS` (concept 6, topic 3,
  comparison 4, synthesis 4, source 6). `lesson` has no mandatory sections but must be clean prose (no
  bare E-IDs; footnote ref/def paired — `find_bare_evidence_ids` / `missing_footnote_defs`).
- **A concept page's frontmatter is the single source of truth:** `canonical_id` / `canonical_name` /
  `aliases` / `scope` / `domain` / `source_refs` / `page_path`. Derived files
  (`_registry.yaml` / `aliases.md` / `index.generated.md`) are rebuilt by the finishing CLI and must
  never be hand-written.
