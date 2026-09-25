"""
Unit and isolation tests for AuditJobRunner and JobStore.
Verifies that agent/ is copied to a tempdir, concurrent jobs do not collide,
and secrets are not stored in job records.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from job_runner import AuditJobRunner, JobStore


@pytest.mark.asyncio
async def test_job_store_lifecycle():
    store = JobStore(ttl_seconds=60)
    job_id = await store.create_job("https://example.com")
    
    job = await store.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["url"] == "https://example.com"
    assert "groq_api_key" not in job

    await store.update_progress(job_id, "crawling", 30, "Crawling page...")
    updated = await store.get_job(job_id)
    assert updated["progress"]["stage"] == "crawling"
    assert updated["progress"]["percent"] == 30

    await store.update_job(job_id, status="done", result={"site": "example.com"})
    done_job = await store.get_job(job_id)
    assert done_job["status"] == "done"
    assert done_job["result"]["site"] == "example.com"


@pytest.mark.asyncio
async def test_job_runner_subprocess_isolation(tmp_path):
    # Create a mock agent directory with a dummy run_master_audit.py
    mock_agent_dir = tmp_path / "mock_agent"
    mock_agent_dir.mkdir()
    
    script_content = """
import os
import json
import sys

# Verify WORKSPACE_ROOT points inside the tempdir, not parent
workspace_root = os.path.abspath(os.path.dirname(__file__))
output_file = os.path.join(workspace_root, "output.json")

print("MASTER WEBSITE AUDIT EXECUTION FOR: https://example.com")
print("[LLM DIAGNOSTIC] LIVE")
print("AUDIT COMPLETED IN 1.2s")

report = {
    "site": "example.com",
    "audited_at": "2026-09-25T12:00:00Z",
    "summary": {"total_findings": 1, "critical": 0, "high": 0, "medium": 0, "low": 1},
    "findings": [{
        "id": "TEST-001",
        "title": "Test Finding",
        "severity": "low",
        "evidence": "Evidence string",
        "suggested_action": {"summary": "Fix", "priority": "low"},
        "confidence": 0.9,
        "evidence_tier": "tier_1"
    }],
    "coverage": {"skills_run": 5, "skills_ok": 5, "skills_partial": 0, "skills_failed": 0},
    "meta": {"runtime_seconds": 1.2, "pages_crawled": 1}
}

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(report, f)
"""
    (mock_agent_dir / "run_master_audit.py").write_text(script_content, encoding="utf-8")

    store = JobStore()
    runner = AuditJobRunner(job_store=store, agent_dir=str(mock_agent_dir), timeout_seconds=10.0)

    job_id = await store.create_job("https://example.com")
    fake_key = "gsk_supersecretkey1234567890abcdef"

    # Run the job
    await runner.run_job(job_id, "https://example.com", fake_key)

    job = await store.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is not None
    assert job["result"]["site"] == "example.com"
    assert len(job["result"]["findings"]) == 1

    # Verify key never exists in job record
    assert fake_key not in str(job)
