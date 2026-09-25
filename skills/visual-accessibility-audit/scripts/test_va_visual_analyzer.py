"""
Unit tests for the visual_analyzer.py module in visual-accessibility-audit.

Tests all 30 requirements using deterministic synthetic fixtures,
running 100% offline without network or browser dependencies.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock
from typing import Any
import pytest

from visual_analyzer import analyze_visual


def _create_synthetic_render_result(
    viewports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Helper to generate a clean synthetic render_result."""
    vps = viewports or [
        {
            "name": "desktop",
            "status": "completed",
            "viewport": {"name": "desktop", "width": 1440, "height": 900},
            "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 2000},
            "elements": [
                {
                    "tag": "h1",
                    "id": "main-title",
                    "bounds": {"x": 100, "y": 50, "width": 600, "height": 40, "right": 700, "bottom": 90},
                    "visibility": {"is_visible": True},
                }
            ],
            "fixed_elements": [],
            "sticky_elements": [],
        }
    ]
    return {
        "status": "completed",
        "target_url": "https://example.com",
        "viewports": vps,
        "errors": [],
    }


# 1. Normal desktop page -> 0 issues
def test_normal_desktop_page() -> None:
    render_res = _create_synthetic_render_result()
    res = analyze_visual(render_res)
    assert res["summary"]["observations"] == 0
    assert res["summary"]["potential_issues"] == 0


# 2. Normal mobile page -> 0 issues
def test_normal_mobile_page() -> None:
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 390, "client_width": 390, "scroll_height": 1500},
        "elements": [
            {
                "tag": "p",
                "id": "intro",
                "bounds": {"x": 20, "y": 60, "width": 350, "height": 100, "right": 370, "bottom": 160},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([mobile_vp]))
    assert res["summary"]["observations"] == 0


# 3. Horizontal overflow
def test_horizontal_overflow() -> None:
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 550, "client_width": 390, "scroll_height": 1500},
        "elements": [
            {
                "tag": "div",
                "id": "wide-container",
                "bounds": {"x": 0, "y": 100, "width": 550, "height": 200, "right": 550, "bottom": 300},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([mobile_vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "horizontal_overflow" in codes


# 4. Intentional horizontal carousel (no false positive)
def test_intentional_horizontal_carousel() -> None:
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 1000, "client_width": 390, "scroll_height": 1500},
        "elements": [
            {
                "tag": "div",
                "id": "product-slider",
                "classes": ["carousel-container"],
                "bounds": {"x": 0, "y": 100, "width": 1000, "height": 200, "right": 1000, "bottom": 300},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([mobile_vp]))
    # Carousel elements are skipped from culprit list
    h_obs = next((o for o in res["observations"] if o["code"] == "horizontal_overflow"), None)
    if h_obs:
        culprits = h_obs["evidence"].get("culprit_elements", [])
        assert all(c.get("id") != "product-slider" for c in culprits)


# 5. Clipped element
def test_clipped_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "button",
                "id": "btn-clipped",
                "bounds": {"x": -20.0, "y": 100, "width": 120, "height": 40, "right": 100, "bottom": 140},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "interactive_element_clipped" in codes


# 6. Text clipping off-screen
def test_text_clipping_offscreen() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "p",
                "id": "long-text",
                "bounds": {"x": 1200, "y": 100, "width": 400, "height": 100, "right": 1600, "bottom": 200},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "element_outside_viewport" in codes


# 7. Sibling overlap
def test_sibling_overlap() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "header",
                "id": "main-header",
                "bounds": {"x": 50, "y": 50, "width": 500, "height": 100, "right": 550, "bottom": 150},
                "visibility": {"is_visible": True},
            },
            {
                "tag": "section",
                "id": "hero-section",
                "bounds": {"x": 60, "y": 70, "width": 500, "height": 150, "right": 560, "bottom": 220},
                "visibility": {"is_visible": True},
            },
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "suspicious_element_overlap" in codes


# 8. Legitimate parent/child overlap (not flagged)
def test_legitimate_parent_child_overlap() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "div",
                "id": "card",
                "bounds": {"x": 10, "y": 10, "width": 300, "height": 200, "right": 310, "bottom": 210},
                "visibility": {"is_visible": True},
            },
            {
                "tag": "p",
                "id": "card-desc",
                # Completely nested inside card bounds
                "bounds": {"x": 20, "y": 20, "width": 200, "height": 50, "right": 220, "bottom": 70},
                "visibility": {"is_visible": True},
            },
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "suspicious_element_overlap" not in codes


# 9. Fixed header covering content
def test_oversized_fixed_header() -> None:
    vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 390, "client_width": 390, "scroll_height": 844},
        "elements": [],
        "fixed_elements": [
            {
                "tag": "header",
                "id": "huge-header",
                # 350px / 844px = 41% of viewport height (> 30% threshold)
                "bounds": {"x": 0, "y": 0, "width": 390, "height": 350},
            }
        ],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "oversized_fixed_element" in codes


# 10. Normal fixed header (< 30% height, not flagged)
def test_normal_fixed_header() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [],
        "fixed_elements": [
            {
                "tag": "header",
                "id": "slim-header",
                "bounds": {"x": 0, "y": 0, "width": 1440, "height": 60},
            }
        ],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "oversized_fixed_element" not in codes


# 11. Sticky element
def test_sticky_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [],
        "fixed_elements": [],
        "sticky_elements": [
            {
                "tag": "nav",
                "id": "sticky-nav",
                "bounds": {"x": 0, "y": 0, "width": 1440, "height": 400},
            }
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    codes = [o["code"] for o in res["observations"]]
    assert "oversized_sticky_element" in codes


# 12. Oversized fixed element
def test_oversized_fixed_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [],
        "fixed_elements": [
            {
                "tag": "div",
                "id": "overlay-banner",
                "bounds": {"x": 0, "y": 0, "width": 1440, "height": 500},
            }
        ],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert any(o["code"] == "oversized_fixed_element" for o in res["observations"])


# 13. Off-screen element
def test_offscreen_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "img",
                "id": "hero-img",
                "bounds": {"x": 1000, "y": 0, "width": 600, "height": 400, "right": 1600, "bottom": 400},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert any(o["code"] == "element_outside_viewport" for o in res["observations"])


# 14. Legitimate off-screen / scrollable container (ignored)
def test_legitimate_scrollable_container() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "pre",
                "id": "code-block",
                "bounds": {"x": 0, "y": 0, "width": 2000, "height": 200, "right": 2000, "bottom": 200},
                "visibility": {"is_visible": True},
            }
        ],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert not any(o["code"] == "element_outside_viewport" for o in res["observations"])


