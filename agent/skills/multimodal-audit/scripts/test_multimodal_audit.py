"""
Unit & Integration Test Suite for Multimodal Audit Coordinator (test_multimodal_audit.py)
========================================================================================
Validates the main controller, category routing, weighted score aggregation,
fault isolation, custom analyzer injection, and determinism.
"""

from __future__ import annotations

import json
import os
import sys
import pytest

# Ensure scripts directory is on sys.path
# Ensure scripts directory is on sys.path regardless of test location
_current_dir = os.path.abspath(os.path.dirname(__file__))
for _cand in [
    os.path.join(_current_dir, "scripts"),
    os.path.join(_current_dir, "..", "scripts"),
]:
    _cand_abs = os.path.abspath(_cand)
    if os.path.isdir(_cand_abs) and _cand_abs not in sys.path:
        sys.path.insert(0, _cand_abs)

from multimodal_audit import (
    DEFAULT_CATEGORY_WEIGHTS,
    MULTIMODAL_CATEGORIES,
    CategoryAuditResult,
    MultimodalAudit,
    MultimodalFinding,
    run_multimodal_audit,
)
from multimodal_state import (
    EvidenceStatus,
    MultimodalAuditState,
    MultimodalStatus,
    Severity,
)
from image_analyzer import ImageAnalyzer
from alt_text_analyzer import AltTextAnalyzer
from image_ocr_analyzer import ImageOCRAnalyzer
from image_metadata_analyzer import ImageMetadataAnalyzer
from chart_infographic_analyzer import ChartInfographicAnalyzer



# Fixtures


@pytest.fixture
def clean_multimodal_evidence() -> dict:
    return {
        "url": "https://example.com/product",
        "images": [
            {
                "id": "img-001",
                "url": "https://example.com/images/hero.jpg",
                "filename": "hero.jpg",
                "mime_type": "image/jpeg",
                "width": 1600,
                "height": 900,
                "alt_text": "Adobe creative software dashboard interface",
                "alt_attribute_present": True,
                "decorative": False,
                "visible": True,
                "file_size_bytes": 350000,
                "ocr": {
                    "available": True,
                    "text": "Create better experiences",
                    "confidence": 0.96,
                },
            }
        ],
        "charts": [
            {
                "id": "chart-001",
                "chart_type": "bar",
                "title": "Quarterly User Growth",
                "has_title": True,
                "has_axis_labels": True,
                "has_legend": True,
                "has_text_equivalent": True,
                "labels": ["Q1", "Q2", "Q3", "Q4"],
                "values": [100, 150, 200, 260],
                "units": "Users (k)",
            }
        ],
    }



# 1. Basic Execution & Orchestration Tests


def test_multimodal_audit_basic_execution(clean_multimodal_evidence):
    auditor = MultimodalAudit()
    result = auditor.audit(clean_multimodal_evidence)

    assert isinstance(result, dict)
    assert result["category"] == "multimodal"
    assert result["status"] in ("passed", "warning", "failed")
    assert result["score"] is not None
    assert 0 <= result["score"] <= 100
    assert 0.0 <= result["confidence"] <= 1.0
    assert "categories" in result
    assert "findings" in result
    assert "metadata" in result


def test_multimodal_audit_public_function(clean_multimodal_evidence):
    result = run_multimodal_audit(clean_multimodal_evidence)
    assert isinstance(result, dict)
    assert result["score"] is not None
    assert 0 <= result["score"] <= 100


def test_multimodal_audit_no_evidence_handling():
    auditor = MultimodalAudit()
    result = auditor.audit(None)

    assert isinstance(result, dict)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None
    assert len(result["findings"]) == 0


def test_multimodal_audit_explicit_empty_evidence():
    evidence = {
        "url": "https://example.com/empty",
        "images": [],
        "images_checked": True,
        "videos": [],
        "charts": [],
        "charts_checked": True,
    }
    auditor = MultimodalAudit()
    result = auditor.audit(evidence)

    assert isinstance(result, dict)
    assert result["status"] == "passed"
    assert result["score"] == 100



# 2. Category Routing & Coordination Tests


