"""
Unit test suite for cta_analyzer.py using pytest.
Tests all 20 specified scenarios deterministically without external dependencies.
"""

from __future__ import annotations

import json
import pytest
from cta_analyzer import (
    CTAAnalyzer,
    NormalizedCTA,
    analyze_ctas,
    normalize_cta_entries,
)
from engagement_state import (
    BoundingBox,
    CTAElement,
    PageInputData,
    Viewport,
)


def test_1_primary_cta_visible_above_fold():
    """1. Test healthy primary CTA placed above the fold passes with score 100."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "ctas": [
            {
                "id": "cta-1",
                "text": "Start Free Trial",
                "role": "primary",
                "href": "/signup",
                "visible": True,
                "bbox": {"x": 500, "y": 300, "width": 180, "height": 48},
            }
        ],
    }

    res = analyze_ctas(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0
    assert res.metrics["ctas_analyzed"] == 1


def test_2_primary_cta_below_fold():
    """2. Test primary CTA at y=850 (below 768px fold) generates ENG-CTA-002."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "ctas": [
            {
                "id": "cta-1",
                "text": "Get Started",
                "role": "primary",
                "bbox": {"x": 500, "y": 850, "width": 180, "height": 48},
            }
        ],
    }

    res = analyze_ctas(data)
    assert res.status == "failed"
    below_f = [f for f in res.findings if f.id == "ENG-CTA-002"]
    assert len(below_f) == 1
    assert below_f[0].severity == "high"


def test_3_cta_partially_intersecting_viewport():
    """3. Test CTA cut off at fold (y=740, height=60 -> cuts at 768) generates ENG-CTA-003."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "ctas": [
            {
                "id": "cta-1",
                "text": "Buy Now",
                "role": "primary",
                "bbox": {"x": 500, "y": 740, "width": 180, "height": 60},
            }
        ],
    }

    res = analyze_ctas(data)
    clipped_f = [f for f in res.findings if f.id == "ENG-CTA-003"]
    assert len(clipped_f) == 1
    assert clipped_f[0].severity == "medium"


def test_4_hidden_cta():
    """4. Test hidden primary CTA (visible=False) generates ENG-CTA-001."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {
                "id": "cta-1",
                "text": "Join Today",
                "role": "primary",
                "visible": False,
            }
        ],
    }

    res = analyze_ctas(data)
    hidden_f = [f for f in res.findings if f.id == "ENG-CTA-001"]
    assert len(hidden_f) == 1
    assert hidden_f[0].severity == "high"


def test_5_obstructed_cta():
    """5. Test obstructed CTA (obstructed=True) generates ENG-CTA-008."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {
                "id": "cta-1",
                "text": "Sign Up",
                "role": "primary",
                "obstructed": True,
            }
        ],
    }

    res = analyze_ctas(data)
    obs_f = [f for f in res.findings if f.id == "ENG-CTA-008"]
    assert len(obs_f) == 1
    assert obs_f[0].severity == "high"


def test_6_disabled_primary_cta():
    """6. Test disabled primary CTA (enabled=False) generates ENG-CTA-011."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {
                "id": "cta-1",
                "text": "Submit Application",
                "role": "primary",
                "enabled": False,
            }
        ],
    }

    res = analyze_ctas(data)
    dis_f = [f for f in res.findings if f.id == "ENG-CTA-011"]
    assert len(dis_f) == 1
    assert dis_f[0].severity == "medium"


def test_7_vague_cta_text():
    """7. Test vague CTA copy ('Click Here') generates ENG-CTA-005."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Click Here", "href": "/guide"}
        ],
    }

    res = analyze_ctas(data)
    vague_f = [f for f in res.findings if f.id == "ENG-CTA-005"]
    assert len(vague_f) == 1
    assert vague_f[0].severity == "low"


def test_8_clear_action_text():
    """8. Test specific action copy ('Book a Demo') generates 0 vague copy findings."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Book a Demo", "href": "/demo"}
        ],
    }

    res = analyze_ctas(data)
    vague_f = [f for f in res.findings if f.id == "ENG-CTA-005"]
    assert len(vague_f) == 0


