---
name: kb-qa
description: Run QA / audit / coverage checks on the published knowledge base or pre-save candidates, producing a report and Review-Queue proposals. Use when the user says "run a KB QA / audit coverage / spot-check evidence / run the Q-chain / check for concept pollution". Semantic-health words (L4, contradiction, Q2 added value) belong to wiki-lint-semantic and must not be hijacked here.
---

# kb-qa — post-publish / pre-save QA report

Run broad QA over the published vault or a pre-save candidate: coverage, evidence spot-checks, formula
screenshot spot-checks, concept pollution, an ljg-qa-style Q-chain. Produces a report and Review-Queue
proposals only; it does not edit content pages.

## 1. Triggers / Non-triggers

- **Triggers:** "run a KB QA", "audit coverage", "spot-check evidence", "run the Q-chain", "check for concept pollution", "pre-save QA".
- **Non-triggers:** "semantic health check", "find contradictions", "does the comparison cover the key dimensions", "Q2 added value" belong to `wiki-lint-semantic`; handling an existing Review-Queue uses `kb-review`; read-only Q&A uses `kb-query`; a new source uses `ingest`. kb-qa and `wiki-lint-semantic` are **mutually exclusive** on triggers.

## 2. Inputs

- Scope: whole vault, a domain, a source, a query-session, or a proposed write candidate.
- Read: `wiki/index.generated.md`, `wiki/concepts/_registry.yaml`, and the relevant source/concept/topic/comparison/synthesis/lesson pages.
- May read: `pipeline-workspace/query-sessions/<run_id>/`, `wiki/Review-Queue/`, the deterministic `lint` result.

## 3. Outputs

- Published-source reviews use `content-review` below: one immutable SQLite review batch,
  reproducible Markdown + JSON under `pipeline-workspace/reports/kb-qa/<run_id>.*`, and
  individual `review_proposals` rows committed with the results. These are model results,
  not human acceptance. Legacy YAML history is read alongside them, never backfilled.
- `pipeline-workspace/reports/kb-qa/<run_id>.md`: the QA report.
- Unregistered/pre-save scopes may still write `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md`
  proposals; label them as report-only, without claiming registered page coverage.
- The report covers scope, the Q-chain, sampled items, findings, risk level, and the suggested follow-up skill (usually `kb-review`).
- For an actionable finding on an `adopted/published` legacy page, distinguish the later
  `revise-adopted` **write authorization** from content attribution: record official evidence for the
  finding, but do not add a book to `source_refs` or claim that the legacy page has acquired a source.

## 4. Dependencies

- CLI: `python scripts/pipeline.py status`; if needed `python scripts/pipeline.py lint --source <source_id>`.
  `revise-adopted` belongs to the confirmed-fix path in `kb-review`, never to this read-only QA run.
- Protocols: `docs/skill-runtime/schema.md`, `save-back-policy.md`, `concept-resolution.md`.
- Triggers mutually exclusive with `wiki-lint-semantic`: semantic-health words are not handled here.

## 5. Persisted artifacts

- `pipeline-workspace/reports/kb-qa/<run_id>.md`.
- `wiki/Review-Queue/kb-qa-<YYYY-MM-DD>.md` (only when an actionable finding exists).
- The spot-check list: sampled pages, evidence refs, and Q-chain conclusions recorded in the report.

## 6. CLI commands

```text
python scripts/pipeline.py status
python scripts/pipeline.py lint --source <source_id>
```

This skill never promotes, rolls back, or edits content pages; fixes go to `kb-review` or the matching write skill.
If the finding targets an adopted legacy page, the handoff must say that `revise-adopted` can authorize
the later edit but cannot validate the evidence or change `source_refs`.

## 7. Workflow

### Published-source registration

Review one explicit source per batch. Whole-vault/domain requests can produce separate
source batches; do not infer report ownership from today's shared-page `source_refs`.
The Web provides a `review` tool with `{action, value}`; other agents use the same CLI:
`python scripts/pipeline.py content-review --action <action> --payload '<JSON>'`
(or `--stdin`). No direct SQLite writes or edits to `content-review-ledger.yaml`.

1. `begin`: `{run_id: UUID, source, method: "full-page" | "sampled-claims", pages?: [vault-relative paths]}`.
   Full-page defaults to every currently published content page of this source. For
   sampling, explicitly select pages. The returned scope is frozen; its denominator
   counts knowledge pages, not original PDF pages. The Web supplies source/run/method.
2. `read`: `{run_id, path}` for **every** selected page at the time you review it. The
   tool returns exact content and a stored `read_id`, revision and time. Read the original
   text/images too. Ordinary file reads alone do not supply review receipts. A full-page
   check examines every substantive assertion; sampling must name only the assertions
   actually checked. Read receipts are evidence of tool delivery, not proof of comprehension.
3. `register`: submit the following object after every scoped page has been checked.
   In Web pass it as `value` to `review`. The CLI validates it, creates both report formats
   from this structured result, and atomically registers results and findings. Do not
   additionally create duplicate Markdown proposals for these findings.

```json
{
  "format": 1,
  "run_id": "<UUID from begin>",
  "summary": "Scope, method, limits and overall observations",
  "pages": [{
    "path": "domains/example/concepts/example.md",
    "read_id": "<receipt from read>",
    "outcome": "no-findings",
    "checks": [{
      "claim": "Exact assertion checked, or the explicit full-page assertion inventory",
      "evidence": [{"path": "pipeline-workspace/staging/<source>/source.md", "locator": "page/section/paragraph"}],
      "judgment": "Evidence-based conclusion and limits"
    }],
    "findings": []
  }]
}
```

