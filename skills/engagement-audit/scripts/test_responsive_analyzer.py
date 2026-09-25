"""
Unit test suite for responsive_analyzer.py using pytest.
Tests all 28 specified multi-viewport responsive scenarios deterministically.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import (
    BoundingBox,
    PageInputData,
    Viewport,
)
from responsive_analyzer import (
    NormalizedLayoutElement,
    NormalizedViewportProfile,
    ResponsiveAnalyzer,
    analyze_responsiveness,
    extract_responsive_evidence,
)


def test_1_desktop_viewport_no_overflow():
    """1. Test desktop viewport without overflow passes with score 100."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "desktop",
                "width": 1366,
                "height": 768,
                "page_width": 1366,
                "horizontal_overflow": False,
                "elements": [
                    {"id": "header", "bbox": {"x": 0, "y": 0, "width": 1366, "height": 80}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0


def test_2_mobile_viewport_no_overflow():
    """2. Test clean mobile viewport without overflow passes with score 100."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "page_width": 390,
                "horizontal_overflow": False,
                "elements": [
                    {"id": "hero", "bbox": {"x": 0, "y": 100, "width": 390, "height": 300}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "passed"
    assert res.score == 100


def test_3_mobile_horizontal_page_overflow():
    """3. Test page_width (650px) > viewport width (390px) generates ENG-RESP-001 (high severity)."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "page_width": 650,
                "horizontal_overflow": True,
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "failed"
    ov_f = [f for f in res.findings if f.id == "ENG-RESP-001"]
    assert len(ov_f) == 1
    assert ov_f[0].severity == "high"


def test_4_element_extending_beyond_viewport():
    """4. Test element right edge (500px) > mobile viewport (390px) generates ENG-RESP-002."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "pricing-card", "type": "card", "bbox": {"x": 0, "y": 200, "width": 500, "height": 300}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    ext_f = [f for f in res.findings if f.id == "ENG-RESP-002"]
    assert len(ext_f) == 1
    assert ext_f[0].severity == "medium"


def test_5_partially_clipped_element():
    """5. Test element marked with clipped=True generates ENG-RESP-003."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "banner-text", "type": "text", "clipped": True, "clipped_pixels": 35}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    clip_f = [f for f in res.findings if f.id == "ENG-RESP-003"]
    assert len(clip_f) == 1
    assert clip_f[0].severity == "medium"


def test_6_element_overlap_detection():
    """6. Test intersecting elements with >500px^2 overlap generates ENG-RESP-004."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "hero-heading", "type": "heading", "bbox": {"x": 20, "y": 100, "width": 350, "height": 80}},
                    {"id": "hero-cta", "type": "cta", "bbox": {"x": 20, "y": 120, "width": 200, "height": 50}},  # Collides with heading
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    ovl_f = [f for f in res.findings if f.id == "ENG-RESP-004"]
    assert len(ovl_f) == 1
    assert ovl_f[0].severity == "medium"


def test_7_intentional_carousel_overflow_ignored():
    """7. Test intentional carousel / scrollable container is not penalized."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "product-carousel", "type": "carousel", "scrollable": True, "bbox": {"x": 0, "y": 300, "width": 900, "height": 200}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert len(res.findings) == 0


def test_8_fixed_width_element_scaling_failure():
    """8. Test element retaining fixed 1000px width on 390px mobile generates ENG-RESP-007."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "desktop",
                "width": 1366,
                "height": 768,
                "elements": [
                    {"id": "content-container", "type": "section", "bbox": {"x": 100, "y": 100, "width": 1000, "height": 600}}
                ],
            },
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "content-container", "type": "section", "bbox": {"x": 0, "y": 100, "width": 1000, "height": 600}}
                ],
            },
        ],
    }

    res = analyze_responsiveness(data)
    fixed_f = [f for f in res.findings if f.id == "ENG-RESP-007"]
    assert len(fixed_f) == 1
    assert fixed_f[0].severity == "medium"


def test_9_responsive_navigation_transformation_valid():
    """9. Test collapsed mobile navigation generates 0 nav overflow findings."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "nav-toggle", "type": "navigation", "bbox": {"x": 340, "y": 20, "width": 44, "height": 44}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    nav_f = [f for f in res.findings if f.id == "ENG-RESP-005"]
    assert len(nav_f) == 0


def test_10_navigation_overflow_on_mobile():
    """10. Test desktop navigation bar (1200px) on mobile generates ENG-RESP-005."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "desktop-nav", "type": "navigation", "bbox": {"x": 0, "y": 0, "width": 1200, "height": 60}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    nav_f = [f for f in res.findings if f.id == "ENG-RESP-005"]
    assert len(nav_f) == 1
    assert nav_f[0].severity == "high"


def test_11_cta_overflow_on_mobile():
    """11. Test CTA extending outside mobile viewport generates ENG-RESP-006."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "primary-cta", "type": "cta", "bbox": {"x": 50, "y": 300, "width": 400, "height": 50}}  # Right edge = 450 > 390
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    cta_f = [f for f in res.findings if f.id == "ENG-RESP-006"]
    assert len(cta_f) == 1
    assert cta_f[0].severity == "high"


def test_12_small_interactive_touch_target():
    """12. Test mobile control with 24x24px dimensions generates ENG-RESP-008."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "filter-btn", "type": "button", "touch_target_size": 24.0, "bbox": {"x": 20, "y": 200, "width": 24, "height": 24}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    touch_f = [f for f in res.findings if f.id == "ENG-RESP-008"]
    assert len(touch_f) == 1
    assert touch_f[0].severity == "low"


def test_13_responsive_image_sizing_valid():
    """13. Test fluid image fitting inside 390px mobile viewport passes."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "hero-img", "type": "image", "bbox": {"x": 20, "y": 100, "width": 350, "height": 200}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert len(res.findings) == 0


def test_14_form_control_overflow():
    """14. Test form input extending outside mobile viewport."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [
                    {"id": "email-input", "type": "form", "bbox": {"x": 0, "y": 200, "width": 450, "height": 45}}
                ],
            }
        ],
    }

    res = analyze_responsiveness(data)
    ext_f = [f for f in res.findings if f.id == "ENG-RESP-002"]
    assert len(ext_f) == 1
    assert ext_f[0].severity == "high"


def test_15_desktop_tablet_mobile_comparison():
    """15. Test multi-viewport comparison across all 3 device categories."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "desktop", "width": 1366, "height": 768, "horizontal_overflow": False},
            {"name": "tablet", "width": 768, "height": 1024, "horizontal_overflow": False},
            {"name": "mobile", "width": 390, "height": 844, "horizontal_overflow": False},
        ],
    }

    res = analyze_responsiveness(data)
    assert res.metrics["viewports_analyzed"] == 3
    assert res.status == "passed"
    assert res.score == 100


