# Troubleshooting

## Startup and Python

Reconnect using the launch URL printed in the terminal. Do not mix localhost and 127.0.0.1 credentials. Close an earlier service if port 8765 is occupied.

If modules are missing, check that the selected Python belongs to study-kb. Paste the full executable path in settings and run `python -m pip install -r requirements.txt` in that environment, then rerun Environment. Missing fsrs affects learning summaries and review scheduling; the environment need not be recreated.

## Models and images

Sign in again after expired authorization. For API errors, check the provider and protocol. A catalogue entry does not guarantee account access; the image test performs a real call.

Any mismatch in the three ordinary-font rounds prevents ingest with that model. Read the task log; declaring image support does not bypass the test.

## Ingest failures and locks

Read the task log and source report. Use Repair from report for lint failures and Resume ingest after network recovery. If the resume packet refuses continuation, retain the error and diagnose with maintenance tools or pipeline-doctor. Never delete the SQLite file.

For ChatGPT / OpenAI Responses, recognized transient connection failures before response headers retry the identical model request twice at most, after 1 and 2 seconds, without rerunning completed tools. Continued failure stops the job. The `[network]` log records only the destination host, error codes, elapsed time and attempt, never tokens or request bodies. Retries may consume additional quota. HTTP errors, certificate errors and unknown causes are not automatically retried; quota exhaustion still requires manual resume. Exhausted header retries do not trigger another round of session retries.

`Request timed out` means the request exceeded its waiting limit. Each attempt waits at most 10 minutes for response headers by default; expiry counts toward the same two-retry limit. Three consecutive timeouts therefore stop after about 30 minutes. Stop immediately cancels the current request or retry wait. A timeout alone cannot distinguish slow inference from network or upstream delays, and does not require signing in or repeating the image test.

`OAuth refresh failed ... fetch failed` indicates a transport failure during renewal, not proof that authorization was revoked. Renewal and account-model listing also retry twice at most, with independent 15-second attempt limits. HTTP rejection stops immediately and preserves credentials without signing in again; OAuth error response bodies are not logged. Cancellation stops waiting and further attempts; an already sent renewal may still save returned rotated tokens through the SDK.

For `terminated` or socket failures while reading a response, `[network] ... stream_code=...` records available underlying codes, or UNKNOWN. Ingest continues in the same session after 1 and 2 seconds, at most twice per window until validated U7 handoff. Pi omits the incomplete failed response from subsequent requests and never executes its tools. `[network-recovery]` means recovery is underway without another click. Authentication, quota, context-limit and pipeline failures do not enter transport recovery. After two unsuccessful recoveries, manual resume is required. Recovery may consume additional quota.

After window accounting, an actual digest change and the resume check, the entire tool batch must finish before handoff. Updating the digest just before accounting in that same batch also works; rewriting unchanged text does not count. `[handoff]` marks a fresh session continuing the same job without another confirmation. It rereads the rules, chapter map and digest instead of carrying old images into later requests; necessary evidence can still be reread. After the final window, synthesis and publication continue; `DONE` only means the windows are finished. The finish session receives separate inventories of source-attributed pages and current-round accounted paths, covering more than the final window. This limits accumulated history, but cannot guarantee freedom from network or upstream timeouts.

Ordinary tool errors, such as a wrong filename or unmatched edit text, are returned to the model for correction without automatically replaying successful tools. Three replies without completed-window or stage progress stop the attempt. Unrecoverable or retry-exhausted model-request failures, inability to read images, failed pipeline state and lint failures still stop; retries cannot conceal them indefinitely.

Use View execution log in source details to reopen a finished task; its failure message also appears there. Failed means this attempt has stopped. Window progress counts completed windows, excluding rule reading and digest initialization.

If the first attempt stops before creating a digest, choose Resume ingest. Digest initialization can restart only when no window has been read or processed and writable pages still match the workorder snapshot. A missing digest after activity still stops recovery; history is never invented.

If the only problem is a RESUME block lagging behind completed windows, the web runtime asks the current model to reconcile the ledger, reread necessary evidence and update the digest before passing the original resume check. Window advancement and knowledge-page writes remain blocked during reconciliation. Two unsuccessful turns stop the attempt; missing workorders and other contradictions still require diagnosis.

Preview maintenance changes, then type the target name to confirm. A live lock cannot be forcibly released. Ensure other sessions stopped, wait for expiry, then use unlock. Stopping a job terminates its subprocess tree, including MinerU children.

## Reading data

Refresh Explorer if published material is missing. JSON and offline bodies share a renderer. For formula issues, retain source Markdown, page path and the actual browser rendering. Error counts alone cannot prove lossless content.
