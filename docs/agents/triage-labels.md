# Triage Labels

Use the default triage label vocabulary.

| Role | Label | Meaning |
|---|---|---|
| Needs evaluation | `needs-triage` | Maintainer needs to evaluate scope, priority, or reproducibility. |
| Waiting on reporter | `needs-info` | More user or maintainer context is required before implementation. |
| Ready for agent | `ready-for-agent` | Fully specified; an agent can implement it without additional human context. |
| Ready for human | `ready-for-human` | Requires human judgment, product choice, or manual review before automation. |
| Will not fix | `wontfix` | The issue will not be actioned. |

For migration work, an issue is `ready-for-agent` only when it includes:

- The migration phase or exact slice
- Target files
- Expected behavior before and after
- Tests or commands that must pass
- Compatibility expectations for the legacy section flow

Avoid creating duplicate labels with slightly different names.
