# Save-back admission gate

`/kb-save` must check this before writing. **At least one** must hold, and evidence must be present
(`evidence_refs` non-empty):

- it forms a cross-source synthesis, model comparison, learning path, common-pitfall note, or self-test;
- it resolves a recurring learning confusion and links to existing concepts/topics;
- it surfaces a duplicate concept, an alias, a cross-domain promotion candidate, or a page contradiction;
- the user explicitly asked to "save to the wiki / make a note / add to a synthesis".

## Do not save by default

- one-off fact lookups, ordinary explanations, source-less speculation, or restating an existing page;
- answers that would overwrite a `managed_by: human` page or exceed the write scope;
- content that cannot be linked to existing `source_refs` / `concept_refs`.

## Hard constraints

- Direct concept create/merge is unsupported in the current kb-save new-page-only path. Route the intended
  concept change to a Review-Queue proposal; write-capable `resolve_or_create_concept` / `resolve-concept`
  remains ingest-only until kb-save has an immutable overwrite baseline.
- `<run_id>` must name exactly one direct child of `pipeline-workspace/query-sessions/`; separators, drive
  prefixes, `.`/`..`, trailing-dot aliases, control characters, and path/link traversal are rejected.
- Every written page is `status: proposed`; the finishing gate is **session-scoped**:
  `lint --source kb-save --session <run_id>` re-checks the saved-mode session contract, then lints/promotes
  **only** the pages listed in that session's `candidate_write_set.json`. Historical/unsaved/other sessions
  never account; ingest lints never read session ledgers. A Q2 semantic judgement can still block.
- **Every written page carries `save_session: <run_id>` in frontmatter and is listed in
  `candidate_write_set.json`** — the ledger records paths, the marker is the content identity; a missing
  path / non-proposed page / mismatched marker fail-closes the whole session (`session-candidate-missing` /
  `session-identity-mismatch`, no partial publish). kb-save batches do not carry ingest phase-E duties
  (overview rewrite / L7 / topics-missing); vault-level invariants (A2 coverage, render-safety preflight)
  still apply.
- **Current direct-write boundary is new-page-only.** Before creating each page, run
  `check-write --source kb-save --session <run_id> --path <path>`. It requires the path in this session's
  candidate set and the kb-save allowlist, verifies current disk truth first (an existing target is denied
  even if an old authorization entry exists), then records
  `{path, mode: "new"}` in `write_authorizations.json`. Existing targets fail closed to a Review-Queue
  proposal; do not fabricate a source/workorder/window ledger or bypass the guard.
- Candidate and authorization files use a strict contract. `candidate_write_set.json` is a non-empty,
  duplicate-free list of canonical vault-relative strings (no `./`, `//`, `..`, backslash aliases, control
  characters, or non-string values). Every authorization is an exact `{path, mode}` object with
  `mode: "new"`; its path is canonical and unique. Saved-mode Q1 requires exact candidate↔authorization
  path equality, and lint rechecks that equality plus the allowlist.
- `write_authorizations.json` is a local cooperative-workflow ledger, not a cryptographic attestation or
  defense against a person manually editing session files. The supported workflow therefore treats a
  missing/malformed/mismatched ledger as fail-closed and permits only `check-write` to generate it; never
  hand-write or retroactively backfill authorization entries.
- `decision.md` must record: why it was saved / which pages were written / which evidence was cited /
  why no existing concept was polluted.

## Legacy session recovery

An older saved session without `write_authorizations.json` fails closed by design. If every candidate
target is still absent, rerun `check-write --source kb-save --session <run_id> --path <path>` once for each
candidate, then create the proposed pages and run `check-session --saved`. If any target already exists,
do not manufacture a retroactive authorization: route the intended change to Review-Queue/`kb-review`, or
start a fresh session with an unused new path.
