"""
Unit tests for engagement_state.py data contracts using pytest.
Verifies typing, serialization, BoundingBox geometry, PageInputData parsing, and fault tolerance.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import (
    AuditError,
    AuditMetadata,
    BoundingBox,
    CategoryResult,
    CTAElement,
    EngagementAuditState,
    EngagementFinding,
    HeadingElement,
    MobileViewportEvidence,
    PageElement,
    PageInputData,
    PopupEvidence,
    Viewport,
)


def test_1_viewport_creation_and_dict():
    """1. Test Viewport default values and dictionary conversion."""
    vp = Viewport()
    assert vp.width == 1366.0
    assert vp.height == 768.0
    d = vp.to_dict()
    assert d == {"width": 1366.0, "height": 768.0}
    vp_custom = Viewport.from_dict({"width": 390, "height": 844})
    assert vp_custom.width == 390.0
    assert vp_custom.height == 844.0


def test_2_bounding_box_properties():
    """2. Test BoundingBox geometry properties."""
    box = BoundingBox(x=100.0, y=200.0, width=300.0, height=150.0)
    assert box.left == 100.0
    assert box.top == 200.0
    assert box.right == 400.0
    assert box.bottom == 350.0
    assert box.area == 45000.0


def test_3_bounding_box_above_fold():
    """3. Test is_above_fold calculation relative to Viewport."""
    vp = Viewport(width=1366.0, height=768.0)
    hero_box = BoundingBox(x=100.0, y=150.0, width=300.0, height=50.0)
    footer_box = BoundingBox(x=100.0, y=950.0, width=300.0, height=50.0)

    assert hero_box.is_above_fold(vp) is True
    assert footer_box.is_above_fold(vp) is False


def test_4_bounding_box_from_dict_safe():
    """4. Test BoundingBox.from_dict handles missing, malformed, or alternate keys."""
    assert BoundingBox.from_dict(None) is None
    assert BoundingBox.from_dict({}) is not None
    # Alternate keys top/left
    box = BoundingBox.from_dict({"left": 50, "top": 80, "width": 100, "height": 40})
    assert box.x == 50.0
    assert box.y == 80.0


def test_5_engagement_finding_validation():
    """5. Test EngagementFinding post-init validation for confidence and severity."""
    # Confidence clamp > 1.0 -> 1.0
    f1 = EngagementFinding(
        id="ENG-CTA-001",
        category="cta",
        title="Sample",
        description="Sample desc",
        severity="high",
        confidence=1.5,
        evidence={"foo": "bar"},
        recommendation="Fix it",
        url="https://example.com",
    )
    assert f1.confidence == 1.0

    # Invalid severity defaults to medium
    f2 = EngagementFinding(
        id="ENG-CTA-002",
        category="cta",
        title="Sample",
        description="Sample desc",
        severity="invalid_sev",  # type: ignore
        confidence=-0.5,
        evidence={},
        recommendation="Fix it",
        url="https://example.com",
    )
    assert f2.confidence == 0.0
    assert f2.severity == "medium"


def test_6_page_input_data_parsing():
    """6. Test parsing raw dictionary into PageInputData."""
    raw = {
        "url": "https://example.com/product",
        "title": "Product Landing",
        "viewport": {"width": 1440, "height": 900},
        "headings": [
            {"level": 1, "text": "Main Headline", "bounding_box": {"x": 100, "y": 200, "width": 500, "height": 60}}
        ],
        "ctas": [
            {"text": "Get Started", "is_primary": True, "bounding_box": {"x": 100, "y": 300, "width": 150, "height": 45}}
        ],
        "popups": [
            {"name": "Promo", "is_blocking": True, "immediate": True}
        ],
        "mobile": {
            "horizontal_overflow": True,
            "scroll_width": 450,
            "client_width": 390,
        },
    }

    input_data = PageInputData.from_dict(raw)
    assert input_data.url == "https://example.com/product"
    assert input_data.viewport.width == 1440.0
    assert len(input_data.headings) == 1
    assert input_data.headings[0].level == 1
    assert len(input_data.ctas) == 1
    assert input_data.ctas[0].is_primary is True
    assert len(input_data.popups) == 1
    assert input_data.mobile is not None
    assert input_data.mobile.horizontal_overflow is True


def test_7_category_result_serialization():
    """7. Test CategoryResult dictionary conversion."""
    res = CategoryResult(
        category="cta",
        status="passed",
        score=95,
        findings=[],
        metrics={"total_ctas": 2},
        confidence=1.0,
    )
    d = res.to_dict()
    assert d["category"] == "cta"
    assert d["status"] == "passed"
    assert d["score"] == 95
    assert d["confidence"] == 1.0


def test_8_engagement_audit_state_json_serialization():
    """8. Test full EngagementAuditState JSON serialization."""
    state = EngagementAuditState(
        input_data=PageInputData(url="https://example.com"),
        overall_score=88,
        category_scores={"above_fold": 90, "cta": 85},
        summary="Good engagement overall.",
    )
    state.add_finding(EngagementFinding(
        id="ENG-CTA-001",
        category="cta",
        title="CTA Warning",
        description="Detailed description",
        severity="low",
        confidence=0.85,
        evidence={"field": "value"},
        recommendation="Actionable fix",
        url="https://example.com",
    ))

    json_str = state.to_json()
    assert isinstance(json_str, str)
    deserialized = json.loads(json_str)
    assert deserialized["skill"] == "engagement-audit"
    assert deserialized["overall_score"] == 88
    assert deserialized["findings_count"] == 1
    assert deserialized["findings"][0]["id"] == "ENG-CTA-001"


def test_9_engagement_audit_state_error_handling():
    """9. Test state can record component errors without failing."""
    state = EngagementAuditState()
    state.add_error("responsive", "Mobile viewport data unavailable", {"attempted_vp": 390})
    assert len(state.errors) == 1
    assert state.errors[0].component == "responsive"
    d = state.to_dict()
    assert len(d["errors"]) == 1


def test_10_rehydration_from_dict():
    """10. Test rehydrating EngagementAuditState from a dictionary."""
    data = {
        "url": "https://example.com",
        "overall_score": 75,
        "summary": "Partial issues",
        "category_scores": {"readability": 60},
        "findings": [
            {
                "id": "ENG-READ-001",
                "category": "readability",
                "title": "Difficult text",
                "description": "Flesch score low",
                "severity": "low",
                "confidence": 0.9,
                "evidence": {},
                "recommendation": "Simplify words",
                "url": "https://example.com",
            }
        ],
    }

    state = EngagementAuditState.from_dict(data)
    assert state.overall_score == 75
    assert len(state.findings) == 1
    assert state.findings[0].id == "ENG-READ-001"
    assert state.has_critical_findings() is False