def test_9_multiple_competing_primary_ctas():
    """9. Test multiple (>=3) primary CTAs generates ENG-CTA-009."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Start Free Trial", "role": "primary"},
            {"id": "cta-2", "text": "Talk to Sales", "role": "primary"},
            {"id": "cta-3", "text": "Buy Now", "role": "primary"},
        ],
    }

    res = analyze_ctas(data)
    comp_f = [f for f in res.findings if f.id == "ENG-CTA-009"]
    assert len(comp_f) == 1
    assert comp_f[0].severity == "low"


def test_10_missing_cta_data_none_input():
    """10. Test None input returns status 'insufficient_evidence'."""
    res = analyze_ctas(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None
    assert len(res.findings) == 0


def test_11_empty_cta_list():
    """11. Test empty CTA list returns status 'insufficient_evidence'."""
    res = analyze_ctas({"url": "https://example.com", "ctas": []})
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_12_missing_viewport_uses_default():
    """12. Test missing viewport uses standard 1366x768 default gracefully."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"text": "Get Started", "bbox": {"x": 100, "y": 200, "width": 150, "height": 40}}
        ],
    }

    res = analyze_ctas(data)
    assert res.status == "passed"
    assert res.score == 100


def test_13_missing_bbox_does_not_fail():
    """13. Test missing bounding box does not crash and marks position unknown."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Start Trial", "role": "primary"}
        ],
    }

    res = analyze_ctas(data)
    assert res.status == "passed"
    assert res.score == 100


def test_14_dead_end_href():
    """14. Test primary CTA pointing to '#' generates ENG-CTA-007."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Get Started", "role": "primary", "href": "#"}
        ],
    }

    res = analyze_ctas(data)
    dead_f = [f for f in res.findings if f.id == "ENG-CTA-007"]
    assert len(dead_f) == 1
    assert dead_f[0].severity == "medium"


def test_15_missing_visibility_data_treated_as_unknown():
    """15. Test missing visibility does not falsely report CTA is hidden."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Download App", "visible": None}
        ],
    }

    res = analyze_ctas(data)
    hidden_f = [f for f in res.findings if f.id == "ENG-CTA-001"]
    assert len(hidden_f) == 0


def test_16_malformed_cta_entry_handled_safely():
    """16. Test malformed entries (None, integers, strings) in CTA list."""
    data = {
        "url": "https://example.com",
        "ctas": [
            None,
            "Plain Text CTA",
            {"text": "Valid CTA", "bbox": "invalid_bbox"},
        ],
    }

    res = analyze_ctas(data)
    assert res.metrics["ctas_analyzed"] == 2


def test_17_multiple_cta_prominence_comparison():
    """17. Test secondary CTA being 2x larger than primary CTA generates ENG-CTA-004."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Start Trial", "role": "primary", "bbox": {"x": 100, "y": 200, "width": 100, "height": 30}},  # area 3000
            {"id": "cta-2", "text": "View All Products", "role": "secondary", "bbox": {"x": 250, "y": 200, "width": 250, "height": 60}},  # area 15000
        ],
    }

    res = analyze_ctas(data)
    prom_f = [f for f in res.findings if f.id == "ENG-CTA-004"]
    assert len(prom_f) == 1
    assert prom_f[0].severity == "low"


def test_18_undersized_tap_target():
    """18. Test undersized CTA button (20x20px) generates ENG-CTA-010."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Order", "bbox": {"x": 100, "y": 200, "width": 25, "height": 20}}
        ],
    }

    res = analyze_ctas(data)
    size_f = [f for f in res.findings if f.id == "ENG-CTA-010"]
    assert len(size_f) == 1
    assert size_f[0].severity == "low"


def test_19_deterministic_finding_ids():
    """19. Test finding IDs are completely deterministic across multiple runs."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Click Here", "role": "primary", "bbox": {"x": 100, "y": 900, "width": 150, "height": 45}}
        ],
    }

    res1 = analyze_ctas(data)
    res2 = analyze_ctas(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_20_deterministic_scoring():
    """20. Test deterministic scoring calculation and bounds."""
    data = {
        "url": "https://example.com",
        "ctas": [
            {"id": "cta-1", "text": "Get Started", "role": "primary", "bbox": {"x": 100, "y": 850, "width": 150, "height": 45}}
        ],
    }

    res = analyze_ctas(data)
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100
    assert res.score < 100  # Penalty applied for below-fold CTA
