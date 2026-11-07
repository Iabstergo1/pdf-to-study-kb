# Glossary

## Sources and pages

- Source: a book, article or document with a unique source_id.
- Domain: the home of a concept; a source may refer to concepts in other domains.
- Concept page: knowledge organized around a concept, not a translated chapter.
- Synthesis layer: topic, comparison and synthesis pages connecting concepts.

## Workflow state

- Reading window: a deterministic CLI reading range, not an AI-designed chapter or writing unit.
- Work order: this round's allowed paths, snapshots and progress information.
- proposed: content awaiting checks.
- published: structural checks passed; independent fidelity review is not implied.
- lint: deterministic checks of links, provenance, write accounting and rendering safety.

## Reading options

- Vault: the wiki folder shared by the browser and Obsidian.
- Reading data: regenerable JSON and HTML exported from published pages.
- Arbitration: resolving parser disagreements by keeping images, ignoring them or requesting human judgment.


**AI ingest task**: one user confirmation runs the remaining evidence review, preparation, reading/writing and publication checks. A progress reply is not completion. Human decisions and errors stop the task.

**Preparation evidence review**: AI resolves parser differences to assemble evidence for writing; part of preparation, not knowledge-page writing.

**Completed window**: a window whose source has been read and output recorded, excluding digest initialization and unfinished work.

## Content review and acceptance

- Model review record: the scope, revisions, conclusions and findings of a model's review. Completion records the results automatically, without implying user acceptance.
- Model review completed: checks of the selected scope have finished; findings may remain open, and correctness is not guaranteed.
- User accepted: after the batch's relevant findings have been handled, the user accepts the review results for an explicit scope and revisions as a batch; rereading every page is not required.
