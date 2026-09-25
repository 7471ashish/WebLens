"""
WebLens FastAPI Backend Service.
Provides RESTful audit scheduling, asynchronous job monitoring, SSRF validation,
and secure Playwright/Chromium execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Optional

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import json
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from job_runner import AuditJobRunner, JobStore
from rate_limiter import SlidingWindowRateLimiter
from sanitizer import SecretSanitizingFilter, mask_key, sanitize_text
from ssrf_guard import SSRFValidationError, validate_target_url

# Configure Logging with Secret Redaction
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
root_logger = logging.getLogger()
root_logger.addFilter(SecretSanitizingFilter())

logger = logging.getLogger("weblens.api")

# Initialize Singletons
job_store = JobStore()
job_runner = AuditJobRunner(job_store=job_store)
rate_limiter = SlidingWindowRateLimiter(max_requests=15, window_seconds=600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation
    logger.info("WebLens Audit Backend starting up...")
    logger.info(f"Agent Source Directory: {job_runner.agent_dir}")
    logger.info(f"Max Concurrent Audit Jobs: {job_runner.semaphore._value}")
    
    # Verify Playwright availability
    try:
        from playwright.async_api import async_playwright
        logger.info("Playwright Python package is available.")
    except ImportError:
        logger.warning("Playwright is NOT installed in this Python environment!")

    yield
    logger.info("WebLens Audit Backend shutting down...")


app = FastAPI(
    title="WebLens Audit Service",
    description="Asynchronous website audit API wrapping the multi-skill agent pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Configuration
# Allow Chrome Extension origins (chrome-extension://<id>) as well as local testing
allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "")
allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
if not allowed_origins:
    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^chrome-extension://[a-z0-9]+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AuditRequest(BaseModel):
    url: str = Field(..., description="Target website HTTP or HTTPS URL to audit.")
    groq_api_key: str = Field(..., min_length=1, description="Groq Cloud API key for reasoning.")

    @field_validator("url")
    @classmethod
    def check_url_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("URL cannot be empty.")
        return cleaned

    @field_validator("groq_api_key")
    @classmethod
    def check_key_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("groq_api_key cannot be empty.")
        return cleaned

    def __repr__(self) -> str:
        # Never expose raw key in string representations
        return f"AuditRequest(url='{self.url}', groq_api_key='{mask_key(self.groq_api_key)}')"


class AuditAcceptedResponse(BaseModel):
    job_id: str
    status: str = "queued"


class AuditStatusResponse(BaseModel):
    job_id: str
    status: str
    url: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


@app.get("/health", summary="Liveness & Readiness Healthcheck")
async def health():
    playwright_ready = False
    try:
        import playwright
        playwright_ready = True
    except ImportError:
        pass

    return {
        "status": "healthy",
        "playwright_ready": playwright_ready,
        "max_concurrent_jobs": job_runner.semaphore._value,
        "active_jobs_remaining_slots": job_runner.semaphore._value,
        "agent_dir_exists": os.path.isdir(job_runner.agent_dir),
    }


@app.post(
    "/audits",
    response_model=AuditAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit New Audit Job",
)
async def create_audit(
    payload: AuditRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    x_install_id: Optional[str] = Header(None, alias="X-Install-ID"),
):
    # 1. Rate Limiting Check
    client_id = x_install_id or request.client.host if request.client else "anonymous"
    rate_res = rate_limiter.check(client_id)
    if not rate_res.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Please wait {rate_res.retry_after_seconds}s before submitting again.",
            headers={"Retry-After": str(rate_res.retry_after_seconds)},
        )

    # 2. SSRF Protection & Scheme Validation
    allow_private = os.environ.get("AUDIT_ALLOW_LOCAL", "").lower() in ("1", "true")
    try:
        validated_url = validate_target_url(payload.url, allow_private=allow_private)
    except SSRFValidationError as ssrf_err:
        logger.warning(f"SSRF rejection for URL '{payload.url}': {ssrf_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target URL: {ssrf_err}",
        )

    # 3. Create Job Record
    job_id = await job_store.create_job(target_url=validated_url)
    logger.info(f"Audit job {job_id} accepted for URL '{validated_url}' from client {client_id}")

    # 4. Schedule Subprocess Runner as Background Task
    background_tasks.add_task(
        job_runner.run_job,
        job_id=job_id,
        target_url=validated_url,
        groq_api_key=payload.groq_api_key,
    )

    return AuditAcceptedResponse(job_id=job_id, status="queued")


@app.get(
    "/audits/{job_id}",
    response_model=AuditStatusResponse,
    summary="Get Audit Job Status & Findings",
)
async def get_audit(job_id: str):
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit job '{job_id}' not found.",
        )
    return AuditStatusResponse(**job)


@app.get(
    "/audits/{job_id}/stream",
    summary="Stream Major Audit Events via Server-Sent Events (SSE)",
)
async def stream_audit(job_id: str, request: Request):
    """
    Server-Sent Events endpoint emitting only major lifecycle events.
    Replaces polling with a single persistent stream.
    """
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit job '{job_id}' not found.",
        )

    async def event_generator():
        last_status = None
        last_stage = None

        while True:
            if await request.is_disconnected():
                logger.info(f"SSE client disconnected for job {job_id}")
                break

            current_job = await job_store.get_job(job_id)
            if not current_job:
                yield f"event: error\ndata: {json.dumps({'error': 'Job expired or deleted'})}\n\n"
                break

            current_status = current_job.get("status")
            progress = current_job.get("progress") or {}
            current_stage = progress.get("stage")

            # Emits strictly when a major event happens (status change or stage change)
            if current_status != last_status or current_stage != last_stage:
                last_status = current_status
                last_stage = current_stage

                event_data = {
                    "job_id": job_id,
                    "status": current_status,
                    "progress": progress,
                    "result": current_job.get("result") if current_status == "done" else None,
                    "error": current_job.get("error") if current_status == "failed" else None,
                }
                yield f"data: {json.dumps(event_data)}\n\n"

                # Terminal state reached: finish stream
                if current_status in ("done", "failed"):
                    break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    sanitized_msg = sanitize_text(str(exc))
    logger.error(f"Unhandled server error: {sanitized_msg}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred.", "error": sanitized_msg},
    )
