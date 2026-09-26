"""
WebLens Subprocess Job Runner.
Manages isolated execution of the agent audit pipeline, progress tracking,
concurrency limits, secret sanitization, and output extraction.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import shutil
import sys
import tempfile
import subprocess
import threading
import time
from typing import Any, Callable, Optional
import uuid

from sanitizer import sanitize_text

logger = logging.getLogger("weblens.job_runner")

# Maximum concurrent audit jobs
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "3"))
# Audit execution hard timeout in seconds (agent has 240s ceiling)
JOB_TIMEOUT_SECONDS = float(os.environ.get("JOB_TIMEOUT_SECONDS", "260.0"))
# In-memory job record retention in seconds (1 hour default)
JOB_TTL_SECONDS = float(os.environ.get("JOB_TTL_SECONDS", "3600.0"))

# Locate source agent directory (default to ../agent relative to this file)
DEFAULT_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "agent")
)
AGENT_SOURCE_DIR = os.environ.get("AGENT_SOURCE_DIR", DEFAULT_AGENT_DIR)
# Active subprocesses mapped by job_id for immediate on-the-spot termination
ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
TERMINATED_JOBS: set[str] = set()


class JobStore:
    """In-memory thread/asyncio-safe store for audit job metadata and results."""

    def __init__(self, ttl_seconds: float = JOB_TTL_SECONDS):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def create_job(self, target_url: str) -> str:
        job_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        job_data = {
            "job_id": job_id,
            "status": "queued",
            "url": target_url,
            "created_at": now_iso,
            "started_at": None,
            "finished_at": None,
            "progress": {
                "stage": "queued",
                "percent": 0,
                "message": "Job queued, waiting for available audit slot...",
            },
            "result": None,
            "error": None,
            "_created_ts": time.monotonic(),
        }
        async with self._lock:
            self._jobs[job_id] = job_data
            self._prune_expired_unlocked()
        return job_id

    async def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            # Return copy without internal tracking keys
            cleaned = dict(job)
            cleaned.pop("_created_ts", None)
            return cleaned

    async def update_job(self, job_id: str, **kwargs: Any) -> None:
        async with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)

    async def update_progress(
        self, job_id: str, stage: str, percent: int, message: str
    ) -> None:
        async with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["progress"] = {
                    "stage": stage,
                    "percent": max(0, min(100, percent)),
                    "message": message,
                }

    def _prune_expired_unlocked(self) -> None:
        now = time.monotonic()
        expired = [
            jid
            for jid, data in self._jobs.items()
            if (now - data.get("_created_ts", now)) > self._ttl_seconds
        ]
        for jid in expired:
            self._jobs.pop(jid, None)


def _run_subprocess_worker(
    cmd_args: list[str],
    cwd: str,
    env: dict[str, str],
    timeout_seconds: float,
    on_stdout_line: Optional[Callable[[str], None]] = None,
) -> tuple[int, list[str], str, bool]:
    """
    Synchronous worker executing the subprocess safely across any OS and any event loop.
    Immune to Windows SelectorEventLoop NotImplementedError.
    """
    proc = subprocess.Popen(
        cmd_args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    _job_id = env.get("WEBLENS_JOB_ID")
    if _job_id:
        ACTIVE_PROCESSES[_job_id] = proc

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout():
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                cleaned = line.strip()
                if cleaned:
                    stdout_lines.append(cleaned)
                    if on_stdout_line:
                        on_stdout_line(cleaned)
            proc.stdout.close()

    def read_stderr():
        if proc.stderr:
            for line in iter(proc.stderr.readline, ""):
                stderr_lines.append(line)
            proc.stderr.close()

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
            proc.wait(timeout=5.0)
        except Exception:
            pass

    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)

    stderr_text = "".join(stderr_lines)
    if _job_id:
        ACTIVE_PROCESSES.pop(_job_id, None)

    return proc.returncode or 0, stdout_lines, stderr_text, timed_out


class AuditJobRunner:
    """Orchestrates isolated subprocess execution with progress streaming and timeout guard."""

    def __init__(
        self,
        job_store: JobStore,
        agent_dir: str = AGENT_SOURCE_DIR,
        max_concurrent: int = MAX_CONCURRENT_JOBS,
        timeout_seconds: float = JOB_TIMEOUT_SECONDS,
    ):
        self.job_store = job_store
        self.agent_dir = agent_dir
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.timeout_seconds = timeout_seconds

        if not os.path.isdir(self.agent_dir):
            logger.warning(
                f"Agent source directory not found at {self.agent_dir}! "
                f"Ensure agent folder is properly located or AGENT_SOURCE_DIR is set."
            )

    async def run_job(self, job_id: str, target_url: str, groq_api_key: str) -> None:
        """
        Executes an audit job in an isolated subprocess.
        Must be scheduled as a background task.
        """
        logger.info(f"Job {job_id} waiting for concurrency slot (url: {target_url})")
        async with self.semaphore:
            if job_id in TERMINATED_JOBS:
                logger.info(f"Job {job_id} was terminated before acquiring execution slot.")
                return

            await self._execute_audit(job_id, target_url, groq_api_key)

    async def _execute_audit(
        self, job_id: str, target_url: str, groq_api_key: str
    ) -> None:
        started_iso = datetime.now(timezone.utc).isoformat()
        await self.job_store.update_job(
            job_id,
            status="running",
            started_at=started_iso,
        )
        await self.job_store.update_progress(
            job_id,
            stage="starting",
            percent=5,
            message="Initializing isolated audit sandbox...",
        )

        temp_dir = tempfile.mkdtemp(prefix=f"weblens_{job_id[:8]}_")
        temp_agent_dir = os.path.join(temp_dir, "agent")

        try:
            # 1. Copy agent/ into temp_agent_dir for total subprocess isolation
            # This ensures WORKSPACE_ROOT in run_master_audit.py points inside temp_dir
            # preventing output.json file collisions across concurrent runs.
            t0 = time.monotonic()
            shutil.copytree(
                self.agent_dir,
                temp_agent_dir,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".pytest_cache", ".git", "venv", ".venv"
                ),
            )
            copy_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.info(f"Isolated agent sandbox for job {job_id} created in {copy_ms}ms")

            script_path = os.path.join(temp_agent_dir, "run_master_audit.py")
            if not os.path.isfile(script_path):
                raise FileNotFoundError(
                    f"run_master_audit.py not found in agent directory: {script_path}"
                )

            # 2. Build child environment: ONLY child subprocess receives GROQ_API_KEY
            child_env = dict(os.environ)
            clean_key = groq_api_key.strip()
            child_env["GROQ_API_KEY"] = clean_key
            child_env["PYTHONUNBUFFERED"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["WEBLENS_JOB_ID"] = job_id


            cmd_args = [sys.executable, "run_master_audit.py", target_url]
            if not clean_key or clean_key.lower() in ("offline", "placeholder", "none", "placeholder_or_offline_key", "test"):
                child_env["AUDIT_USE_LLM"] = "false"
                cmd_args.append("--no-llm")

            await self.job_store.update_progress(
                job_id,
                stage="crawling",
                percent=15,
                message="Starting Chromium browser and crawling target site...",
            )

            # 3. Launch subprocess using thread-safe worker to support Windows SelectorEventLoop
            loop = asyncio.get_running_loop()

            def on_line(line: str):
                try:
                    loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._parse_progress(job_id, line)
                    )
                except Exception:
                    pass

            return_code, stdout_lines, stderr_text, timed_out = await asyncio.to_thread(
                _run_subprocess_worker,
                cmd_args,
                temp_agent_dir,
                child_env,
                self.timeout_seconds,
                on_line,
            )
            if job_id in TERMINATED_JOBS:
                logger.info(f"Job {job_id} was terminated by user; aborting post-processing.")
                return
            if timed_out:
                logger.error(f"Job {job_id} timed out after {self.timeout_seconds}s.")
                await self.job_store.update_job(
                    job_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=f"Audit execution timed out (exceeded {self.timeout_seconds}s limit).",
                )
                return

            sanitized_stderr = sanitize_text(stderr_text[-2048:])  # Last 2KB

            # 4. Process Output
            output_json_path = os.path.join(temp_agent_dir, "output.json")
            if return_code == 0 and os.path.isfile(output_json_path):
                try:
                    with open(output_json_path, "r", encoding="utf-8") as f:
                        report_data = json.load(f)

                    await self.job_store.update_progress(
                        job_id,
                        stage="complete",
                        percent=100,
                        message="Audit completed successfully.",
                    )
                    await self.job_store.update_job(
                        job_id,
                        status="done",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        result=report_data,
                    )
                    logger.info(
                        f"Job {job_id} completed successfully with {len(report_data.get('findings', []))} findings"
                    )
                    return
                except Exception as parse_err:
                    logger.error(f"Job {job_id} failed to parse output.json: {parse_err}")
                    await self.job_store.update_job(
                        job_id,
                        status="failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error=f"Failed to parse audit results: {parse_err}",
                    )
                    return
            else:
                stdout_tail = "\n".join(stdout_lines[-10:]) if stdout_lines else ""
                err_msg = (
                    sanitized_stderr.strip()
                    or sanitize_text(stdout_tail).strip()
                    or f"Audit subprocess exited with code {return_code}"
                )
                logger.warning(f"Job {job_id} failed with exit code {return_code}: {err_msg[:200]}")
                await self.job_store.update_job(
                    job_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=err_msg,
                )

        except Exception as exc:
            logger.exception(f"Unexpected exception while running job {job_id}: {exc}")
            await self.job_store.update_job(
                job_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=sanitize_text(str(exc)),
            )

        finally:
            # 6. Secure cleanup of temp directory
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as clean_err:
                logger.warning(f"Could not remove temp dir {temp_dir}: {clean_err}")

    async def _parse_progress(self, job_id: str, line: str) -> None:
        """Opportunistically detect progress milestones from stdout."""
        line_lower = line.lower()
        if "master website audit execution for" in line_lower:
            await self.job_store.update_progress(
                job_id, "init", 10, "Initializing audit modules..."
            )
        elif "[llm diagnostic]" in line_lower:
            await self.job_store.update_progress(
                job_id, "diagnostic", 20, "Verifying AI reasoning connectivity..."
            )
        elif "crawl-render audit report" in line_lower or "pages discovered:" in line_lower:
            await self.job_store.update_progress(
                job_id, "crawled", 40, "Completed initial crawl and DOM hydration analysis..."
            )
        elif "[modules 2-5/5] executing per-page audit" in line_lower:
            await self.job_store.update_progress(
                job_id, "domain_skills", 60, "Running Engagement, Multimodal & Accessibility skills..."
            )
        elif "findings across pages" in line_lower:
            await self.job_store.update_progress(
                job_id, "aggregating", 80, "Aggregating findings and gating evidence tiers..."
            )
        elif "audit completed in" in line_lower or "standardized final report saved to" in line_lower:
            await self.job_store.update_progress(
                job_id, "finalizing", 95, "Synthesizing standardized report..."
            )
    async def terminate_job(self, job_id: str) -> bool:
        """
        Immediately stops the auditing process for job_id on the spot.
        Kills the child subprocess and any spawned Chromium browser tree forcefully.
        """
        TERMINATED_JOBS.add(job_id)
        proc = ACTIVE_PROCESSES.pop(job_id, None)

        if proc and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    proc.kill()
            except Exception as e:
                logger.warning(f"Error terminating process for job {job_id}: {e}")
                try:
                    proc.kill()
                except Exception:
                    pass

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.job_store.update_job(
            job_id,
            status="failed",
            finished_at=now_iso,
            error="Audit terminated by user.",
        )
        await self.job_store.update_progress(
            job_id,
            stage="terminated",
            percent=0,
            message="Audit terminated by user.",
        )
        logger.info(f"Job {job_id} successfully terminated on the spot by user request.")
        return True
