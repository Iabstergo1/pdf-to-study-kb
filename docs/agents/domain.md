# Domain Docs

This repository uses a single-context domain layout.

Current state:

- The implemented pipeline is still primarily the legacy section flow.
- The target architecture is the semantic unit flow described in `docs/semantic-pdf-to-obsidian-implementation-guide.md`.
- `README.md` and `CLAUDE.md` describe the target architecture, but implementation must be checked against the current code before making changes.

Domain documentation layout:

- Future glossary and domain language: `CONTEXT.md`
- Architectural decisions: `docs/adr/`
- Migration execution guide: `docs/semantic-pdf-to-obsidian-implementation-guide.md`

Agents should treat these terms carefully:

- `section`: the current legacy processing unit from `section-manifest.yaml`.
- `source-slice`: extracted PDF text for one legacy section, stored under `pipeline-workspace/staging/<section-id>/source-slice.md`.
- `semantic unit`: the target processing unit from `semantic-unit-plan.yaml`.
- `unit graph`: the target per-unit LangGraph workflow.
- `business SQLite`: target database for runs, events, costs, memory snapshots, and evidence ledger.
- `LangGraph checkpoint SQLite`: target database only for graph resume checkpoints.
- `Review-Queue`: target holding area for OCR failures, missing evidence, formula risk, or exhausted revise loops.
- `managed_by: pipeline`: frontmatter marker that allows generated Obsidian files to be overwritten by the pipeline.

Migration rules:

1. Preserve the legacy section flow until the semantic unit flow has end-to-end coverage.
2. Follow the implementation guide phase by phase.
3. Prefer deterministic foundations before LLM-dependent behavior.
4. Add focused tests before or alongside implementation.
5. Do not publish notes that fail evidence, formula, OCR, or review gates.
6. Do not overwrite human-written Obsidian notes unless they include `managed_by: pipeline`.
7. Keep compatibility wrappers when moving legacy modules into `scripts/legacy/`.
8. After a module has been fully reimplemented, verified, and accepted, the corresponding legacy files, wrappers, summaries, and scripts may be removed in a focused cleanup change.
9. Verification should include `python scripts/pipeline.py --help` and relevant `pytest` targets for the touched phase.
