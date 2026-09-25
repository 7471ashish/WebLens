# WebLens — Backend + Chrome Extension Build Brief

Paste this whole document into the coding agent as the task context. It assumes the agent has (or you attach) the `agent/` folder from `WebLens-main.zip` unmodified.

---

## 1. What already exists (do not rewrite)

The repo root `agent/` contains a working, tested, offline-capable Python audit pipeline: `run_master_audit.py` (CLI entrypoint), `llm_client.py` (Groq/OpenRouter/OpenAI wrapper), and five domain skills under `skills/*/scripts/`. It is invoked today as:

```bash
python run_master_audit.py https://example.com/
```

and writes a report to `output.json` matching a fixed schema (site, audited_at, summary, findings[], coverage, meta). Full behavioral spec is in `agent/README.md` — read it first.

**`agent/` is strictly read-only. Do not edit, patch, fork, or restructure anything inside it — not `run_master_audit.py`, not `llm_client.py`, not any file under `skills/`. Copy it into the backend image/build verbatim.**

This constraint is achievable without any code change, because of how the existing code already behaves:
- `run_master_audit.py` calls `LLMClient()` with no argument (line ~147). `LLMClient.__init__` (in `llm_client.py`, line ~144) falls back to `os.environ.get("GROQ_API_KEY")` when no explicit key is passed. So a per-request key is delivered purely by **setting the `GROQ_API_KEY` environment variable on the subprocess the backend spawns** (see §3.1) — the agent code reads it exactly as designed, zero edits needed.
- The module uses process-global state (`_GLOBAL_VALIDATION` in `llm_client.py`, `sys.path.insert` in `run_master_audit.py`) and launches Playwright/Chromium per run. Running two audits concurrently *in one Python process* is unsafe — this is precisely why the backend isolates every job in its own subprocess (§3.1) instead of importing the agent as a library. Subprocess isolation gets you per-request keys *and* concurrency safety with the agent completely untouched.

If any backend requirement seems to demand an agent-side change, treat that as a sign the backend design is wrong, not a reason to touch `agent/` — stop and reconsider the backend approach instead.

---

## 2. Product flow (already decided, build to this exactly)

1. User installs the Chrome extension and opens it (popup or side panel — your call, see §4).
2. User enters their Groq Cloud API key once; it's saved in `chrome.storage.local` and reused on future opens.
3. User clicks a single "Audit this page" button.
4. Extension reads the current active tab's URL (`chrome.tabs.query({active:true,currentWindow:true})`) — editable by the user before submitting, not forced.
5. Extension POSTs `{ url, groq_api_key }` to the backend.
6. Backend starts the audit job and returns a `job_id` immediately (never blocks the HTTP request for the full audit duration — audits can take up to 240s).
7. Extension polls for job status/result and renders findings when done.

---

## 3. Backend — build this

**Stack:** FastAPI + Uvicorn, Python, wrapping the existing agent as a subprocess.

**Chosen approach: subprocess-per-job**, containerized. (We deliberately did *not* choose an in-process worker pool for v1 — see §7 if asked to upgrade later.)

### 3.1 Endpoints

