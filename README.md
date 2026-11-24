# UnderwriteIQ — Universal Financial Document Underwriting Engine

Upload bank statements, funding applications, driver licences and voided checks. The engine classifies each document, extracts structured data with an LLM of your choice (bring your own API key), reconciles statement math, and flags MCA, loan, credit-card, BNPL and returned-item activity for underwriting review.

## Render Link: https://underwriteiq.onrender.com

## Features

- **Automatic triage:** each upload is classified as a bank statement, funding application, driver licence / ID, voided check or other. Single- vs multi-account statements are detected and shown on the document card as soon as they are known.
- **Bank statements:**
  - Micro-batched transaction extraction (text for digital PDFs, vision for scans and images).
  - Reconciliation audit: beginning balance + deposits − withdrawals = ending balance, plus deposit and withdrawal tie-outs.
  - **Partial-statement warning** when transactions cover 70% or less of the statement period.
  - MCA & Underwriting tab with collapsible groups (MCA, business loans, credit cards, BNPL, returned/NSF).
- **Applications, IDs and checks:** a dedicated details view instead of the statement interface. Applications get a completeness check, ownership summary and masked SSNs. IDs get an expiry badge. Checks show account and routing details.
- **Split viewer:** source document (PDF.js or image) next to the extracted data, with CSV and JSON export.
- **Insights:** monthly deposit/withdrawal bars, running-balance line and debt-category donut per statement; a cross-statement **Portfolio Insights** view (avg monthly deposits, lowest balance, debt burden %, NSF count, detected lenders); flagged transactions highlighted and filterable in the grid with sortable columns.
- **Productivity:** Ctrl/Cmd+K command palette, one-page printable underwriting summary, per-document token and cost badge.
- **Account selector:** extracted accounts and holders are listed in a dropdown that filters the library.
- **BYOK gateway:** OpenAI, Anthropic, OpenRouter or any OpenAI-compatible endpoint.

## Privacy

- Your API key lives in the browser's `sessionStorage` and is sent only with your own requests. Never put a key in Render environment variables.
- Uploaded originals are kept in the browser (IndexedDB) and extraction results in `localStorage`. The server processes each file in a temporary folder and deletes it immediately; nothing is stored server-side.
- Browser storage is per device and browser. Add object storage (S3, Cloudflare R2) before introducing a shared server-side library.

## Project structure

```text
├── Dockerfile
├── render.yaml            # Render Blueprint (free Docker web service)
├── requirements.txt       # pinned dependencies
├── app/
│   ├── main.py            # FastAPI server, SSE progress, static hosting
│   ├── gateway.py         # Universal LLM gateway
│   ├── pipeline.py        # Triage → topology → extraction → audit → underwriting
│   ├── prompts.py         # Detection & extraction prompts
│   ├── schemas.py         # Structured-output JSON schemas
│   └── config.py          # Providers, models, pricing
├── static/
│   ├── index.html
│   ├── app.js
│   └── vendor/            # Self-hosted PDF.js (no CDN dependency for previews)
└── root_files/            # Original research scripts (not deployed)
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, click the settings button, choose a provider and model, paste your API key and test the connection.

Or with Docker:

```bash
docker build -t underwriteiq . && docker run -p 8000:8000 underwriteiq
```

## Deploy to Render (free)

1. Push to GitHub:

   ```bash
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

2. In Render choose **New + → Blueprint**, connect the repo and apply. Render reads `render.yaml` (Docker runtime, free plan, health check `/api/health`).
   Alternatively choose **New + → Web Service**, pick the repo, select **Docker** and the **Free** instance type, and set the health check path to `/api/health`.
3. No environment variables are required. `PORT` is provided by Render. Set `CORS_ALLOWED_ORIGINS` only if you serve the frontend from a different domain.
4. Open the service URL, add your API key in settings and upload a document.

### Free-tier notes

- The service sleeps after about 15 minutes without traffic; the next request takes roughly 30–60 seconds to wake it.
- The instance has 512 MB of RAM. Typical statements are fine; very long scanned PDFs (dozens of pages) render page images and may run out of memory. Split those first.
- Extractions run for the length of the request, with live progress over server-sent events. Render's proxy allows long requests, but a sleeping or restarting instance will interrupt one in progress; use **Extract Again**.
- Sample or customer PDFs are git-ignored (`*.pdf`). Keep it that way before pushing.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Health check |
| `GET /api/providers` | Provider and model registry |
| `POST /api/test-connection` | Validate provider, key and model |
| `POST /api/extract` | Upload and process a document |
| `GET /api/progress/{job_id}` | SSE progress stream |

Provider credentials are passed per request in `x-provider`, `x-api-key`, `x-model`, `x-base-url` and `x-reasoning-effort` headers. Always use the HTTPS URL Render provides.
