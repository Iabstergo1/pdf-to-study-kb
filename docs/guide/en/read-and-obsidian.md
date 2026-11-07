# Reading and Obsidian

## Web reading

The home page offers continue reading, planned review and free practice. Explorer groups overview, cross-domain pages and domains. Search titles, aliases and bodies or filter by domain, type and source. Ctrl+K focuses search; arrows select and Enter opens. Breadcrumbs open filtered lists, and Back restores the previous reading position.

Hover over a knowledge link to preview it; move into the preview, open the full page or pin it to the reference panel. Moving away or Esc dismisses it. Wide screens support draggable dividers; panels can collapse. Focus mode (Z), font size and line height improve reading. Narrow screens use dismissible drawers. The task button reopens logs; backdrop clicks and Esc collapse them without stopping execution.

Markdown `==highlights==` render as highlights without changing the original Markdown, formulas or citations. Compare original source images and extracted text in the reference panel. Writing-window associations are explicitly identified and are not sentence-level evidence; select a source page manually when precise mapping is absent. Graph nodes open reference previews, and returning to the graph keeps its position.

Select text to highlight, annotate or flag a question with an optional original page. Personal annotations and review proposals are stored separately; neither edits knowledge text automatically. Changed or ambiguous quotes require explicit relinking.

## Web learning and backup

Learning includes planned review, free practice filtered by source/domain/page, settings and history. Questions come from published pages. Reveal an answer and follow its knowledge link; questions without answers cannot be rated.

Enroll selected sources first. Defaults are 20 distinct questions per day, including at most 5 new questions. Due cards take priority, and the cap never postpones their actual due dates. Again, Hard, Good and Easy feed FSRS; the latest rating can be undone. Short-interval follow-ups become available again when due without consuming another distinct-question slot. Free practice leaves scheduling unchanged unless selected. Sessions can pause and resume. Changed answers require rechecking; substantially rewritten stems become new cards while old history remains.

Records live in the existing local SQLite file and survive browser changes. Export JSON or preview an import, then keep local records or replace conflicts. Replacing a conflicting card also replaces its review history, with affected counts shown first. History can be downloaded as Markdown. Browser storage caches display preferences and reading positions, not the sole copy of review records.

## Reports and review

Published means publication checks passed. Content review separately shows this source's historical coverage, scope, outcome and revision. Report associations use explicitly recorded sources or complete source IDs in legacy filenames. A shared page gaining a new source does not transfer its old report to that source. Reviews from other scopes remain separate; unassigned reports remain available in all reports and page history. Records without a revision explain that later changes cannot be determined, rather than implying a content error. Missing report files do not erase history.

Choose Content review in a source's details, select sampled claims or full-page review, enter requirements and start `kb-qa`. The batch freezes its knowledge-page scope. A dedicated read tool supplies each page and its revision at review time; the model compares original text/images and submits structured results. The CLI commits results, readable reports and individual findings together in the existing SQLite, then produces `pipeline-workspace/reports/kb-qa/<batch ID>.md` and `.json`. No manual ledger synchronization is needed. Legacy YAML history remains unchanged, without invented historical revisions. Reading reports or running `review-coverage` does not start a model review.

Reports & review displays each batch's scope, method, page outcomes, pending findings and human acceptance. The denominator counts source-linked knowledge pages at the start, not PDF pages; sampling covers only listed claims. Incomplete or inconclusive reviews, changed pages and relevant open findings prevent batch acceptance. After confirmed repairs, Recheck affected pages reads fresh revisions and appends results without overwriting original findings. Once findings and rechecks are handled, read the batch results and confirm acceptance; rereading every page is unnecessary. Later shared-page changes retain acceptance history and mark affected pages for recheck.

The queue contains ingest checks, reader questions and registered model findings. Enter a reason to close, reject or defer an item; closing requires explicit verification and deferring leaves it open. Original findings and subsequent decisions remain separate. For old placeholder, topic-membership and write-accounting issues, use Recheck structural findings, inspect evidence, select items and confirm closure. Closure checks the evidence again. Structural checks do not certify content, and publication never automatically closes proposals.

Choose Analyse items with a model for `kb-review`. Content repairs require confirmation and the appropriate work-order protections. `reopen` restarts a source's incremental workflow; it does not authorize unrestricted edits to arbitrary pages. Review registration, dispositions and acceptance do not edit PDFs or knowledge bodies.

Usage separates uncached input, cache reads and output for one execution or all saved executions of a source. The input hit-rate denominator is uncached input plus cache reads. Failed requests may omit usage. No cost is invented without applicable pricing, and subscription usage is not an API invoice.

## Obsidian

Open this project's wiki folder as an Obsidian vault. Web links locate the same knowledge pages. Rendering and personal annotations never rewrite source material or page bodies. After an Obsidian edit, the reader prompts you to refresh its generated data.

## Offline site and Anki

Explorer retains HTML and Anki TSV exports. Export before downloading; image-enabled offline sites require copying the entire directory. Offline HTML is a reading snapshot; personal learning, scheduling and annotations require the local Web service. Anki remains optional, with no automatic synchronization between its schedule and Web learning records.
