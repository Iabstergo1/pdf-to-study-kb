# Privacy and safety

## Local files

Sources live in books/, knowledge pages in wiki/, and working records in pipeline-workspace/. The service binds to 127.0.0.1 and checks Host, Origin and a random connection token. Do not expose it publicly.

Keys and authorization files live in the user configuration directory: %APPDATA%/study-kb on Windows, ~/Library/Application Support/study-kb on macOS, and ~/.config/study-kb on Linux. Never commit that directory.

## Material sent to models

Model calls occur when you explicitly start an image test, arbitration, AI ingest or an assistant workflow. Text and images read through the file tools go to the selected provider. Its service terms govern its handling of data. Deterministic preparation, maintenance commands and export do not call an LLM.

## Writes and recovery

The AI has no arbitrary shell, and file tools cannot read the credentials directory. Ingest writes are limited to wiki and the current source's digest/decisions; existing-page edits pass CLI check-write, and lint decides publication. Other skills are restricted to their query records, reports, review items or authorized candidates. Report-only skills cannot edit knowledge pages. Save publication, reviewed revisions and skill adoption show the proposed changes and wait for your confirmation. Skill evolution edits only the selected skill in an isolated worktree.

These restrictions are not an operating-system sandbox. Retain original sources and Git history, and avoid concurrent editing sessions. Source removal and state resets use maintenance previews followed by explicit confirmation.
