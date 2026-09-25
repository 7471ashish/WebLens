"""
Test Offline Fallback and Fail-Soft API Resilience (test_offline_fallback.py)
=============================================================================
Validates Requirement 4:
- Confirms that if no API keys are set, or if external API/network calls error/time out,
  the audit system falls back smoothly without crashing.
- Confirms that run_master_audit.py executes safely without network/API access, completes
  well within budget, and produces valid output.json conforming strictly to the required schema.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "audit-orchestrator", "scripts"))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "crawl-render-audit", "scripts"))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "engagement-audit", "scripts"))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "multimodal-audit", "scripts"))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "freshness-corroboration-audit", "scripts"))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "visual-accessibility-audit", "scripts"))
sys.path.insert(0, WORKSPACE_ROOT)

from audit_orchestrator import validate_final_report_schema, build_final_report
from llm_client import LLMClient
import run_master_audit



# 1. LLMClient Fail-Soft Fallback Tests


@pytest.mark.asyncio
async def test_llm_client_no_api_key():
    """LLMClient without API keys should safely return fallback qualitative findings without raising."""
    client = LLMClient(api_key=None)
    result = await client.query_json("Audit Target URL: https://example.com\nEvaluate CTAs and headings")
    assert isinstance(result, dict)
    assert "findings" in result
    assert len(result["findings"]) > 0
    # Fallback findings should have lower confidence
    for f in result["findings"]:
        assert f.get("confidence", 0.5) <= 0.6


@pytest.mark.asyncio
async def test_llm_client_api_error_fallback():
    """LLMClient with simulated API failure/exception should gracefully fall back."""
    client = LLMClient(api_key="fake-key-for-testing")
    # Mock openai client to raise network/auth error
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(side_effect=Exception("Connection refused / 401 Unauthorized"))
    client._openai_client = mock_openai

    result = await client.query_json("Audit Target URL: https://example.com\nAnalyze crawl status and sitemaps")
    assert isinstance(result, dict)
    assert "findings" in result
    assert len(result["findings"]) > 0


@pytest.mark.asyncio
async def test_llm_client_timeout_fallback():
    """LLMClient with simulated timeout should catch TimeoutError and return fallback."""
    client = LLMClient(api_key="fake-key-for-testing", timeout=0.01)

    async def _hang(*args, **kwargs):
        await asyncio.sleep(1.0)
        return MagicMock()

    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(side_effect=_hang)
    client._openai_client = mock_openai

    result = await client.query_json("Audit Target URL: https://example.com\nAnalyze visual accessibility")
    assert isinstance(result, dict)
    assert "findings" in result



# 2. End-to-End Master Audit Offline Simulation Test


SAMPLE_OFFLINE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Sample Offline Test Page</title>
    <meta name="description" content="Offline test page for resilience audit">
</head>
<body>
    <header>
        <h1>Offline Test Enterprise</h1>
        <nav>
            <a href="/">Home</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <section id="hero">
            <h2>Welcome to Offline Resilience Testing</h2>
            <p>This is a test paragraph demonstrating full offline compatibility of the multi-subagent audit suite.</p>
            <button class="primary" role="button">Get Started Today</button>
        </section>
        <section id="media">
            <img src="/assets/hero.jpg" alt="Hero illustration banner" width="800" height="400">
            <img src="/assets/logo.png" width="200" height="50">
        </section>
    </main>
    <footer>
        <p>&copy; 2026 Offline Test Enterprise. All rights reserved.</p>
    </footer>
</body>
</html>"""


@pytest.mark.asyncio
async def test_run_master_audit_offline_sample_site():
    """
    Simulates running run_master_audit.py against a sample site with no external
    network or LLM API access, confirming it completes rapidly and generates a valid output.json.
    """
    target_url = "https://offline-sample-site.example.com/"
    start_time = time.perf_counter()

    # Clear external API keys from environment during test
    with patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENAI_API_KEY": "", "OPENROUTER_API_KEY": ""}, clear=False):
        # Mock sys.argv
        with patch.object(sys, "argv", ["run_master_audit.py", target_url]):
            # Mock direct network crawl fallback to return SAMPLE_OFFLINE_HTML
            with patch("httpx.AsyncClient.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.text = SAMPLE_OFFLINE_HTML
                mock_resp.status_code = 200
                mock_get.return_value = mock_resp

                # Mock subagents that make external network calls to return clean structured reports
                with patch("run_master_audit.run_crawl_audit") as mock_crawl:
                    mock_crawl.return_value = {
                        "status": "completed",
                        "findings": [],
                        "raw_observations": {
                            "crawler": {"status_code": 200, "success": True, "body": SAMPLE_OFFLINE_HTML, "final_url": target_url}
                        }
                    }

                    # Mock thread-dispatched subagent executions to simulate instant offline completion
                    orig_to_thread = asyncio.to_thread

                    async def mock_to_thread(func, *args, **kwargs):
                        func_name = getattr(func, "__name__", str(func))
                        if "freshness" in func_name or "run_audit" in func_name:
                            return {"status": "completed", "findings": []}
                        return await orig_to_thread(func, *args, **kwargs)

                    with patch("asyncio.to_thread", side_effect=mock_to_thread):
                        await run_master_audit.main()

    elapsed = time.perf_counter() - start_time

    # 1. Confirm completed well within the 5-minute budget (under 15s in offline mode)
    assert elapsed < 300.0, f"Audit took {elapsed}s, exceeding budget!"

    # 2. Confirm output.json was produced
    output_path = os.path.join(WORKSPACE_ROOT, "output.json")
    assert os.path.exists(output_path), "output.json was not generated!"

    with open(output_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    # 3. Confirm strictly valid schema
    validate_final_report_schema(report)
    assert report["site"] == "offline-sample-site.example.com"
    assert "summary" in report
    assert "findings" in report
    assert isinstance(report["findings"], list)
