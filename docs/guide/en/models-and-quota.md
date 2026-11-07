# Models and quota

## Connection options

- ChatGPT subscription: sign in through the authorization page. Claude Pro/Max subscription login is not offered.
- API key: select a provider and save its key. Billing and limits are determined by the provider.
- Custom provider: enter an ID, name, protocol, endpoint and model IDs; multiple configurations can be saved. Discovered IDs still require an image capability declaration and an actual test.

OpenAI API keys and ChatGPT subscriptions use separate configurations. An image-support checkbox is only a declaration.

The ChatGPT picker uses `GET https://api.openai.com/v1/models` for the signed-in account, retaining entries intended for display and their server order. Models unsupported by the installed runtime remain visible but disabled. A failed listing shows an error instead of substituting the built-in API catalogue. API-key providers still show their runtime catalogue; successful access depends on the account. Reference: [OpenAI models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

Choose a supported reasoning effort, then use “Use this model” to save it. Each provider/model retains its setting across restarts, and execution records show the actual effort. Non-reasoning models offer only `off`. Pi SDK creates this app's agent session; it does not launch Codex or Claude Code. Using the same model does not make prompts, tools, context or output identical.

## Image test

The app uses the existing PyMuPDF dependency to render random digits in an ordinary font. The model must see the image through the actual read tool and match all three rounds exactly. Changing the model, provider configuration or login requires a new test; automatic token refresh for the same ChatGPT account keeps its verification valid. Tests consume model quota.

A pass confirms the image path works, not the quality of a whole book. The CLI cannot determine whether a model understood all the evidence.

## Verified runs and limits

2026-10-23: pi 1.0.3, ChatGPT OAuth, openai/gpt-6.1-sol. Ordinary-font random image test 3/3; an isolated Markdown source with four windows reached lint / published after repairing an initial lint failure. Content review remains pending.

Other API and custom providers have no live-quota verification record yet. The older gpt-5.5 returned unsupported for the tested ChatGPT account; use your own actual test result.

Quota errors pause a task. Authentication, network and image errors retain a failure record. Resolve the cause and resume manually; the app does not retry indefinitely.


2026-10-24 correction: pi 1.0.3 ChatGPT credentials use clientId, not accountId. New image-test records bind to that login’s clientId and model and survive token refresh and service restarts. Old token-bound records that already expired need one new test; they cannot be safely relabelled as current. An explicit new login opens a new connection and needs verification again.