- `POST /audits`
  - Body: `{ "url": string, "groq_api_key": string }`
  - Validates: `url` is http(s), not empty; `groq_api_key` is non-empty.
  - **SSRF guard**: reject/resolve-and-block URLs pointing at localhost, `127.0.0.0/8`, `169.254.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and any non-http(s) scheme.
  - Creates a job record `{job_id, status: "queued", created_at}` in an in-memory dict (fine for v1; swap for Redis/SQLite only if asked).
  - Enforces a global concurrency cap (`MAX_CONCURRENT_JOBS`, e.g. 3) via `asyncio.Semaphore`; jobs beyond the cap stay `"queued"` until a slot frees up.
  - Kicks off the job as a background `asyncio.Task` (or via FastAPI `BackgroundTasks`) that:
    1. Creates a fresh temp directory (`tempfile.mkdtemp()`) as the subprocess's CWD, so `output.json` from concurrent jobs never collide.
    2. Spawns `run_master_audit.py <url>` via `asyncio.create_subprocess_exec`, passing `env={**minimal_env, "GROQ_API_KEY": groq_api_key}` — **do not** set this key in the parent process's environment, only in the child's.
    3. Streams/collects stdout; parse lines for progress signal (the script logs skill status — use these to update `job["progress"]` opportunistically; don't fail the job if parsing finds nothing).
    4. Applies a hard timeout slightly above the agent's own 240s ceiling (e.g. 260s) via `asyncio.wait_for`; on timeout, kill the subprocess and mark job `"failed"` with a clear error.
    5. On exit code 0, reads `output.json` from the temp dir, parses it, stores it as `job["result"]`, sets `status="done"`.
    6. On nonzero exit or unparseable output, sets `status="failed"` with `job["error"]` = captured stderr tail (last ~2KB) — but **never include the API key in any stored/logged error text**.
    7. Deletes the temp directory after the result is read (or on a delay/cleanup pass — your call).
  - Response: `{ "job_id": "..." }` (HTTP 202).

- `GET /audits/{job_id}`
  - Returns `{ status: "queued"|"running"|"done"|"failed", progress?: {...}, result?: <output.json content>, error?: string }`.
  - 404 if unknown job_id.

- `GET /health` — trivial liveness check (also verify Playwright/Chromium is importable/launchable at startup, not per-request).

### 3.2 Security / hygiene requirements (non-negotiable, not just nice-to-have)
- Never log the `groq_api_key` value anywhere (stdout, error messages, stored job records).
- Never write the key to disk except transiently as the subprocess's own env (which the OS clears when it exits).
- CORS: restrict `Access-Control-Allow-Origin` to the extension's origin (`chrome-extension://<your-extension-id>`) once you have a stable ID — don't leave it wide open (`*`) in anything beyond local dev.
- Rate limit `POST /audits` per client (simple in-memory token bucket keyed by an extension-generated install ID sent in a header, or by IP as a fallback) — each audit costs the user's Groq credits and your server's CPU.
- Job records and temp `output.json` files should not persist indefinitely — expire/evict jobs from the in-memory store after e.g. 1 hour.

### 3.3 Dockerization
- Base image: `mcr.microsoft.com/playwright/python` (has Chromium + all OS-level shared libs preinstalled — don't hand-roll this on `python:slim`, you'll spend hours chasing missing `libnss3`/`libatk` etc.).
- Copy in `agent/` and the new backend code; `pip install -r agent/requirements.txt` plus `fastapi`, `uvicorn`.
- Do **not** bake a real Groq key into the image or a committed `.env` — keys arrive per-request only.
- Expose the FastAPI port; run with `uvicorn main:app --host 0.0.0.0`.
- Single `docker-compose.yml` (or just a `Dockerfile` + run command) is enough for v1 — no need for multi-service orchestration yet.

### 3.4 File layout to create
```
backend/
  main.py            # FastAPI app: endpoints from §3.1
  job_runner.py       # subprocess spawn/monitor logic
  ssrf_guard.py        # URL validation helper
  requirements.txt     # fastapi, uvicorn, (+ agent/requirements.txt contents or pip install -r both)
  Dockerfile
  .dockerignore
agent/                 # copied in byte-for-byte, zero modifications — see §1
```

---

## 4. Chrome extension — build this

**Manifest V3.**

### 4.1 Structure
```
extension/
  manifest.json
  popup.html / popup.js / popup.css      (or side_panel.html/js if you choose sidePanel)
  background.js                          # service worker: polling loop, notifications
  options.html / options.js              # optional: dedicated place to (re)set the Groq key
  icons/
```

### 4.2 Permissions
- `"permissions": ["activeTab", "storage", "notifications"]`
- `"host_permissions": ["https://<your-backend-domain>/*"]`
- Do **not** request `<all_urls>` or scripting permissions — the extension never touches page content itself; the backend does the crawling server-side.

