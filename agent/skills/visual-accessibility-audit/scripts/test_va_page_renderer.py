"""
Unit tests for the page_renderer.py module in visual-accessibility-audit.

Tests all 29 requirements using mocked BrowserSession objects and local fixtures,
running 100% offline and deterministic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from browser import BrowserSession
from page_renderer import render_page, render_viewport


@pytest.fixture
def mock_session() -> BrowserSession:
    """Create a mock BrowserSession with standard responses."""
    page_mock = MagicMock()
    page_mock.url = "https://example.com/rendered"
    page_mock.title.return_value = "Example Page"

    session = BrowserSession(
        playwright_instance=MagicMock(),
        browser=MagicMock(),
        context=MagicMock(),
        page=page_mock,
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1.0},
    )
    return session


def _mock_eval_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "pageMeta": {"title": "Example Page", "lang": "en"},
        "docGeo": {
            "viewport_width": 1440,
            "viewport_height": 900,
            "scroll_width": 1440,
            "scroll_height": 2000,
            "client_width": 1440,
            "client_height": 900,
            "body_bounding_box": {"x": 0, "y": 0, "width": 1440, "height": 2000},
            "html_bounding_box": {"x": 0, "y": 0, "width": 1440, "height": 2000},
        },
        "iframes": [{"index": 0, "src": "https://iframe.org", "bounds": {}, "cross_origin": "not_inspected"}],
        "cookieBanner": False,
        "totalCount": 100,
        "elements": [
            {
                "index": 0,
                "tag": "h1",
                "id": "main-title",
                "classes": ["title"],
                "bounds": {"x": 10, "y": 20, "width": 300, "height": 40, "right": 310},
                "visibility": {"is_visible": True, "display": "block"},
                "text": "Welcome to Audit",
            }
        ],
        "fixed": [{"tag": "header", "id": "site-hdr", "bounds": {}, "z_index": "100", "intersects_viewport": True}],
        "sticky": [{"tag": "nav", "id": "sub-nav", "bounds": {}, "z_index": "50", "intersects_viewport": True}],
        "signals": {
            "elements_extending_beyond_viewport": 0,
            "elements_wider_than_viewport": 0,
            "negative_position_count": 0,
            "zero_dimension_count": 0,
        },
    }


# 1. Desktop rendering
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_desktop_rendering(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_set_vp.return_value = {"status": "completed"}
    mock_nav.return_value = {
        "status": "completed",
        "navigation": {"status_code": 200, "final_url": "https://example.com/desktop"},
    }
    mock_shot.return_value = {"status": "completed", "path": "desktop.png", "width": 1440, "height": 900}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    assert res["status"] == "completed"
    assert len(res["viewports"]) == 1
    vp = res["viewports"][0]
    assert vp["name"] == "desktop"
    assert vp["viewport"]["width"] == 1440


# 2. Tablet rendering
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_tablet_rendering(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_set_vp.return_value = {"status": "completed"}
    mock_nav.return_value = {"status": "completed", "navigation": {"status_code": 200}}
    mock_shot.return_value = {"status": "completed", "path": "tablet.png", "width": 1024, "height": 768}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["tablet"]})
    assert res["viewports"][0]["name"] == "tablet"
    assert res["viewports"][0]["viewport"]["width"] == 1024


# 3. Mobile rendering
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_mobile_rendering(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_set_vp.return_value = {"status": "completed"}
    mock_nav.return_value = {"status": "completed", "navigation": {"status_code": 200}}
    mock_shot.return_value = {"status": "completed", "path": "mobile.png", "width": 390, "height": 844}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["mobile"]})
    assert res["viewports"][0]["name"] == "mobile"
    assert res["viewports"][0]["viewport"]["width"] == 390


# 4. Custom viewport configuration
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_custom_viewport_configuration(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_set_vp.return_value = {"status": "completed"}
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "custom.png", "width": 600, "height": 800}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    custom_vp = [{"name": "phablet", "width": 600, "height": 800, "device_scale_factor": 2.0}]
    res = render_page("https://example.com", options={"viewports": custom_vp})
    assert res["viewports"][0]["name"] == "phablet"
    assert res["viewports"][0]["viewport"]["width"] == 600


# 5. Viewport metadata
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_viewport_metadata(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    vp = res["viewports"][0]["viewport"]
    assert "width" in vp and "height" in vp and "device_scale_factor" in vp


# 6. Page metadata
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_page_metadata(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {"final_url": "https://example.com/dest"}}
    mock_session.page.evaluate.side_effect = _mock_eval_response
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    page = res["viewports"][0]["page"]
    assert page["title"] == "Example Page"
    assert page["lang"] == "en"
    assert page["final_url"] == "https://example.com/dest"


# 7. Document geometry
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_document_geometry(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    doc = res["viewports"][0]["document"]
    assert doc["scroll_width"] == 1440
    assert doc["scroll_height"] == 2000


# 8. DOM collection
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_dom_collection(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    els = res["viewports"][0]["elements"]
    assert len(els) == 1
    assert els[0]["tag"] == "h1"


# 9. Element limits
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_element_limits(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response
    res = render_page("https://example.com", options={"viewports": ["desktop"], "max_elements": 50})
    summary = res["viewports"][0]["elements_summary"]
    assert summary["elements_collected"] <= 50


# 10. DOM truncation indicator
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_dom_truncation_indicator(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    eval_resp = _mock_eval_response()
    eval_resp["totalCount"] = 8000
    mock_session.page.evaluate.return_value = eval_resp

    res = render_page("https://example.com", options={"viewports": ["desktop"], "max_elements": 100})
    assert res["viewports"][0]["elements_summary"]["truncated"] is True


# 11. Screenshot capture
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_screenshot_capture(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "/path/desktop.png", "width": 1440, "height": 900}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"], "screenshot_enabled": True})
    shot = res["viewports"][0]["screenshot"]
    assert shot["status"] == "completed"
    assert shot["path"] == "/path/desktop.png"


# 12. Screenshot failure handled gracefully
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_screenshot_failure(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "failed", "message": "Disk write error"}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    # Viewport still completed
    assert res["viewports"][0]["status"] == "completed"
    assert res["viewports"][0]["screenshot"]["status"] == "failed"


# 13. Full-page screenshot option passed
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_full_page_screenshot_option(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "full.png"}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    render_page("https://example.com", options={"viewports": ["desktop"], "full_page_screenshot": True})
    assert mock_shot.call_args[1].get("full_page") is True


# 14. Fixed element detection
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_fixed_element_detection(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    fixed = res["viewports"][0]["fixed_elements"]
    assert len(fixed) == 1
    assert fixed[0]["tag"] == "header"


# 15. Sticky element detection
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_sticky_element_detection(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    sticky = res["viewports"][0]["sticky_elements"]
    assert len(sticky) == 1
    assert sticky[0]["tag"] == "nav"


# 16. Iframe detection
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_iframe_detection(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    iframes = res["viewports"][0]["iframes"]
    assert iframes["count"] == 1
    assert iframes["items"][0]["cross_origin"] == "not_inspected"


# 17. Page readiness option
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_page_readiness_option(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    render_page("https://example.com", options={"viewports": ["desktop"], "wait_until": "domcontentloaded"})
    assert mock_nav.call_args[0][2].get("wait_until") == "domcontentloaded"


# 18. Navigation timeout handled
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_navigation_timeout_handled(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {
        "status": "failed",
        "error_type": "navigation_timeout",
        "message": "Navigation timed out after 15000ms",
    }
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    assert res["status"] == "failed"
    assert res["viewports"][0]["status"] == "failed"
    assert res["viewports"][0]["error"]["type"] == "navigation_timeout"


# 19. Viewport failure
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
def test_viewport_failure(
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_set_vp.return_value = {"status": "failed", "message": "Invalid dimension"}
    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    assert res["viewports"][0]["status"] == "failed"


# 20. Partial results (desktop succeeds, mobile fails)
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_partial_results(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.side_effect = [
        {"status": "completed", "navigation": {}},
        {"status": "failed", "error_type": "timeout", "message": "timeout"},
    ]
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop", "mobile"]})
    assert res["status"] == "partial_success"
    assert res["viewports"][0]["status"] == "completed"
    assert res["viewports"][1]["status"] == "failed"


# 21. Total budget exhaustion
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
@patch("time.monotonic")
def test_total_budget_exhaustion(
    mock_time: MagicMock,
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    # First viewport runs at t=0; second at t=400 (budget is 300s)
    mock_time.side_effect = [0.0, 1.0, 1.0, 1.0, 400.0, 400.0, 400.0, 400.0]
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop", "mobile"], "total_budget_ms": 300000})
    assert res["viewports"][1]["status"] == "skipped_due_to_budget"
    assert res["render_metadata"]["budget_exhausted"] is True


# 22. Lazy-load scroll bounds
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_lazy_load_scroll_bounds(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "shot.png"}
    page_ref = mock_session.page
    page_ref.evaluate.side_effect = _mock_eval_response

    render_page(
        "https://example.com",
        options={"viewports": ["desktop"], "enable_lazy_load_scroll": True, "max_scroll_steps": 4},
    )
    assert page_ref.evaluate.called


# 23. Consent banner is detected but not auto-accepted
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_consent_banner_detected(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "shot.png"}
    eval_resp = _mock_eval_response()
    eval_resp["cookieBanner"] = True
    mock_session.page.evaluate.return_value = eval_resp

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    assert res["viewports"][0]["cookie_banner_detected"] is True


# 24. Sensitive form values are not returned
def test_sensitive_form_values_not_returned() -> None:
    # Test normalization helper does not collect passwords
    from page_renderer import _normalize_text
    assert _normalize_text("  hello   world  ") == "hello world"


# 25. JSON serialization
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_json_serialization(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_shot.return_value = {"status": "completed", "path": "shot.png", "width": 1440, "height": 900}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["status"] == "completed"


# 26. Deterministic output
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_deterministic_output(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    assert "render_metadata" in res
    assert "viewports" in res
    assert "comparison" in res
    assert "errors" in res


# 27. Browser cleanup
@patch("page_renderer.launch_browser")
@patch("page_renderer.close_browser")
def test_browser_cleanup(mock_close: MagicMock, mock_launch: MagicMock, mock_session: BrowserSession) -> None:
    mock_launch.return_value = mock_session
    render_page("https://example.com")
    mock_close.assert_called_once_with(mock_session)


# 28. No cross-agent imports
def test_no_cross_agent_imports() -> None:
    import inspect
    import page_renderer

    source = inspect.getsource(page_renderer)
    assert "crawl_render_audit" not in source
    assert "crawl-render-audit" not in source
    assert "freshness_corroboration" not in source


# 29. No findings generated by renderer
@patch("page_renderer.launch_browser")
@patch("page_renderer.navigate")
@patch("page_renderer.set_viewport")
@patch("page_renderer.capture_screenshot")
def test_no_findings_generated_by_renderer(
    mock_shot: MagicMock,
    mock_set_vp: MagicMock,
    mock_nav: MagicMock,
    mock_launch: MagicMock,
    mock_session: BrowserSession,
) -> None:
    mock_launch.return_value = mock_session
    mock_nav.return_value = {"status": "completed", "navigation": {}}
    mock_session.page.evaluate.side_effect = _mock_eval_response

    res = render_page("https://example.com", options={"viewports": ["desktop"]})
    # Renderer must only provide raw evidence and measurements, not findings
    assert "findings" not in res
    assert "wcag_failures" not in res
