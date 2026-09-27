# WebLens — AI Technical Health & Readiness Auditor

**WebLens** is an autonomous, multi-skill website auditing platform delivered as a Chrome Extension backed by a containerized FastAPI service. It performs non-destructive, read-only technical audits across AI discoverability, WCAG accessibility, rendering health, engagement friction, and multimodal asset quality — and surfaces evidence-gated findings directly in the browser Side Panel.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Skill Catalog](#skill-catalog)
- [Prerequisites](#prerequisites)
- [Local Setup](#local-setup)
  - [1. Backend Service](#1-backend-service)
  - [2. Agent Pipeline (Standalone)](#2-agent-pipeline-standalone)
  - [3. Chrome Extension](#3-chrome-extension)
- [Cloud Deployment](#cloud-deployment)
  - [Docker Compose](#docker-compose)
  - [Manual Docker Build](#manual-docker-build)
  - [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Security](#security)
- [Testing](#testing)
- [Output Schema](#output-schema)

---

## Architecture Overview

```
┌─────────────────────────────────────┐
│        Chrome Extension (MV3)       │
│  Side Panel · Active-Tab Sync · SSE │
└──────────────────┬──────────────────┘
                   │ HTTP / SSE
                   ▼
┌─────────────────────────────────────┐
│      FastAPI Backend  (:8000)       │
│  SSRF Guard · Rate Limiter · Jobs   │
│  Subprocess-per-Job Isolation       │
└──────────────────┬──────────────────┘
                   │ subprocess
                   ▼
┌─────────────────────────────────────┐
│       Agent Audit Pipeline          │
│  audit-orchestrator (entrypoint)    │
│  ├── crawl-render-audit             │
│  ├── freshness-corroboration-audit  │
│  ├── engagement-audit               │
│  ├── multimodal-audit               │
│  └── visual-accessibility-audit     │
└─────────────────────────────────────┘
```

The backend spawns each audit as an **isolated subprocess** with its own temporary working directory so concurrent jobs never share state. The Chrome Extension communicates with the backend via REST polling and a real-time **Server-Sent Events** stream.

---

## Skill Catalog

| Skill | Responsibility |
|---|---|
| `audit-orchestrator` | Dispatches all domain skills, gates severity, deduplicates findings, and emits `output.json` |
| `crawl-render-audit` | HTTP headers, robots.txt, XML sitemaps, raw HTML vs. Chromium-rendered DOM diff |
| `freshness-corroboration-audit` | Schema.org JSON-LD structured data, entity `sameAs` links, temporal recency |
| `engagement-audit` | Above-the-fold hero, CTA clarity, navigation cognitive load, readability (Flesch) |
| `multimodal-audit` | AI extractability of images — missing alt text, OCR text in rasters, infographic structures |
| `visual-accessibility-audit` | WCAG 2.1/2.2 AA contrast, keyboard focus, ARIA landmarks, multi-viewport responsive rendering |

Every finding tracks an explicit **evidence tier**:

| Tier | Type |
|---|---|
| Tier 1 | Directly measured — HTTP codes, contrast ratios, viewport scroll dimensions |
| Tier 2 | Parsed DOM — HTML tags, CSS selectors, JSON-LD blocks, robots directives |
| Tier 3 | Externally corroborated — search verification, authoritative social profile linkage |
| Tier 4 | Heuristic / inferred — Flesch reading ease, content density, qualitative UX |

A finding is forbidden from being rated `high` or `critical` based solely on Tier-4 evidence.

---

## Prerequisites

| Dependency | Minimum Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.10, 3.11, 3.12, or 3.13 |
| Docker | 24+ | Required for containerized deployment only |
| Docker Compose | v2 | Bundled with Docker Desktop |
| Google Chrome | 114+ | Required for extension Side Panel API |
| Groq Cloud API Key | — | Optional — audit pipeline runs fully offline without it |

---

## Local Setup

### 1. Backend Service

The backend is a FastAPI application located in `backend/`. It requires the `agent/` directory to be present at the same level.

```bash
# Clone the repository
git clone <repository-url>
cd WebLens

# Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install backend dependencies
pip install -r backend/requirements.txt

# Install Playwright's headless Chromium browser
playwright install chromium

# Start the backend development server
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive Swagger UI.

**Optional — allow auditing localhost targets (development only):**

```bash
AUDIT_ALLOW_LOCAL=true uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

### 2. Agent Pipeline (Standalone)

The agent can be run independently of the backend to audit any URL directly from the command line.

```bash
cd agent

# Install agent dependencies
pip install -r requirements.txt
playwright install chromium

# Copy the environment template and provide optional LLM credentials
cp .env.example .env
# Edit .env and set GROQ_API_KEY, OPENROUTER_API_KEY, or OPENAI_API_KEY
# The pipeline runs at full fidelity in offline mode if no keys are provided

# Run an audit
python run_master_audit.py https://example.com
```

Findings are written to `output.json` in the current directory.

---

### 3. Chrome Extension

The extension is an unpacked Manifest V3 extension and does not require a build step.

1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** using the toggle in the top-right corner.
3. Click **Load unpacked** and select the `extension/` folder inside this repository.
4. The WebLens icon will appear in your Chrome toolbar.
5. Click the icon to open the Side Panel.
6. Enter your **Groq Cloud API key** in the settings area.
7. Navigate to any website and click **Audit This Page**.

The extension connects to `http://localhost:8000` by default. Ensure the backend service is running before submitting an audit.

---

## Cloud Deployment

### Docker Compose

The recommended production deployment uses the included `docker-compose.yml`, which builds the backend image and starts the service with sane defaults.

```bash
# From the repository root
docker compose up --build
```

The backend will be available on port `8000` of the host machine. To run in detached mode:

```bash
docker compose up --build -d
```

To stop the service:

```bash
docker compose down
```

---

### Manual Docker Build

Build and run the image directly with Docker:

```bash
# Build from repository root (context must include both backend/ and agent/)
docker build -f backend/Dockerfile -t weblens-backend .

# Run the container
docker run -d \
  --name weblens-backend \
  -p 8000:8000 \
  -e MAX_CONCURRENT_JOBS=3 \
  -e JOB_TIMEOUT_SECONDS=260 \
  weblens-backend
```

The container uses the official Microsoft Playwright Python image (`mcr.microsoft.com/playwright/python:v1.49.0-noble`), which ships with Chromium and all required OS-level shared libraries — no separate browser installation is required inside the container.

---

### Deploying to a Cloud Provider

The service is a standard stateless Docker container and can be deployed to any platform that supports containers.

**Google Cloud Run:**

```bash
# Authenticate and set project
gcloud auth login
gcloud config set project <PROJECT_ID>

# Build and push to Google Artifact Registry
gcloud builds submit --tag gcr.io/<PROJECT_ID>/weblens-backend .

# Deploy
gcloud run deploy weblens-backend \
  --image gcr.io/<PROJECT_ID>/weblens-backend \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --memory 2Gi \
  --cpu 2 \
  --set-env-vars MAX_CONCURRENT_JOBS=3,JOB_TIMEOUT_SECONDS=260
```

**AWS App Runner / ECS:**

Push the image to Amazon ECR and create a service pointing to port `8000`. Set the environment variables listed below under the task or service definition.

**After cloud deployment**, update the Chrome Extension's backend URL:

Open `extension/background.js` and `extension/side_panel.js` and replace the `localhost:8000` base URL constant with your deployed service URL. Then reload the unpacked extension in Chrome.

---

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MAX_CONCURRENT_JOBS` | `3` | Maximum number of audit jobs running in parallel |
| `JOB_TIMEOUT_SECONDS` | `260.0` | Hard timeout per audit job in seconds |
| `JOB_TTL_SECONDS` | `3600.0` | How long completed job records are retained in memory |
| `AGENT_SOURCE_DIR` | `../agent` | Absolute path to the agent source directory |
| `ALLOWED_ORIGINS` | *(see below)* | Comma-separated list of additional CORS origins |
| `AUDIT_ALLOW_LOCAL` | `false` | Set to `true` to allow auditing private/localhost URLs (development only) |

Default CORS allowed origins when `ALLOWED_ORIGINS` is unset:
- `http://localhost:3000`
- `http://localhost:5173`
- `http://localhost:8000`
- `http://127.0.0.1:8000`
- Any `chrome-extension://<id>` origin (matched by regex)

---

## API Reference

All endpoints are served from `http://<host>:8000`.

### `GET /health`

Liveness and readiness check. Returns Playwright availability and concurrency capacity.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "playwright_ready": true,
  "max_concurrent_jobs": 3,
  "active_jobs_remaining_slots": 3,
  "agent_dir_exists": true
}
```

---

### `POST /audits` — Submit Audit Job

Returns HTTP `202 Accepted` immediately. The audit runs asynchronously.

```bash
curl -X POST http://localhost:8000/audits \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "groq_api_key": "gsk_your_groq_api_key"
  }'
```

```json
{
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "status": "queued"
}
```

---

### `GET /audits/{job_id}` — Poll Job Status

Returns the current job state, progress milestones, and final findings once complete.

```bash
curl http://localhost:8000/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1
```

---

### `GET /audits/{job_id}/stream` — Server-Sent Events Stream

Streams major lifecycle events (status transitions, stage changes, completion) as SSE. Replaces polling for real-time clients.

```bash
curl -N http://localhost:8000/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1/stream
```

---

### `POST /audits/{job_id}/terminate` — Cancel Job

Immediately terminates an active audit job.

```bash
curl -X POST http://localhost:8000/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1/terminate
```

---

## Security

- **SSRF Protection**: All submitted URLs are validated before execution. The guard resolves DNS and rejects private RFC-1918 ranges, loopback (`127.x.x.x`), link-local (`169.254.x.x`), cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`), and IPv6 ULA/link-local ranges.
- **API Key Hygiene**: Groq API keys are passed exclusively through child subprocess environment variables. They are never logged, stored, or included in any response payload. All log output is scrubbed by a `SecretSanitizingFilter`.
- **Subprocess Isolation**: Each job runs in a temporary copy of the agent directory. Concurrent jobs cannot interfere with each other's `output.json` or state.
- **Rate Limiting**: A sliding window rate limiter enforces a maximum of 15 requests per 10-minute window per client IP or `X-Install-ID` header.

---

## Testing

### Backend Tests

```bash
cd WebLens
pytest backend/tests -v
```

### Agent Test Suite

```bash
cd agent
pytest -q
```

### Targeted Integration Suites

```bash
cd agent
pytest test_multipage_audit.py test_master_integration.py test_network_resilience.py test_final_output_schema.py -v
```

Test coverage includes:
- Multi-page URL discovery, candidate scoring, and diversity budgeting
- Sitemap and sitemap index recursion up to 2,000 URLs
- Cross-page finding aggregation and isolated defect preservation
- Full end-to-end audit scenarios: browser fallback, network timeout, robots.txt strict stop, redirect chains, zero-defect clean sites, and output schema invariants

---

## Output Schema

Audit results conform to the following canonical schema:

```json
{
  "site": "example.com",
  "audited_at": "2025-01-01T12:00:00Z",
  "summary": {
    "total_findings": 10,
    "critical": 0,
    "high": 2,
    "medium": 4,
    "low": 4
  },
  "findings": [
    {
      "id": "CR-HYDRATE-001",
      "title": "Client-side rendering dependency with viewport-blocking preloader",
      "severity": "high",
      "evidence": "...",
      "suggested_action": {
        "summary": "Ensure critical content is server-rendered.",
        "priority": "high"
      },
      "confidence": 0.9,
      "evidence_tier": "tier_1"
    }
  ],
  "coverage": {
    "skills_run": 5,
    "skills_ok": 5,
    "skills_partial": 0,
    "skills_failed": 0
  },
  "meta": {
    "runtime_seconds": 28.96,
    "pages_discovered": 8,
    "pages_selected": 8,
    "pages_crawled": 8,
    "pages_successfully_audited": 8,
    "page_types_covered": ["homepage", "pricing", "about_company", "contact"]
  }
}
```

---

## License

All skills are released under the MIT License. See `agent/LICENSE` for details.