Use `outcome: "findings"` with `findings: [{id: "F1", reason: "specific issue and evidence"}]`;
IDs must be unique within the batch. Use `inconclusive` when evidence cannot establish a
conclusion. Evidence references can also name source images or official HTTPS citations.
Missing evidence or incomplete work must not become `no-findings`. Retrying the identical
registration is safe; changing a registered result is refused.

4. After a confirmed repair, start a new batch with `parent_id` equal to the **original**
   batch ID and `pages` listing affected pages; review their new contents with new receipts.
   The Web recheck action supplies these fields. Old findings stay in history; neither
   recheck nor lint closes them automatically. Their disposition is a separate human action.
5. `status` shows batch results and changed versions. Never call `accept` or `close` on
   behalf of the user. They inspect reports/dispositions and confirm the batch in Web.
   Do not promise complete factual correctness. Reading all knowledge pages does not
   prove every statement in the original source was captured.

If the model exits without successful structured registration, the Web marks this source
review incomplete. Continue the same job to finish it. Do not re-hash repaired pages at
registration time: the receipt version is always the reviewed version.

| Sub-unit | Input | Output | Acceptance | Persisted | Failure stop |
|---|---|---|---|---|---|
| QA1 scope | request + index | scope and samples | does not hijack wiki-lint-semantic triggers | report draft | scope unclear |
| QA2 status background | source/status/lint | deterministic background | does not re-implement deterministic lint | report | lint already blocked |
| QA3 Q-chain | in-scope material | question→evidence→judgement→action | every Q has an evidence path | report | evidence thin |
| QA4 spot-check | page/formula/evidence samples | spot-check results | samples and conclusions traceable | report | sample missing |
| QA5 file findings | actionable findings | Review-Queue proposal | no content-page edits | kb-qa proposal | duplicate proposal |

### Evidence discipline (two rules that cost real rework when broken)

**A "not in the source" verdict requires an exhaustive search, and the search must be shown.** Before
writing that a term/number/mechanism is absent, search the source for the Chinese rendering, the English
name, the abbreviation, and case/hyphen variants — then **record every search term** in the finding. A
`single-language search` is the documented way this goes wrong: an audit once declared a bilingual term
"not mentioned" after searching only its English form, while the book used the Chinese form several
times. The cost is asymmetric — a missed finding leaves one clause unfixed, but a **false** finding
triggers a rewrite of a correct page, and every rewrite is a fresh chance to introduce an error.

**Normalize five PDF extraction deformations before you conclude a term is absent.** A page's word can be
verbatim in the source yet fail a naive string search because the extracted `source.md` mangled it. One
audit hit the first three at once (**20 false "not in source" candidates**),
then the full page-by-page read surfaced two more — each of which, if believed, would have rewritten a
correct page. Apply all five before the search, not after:

1. **Ligatures** — the PDF stores `ﬃ`/`ﬀ`/`ﬁ`/`ﬂ` (single code points, e.g. U+FB03) so `Efficiency` is
   `Eﬀiciency`, `Parameter-Efficient` is `Parameter-Eﬀicient`. Expand ligatures to their ASCII letters.
2. **Line-break hyphenation** — a word split across a line break keeps its hyphen: `Accurately` becomes
   `Ac-`⏎`curately`, `Prefixes` becomes `Pre-`⏎`fixes`. Rejoin `-`⏎ before searching.
3. **CJK–Latin spacing** — the extractor inserts a space between CJK and Latin runs, so `Encoder-only架构`
   is stored as `Encoder-only 架构`. Collapse all whitespace (this also covers the plain
   Chinese/English/abbreviation/case/hyphen variants above).
4. **Thousands separator** — the page writes `1000` but the source prints the grouped form `1,000`
   (ROME's "1000 条知识" is `1,000` in the source). Strip `,` from digit runs before matching numbers.
5. **Chinese numerals** — the page writes an Arabic digit but the source uses the Chinese numeral
   (T5's "连续 3 个" is `连续三个` in the source). Try the 一/二/三… form of any small integer.

Record the normalized form you searched, not just the raw term.

**A sampling PASS is not a clean bill for the page.** When you spot-check assertions rather than reading
the page whole, the verdict covers **only the assertions actually sampled** — say so in the report, and
later rounds must **never inherit** it as evidence that the page is clean. Three consecutive rounds on
one source overturned pages that an earlier sampling round had marked clean; only an integral,
per-assertion pass supports a page-level conclusion.

## 8. Failure stops / recovery

vault/index missing; scope unclear; the request is really a semantic-health check (switch to
`wiki-lint-semantic`); deterministic lint already failed with no stable QA scope; an evidence path is
missing; the user asks to edit content pages directly (switch to `kb-review` and wait for confirmation);
an adopted-page finding has no official evidence strong enough to support the proposed correction.
**Recovery:** the report + proposals are the durable output; re-run after the scope is clarified.

## 9. Acceptance criteria

- The QA report is written under `pipeline-workspace/reports/kb-qa/`.
- Every Q-chain item has a question, evidence, judgement, and a follow-up action.
- Actionable findings are written as Review-Queue proposals.
- Adopted legacy findings separate “evidence supports this correction” from “the pipeline authorizes a
  later write”; no new `source_refs` are inferred from a citation or from `revise-adopted`.
- No wiki content page was edited directly.
- None of `wiki-lint-semantic`'s exclusive triggers were handled.