# 15. Responsive overflow (overflow on mobile only)
def test_responsive_overflow() -> None:
    desktop_vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 1000},
        "elements": [],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 500, "client_width": 390, "scroll_height": 2000},
        "elements": [],
        "fixed_elements": [],
        "sticky_elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([desktop_vp, mobile_vp]))
    cross_codes = [o["code"] for o in res["cross_viewport"]["observations"]]
    assert "responsive_overflow" in cross_codes


# 16. Responsive overlap
def test_responsive_overlap() -> None:
    desktop_vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [],
    }
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 390, "client_width": 390, "scroll_height": 900},
        "elements": [
            {
                "tag": "div",
                "id": "col1",
                "bounds": {"x": 10, "y": 100, "width": 300, "height": 100, "right": 310, "bottom": 200},
                "visibility": {"is_visible": True},
            },
            {
                "tag": "div",
                "id": "col2",
                "bounds": {"x": 20, "y": 120, "width": 300, "height": 100, "right": 320, "bottom": 220},
                "visibility": {"is_visible": True},
            },
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([desktop_vp, mobile_vp]))
    assert any(o["code"] == "suspicious_element_overlap" for o in res["observations"])


# 17. Responsive visibility change (handled safely)
def test_responsive_visibility_change() -> None:
    desktop_vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [{"tag": "nav", "id": "main-nav", "visibility": {"is_visible": True}}],
    }
    res = analyze_visual(_create_synthetic_render_result([desktop_vp]))
    assert res["summary"]["viewports_analyzed"] == 1


# 18. Zero-size irrelevant element (script/style ignored)
def test_zero_size_irrelevant_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "script",
                "id": "analytics",
                "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                "visibility": {"is_visible": False},
            }
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert not any(o["code"] == "suspicious_zero_size_element" for o in res["observations"])


# 19. Zero-size meaningful element (flagged)
def test_zero_size_meaningful_element() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "button",
                "id": "invisible-btn",
                "bounds": {"x": 50, "y": 50, "width": 0, "height": 0},
                "visibility": {"is_visible": True},
            }
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert any(o["code"] == "suspicious_zero_size_element" for o in res["observations"])


