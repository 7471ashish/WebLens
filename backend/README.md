# WebLens Backend Service

An asynchronous, containerized FastAPI gateway that orchestrates the WebLens multi-skill audit pipeline. The service encapsulates Chromium-based crawling, heuristic and LLM diagnostic evaluations, SSRF network protection, sliding-window rate limiting, real-time Server-Sent Events (SSE) streaming, sub-second pre-flight security scanning, and automated local disk archival.

---

## Core Architecture Principles

- **Subprocess-per-Job Isolation**: Every audit job executes in an ephemeral directory sandbox (`tempfile.mkdtemp(prefix="weblens_<job_id>_")`). Pristine agent assets are atomically copied, ensuring concurrent runs never collide on output files or shared memory.
- **Strict Read-Only Agent Immutability**: The core `agent/` codebase remains strictly unmodified, pure, and read-only.
- **Instant Pre-Flight Security Scanner (< 500ms)**: Executes a lightweight asynchronous network probe evaluating protocol and security headers (HSTS, CSP, X-Frame-Options, MIME sniffing, Caching) before Chromium boots, delivering instant security diagnostics and a 0–100 Security Score.
- **Zero-Polling Real-Time SSE Stream**: Clients subscribe to `GET /audits/{job_id}/stream` to receive filtered major lifecycle milestones without client-side polling loops.
- **On-the-Spot Subprocess Termination**: `POST /audits/{job_id}/cancel` and `/terminate` trigger immediate process tree termination (`taskkill` on Windows, `SIGKILL` on POSIX) and clean up temporary sandboxes immediately.
- **Local Disk Report Archival**: Completed audit reports are automatically copied to `audit_reports/output.json` and a timestamped immutable copy (`audit_reports/output_<job_id[:8]>_<timestamp>.json`).
- **Strict Secret Hygiene**: LLM API keys (Groq / OpenAI) are passed exclusively through child process environment variables and are never logged, persisted, or returned in API responses. A custom logging filter scrubs tokens and keys from all log records and stderr traces.
- **SSRF Hardening**: Validates schemes (HTTP/HTTPS only), resolves DNS before connection, and rejects private subnets (RFC 1918), loopback IPs (`127.0.0.1`), link-local addresses, and cloud provider metadata endpoints (`169.254.169.254`). Local intranet testing can be enabled via `AUDIT_ALLOW_LOCAL=true`.
- **FIFO Queue & Concurrency Gating**: Governed by an `asyncio.Semaphore` (default: 3 concurrent jobs). Excess requests queue gracefully in-memory with status `"queued"`.

---

## Environment Configuration

The backend service is configured via environment variables:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `MAX_CONCURRENT_JOBS` | `int` | `3` | Maximum number of concurrent audit jobs allowed before queuing. |
| `JOB_TIMEOUT_SECONDS` | `float` | `260.0` | Hard execution ceiling for audit subprocess execution. |
| `JOB_TTL_SECONDS` | `float` | `3600.0` | In-memory job history retention window (seconds). |
| `AGENT_SOURCE_DIR` | `str` | `../agent` | Path to the pristine, read-only agent pipeline directory. |
| `ALLOWED_ORIGINS` | `str` | `""` | Comma-separated CORS origins. Extension origins (`chrome-extension://*`) are allowed via regex. |
| `AUDIT_ALLOW_LOCAL` | `bool` | `false` | When set to `true` or `1`, bypasses SSRF private IP checks for localhost/intranet audits. |

---

## API Reference

### 1. Liveness & Readiness Healthcheck
Verifies service availability, Playwright readiness, active semaphore slots, and agent directory presence.

- **Method**: `GET /health`
- **Response**: `200 OK`

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

### 2. Submit Audit Job
Submits a target URL and API credentials for audit execution.

- **Method**: `POST /audits`
- **Status Code**: `202 Accepted`
- **Headers**:
  - `Content-Type: application/json`
  - `X-Install-ID` *(optional)*: Unique extension installation identifier used for per-client rate limiting.
- **Request Body**:

```json
{
  "url": "https://example.com",
  "groq_api_key": "gsk_your_groq_api_key_here"
}
```

- **Response (`202 Accepted`)**:

```json
{
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "status": "queued"
}
```

- **Error Codes**:
  - `400 Bad Request`: Target URL failed SSRF validation (private subnet, loopback, or non-HTTP scheme).
  - `422 Unprocessable Entity`: Missing or empty `url` or `groq_api_key`.
  - `429 Too Many Requests`: Client exceeded rate limits (default: 15 audits / 10 minutes). Includes `Retry-After` header.

