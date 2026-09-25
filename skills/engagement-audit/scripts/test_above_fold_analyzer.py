"""
Unit tests for above_fold_analyzer.py using pytest.
Tests the 8 core test cases from the specification and edge cases deterministically.
"""

from __future__ import annotations

import json
import pytest
from above_fold_analyzer import (
    analyze_above_fold,
    classify_fold_visibility,
)
from engagement_state import (
    BoundingBox,
    CTAElement,
    HeadingElement,
    PageInputData,
    PopupEvidence,
    Viewport,
)


def test_case_1_heading_and_cta_visible_above_fold():
    """TEST CASE 1: 1366x768 viewport, heading visible, CTA visible -> status: passed."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Master AI Agent Workflows", "bounding_box": {"x": 100, "y": 120, "width": 600, "height": 80}}
        ],
        "ctas": [
            {"text": "Get Started", "is_primary": True, "bounding_box": {"x": 100, "y": 240, "width": 180, "height": 50}}
        ],
    }

    res = analyze_above_fold(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0
    assert res.metrics["main_heading"]["visibility"] == "fully_visible"
    assert res.metrics["primary_cta"]["visibility"] == "fully_visible"


def test_case_2_cta_below_fold():
    """TEST CASE 2: CTA positioned at y=850 (below 768px fold) -> generates ENG-AF-002."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Hero Title", "bounding_box": {"x": 100, "y": 120, "width": 600, "height": 80}}
        ],
        "ctas": [
            {"text": "Sign Up Free", "is_primary": True, "bounding_box": {"x": 100, "y": 850, "width": 180, "height": 50}}
        ],
    }

    res = analyze_above_fold(data)
    assert res.status == "failed"
    assert res.score < 90
    cta_findings = [f for f in res.findings if f.id == "ENG-AF-002"]
    assert len(cta_findings) == 1
    assert cta_findings[0].severity == "high"
    assert cta_findings[0].evidence["y_position"] == 850.0


def test_case_3_cta_partially_visible():
    """TEST CASE 3: CTA intersects viewport boundary (y=740, height=60, cuts off at 768) -> ENG-AF-003."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Hero Title", "bounding_box": {"x": 100, "y": 120, "width": 600, "height": 80}}
        ],
        "ctas": [
            {"text": "Buy Now", "is_primary": True, "bounding_box": {"x": 100, "y": 740, "width": 180, "height": 60}}
        ],
    }

    res = analyze_above_fold(data)
    assert res.status == "warning"
    clipped_f = [f for f in res.findings if f.id == "ENG-AF-003"]
    assert len(clipped_f) == 1
    assert clipped_f[0].severity == "medium"


def test_case_4_heading_below_fold():
    """TEST CASE 4: Main heading pushed below the 768px fold line -> generates ENG-AF-001."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Pushed Down Heading", "bounding_box": {"x": 100, "y": 920, "width": 600, "height": 80}}
        ],
        "ctas": [
            {"text": "Explore", "is_primary": True, "bounding_box": {"x": 100, "y": 200, "width": 180, "height": 50}}
        ],
    }

    res = analyze_above_fold(data)
    h_findings = [f for f in res.findings if f.id == "ENG-AF-001"]
    assert len(h_findings) == 1
    assert h_findings[0].severity == "medium"


def test_case_5_popup_obstructs_cta():
    """TEST CASE 5: Immediate blocking popup overlaps the primary CTA -> generates ENG-AF-004."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Welcome", "bounding_box": {"x": 100, "y": 100, "width": 500, "height": 60}}
        ],
        "ctas": [
            {"text": "Get Started", "is_primary": True, "bounding_box": {"x": 100, "y": 200, "width": 180, "height": 50}}
        ],
        "popups": [
            {
                "name": "10% Discount Overlay",
                "is_blocking": True,
                "immediate": True,
                "bounding_box": {"x": 50, "y": 150, "width": 500, "height": 400},
            }
        ],
    }

    res = analyze_above_fold(data)
    pop_f = [f for f in res.findings if f.id == "ENG-AF-004"]
    assert len(pop_f) == 1
    assert pop_f[0].severity == "high"


def test_case_6_no_evidence_returns_insufficient_evidence():
    """TEST CASE 6: Empty input data returns status 'insufficient_evidence'."""
    res = analyze_above_fold({})
    assert res.status == "insufficient_evidence"
    assert res.score is None
    assert len(res.findings) == 0


def test_case_7_no_cta_data_does_not_falsely_report_below_fold():
    """TEST CASE 7: Heading exists but no CTA data -> does not fabricate below-the-fold CTA finding."""
    data = {
        "url": "https://example.com/article",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Informational Article", "bounding_box": {"x": 100, "y": 100, "width": 600, "height": 60}}
        ],
        "ctas": [],
    }

    res = analyze_above_fold(data)
    cta_below = [f for f in res.findings if f.id in ("ENG-AF-002", "ENG-AF-003")]
    assert len(cta_below) == 0
    assert res.metrics["primary_cta"]["visibility"] == "unknown"


def test_case_8_custom_viewport_dimensions():
    """TEST CASE 8: Custom viewport options (1920x1080) accurately evaluates larger fold."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1920, "height": 1080},
        "headings": [
            {"level": 1, "text": "Wide Screen", "bounding_box": {"x": 200, "y": 150, "width": 800, "height": 100}}
        ],
        "ctas": [
            # y=850 is below a 768px fold, but ABOVE a 1080px fold!
            {"text": "Start Now", "is_primary": True, "bounding_box": {"x": 200, "y": 850, "width": 200, "height": 60}}
        ],
    }

    res = analyze_above_fold(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0
    assert res.metrics["primary_cta"]["visibility"] == "fully_visible"


def test_classify_fold_visibility_edge_cases():
    """Test geometry helper edge cases."""
    vp = Viewport(1366, 768)

    # Off screen to right
    box_right = BoundingBox(x=1400, y=100, width=200, height=50)
    vis, ratio = classify_fold_visibility(box_right, vp)
    assert vis == "off_screen"
    assert ratio == 0.0

    # Zero dimensions
    box_zero = BoundingBox(x=100, y=100, width=0, height=0)
    vis_z, ratio_z = classify_fold_visibility(box_zero, vp)
    assert vis_z == "unknown"

    # None
    vis_n, _ = classify_fold_visibility(None, vp)
    assert vis_n == "unknown"


def test_page_input_data_object_direct_support():
    """Test analyzer directly accepts PageInputData instance."""
    input_data = PageInputData(
        url="https://example.com",
        viewport=Viewport(1366, 768),
        headings=[
            HeadingElement(level=1, text="Direct Instance", bounding_box=BoundingBox(100, 100, 400, 50))
        ],
        ctas=[
            CTAElement(text="Click Me", is_primary=True, bounding_box=BoundingBox(100, 200, 150, 40))
        ],
    )

    res = analyze_above_fold(input_data)
    assert res.status == "passed"
    assert res.score == 100
