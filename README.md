# Universal Statement & Financial Document Underwriting Engine

The solution unites four research scripts into an end-to-end processing pipeline, incorporates a Bring-Your-Own-Key (BYOK) Universal LLM Gateway, and provides a responsive single-page application with date grouping, search, a full scrollable PDF preview, reconciliation badges, transaction exports, and underwriting risk review.

## Document privacy and persistence

Uploaded originals and extraction results are kept in the browser on the device that uploaded them. Source files use IndexedDB; extraction results use local storage. This makes previews and original-file downloads survive page refreshes without storing customer documents on the Render server.

Browser storage is not shared across devices or browsers. Add object storage (for example, S3 or Cloudflare R2) before introducing a shared server-side document library.

## Project structure

```text
statement_extractor_webapp/
├── requirements.txt
├── Dockerfile
├── README.md
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── schemas.py
│   ├── prompts.py
│   ├── gateway.py
│   ├── pipeline.py
│   └── main.py
└── static/
    ├── index.html
    └── app.js
```

## Install dependencies

Activate your Python virtual environment, then run:

```bash
pip install -r requirements.txt
```

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Deploy to Render via GitHub

1. Push the project to a GitHub repository:

   ```bash
   git init
   git add .
   git commit -m "Initial commit: Universal Underwriting Web Engine"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo-name>.git
   git push -u origin main
   ```

2. Go to Render.com and create an account.
3. Select **New + > Web Service**.
4. Choose **Build and deploy from a Git repository** and connect your GitHub repository.
5. Choose the Docker runtime; Render uses the included Dockerfile and automatically respects Render's `PORT` environment variable.
6. Optionally set `CORS_ALLOWED_ORIGINS` to your deployed service URL if you host the frontend separately. It is not required for this same-origin deployment.
7. Select an instance type and deploy. The health check endpoint is `/api/health`.

The original source describes selecting the Free instance type. Availability and pricing may change; check Render's current plan details before deployment.