---

### 3. Server-Sent Events (SSE) Real-Time Progress Stream
Establishes a persistent SSE stream that emits major audit lifecycle transitions, pre-flight security metrics, and final results.

- **Method**: `GET /audits/{job_id}/stream`
- **Headers**: `Accept: text/event-stream`
- **Stream Event Format**:

```
data: {
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "status": "running",
  "progress": {
    "stage": "domain_skills",
    "percent": 60,
    "message": "Running Engagement, Multimodal & Accessibility skills..."
  },
  "preflight": {
    "scanned": true,
    "status_code": 200,
    "http_version": "HTTP/2",
    "hsts": true,
    "csp": true,
    "x_frame_options": "DENY",
    "x_content_type_options": "nosniff",
    "cache_control": "public, max-age=3600",
    "etag": true,
    "server": "cloudflare",
    "security_score": 100,
    "highlights": []
  },
  "result": null,
  "error": null
}
```

#### Major Lifecycle Stages Filter

| Stage ID | Progress % | Pipeline Milestone |
| :--- | :--- | :--- |
| `starting` | 5% | Ephemeral sandbox creation & key injection. |
| `preflight` | 8% | Sub-500ms protocol & security headers evaluation. |
| `crawling` | 15% | Headless Chromium initialization & sitemap discovery. |
| `init` | 10% | Module wiring & master audit setup. |
| `diagnostic` | 20% | LLM connectivity & prompt reasoning probe. |
| `crawled` | 40% | Page crawl completion & DOM hydration analysis. |
| `domain_skills` | 60% | Engagement, Multimodal & Accessibility evaluations. |
| `aggregating` | 80% | Multi-page findings synthesis & evidence tier grading. |
| `finalizing` | 95% | Canonical JSON compilation & report archival. |
| `complete` | 100% | Final report delivered. SSE stream finishes. |

---

### 4. Poll Audit Status & Findings
Retrieves current job status, progress, preflight telemetry, findings, or error message via standard REST.

- **Method**: `GET /audits/{job_id}`
- **Response**: `200 OK`

```json
{
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "status": "done",
  "url": "https://example.com",
  "created_at": "2026-09-27T10:00:00.000Z",
  "started_at": "2026-09-27T10:00:01.000Z",
  "finished_at": "2026-09-27T10:00:45.000Z",
  "progress": {
    "stage": "complete",
    "percent": 100,
    "message": "Audit completed successfully."
  },
  "preflight": {
    "scanned": true,
    "security_score": 100,
    "http_version": "HTTP/2",
    "hsts": true,
    "csp": true
  },
  "result": {
    "audit_metadata": { "target_url": "https://example.com", "pages_audited": 3 },
    "executive_summary": { "overall_score": 88, "critical_issues": 0 },
    "findings": [ ... ]
  },
  "error": null
}
```

---

### 5. Terminate / Cancel Audit Job
Immediately cancels a queued or running audit, terminates the underlying Chromium subprocess tree, cleans up temporary directories, and releases the concurrency semaphore slot.

- **Method**: `POST /audits/{job_id}/cancel` (or `POST /audits/{job_id}/terminate`)
- **Response**: `200 OK`

```json
{
  "status": "terminated",
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "message": "Audit job terminated successfully."
}
```

---

## Local Development & Setup

### Prerequisites
- Python 3.10+ (Tested up to Python 3.14)
- Playwright Chromium browser binaries

### Installation
```bash
# Navigate to backend directory
cd backend

# Create and activate virtual environment
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

### Starting the Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
The interactive API documentation is available at `http://localhost:8000/docs`.

### Running Unit & Integration Tests
```bash
# Run pytest from the WebLens root or backend directory
pytest backend/tests -v
```
All 43 unit and integration tests validate SSRF guards, rate limiters, subprocess isolation, secret redaction, and API endpoints.

---

## Running with Docker

### Docker Compose
```bash
# Build and run backend container
docker compose up --build
```

### Standalone Docker
```bash
# Build image from repository root
docker build -f backend/Dockerfile -t weblens-backend .

# Run container
docker run -p 8000:8000 \
  -e MAX_CONCURRENT_JOBS=3 \
  -e AUDIT_ALLOW_LOCAL=false \
  weblens-backend
```