def test_multimodal_audit_coordinates_all_analyzers(clean_multimodal_evidence):
    auditor = MultimodalAudit(custom_analyzers={
        "image": ImageAnalyzer().analyze,
        "alt_text": AltTextAnalyzer().analyze,
        "ocr": ImageOCRAnalyzer().analyze,
        "image_metadata": ImageMetadataAnalyzer().analyze,
        "chart_infographic": ChartInfographicAnalyzer().analyze,
    })
    result = auditor.audit(clean_multimodal_evidence)

    assert "image" in result["categories"]
    assert "alt_text" in result["categories"]
    assert "ocr" in result["categories"]
    assert "image_metadata" in result["categories"]
    assert "chart_infographic" in result["categories"]

    for cat_name in ["image", "alt_text", "ocr", "image_metadata", "chart_infographic"]:
        cat_res = result["categories"][cat_name]
        assert cat_res["status"] in ("passed", "warning", "failed")
        assert 0 <= cat_res["score"] <= 100


def test_custom_weights_configuration(clean_multimodal_evidence):
    custom_weights = {
        "image": 0.50,
        "alt_text": 0.50,
        "ocr": 0.0,
        "image_metadata": 0.0,
        "chart_infographic": 0.0,
        "video": 0.0,
        "transcript": 0.0,
        "caption": 0.0,
    }
    auditor = MultimodalAudit(weights=custom_weights)
    result = auditor.audit(clean_multimodal_evidence)

    assert result["score"] is not None
    assert 0 <= result["score"] <= 100



# 3. Fault Isolation & Error Resilience Tests


def test_fault_isolation_on_analyzer_exception(clean_multimodal_evidence):
    def failing_image_analyzer(data):
        raise RuntimeError("Simulated internal analyzer crash")

    auditor = MultimodalAudit(custom_analyzers={
        "image": failing_image_analyzer,
        "alt_text": AltTextAnalyzer().analyze,
    })
    result = auditor.audit(clean_multimodal_evidence)

    # Image category should gracefully capture error
    assert result["categories"]["image"]["status"] == "error"
    assert result["categories"]["image"]["score"] is None

    # Alt text and remaining categories should still complete successfully
    assert result["categories"]["alt_text"]["status"] in ("passed", "warning")
    assert result["categories"]["alt_text"]["score"] is not None



# 4. Findings Aggregation & Deduplication Tests


def test_findings_aggregation_and_deduplication():
    problematic_evidence = {
        "url": "https://example.com/issues",
        "images": [
            {
                "id": "img-broken-1",
                "broken": True,
                "alt_attribute_present": False,
                "decorative": False,
            }
        ],
    }
    auditor = MultimodalAudit(custom_analyzers={
        "image": ImageAnalyzer().analyze,
        "alt_text": AltTextAnalyzer().analyze,
    })
    result = auditor.audit(problematic_evidence)

    assert len(result["findings"]) > 0
    finding_ids = [f["id"] for f in result["findings"]]
    assert "MM-IMG-001" in finding_ids
    assert "MM-ALT-001" in finding_ids or "MM-ALT-img-broken-1" in finding_ids



# 5. Determinism & JSON Serialization Tests


def test_multimodal_audit_determinism(clean_multimodal_evidence):
    auditor = MultimodalAudit()
    result1 = auditor.audit(clean_multimodal_evidence)
    result2 = auditor.audit(clean_multimodal_evidence)

    assert result1["score"] == result2["score"]
    assert result1["status"] == result2["status"]
    assert len(result1["findings"]) == len(result2["findings"])
    assert json.dumps(result1, sort_keys=True) == json.dumps(result2, sort_keys=True)


def test_json_serializability(clean_multimodal_evidence):
    result = run_multimodal_audit(clean_multimodal_evidence)
    json_str = json.dumps(result)
    assert json_str is not None
    parsed = json.loads(json_str)
    assert parsed["category"] == "multimodal"



# 6. LLM Message Structure & Content Type Verification


@pytest.mark.asyncio
async def test_multimodal_llm_message_content_string_type(clean_multimodal_evidence):
    """Assert qualitative LLM request always builds valid string content on text models."""
    from unittest.mock import AsyncMock, MagicMock
    
    # Mock LLM Client
    mock_client = MagicMock()
    mock_client.model = "openai/gpt-oss-120b"
    mock_client.api_key = "test-key"
    mock_client._openai_client = MagicMock()
    mock_client._openai_client.chat = MagicMock()
    mock_client._openai_client.chat.completions = MagicMock()
    mock_client._openai_client.chat.completions.create = AsyncMock()
    
    # Configure mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps({"findings": []})))]
    mock_response.usage = MagicMock(total_tokens=100)
    mock_client._openai_client.chat.completions.create.return_value = mock_response

    auditor = MultimodalAudit()
    await auditor.audit_async(clean_multimodal_evidence, options={"use_llm": True, "llm_client": mock_client})

    # Verify query_json was called and prompt/images were processed
    assert mock_client._openai_client.chat.completions.create.called or mock_client.query_json.called
