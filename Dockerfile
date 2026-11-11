FROM python:3.11-slim

# Install system dependencies including poppler-utils for PDF image rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files and frontend
COPY app/ app/
COPY static/ static/

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
