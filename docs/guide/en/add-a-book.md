# Add a book

## Upload and preparation

Choose PDF, DOCX, PPTX or Markdown, then enter a domain and source ID. Domains use lowercase words separated by hyphens, such as research-method. A source ID must be unique in the vault. Uploads go to books/<domain>/input/.

Preparation calls the existing Python CLI for registration, profiling, conversion, structural review, windows and a work order. It uses no model quota. MinerU PDF review may take time and require local model files.

## Extraction disagreements

Use an image-tested model to examine differences between parsers. Pages it cannot decide are shown to you. Keep the original image for the writer, or explain why the difference can be ignored. One AI ingest confirmation continues these steps through writing and publication. Only human decisions pause the flow; resolve them and choose Resume ingest.

## AI ingest and recovery

Before starting, the app shows which provider will receive the source. Writing proceeds through reading windows; command activity, usage and logs are available. Process one source at a time and avoid simultaneous vault edits from another Codex/Claude session.

Start or Resume ingest runs through publication without repeated clicks. `[continue]` marks an intermediate reply followed by continued work in the current session. `[handoff]` marks a fresh session after window accounting, an actual U7 digest change and resume validation; it remains the same job. The final window is followed by synthesis and publication. Ordinary tool errors are returned to the model for correction; three replies without completed-window or stage progress stop the attempt. Quota, inability to read images, failed pipeline state and publication-check failures still stop or pause; network retries are limited as described in [Troubleshooting](troubleshooting.md).

Use Resume ingest after a stop. After a service restart, the CLI resume packet determines unfinished windows. For lint failures, inspect the report and choose Repair from report. Rolled-back edits must be reapplied, not fixed by merely rerunning lint.

## After publication

Structural checks promote pages to published and refresh reading data. Published, content review pending does not replace independent fidelity review. Keep the source files for checking pages and formulas.


Details expand under the selected source. The fixed header task button stays available across pages. Collapsing logs does not stop the task; Stop task has a separate confirmation. Completed windows count only read and recorded windows, excluding initialization and unfinished work.

The source learning goal applies only to that source, stored in its staging/learning-goal.md. It is never appended to global wiki/_meta/purpose.md.