def test_16_responsive_regression_on_mobile_only():
    """16. Test responsive regression (passed desktop and tablet, failed on mobile)."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "desktop", "width": 1366, "height": 768, "horizontal_overflow": False},
            {"name": "tablet", "width": 768, "height": 1024, "horizontal_overflow": False},
            {"name": "mobile", "width": 390, "height": 844, "page_width": 550, "horizontal_overflow": True},
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "failed"
    ov_f = [f for f in res.findings if f.id == "ENG-RESP-001"]
    assert len(ov_f) == 1


def test_17_missing_mobile_evidence_handled():
    """17. Test only desktop viewport provided analyzes desktop without crash."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "desktop", "width": 1366, "height": 768, "horizontal_overflow": False}
        ],
    }

    res = analyze_responsiveness(data)
    assert res.metrics["viewports_analyzed"] == 1
    assert res.status == "passed"


def test_18_missing_tablet_evidence_handled():
    """18. Test desktop and mobile provided without tablet analyzes gracefully."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "desktop", "width": 1366, "height": 768},
            {"name": "mobile", "width": 390, "height": 844},
        ],
    }

    res = analyze_responsiveness(data)
    assert res.metrics["viewports_analyzed"] == 2


def test_19_missing_bbox_handled_safely():
    """19. Test element without bbox does not cause an error."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "mobile",
                "width": 390,
                "height": 844,
                "elements": [{"id": "elem-no-box", "type": "section"}],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "passed"


def test_20_missing_page_width_handled_safely():
    """20. Test missing page_width handled safely."""
    data = {
        "url": "https://example.com",
        "viewports": [{"name": "desktop", "width": 1366, "height": 768}],
    }

    res = analyze_responsiveness(data)
    assert res.status == "passed"


def test_21_malformed_viewport_skipped_safely():
    """21. Test None and invalid objects in viewports list."""
    data = {
        "url": "https://example.com",
        "viewports": [
            None,
            "corrupt string",
            {"name": "valid-desktop", "width": 1366, "height": 768},
        ],
    }

    res = analyze_responsiveness(data)
    assert res.metrics["viewports_analyzed"] == 1


def test_22_malformed_element_skipped_safely():
    """22. Test None and invalid items in elements list."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {
                "name": "desktop",
                "width": 1366,
                "height": 768,
                "elements": [None, 123, {"id": "valid-elem"}],
            }
        ],
    }

    res = analyze_responsiveness(data)
    assert res.status == "passed"


def test_23_mobile_data_key_alias_support():
    """23. Test mobile_data key alias."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "mobile_data": {
            "width": 390,
            "height": 844,
            "page_width": 390,
            "horizontal_overflow": False,
        },
    }

    res = analyze_responsiveness(data)
    assert res.metrics["viewports_analyzed"] == 2


def test_24_deterministic_finding_ids():
    """24. Test finding IDs are completely deterministic."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "mobile", "width": 390, "height": 844, "page_width": 600, "horizontal_overflow": True}
        ],
    }

    res1 = analyze_responsiveness(data)
    res2 = analyze_responsiveness(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_25_deterministic_scoring_bounds():
    """25. Test score is integer bounded between 0 and 100."""
    data = {
        "url": "https://example.com",
        "viewports": [
            {"name": "mobile", "width": 390, "height": 844, "page_width": 600, "horizontal_overflow": True}
        ],
    }

    res = analyze_responsiveness(data)
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100
    assert res.score < 100


def test_26_insufficient_evidence_on_empty_input():
    """26. Test empty dictionary returns status 'insufficient_evidence'."""
    res = analyze_responsiveness({"url": "https://example.com"})
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_27_none_input_handled_safely():
    """27. Test None input returns status 'insufficient_evidence'."""
    res = analyze_responsiveness(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_28_page_input_data_support():
    """28. Test direct PageInputData instance support."""
    input_data = PageInputData(
        url="https://example.com",
        viewport=Viewport(width=1366, height=768),
    )

    res = analyze_responsiveness(input_data)
    assert res.metrics["viewports_analyzed"] == 1
    assert res.status == "passed"
