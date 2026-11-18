FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# PyMuPDF ships its own wheels; no poppler or compiler needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY static/ static/

EXPOSE 8000

# Render injects $PORT. Single worker keeps memory low on the free tier.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
