"""
Unit tests for FastAPI endpoints.
"""

import sys
import os
import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "max_concurrent_jobs" in data


def test_create_audit_ssrf_blocked():
    payload = {
        "url": "http://127.0.0.1:8000/internal",
        "groq_api_key": "gsk_testdummykey1234567890abcdef",
    }
    response = client.post("/audits", json=payload)
    assert response.status_code == 400
    assert "Invalid target URL" in response.json()["detail"]


def test_create_audit_invalid_empty_url():
    payload = {
        "url": "",
        "groq_api_key": "gsk_testdummykey1234567890abcdef",
    }
    response = client.post("/audits", json=payload)
    assert response.status_code == 422


def test_get_nonexistent_job():
    response = client.get("/audits/nonexistent-uuid-1234")
    assert response.status_code == 404


def test_stream_nonexistent_job():
    response = client.get("/audits/nonexistent-uuid-1234/stream")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_completed_job():
    from main import job_store
    job_id = await job_store.create_job("https://example.com")
    await job_store.update_job(
        job_id=job_id,
        status="done",
        result={"site": "https://example.com", "findings": []},
    )

    with client.stream("GET", f"/audits/{job_id}/stream") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        lines = [line for line in response.iter_lines() if line]
        data_lines = [l for l in lines if l.startswith("data:")]
        assert len(data_lines) == 1
        import json
        payload = json.loads(data_lines[0].replace("data: ", ""))
        assert payload["status"] == "done"
        assert payload["job_id"] == job_id
        assert payload["result"]["site"] == "https://example.com"

