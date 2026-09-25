# WebLens Backend Container
# Uses official Playwright Python image containing Chromium & all required OS shared libraries
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000 \
    AGENT_SOURCE_DIR=/app/agent

WORKDIR /app

# Copy dependency requirements
COPY backend/requirements.txt /app/backend/requirements.txt

# Install Python packages
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy pristine agent codebase byte-for-byte (read-only)
COPY agent /app/agent

# Copy backend application source
COPY backend /app/backend

WORKDIR /app/backend

EXPOSE 8000

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8000/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