### 4.3 Behavior
- On first open (or via an options page), let the user paste their Groq API key; save via `chrome.storage.local.set({groq_api_key})`. Mask it in the input (`type=password`) with a show/hide toggle.
- Main view: URL field prefilled from `chrome.tabs.query({active:true,currentWindow:true})` but editable, plus an "Audit this page" button.
- On click: `POST {backend}/audits` with `{url, groq_api_key}`. Disable the button, show a loading state.
- Persist the returned `job_id` in `chrome.storage.local` immediately (so reopening the popup mid-run resumes polling instead of losing track of the job).
- Poll `GET {backend}/audits/{job_id}` every ~2s (via `background.js` using `chrome.alarms`, since MV3 service workers can be killed — don't rely on a plain `setInterval` in the popup, it dies when the popup closes).
- On `status: "done"`, render results (see §4.4) and fire a `chrome.notifications.create` if the popup/side panel isn't currently open.
- On `status: "failed"`, show the error message plainly (never surface the API key even if it somehow appears in an error string — treat that as a backend bug to fix, not something to filter client-side).
- Prefer `chrome.sidePanel` over a popup if you want the report to stay visible while the user reads the audited site — popups close on focus loss, which is awkward for a 10–30s+ wait. If you use a popup, the `chrome.storage`-backed job resume above is what makes that tolerable.

### 4.4 Rendering the report
`output.json`'s schema (from `agent/README.md` §11) gives you: `summary.{total_findings,critical,high,medium,low}`, and `findings[]` each with `id, title, severity, evidence, suggested_action.{summary,priority}, confidence, evidence_tier`.
- Render a summary strip (counts per severity, color-coded: critical=red, high=orange, medium=yellow, low=gray/blue).
- Render findings as a list, grouped or sortable by severity.
- Each finding: title + severity badge, collapsible to show `evidence` and `suggested_action.summary`.
- Visually distinguish `evidence_tier: tier_1`/`tier_2` (hard, measured evidence) from `tier_4` (heuristic) — e.g. a small "measured" vs "heuristic" tag — since the backend's whole design principle is not conflating the two.
- Show `coverage` (skills_run/ok/partial/failed) somewhere unobtrusive (e.g. a footer) so the user can tell if a skill degraded.

---

## 5. Explicit non-goals for v1
- No user accounts/auth beyond the per-request Groq key.
- No persistent database — in-memory job store is fine.
- No in-process worker pool / Redis queue (see §7 — later upgrade, not v1).
- **No edits to anything under `agent/`, including `run_master_audit.py`, `llm_client.py`, or any `skills/*/scripts/` file — this is a hard constraint, not a preference.**
- No changes to the `output.json` schema.

---

## 6. Acceptance criteria
- `docker build` + `docker run` brings up the backend with no manual steps beyond providing the container port.
- `curl -X POST /audits -d '{"url":"https://example.com","groq_api_key":"<real or placeholder key>"}'` returns a `job_id` within ~1s.
- Polling `GET /audits/{job_id}` transitions `queued → running → done` and the final payload matches the schema in `agent/README.md` §11.
- Submitting a `localhost`/private-IP URL is rejected with a 4xx, not silently crawled.
- The extension: install unpacked → enter a Groq key → click audit on any real webpage → see a rendered, severity-colored findings list within the agent's normal 10–30s runtime.
- No Groq API key appears in any backend log line, stored job record, or error message returned to the client.

---

## 7. Only if asked to optimize later (do not build this in v1)
Replace subprocess-per-job with a small fixed pool of long-lived worker processes (e.g. `ProcessPoolExecutor` or `arq`/Redis) that import the agent modules once and call an in-process function. **Note this would require refactoring `run_master_audit.py`'s `main()` into an importable async function — i.e. it directly conflicts with the "no edits to `agent/`" rule above.** Only pursue this if the user explicitly lifts that constraint; until then, subprocess-per-job is not just the v1 choice but the only approach compatible with the no-edit rule.