# Quick start

Study KB turns source material into a connected concept library. Files stay local; AI writing sends the material it reads to your selected model provider.

## Prepare the environment

Use Node.js 22.19 or newer and Python 3.12. Reuse an existing study-kb Conda environment; do not install it again.

```powershell
conda create -n study-kb python=3.12
conda run -n study-kb python -m pip install -r requirements.txt
conda activate study-kb
```

Strict PDF review, scans, Word and PowerPoint parsing require MinerU. The Environment page provides instructions and never installs software for you.

## Start the app

From the repository root, on the first run:

```powershell
cd web
npm ci
npm run dev
```

The browser opens automatically. Use `npm start` from the `web` directory on later runs; run `npm run build` and restart the service after code updates. Keep the terminal open while using the app; press Ctrl+C to stop the service.

If the browser does not open, copy the **complete URL** after `Study KB:` in the terminal, including `#token=…`. Opening only `http://127.0.0.1:8765` cannot establish the first connection. Each service restart generates a new link. If you see “Open the launch link to connect”, reopen the complete link from the current terminal. Do not share its connection token.

## Get started

- Open Environment and check Python and the pipeline. Set the full Python executable path in Models & settings if needed.
- Sign in with ChatGPT or add an API key/custom provider. Select a model, pass its image test, then set it as the current model.
- Upload a source in Library. Start AI ingest after preparation completes.
- Read after publication. Published means structural checks passed; content still requires independent review.

## Knowledge assistant and maintenance

Knowledge assistant provides queries, saving, content review, queue review, semantic checks, retrospectives, reading analysis, pipeline diagnosis and skill evolution. Continue a query or hand it to Save. Questions and change approvals stay in the execution record across page navigation. Save creates candidates first, then asks before publication checks. Published source cards offer content review and targeted content rework.

Maintenance tools expose classified Python CLI commands with their actual parameter descriptions, including vault adoption, cross-vault reuse, resealing, source relocation and concept promotion. Use Reports & review for structured content-review registration and human batch acceptance. Preview-based changes require a successful preview with identical arguments; apply and direct state changes require typing the exact target. Adoption operates on the existing wiki in the current workspace. Reuse needs an origin path and mapping file; it does not create mappings or merge content for you.

Skill evolution requires a clean Git checkout, creates an isolated branch for one skill in both trees, runs checks and stages a candidate. Only after reviewing the complete diff and typing its ID does adoption rerun checks, commit the isolated branch and fast-forward the project. The app does not commit your existing changes. Restart Node after code updates; the new version warns when a running server's code has changed.
