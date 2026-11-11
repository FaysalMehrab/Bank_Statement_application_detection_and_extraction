# Universal Statement & Financial Document Underwriting Engine

The solution unites four research scripts into an end-to-end processing pipeline, incorporates a Bring-Your-Own-Key (BYOK) Universal LLM Gateway (OpenAI, Anthropic, DeepSeek, HuggingFace, Custom), and provides a responsive Single-Page Application with date grouping, search, split-screen PDF preview, math reconciliation badges, and interactive warning tooltips.

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
5. Choose the Docker runtime; Render uses the included Dockerfile.
6. Select an instance type and deploy.

The original source describes selecting the Free instance type. Availability and pricing may change; check Render's current plan details before deployment.
