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

- Concept writes still go through the `resolve_or_create_concept` protocol (merge on hit, never create a
  duplicate).
- Every written page is `status: proposed`; the finishing `lint` decides promotion, and a Q2 semantic
  judgement can block.
- `decision.md` must record: why it was saved / which pages were written / which evidence was cited /
  why no existing concept was polluted.
