# WebLens Backend Service

A containerized, asynchronous FastAPI service wrapping the multi-skill WebLens audit pipeline into an isolated subprocess runner with SSRF protection, rate limiting, and real-time progress streaming.

---

## Features
- **Subprocess-per-Job Isolation**: Each audit job executes in a completely isolated temp directory copy of `agent/`. Concurrent jobs never collide on `output.json`.
- **Zero Modifications to Agent Codebase**: The underlying `agent/` remains strictly unmodified and read-only.
- **Strict Key Hygiene**: Groq Cloud API keys are passed solely through subprocess child environment variables and never logged or persisted. All stderr traces are scrubbed via regex sanitizers.
- **SSRF Hardening**: Validates HTTP/HTTPS schemes, performs DNS resolution, and rejects private subnets (RFC 1918), link-local addresses, AWS metadata (`169.254.169.254`), and loopback IPs.
- **Concurrency & Rate Limiting**: Global concurrency limit via `asyncio.Semaphore` (default: 3 concurrent jobs) and sliding window rate limiter per client/IP.
- **Progress Tracking**: Opportunistically parses stdout from the agent pipeline and surfaces real-time progress milestones (0% to 100%).

---

## API Endpoints

### 1. `GET /health`
Liveness check verifying Playwright availability and concurrency capacity.
```bash
curl http://localhost:8000/health
```

### 2. `POST /audits` (HTTP 202 Accepted)
Submits a new audit job.
```bash
curl -X POST http://localhost:8000/audits \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "groq_api_key": "gsk_your_groq_api_key"
  }'
```
Response:
```json
{
  "job_id": "4b689a77-3e0e-4fa0-8fca-2cb9e86338b1",
  "status": "queued"
}
```

### 3. `GET /audits/{job_id}`
Polls status, progress, findings, or error.
```bash
curl http://localhost:8000/audits/4b689a77-3e0e-4fa0-8fca-2cb9e86338b1
```

---

## Local Development & Testing

### Running with Python
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Running Backend Tests
```bash
pytest backend/tests -v
```

---

## Running with Docker
```bash
cd ..
docker compose up --build
```
Or directly with Docker:
```bash
docker build -f backend/Dockerfile -t weblens-backend .
docker run -p 8000:8000 weblens-backend
```

---

## Deploying to Render & Keep-Alive Setup

### 1. Render Deployment (Blueprint or Web Service)
1. Push this repository to GitHub (`git push origin main`).
2. Go to [Render Dashboard](https://dashboard.render.com).
3. Click **New +** -> **Blueprint** and select your GitHub repo `WebLens` (it will auto-detect [`render.yaml`](../render.yaml)).
   - Or click **New +** -> **Web Service**:
     - Environment: **Docker**
     - Root Directory: `.`
     - Dockerfile Path: `backend/Dockerfile`
     - Plan: **Free**
     - Health Check Path: `/health`
     - Env Var `MAX_CONCURRENT_JOBS`: `2`
     - Env Var `KEEP_ALIVE_URL`: `https://your-service-name.onrender.com/health` (optional internal ping)

### 2. Cron Job Setup (Never Sleep / Keep-Alive)
Render free tier puts web services to sleep after 15 minutes of inactivity. To prevent this, two automatic methods are provided:

#### Option A: GitHub Actions Cron (Built-in)
The workflow [`.github/workflows/keep_alive.yml`](../.github/workflows/keep_alive.yml) runs every 10 minutes automatically.
1. In your GitHub repository, go to **Settings** -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret** or **New repository variable**.
3. Name: `RENDER_BACKEND_URL`
4. Value: `https://your-app-name.onrender.com` (your deployed Render service URL).

#### Option B: Free External Cron (cron-job.org / UptimeRobot)
1. Go to [cron-job.org](https://cron-job.org) or [uptimerobot.com](https://uptimerobot.com).
2. Create a free HTTP monitor/cron pointing to: `https://your-app-name.onrender.com/health`
3. Schedule: Every **10 minutes** or **14 minutes**.

