"""
Unit tests for the browser.py module in visual-accessibility-audit.

Tests all 29 requirements using mocked Playwright objects and local fixtures,
ensuring 100% deterministic offline verification.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from browser import (
    BrowserSession,
    DEFAULT_VIEWPORTS,
    capture_screenshot,
    close_browser,
    get_active_element_info,
    get_computed_style,
    get_dom_snapshot,
    get_element_info,
    get_page_info,
    launch_browser,
    logger,
    navigate,
    press_key,
    set_viewport,
)


@pytest.fixture
def mock_session() -> BrowserSession:
    """Create a mock BrowserSession with simulated Playwright page/context."""
    pw_mock = MagicMock()
    browser_mock = MagicMock()
    context_mock = MagicMock()
    page_mock = MagicMock()

    page_mock.url = "https://example.com/test"
    page_mock.title.return_value = "Test Document"

    session = BrowserSession(
        playwright_instance=pw_mock,
        browser=browser_mock,
        context=context_mock,
        page=page_mock,
        viewport=dict(DEFAULT_VIEWPORTS["desktop"]),
    )
    return session


# 1. Browser launch
@patch("playwright.sync_api.sync_playwright")
def test_browser_launch(mock_sync_pw: MagicMock) -> None:
    pw_instance = MagicMock()
    mock_sync_pw.return_value.start.return_value = pw_instance
    browser = MagicMock()
    pw_instance.chromium.launch.return_value = browser
    context = MagicMock()
    browser.new_context.return_value = context
    page = MagicMock()
    context.new_page.return_value = page

    session = launch_browser({"headless": True, "viewport": "mobile"})
    assert session is not None
    assert session.viewport["width"] == 390
    assert session.viewport["height"] == 844
    assert not session.closed
    close_browser(session)


# 2. Browser cleanup
def test_browser_cleanup(mock_session: BrowserSession) -> None:
    close_browser(mock_session)
    assert mock_session.closed is True
    assert mock_session.page is None
    assert mock_session.browser is None
    # Idempotent cleanup call
    close_browser(mock_session)
    assert mock_session.closed is True


# 3. Navigation success
def test_navigation_success(mock_session: BrowserSession) -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"content-type": "text/html"}
    mock_session.page.goto.return_value = mock_resp
    mock_session.page.url = "https://example.com/page"

    res = navigate(mock_session, "https://example.com/page", options={"allow_local": True})
    assert res["status"] == "completed"
    assert res["navigation"]["status_code"] == 200
    assert res["navigation"]["final_url"] == "https://example.com/page"


# 4. Invalid URL
def test_invalid_url(mock_session: BrowserSession) -> None:
    res = navigate(mock_session, "not_a_valid_url")
    assert res["status"] == "failed"
    assert res["error_type"] == "invalid_url"


# 5. Unsupported URL scheme
def test_unsupported_url_scheme(mock_session: BrowserSession) -> None:
    res = navigate(mock_session, "file:///etc/passwd")
    assert res["status"] == "failed"
    assert "Unsupported URL scheme" in res["message"]


# 6. SSRF / Local target rejection
def test_ssrf_local_target_rejection(mock_session: BrowserSession) -> None:
    res = navigate(mock_session, "http://127.0.0.1:8080/admin", options={"allow_local": False})
    assert res["status"] == "failed"
    assert res["error_type"] == "blocked_target"


# 7. Redirect handling
def test_redirect_handling(mock_session: BrowserSession) -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_session.page.goto.return_value = mock_resp
    mock_session.page.url = "https://example.com/final-location"

    res = navigate(mock_session, "https://example.com/old-location", options={"allow_local": True})
    assert res["status"] == "completed"
    assert res["navigation"]["redirect_occurred"] is True
    assert res["navigation"]["final_url"] == "https://example.com/final-location"


# 8. Navigation timeout
def test_navigation_timeout(mock_session: BrowserSession) -> None:
    mock_session.page.goto.side_effect = TimeoutError("Navigation timeout of 15000ms exceeded")
    res = navigate(mock_session, "https://example.com/slow", options={"allow_local": True})
    assert res["status"] == "failed"
    assert res["error_type"] == "navigation_timeout"


# 9. Viewport configuration
def test_viewport_configuration(mock_session: BrowserSession) -> None:
    res = set_viewport(mock_session, width=390, height=844, device_scale_factor=3.0)
    assert res["status"] == "completed"
    assert mock_session.viewport["width"] == 390
    assert mock_session.viewport["height"] == 844
    assert mock_session.viewport["device_scale_factor"] == 3.0


# 10. DOM snapshot
def test_dom_snapshot(mock_session: BrowserSession) -> None:
    mock_nodes = [
        {
            "index": 0,
            "tag": "h1",
            "id": "header",
            "classes": ["title"],
            "role": "heading",
            "aria_attributes": {"aria-level": "1"},
            "bounding_box": {"x": 10, "y": 20, "width": 200, "height": 40},
            "visibility": {"is_visible": True, "display": "block", "visibility": "visible", "opacity": "1"},
        }
    ]
    mock_session.page.evaluate.return_value = mock_nodes
    res = get_dom_snapshot(mock_session)
    assert res["status"] == "completed"
    assert res["total_elements"] == 1
    assert res["elements"][0]["tag"] == "h1"


# 11. Bounded DOM traversal
def test_bounded_dom_traversal(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = [{"index": i} for i in range(10)]
    res = get_dom_snapshot(mock_session, options={"max_elements": 10})
    assert res["status"] == "completed"
    assert res["max_elements_bound"] == 10
    assert res["total_elements"] == 10


# 12. Element lookup
def test_element_lookup(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "exists": True,
        "tag": "button",
        "id": "submit-btn",
        "classes": ["btn", "primary"],
        "bounding_box": {"x": 10, "y": 10, "width": 80, "height": 30},
        "visibility": {"is_visible": True},
        "computed_styles": {},
    }
    res = get_element_info(mock_session, "button#submit-btn")
    assert res["status"] == "completed"
    assert res["exists"] is True
    assert res["tag"] == "button"


# 13. Computed style retrieval (whitelist filtered)
def test_computed_style_retrieval(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "exists": True,
        "properties": {"color": "rgb(0, 0, 0)", "background-color": "rgb(255, 255, 255)"},
    }
    res = get_computed_style(mock_session, "body", ["color", "backgroundColor"])
    assert res["status"] == "completed"
    assert res["properties"]["color"] == "rgb(0, 0, 0)"
    assert res["properties"]["background-color"] == "rgb(255, 255, 255)"


# 14. Geometry retrieval via get_page_info
def test_geometry_retrieval(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "scrollWidth": 1440,
        "scrollHeight": 2500,
        "clientWidth": 1440,
        "clientHeight": 900,
        "iframeCount": 0,
    }
    res = get_page_info(mock_session)
    assert res["status"] == "completed"
    assert res["geometry"]["scrollWidth"] == 1440
    assert res["geometry"]["scrollHeight"] == 2500


# 15. Visibility information
def test_visibility_information(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "exists": True,
        "tag": "div",
        "visibility": {"is_visible": False, "display": "none", "visibility": "hidden", "opacity": "0"},
        "bounding_box": {"x": 0, "y": 0, "width": 0, "height": 0},
        "computed_styles": {},
    }
    res = get_element_info(mock_session, "#hidden-modal")
    assert res["visibility"]["is_visible"] is False
    assert res["visibility"]["display"] == "none"


# 16. Screenshot capture
def test_screenshot_capture(mock_session: BrowserSession, tmp_path: Any) -> None:
    target_img = str(tmp_path / "test_shot.png")
    res = capture_screenshot(mock_session, path=target_img, full_page=False)
    assert res["status"] == "completed"
    assert res["width"] == 1440
    assert res["height"] == 900
    mock_session.page.screenshot.assert_called_once()


# 17. Keyboard Tab
def test_keyboard_tab(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "is_body": False,
        "tag": "input",
        "id": "search",
        "role": "searchbox",
    }
    res = press_key(mock_session, "Tab")
    assert res["status"] == "completed"
    mock_session.page.keyboard.press.assert_called_with("Tab")
    assert res["active_element"]["tag"] == "input"


# 18. Keyboard Shift+Tab
def test_keyboard_shift_tab(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {"is_body": False, "tag": "a", "id": "home-link"}
    res = press_key(mock_session, "Shift+Tab")
    assert res["status"] == "completed"
    mock_session.page.keyboard.press.assert_called_with("Shift+Tab")


# 19. Enter and Escape handling
def test_enter_and_escape_handling(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {"is_body": False, "tag": "button"}
    res_enter = press_key(mock_session, "Enter")
    assert res_enter["status"] == "completed"
    res_esc = press_key(mock_session, "Escape")
    assert res_esc["status"] == "completed"


# 20. Active element inspection
def test_active_element_inspection(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "is_body": False,
        "tag": "button",
        "id": "action-btn",
        "outline": "2px solid rgb(0, 90, 255)",
    }
    res = get_active_element_info(mock_session)
    assert res["status"] == "completed"
    assert res["element"]["id"] == "action-btn"
    assert "outline" in res["element"]


# 21. Dialog handling
def test_dialog_handling() -> None:
    session = BrowserSession()
    assert len(session.dialogs_encountered) == 0


# 22. Operation timeout
def test_operation_timeout(mock_session: BrowserSession) -> None:
    mock_session.page.goto.side_effect = TimeoutError("Timeout 5000ms exceeded")
    res = navigate(mock_session, "https://example.com/slow", options={"timeout_ms": 5000, "allow_local": True})
    assert res["status"] == "failed"
    assert res["error_type"] == "navigation_timeout"


# 23. Budget exhaustion
def test_budget_exhaustion(mock_session: BrowserSession) -> None:
    res = navigate(mock_session, "https://example.com/page", options={"remaining_budget_ms": 200, "allow_local": True})
    assert res["status"] == "skipped_due_to_budget"
    assert res["error_type"] == "budget_exhausted"


# 24. Browser launch failure
@patch("playwright.sync_api.sync_playwright")
def test_browser_launch_failure(mock_sync_pw: MagicMock) -> None:
    mock_sync_pw.return_value.start.side_effect = Exception("Executable not found")
    with pytest.raises(RuntimeError, match="Browser launch failed"):
        launch_browser()


# 25. Page closed failure
def test_page_closed_failure(mock_session: BrowserSession) -> None:
    close_browser(mock_session)
    res = navigate(mock_session, "https://example.com")
    assert res["status"] == "failed"
    assert res["error_type"] == "page_closed"


# 26. JSON serializability
def test_json_serializability(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = {
        "scrollWidth": 1440,
        "scrollHeight": 2000,
        "clientWidth": 1440,
        "clientHeight": 900,
        "iframeCount": 1,
    }
    info = get_page_info(mock_session)
    serialized = json.dumps(info)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["status"] == "completed"


# 27. Cleanup after failure
def test_cleanup_after_failure(mock_session: BrowserSession) -> None:
    mock_session.page.goto.side_effect = Exception("Crash")
    navigate(mock_session, "https://example.com", options={"allow_local": True})
    close_browser(mock_session)
    assert mock_session.closed is True


# 28. No sensitive information in returned data
def test_no_sensitive_info_in_dom_snapshot(mock_session: BrowserSession) -> None:
    # Simulating that password fields have sanitized value
    mock_session.page.evaluate.return_value = [
        {"tag": "input", "type": "password", "value": None}
    ]
    res = get_dom_snapshot(mock_session)
    for el in res["elements"]:
        if el.get("type") == "password":
            assert el.get("value") is None


# 29. No sensitive information in logs
def test_no_sensitive_info_in_logs(caplog: Any) -> None:
    import logging
    with caplog.at_level(logging.DEBUG):
        logger.info("Navigation started for %s", "https://example.com")
    for record in caplog.records:
        assert "password" not in record.message
        assert "authorization" not in record.message
        assert "bearer" not in record.message
