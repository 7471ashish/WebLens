# WebLens — AI Technical Health & Readiness Auditor

[![Version](https://img.shields.io/badge/version-1.0.1-blue.svg)](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/package.json)
[![Browser](https://img.shields.io/badge/browsers-Chrome%20%7C%20Firefox-orange.svg)](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/extension/)
[![Backend](https://img.shields.io/badge/backend-FastAPI%20%7C%20Playwright-green.svg)](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/backend/)
[![Live Deployment](https://img.shields.io/badge/deployed-Render-46E3B7.svg)](https://weblens-backend-i7n8.onrender.com)

**WebLens** is an autonomous, multi-skill website auditing platform delivered as a **Cross-Browser Extension (Google Chrome & Mozilla Firefox)** backed by a containerized FastAPI service. It performs non-destructive, read-only technical audits across AI search discoverability, WCAG 2.1 AA accessibility, crawl-rendering health, engagement friction, and multimodal asset extractability — and surfaces evidence-gated findings directly in the browser's side panel.

---

## Quick Reference Links

| Resource | Link / Path | Description |
| :--- | :--- | :--- |
| ☁️ **Live Cloud Backend** | [`https://weblens-backend-i7n8.onrender.com`](https://weblens-backend-i7n8.onrender.com) | Hosted production backend on Render |
| 📖 **Interactive API Docs** | [`https://weblens-backend-i7n8.onrender.com/docs`](https://weblens-backend-i7n8.onrender.com/docs) | Swagger UI for live REST & SSE endpoints |
| 🦊 **Firefox Submission Guide** | [FIREFOX_SUBMISSION_GUIDE.md](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/FIREFOX_SUBMISSION_GUIDE.md) | Step-by-step AMO store submission manual |
| 📦 **Firefox AMO Store Zip** | `dist/weblens-firefox-v1.0.1.zip` | Store-ready, Mozilla linter-validated package |
| 📦 **Chrome Web Store Zip** | `dist/weblens-chrome-v1.0.1.zip` | Store-ready Chrome MV3 package |

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Skill Catalog & Evidence Tiers](#skill-catalog--evidence-tiers)
- [Mode 1: Running with Deployed Cloud Backend (Zero-Setup)](#mode-1-running-with-deployed-cloud-backend-zero-setup)
  - [Google Chrome Setup](#google-chrome-setup)
  - [Mozilla Firefox Setup](#mozilla-firefox-setup)
  - [Cloud Backend Free-Tier Keep-Alive](#cloud-backend-free-tier-keep-alive)
- [Mode 2: Running Locally (Full-Stack Development)](#mode-2-running-locally-full-stack-development)
  - [1. Local Backend via Python / Uvicorn](#1-local-backend-via-python--uvicorn)
  - [2. Local Backend via Docker Compose](#2-local-backend-via-docker-compose)
  - [3. Pointing the Extension to Local Backend](#3-pointing-the-extension-to-local-backend)
  - [4. Standalone CLI Audit Pipeline](#4-standalone-cli-audit-pipeline)
- [Building & Packaging the Extensions](#building--packaging-the-extensions)
- [Cloud Deployment (Render / Docker)](#cloud-deployment-render--docker)
- [API Reference](#api-reference)
- [Security & SSRF Hardening](#security--ssrf-hardening)
- [Testing Suite](#testing-suite)
- [Canonical Output Schema](#canonical-output-schema)
- [License](#license)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                 Cross-Browser Extension                     │
│  Chrome (MV3 sidePanel)   ·   Firefox (MV3 sidebarAction)   │
│  Active-Tab Sync  ·  Real-Time SSE  ·  Inline Settings UI   │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS / SSE
                               ▼
┌─────────────────────────────────────────────────────────────┐
│          WebLens FastAPI Backend Service                    │
│  Cloud: https://weblens-backend-i7n8.onrender.com           │
│  Local: http://localhost:8000                               │
│  ─────────────────────────────────────────────────────────  │
│  • Instant Pre-Flight Security Scanner (< 500ms)            │
│  • SSRF Protection Guard & Sliding-Window Rate Limiter      │
│  • Ephemeral Subprocess-per-Job Isolation Sandbox           │
└──────────────────────────────┬──────────────────────────────┘
                               │ subprocess
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 Agent Audit Pipeline                        │
│  run_master_audit.py (Multi-Page Intelligent Orchestrator)  │
│  ├── crawl-render-audit         (Chromium vs. Raw HTML DOM) │
│  ├── freshness-corroboration    (Schema.org JSON-LD, Facts) │
│  ├── engagement-audit           (Hero, CTAs, Readability)   │
│  ├── multimodal-audit           (OCR, Alt-Text, Charts)     │
│  └── visual-accessibility-audit (WCAG AA, Contrast, Focus)  │
└─────────────────────────────────────────────────────────────┘
```

The backend executes every audit as an **ephemeral subprocess sandbox** (`tempfile.mkdtemp`), atomically copying pristine agent files so concurrent jobs never collide on output files or memory. The extension communicates via REST endpoints and a real-time **Server-Sent Events (SSE)** stream.

---

## Skill Catalog & Evidence Tiers

| Skill | Primary Focus & Checks |
| :--- | :--- |
| `audit-orchestrator` | Multi-page crawl coordination, cross-page fact consistency, evidence tier validation, canonical `output.json` emission. |
| `crawl-render-audit` | Protocol headers, `robots.txt`, XML sitemaps, raw HTML vs. Playwright Chromium-rendered DOM hydration gap. |
| `freshness-corroboration-audit` | Schema.org JSON-LD structured data, entity authority (`sameAs`), temporal recency, cross-page fact corroborate. |
| `engagement-audit` | Above-the-fold hero impact, CTA prominence, navigation cognitive friction, Flesch reading ease score. |
| `multimodal-audit` | Image extractability by AI agents: missing alt attributes, raster-embedded text (OCR), infographic structures. |
| `visual-accessibility-audit` | WCAG 2.1/2.2 AA contrast ratios, keyboard navigation accessibility, ARIA landmark semantics, multi-viewport layout. |

### Evidence Tier Rating System

Every finding is attributed to an explicit **evidence tier**:

| Tier | Classification | Verification Standard |
| :--- | :--- | :--- |
| **Tier 1** | Directly Measured | Deterministic measurements: HTTP status codes, color contrast ratios, CSS box model overflows. |
| **Tier 2** | Parsed DOM / Protocol | Structural facts: HTML tags, CSS selectors, Schema.org JSON-LD blocks, `robots.txt` directives. |
| **Tier 3** | Externally Corroborated | External verification: search engine indexability, verified social profile linkages. |
| **Tier 4** | Heuristic / Inferred | Algorithmic scoring: Flesch reading ease, content density heuristics, qualitative UX estimates. |

> [!IMPORTANT]
> **Defect Gating Rule:** A finding is strictly forbidden from being rated `high` or `critical` if backed solely by Tier 4 heuristic evidence.

---

## Mode 1: Running with Deployed Cloud Backend (Zero-Setup)

The fastest way to use WebLens without installing Python, Docker, or Chromium locally. The extension is preconfigured out of the box to connect directly to the hosted Render cloud backend:
**`https://weblens-backend-i7n8.onrender.com`**

### Google Chrome Setup

1. Open Google Chrome and enter `chrome://extensions/` in the address bar.
2. Enable **Developer mode** via the toggle in the top-right corner.
3. Click **Load unpacked** and select the unpacked Chrome directory:
   ```
   WebLens/dist/chrome
   ```
   *(Or unpack `dist/weblens-chrome-v1.0.1.zip` and load that folder).*
4. Click the WebLens icon in your toolbar to open the Side Panel.
5. Click the gear icon (**Settings**) in the header:
   - Notice the **Backend Server URL** is already set to `https://weblens-backend-i7n8.onrender.com`.
   - Click **"Test Connection"** to verify backend readiness.
   - *(Optional)* Add your free **Groq Cloud API Key** from [console.groq.com/keys](https://console.groq.com/keys) for enhanced AI reasoning.
6. Navigate to any website (e.g. `https://example.com`) and click **Audit This Page**!

---

### Mozilla Firefox Setup

1. Open Mozilla Firefox and enter `about:debugging#/runtime/this-firefox` in the address bar.
2. Under **Temporary Extensions**, click **Load Temporary Add-on...**.
3. Select the Firefox manifest:
   ```
   WebLens/dist/firefox/manifest.json
   ```
   *(Or select the pre-built `dist/weblens-firefox-v1.0.1.zip` archive directly).*
4. Open the Firefox Sidebar (**Ctrl+B** on Windows/Linux or **Cmd+B** on macOS) and select **WebLens** from the dropdown menu.
5. Click the gear icon (**Settings**), verify the connection, and run an audit on any active webpage.

> [!TIP]
> To publish to the official Firefox Add-ons Store (AMO), consult the complete [FIREFOX_SUBMISSION_GUIDE.md](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/FIREFOX_SUBMISSION_GUIDE.md).

---

### Cloud Backend Free-Tier Keep-Alive

Render's free tier spins down (sleeps) web service instances after **15 minutes of inactivity**.

- **Waking up from sleep:** When waking up from an idle state, the first request takes **~30–50 seconds**.
- **Instant Wake-Up:** Click **"Test Connection"** in the extension Settings panel before auditing. This issues a `/health` ping that wakes up the container.
- **Automated Keep-Alive:** The repository includes a GitHub Actions workflow ([`.github/workflows/keep_alive.yml`](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/.github/workflows/keep_alive.yml)) that pings the `/health` endpoint every 10 minutes. Set `RENDER_BACKEND_URL=https://weblens-backend-i7n8.onrender.com` in your repository secrets to enable it.

---

## Mode 2: Running Locally (Full-Stack Development)

For local development, offline auditing, or modifying backend and agent logic:

### 1. Local Backend via Python / Uvicorn

```bash
# Clone the repository
git clone https://github.com/7471ashish/WebLens.git
cd WebLens

# Create and activate a virtual environment
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install backend dependencies
pip install -r backend/requirements.txt

# Install Playwright Chromium browser binaries
playwright install chromium

# Launch the FastAPI backend
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The local API is accessible at `http://localhost:8000`. Test it:
```bash
curl http://localhost:8000/health
```

*(Optional) Allow auditing local loopback / private IP targets (development only):*
```bash
# Windows PowerShell:
$env:AUDIT_ALLOW_LOCAL="true"; uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Linux / macOS:
AUDIT_ALLOW_LOCAL=true uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

### 2. Local Backend via Docker Compose

```bash
# Build and start container from repository root
docker compose up --build

# Run in background (detached mode):
docker compose up --build -d

# Stop container:
docker compose down
```

The container automatically configures Chromium and OS-level dependencies via the official Microsoft Playwright image (`mcr.microsoft.com/playwright/python:v1.49.0-noble`).

---

### 3. Pointing the Extension to Local Backend

You do **not** need to edit code to switch between cloud and local backends:

1. Open the WebLens Side Panel in Chrome or Firefox.
2. Click the gear icon (**Settings**).
3. In the **Backend Server URL** input, enter:
   ```
   http://localhost:8000
   ```
4. Click **Test Connection** (returns green confirmation).
5. Click **Save Settings**.
6. *(To revert back to the cloud backend at any time, click the circular reset icon next to the URL input).*

---

### 4. Standalone CLI Audit Pipeline

Run audits directly from the command line without starting the FastAPI web service:

```bash
cd agent

# Install agent dependencies
pip install -r requirements.txt
playwright install chromium

# Optional: configure API keys
cp .env.example .env

# Run CLI audit
python run_master_audit.py https://example.com

# Run in offline mode (deterministic heuristics, no LLM):
python run_master_audit.py https://example.com --no-llm
```

Results are printed to console and saved as `output.json`.

---

## Building & Packaging the Extensions

WebLens uses a single shared frontend codebase in `extension/` and compiles target-specific distributions for both Chrome and Firefox with one command.

### Building via Node.js or Python

```bash
# Using Node.js:
node scripts/build_extension.js

# OR using Python:
python scripts/package_extensions.py
```

### Build Outputs in `dist/`

```
dist/
├── chrome/                      # Unpacked Chrome MV3 extension (load in chrome://extensions)
├── firefox/                     # Unpacked Firefox MV3 extension (load in about:debugging)
├── weblens-chrome-v1.0.1.zip     # Chrome Web Store submission package
└── weblens-firefox-v1.0.1.zip    # Mozilla Add-ons (AMO) submission package
```

### Automated Linter Verification
Verify Mozilla compatibility using Mozilla's official linter:
```bash
npx addons-linter dist/weblens-firefox-v1.0.1.zip
# Returns: 0 errors, 0 notices
```

---

## Cloud Deployment (Render / Docker)

### Automated Blueprint via `render.yaml`
1. Fork or push this repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** $\rightarrow$ **Blueprint**.
3. Connect your repository. Render automatically reads [`render.yaml`](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/render.yaml) and provisions the Dockerized web service.

### Manual Standalone Docker Container
```bash
# Build from repository root (build context must include both backend/ and agent/)
docker build -f backend/Dockerfile -t weblens-backend .

# Run container
docker run -d \
  -p 8000:8000 \
  --name weblens-backend \
  -e MAX_CONCURRENT_JOBS=3 \
  -e JOB_TIMEOUT_SECONDS=0 \
  weblens-backend
```

### Environment Configuration Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MAX_CONCURRENT_JOBS` | `3` | Maximum number of concurrent audit jobs running in parallel. |
| `JOB_TIMEOUT_SECONDS` | `0` | Execution timeout in seconds (`0` = unlimited execution). |
| `JOB_TTL_SECONDS` | `3600.0` | Retention window for in-memory job status records (seconds). |
| `AGENT_SOURCE_DIR` | `../agent` | Path to pristine agent source directory. |
| `ALLOWED_ORIGINS` | `""` | Comma-separated list of additional CORS origins. |
| `AUDIT_ALLOW_LOCAL` | `false` | When `true`, allows auditing `localhost` / private IPs (development only). |

---

## API Reference

Endpoints can be queried on the live cloud service (`https://weblens-backend-i7n8.onrender.com`) or locally (`http://localhost:8000`).

### 1. `GET /health` — Service Readiness Check
```bash
curl https://weblens-backend-i7n8.onrender.com/health
```
```json
{
  "status": "healthy",
  "playwright_ready": true,
  "max_concurrent_jobs": 2,
  "active_jobs_remaining_slots": 2,
  "agent_dir_exists": true
}
```

### 2. `POST /audits` — Submit Audit Job
```bash
curl -X POST https://weblens-backend-i7n8.onrender.com/audits \
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

### 3. `GET /audits/{job_id}/stream` — Server-Sent Events (SSE) Stream
Streams real-time progress, instant pre-flight security scan metrics, and final findings:
```bash
curl -N https://weblens-backend-i7n8.onrender.com/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1/stream
```

### 4. `GET /audits/{job_id}` — Poll Job Status & Report
```bash
curl https://weblens-backend-i7n8.onrender.com/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1
```

### 5. `POST /audits/{job_id}/cancel` — Terminate Audit Job
Immediately kills subprocess execution and reclaims sandbox resources.
```bash
curl -X POST https://weblens-backend-i7n8.onrender.com/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1/cancel
```

---

## Security & SSRF Hardening

- **SSRF Protection:** All target URLs are parsed and validated before network dispatch. DNS is resolved to verify IP ranges; private subnets (RFC 1918), loopback (`127.0.0.1`), link-local (`169.254.0.0/16`), cloud metadata endpoints (`169.254.169.254`), and IPv6 equivalents are strictly rejected.
- **Zero API Key Persistence:** Groq API keys are provided directly per request by the client, held purely in memory for subprocess environment injection, and scrubbed from all logs via a `SecretSanitizingFilter`.
- **Subprocess-per-Job Isolation:** Jobs execute in isolated temporary working directories. Concurrent jobs never collide on file handles or shared memory.
- **Non-Destructive Audits:** All network requests and headless browser interactions are read-only (`GET`/`HEAD`). No forms are submitted and no cookies are stored.

---

## Testing Suite

### Run All Backend Unit & Integration Tests (43 Tests)
```bash
pytest backend/tests -v
```

### Run Agent Integration & Master Audit Tests
```bash
cd agent
pytest test_master_integration.py test_multipage_audit.py test_network_resilience.py test_final_output_schema.py -v
```

---

## Canonical Output Schema

Reports conform to the standardized schema saved in `output.json`:

```json
{
  "site": "example.com",
  "audited_at": "2026-09-27T12:00:00Z",
  "summary": {
    "total_findings": 12,
    "critical": 0,
    "high": 2,
    "medium": 5,
    "low": 5
  },
  "findings": [
    {
      "id": "CR-HYDRATE-001",
      "title": "Client-side rendering dependency with pre-hydration content gap",
      "severity": "high",
      "evidence": "Website relies heavily on client-side JS (4.2x expansion from raw HTML to rendered DOM).",
      "suggested_action": {
        "summary": "Ensure critical product information is server-rendered.",
        "priority": "high"
      },
      "confidence": 0.9,
      "evidence_tier": "tier_1",
      "scope": "site-wide",
      "affected_pages": 4,
      "pages_examined": 5,
      "affected_ratio": 0.8
    }
  ],
  "coverage": {
    "skills_run": 5,
    "skills_ok": 5,
    "skills_partial": 0,
    "skills_failed": 0
  },
  "meta": {
    "runtime_seconds": 24.5,
    "pages_discovered": 12,
    "pages_selected": 5,
    "pages_crawled": 5,
    "pages_successfully_audited": 5,
    "page_types_covered": ["homepage", "products", "about", "contact"]
  },
  "preflight": {
    "scanned": true,
    "status_code": 200,
    "http_version": "HTTP/2",
    "hsts": true,
    "csp": true,
    "x_frame_options": "DENY",
    "security_score": 100
  }
}
```

---

## License

WebLens is licensed under the [MIT License](file:///C:/Users/rg060/Desktop/Work/Website/Hackathon/Web%20Lens/WebLens/agent/LICENSE).