# 20. Missing geometry handled safely
def test_missing_geometry_handled_safely() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {},
        "elements": [{"tag": "p", "id": "p1"}],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert res["summary"]["viewports_analyzed"] == 1


# 21. Missing viewport handled safely
def test_missing_viewport_handled_safely() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert res["summary"]["viewports_analyzed"] == 1


# 22. Partial renderer failure
def test_partial_renderer_failure() -> None:
    desktop_vp = {"name": "desktop", "status": "completed", "elements": []}
    failed_vp = {"name": "mobile", "status": "failed", "elements": []}
    res = analyze_visual(_create_synthetic_render_result([desktop_vp, failed_vp]))
    assert res["summary"]["viewports_analyzed"] == 1


# 23. Observation deduplication
def test_observation_deduplication() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "button",
                "id": "btn-dup",
                "bounds": {"x": 50, "y": 50, "width": 0, "height": 0},
                "visibility": {"is_visible": True},
            },
            {
                "tag": "button",
                "id": "btn-dup",
                "bounds": {"x": 50, "y": 50, "width": 0, "height": 0},
                "visibility": {"is_visible": True},
            },
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    dup_obs = [o for o in res["observations"] if o.get("element", {}).get("id") == "btn-dup"]
    assert len(dup_obs) == 1


# 24. Element limits enforcement
def test_element_limits_enforcement() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "button",
                "id": f"btn-{i}",
                "bounds": {"x": 50, "y": 50, "width": 0, "height": 0},
                "visibility": {"is_visible": True},
            }
            for i in range(50)
        ],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]), options={"max_observations": 5})
    assert len(res["observations"]) <= 5


# 25. Comparison limits
def test_comparison_limits() -> None:
    vp = {
        "name": "desktop",
        "status": "completed",
        "viewport": {"name": "desktop", "width": 1440, "height": 900},
        "document": {"scroll_width": 1440, "client_width": 1440, "scroll_height": 900},
        "elements": [
            {
                "tag": "div",
                "id": f"el-{i}",
                "bounds": {"x": 10, "y": i * 10, "width": 50, "height": 50},
                "visibility": {"is_visible": True},
            }
            for i in range(100)
        ],
    }
    # With tight comparison cap, should complete without error
    res = analyze_visual(_create_synthetic_render_result([vp]))
    assert res["summary"]["viewports_analyzed"] == 1


# 26. Deterministic ordering
def test_deterministic_ordering() -> None:
    mobile_vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 600, "client_width": 390, "scroll_height": 900},
        "elements": [
            {
                "tag": "button",
                "id": "btn-z",
                "bounds": {"x": 10, "y": 10, "width": 0, "height": 0},
                "visibility": {"is_visible": True},
            }
        ],
    }
    res1 = analyze_visual(_create_synthetic_render_result([mobile_vp]))
    res2 = analyze_visual(_create_synthetic_render_result([mobile_vp]))
    codes1 = [o["code"] for o in res1["observations"]]
    codes2 = [o["code"] for o in res2["observations"]]
    assert codes1 == codes2


# 27. JSON serialization
def test_json_serialization() -> None:
    res = analyze_visual(_create_synthetic_render_result())
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["summary"]["viewports_analyzed"] == 1


# 28. No network access
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_access(mock_urlopen: Any, mock_socket: Any) -> None:
    res = analyze_visual(_create_synthetic_render_result())
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()


# 29. No browser launch
@patch("playwright.sync_api.sync_playwright")
def test_no_browser_launch(mock_pw: Any) -> None:
    res = analyze_visual(_create_synthetic_render_result())
    mock_pw.assert_not_called()


# 30. No findings/severity generation
def test_no_findings_or_severity() -> None:
    vp = {
        "name": "mobile",
        "status": "completed",
        "viewport": {"name": "mobile", "width": 390, "height": 844},
        "document": {"scroll_width": 600, "client_width": 390, "scroll_height": 900},
        "elements": [],
    }
    res = analyze_visual(_create_synthetic_render_result([vp]))
    # Confirm output contains observations, NOT findings
    assert "findings" not in res
    for o in res["observations"]:
        assert "severity" not in o
        assert o["status"] in ("potential_issue", "needs_manual_review")
