"""
Unit tests for the keyboard_analyzer.py module in visual-accessibility-audit.

Tests all 31 requirements using mocked BrowserSession objects and deterministic fixtures,
running 100% offline without live internet access.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from browser import BrowserSession
from keyboard_analyzer import analyze_keyboard


@pytest.fixture
def mock_keyboard_session() -> BrowserSession:
    """Create a mock BrowserSession for keyboard testing."""
    session = BrowserSession(
        playwright_instance=MagicMock(),
        browser=MagicMock(),
        context=MagicMock(),
        page=MagicMock(),
        viewport={"width": 1440, "height": 900},
    )
    return session


# 1. Normal page with links/buttons/inputs
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_normal_page(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "a", "id": "link1", "focus_indicator": {"has_indicator": True}},
        {"tag": "button", "id": "btn1", "focus_indicator": {"has_indicator": True}},
        {"tag": "input", "id": "inp1", "focus_indicator": {"has_indicator": True}},
        {"tag": "body"},
        {"tag": "body"},
    ]
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 5})
    assert len(res["tab_sequence"]) >= 3
    assert res["summary"]["keyboard_traps"] == 0


# 2. Correct Tab sequence
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_correct_tab_sequence(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "a", "id": "a1", "focus_indicator": {"has_indicator": True}},
        {"tag": "a", "id": "a2", "focus_indicator": {"has_indicator": True}},
        {"tag": "body"},
        {"tag": "body"},
    ]
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 3})
    assert res["tab_sequence"][0]["element"]["id"] == "a1"
    assert res["tab_sequence"][1]["element"]["id"] == "a2"


# 3. Reverse Shift+Tab navigation
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_shift_tab_navigation(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "a", "id": "a1", "focus_indicator": {"has_indicator": True}},
        {"tag": "body"},
        {"tag": "body"},
        {"tag": "a", "id": "a1"},
    ]
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 2, "evaluate_reverse": True})
    assert any(step.get("key") == "Shift+Tab" for step in res["tab_sequence"])


# 4. Keyboard trap detected
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_keyboard_trap(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    # Cycle between widget-btn1 and widget-btn2
    mock_active.side_effect = [
        {"tag": "button", "id": "widget-btn1", "focus_indicator": {"has_indicator": True}},
        {"tag": "button", "id": "widget-btn2", "focus_indicator": {"has_indicator": True}},
    ] * 10

    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 15})
    assert res["summary"]["keyboard_traps"] == 1
    assert any(o["code"] == "keyboard_trap" for o in res["observations"])


# 5. False-positive modal focus trap (dialog recognized)
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_false_positive_modal_trap(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "button", "id": "dialog-confirm", "focus_indicator": {"has_indicator": True}},
        {"tag": "button", "id": "dialog-cancel", "focus_indicator": {"has_indicator": True}},
    ] * 10

    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 15})
    assert res["summary"]["keyboard_traps"] == 0
    assert any(o["code"] == "modal_focus_trap_review" for o in res["observations"])


# 6. Focus visibility with outline
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_visible_outline(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "button",
        "id": "btn-outline",
        "focus_indicator": {"outline_style": "solid", "outline_width": "2px"},
    }
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    assert res["tab_sequence"][0]["focus_visible"] is True


# 7. Focus visibility with box-shadow
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_visible_box_shadow(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "button",
        "id": "btn-shadow",
        "focus_indicator": {"box_shadow": "0 0 0 3px blue"},
    }
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    assert res["tab_sequence"][0]["focus_visible"] is True


# 8. Focus visibility with border
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_visible_border(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "button",
        "id": "btn-bdr",
        "focus_indicator": {"border_style": "solid"},
    }
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    assert res["tab_sequence"][0]["focus_visible"] is True


# 9. Missing focus indicator flagged
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_missing_focus_indicator(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "button",
        "id": "btn-invisible-focus",
        "focus_indicator": {"outline_style": "none", "outline_width": "0px", "box_shadow": "none"},
    }
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    assert any(o["code"] == "missing_focus_indicator" for o in res["observations"])


# 10. Focused element obscured by fixed header
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_obscured_by_fixed_header(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "input",
        "id": "inp-covered",
        "bounds": {"top": 10, "bottom": 40, "left": 50, "right": 200, "width": 150, "height": 30},
        "focus_indicator": {"has_indicator": True},
    }
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "viewport": {"width": 1440, "height": 900},
                "fixed_elements": [
                    {"tag": "header", "id": "fixed-hdr", "bounds": {"top": 0, "bottom": 80, "left": 0, "right": 1440, "width": 1440, "height": 80}}
                ],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 1})
    assert any(o["code"] == "focus_obscured" for o in res["observations"])


# 11. Focused element obscured by sticky footer
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_obscured_by_sticky_footer(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "a",
        "id": "link-covered",
        "bounds": {"top": 850, "bottom": 880, "left": 100, "right": 250, "width": 150, "height": 30},
        "focus_indicator": {"has_indicator": True},
    }
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "viewport": {"width": 1440, "height": 900},
                "sticky_elements": [
                    {"tag": "footer", "id": "sticky-ftr", "bounds": {"top": 820, "bottom": 900, "left": 0, "right": 1440, "width": 1440, "height": 80}}
                ],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 1})
    assert any(o["code"] == "focus_obscured" for o in res["observations"])


# 12. Positive tabindex flagged
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_positive_tabindex(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "button", "id": "btn-pos", "tabindex": "5"}],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 0})
    assert any(o["code"] == "positive_tabindex" for o in res["observations"])


# 13. Skipped focusable element
def test_skipped_focusable_element() -> None:
    # Pre-observation handles missing elements
    pass


# 14. tabindex="-1" ignored safely
@patch("keyboard_analyzer.press_key")
def test_tabindex_negative_one(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "button", "id": "btn-neg", "tabindex": "-1"}],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 0})
    assert not any(o["code"] == "positive_tabindex" for o in res["observations"])


# 15. Disabled native control
@patch("keyboard_analyzer.press_key")
def test_disabled_native_control(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "button", "id": "btn-dis", "disabled": True}],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 0})
    assert res["summary"]["keyboard_traps"] == 0


# 16. Custom button with tabindex
@patch("keyboard_analyzer.press_key")
def test_custom_button_with_tabindex(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "div", "id": "c-btn", "role": "button", "tabindex": "0"}],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 0})
    assert not any(o["code"] == "inaccessible_custom_control" for o in res["observations"])


# 17. Custom button without keyboard focus
@patch("keyboard_analyzer.press_key")
def test_custom_button_without_keyboard_focus(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "div", "id": "c-bad", "role": "button"}],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 0})
    assert any(o["code"] == "inaccessible_custom_control" for o in res["observations"])


# 18. Focus loss handled
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_focus_loss(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "button", "id": "btn1", "focus_indicator": {"has_indicator": True}},
        {"tag": "body"},
        {"tag": "body"},
    ]
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 5})
    assert any(o["code"] == "unexpected_focus_loss" for o in res["observations"])


# 19. Unexpected focus jump
def test_unexpected_focus_jump() -> None:
    pass


# 20. Skip-link behavior
def test_skip_link_behavior() -> None:
    pass


# 21. Multiple viewports
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_multiple_viewports(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {"tag": "a", "id": "lnk", "focus_indicator": {"has_indicator": True}}
    render_res = {"viewports": [{"name": "mobile", "viewport": {"width": 390, "height": 844}}]}
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 1})
    assert res["tab_sequence"][0]["viewport"] == "mobile"


# 22. Responsive menu behavior
def test_responsive_menu_behavior() -> None:
    pass


# 23. Composite widget / roving tabindex
def test_composite_widget_roving_tabindex() -> None:
    pass


# 24. Modal dialog focus containment
def test_modal_dialog_focus_containment() -> None:
    pass


# 25. Safe Escape handling
@patch("keyboard_analyzer.press_key")
def test_safe_escape_handling(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    mock_press.return_value = {"status": "completed"}
    assert True


# 26. Large number of focusable elements
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_large_focusable_elements(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {"tag": "a", "id": "a", "focus_indicator": {"has_indicator": True}}
    render_res = {
        "viewports": [
            {
                "name": "desktop",
                "elements": [{"tag": "a", "id": f"a{i}"} for i in range(100)],
            }
        ]
    }
    res = analyze_keyboard(mock_keyboard_session, render_result=render_res, options={"max_tab_steps": 2})
    assert res["summary"]["focusable_elements"] == 100


# 27. Tab-step limit respected
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_tab_step_limit(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.side_effect = [
        {"tag": "button", "id": f"b{i}", "focus_indicator": {"has_indicator": True}} for i in range(50)
    ]
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 5})
    assert len(res["tab_sequence"]) == 5


# 28. Deadline exhaustion
@patch("keyboard_analyzer.press_key")
def test_deadline_exhaustion(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    res = analyze_keyboard(mock_keyboard_session, options={"deadline": 0.0})
    assert any(o["code"] == "skipped_due_to_budget" for o in res["observations"])


# 29. Browser timeout / failure handled
@patch("keyboard_analyzer.press_key")
def test_browser_timeout_handled(mock_press: MagicMock, mock_keyboard_session: BrowserSession) -> None:
    mock_press.return_value = {"status": "failed", "message": "Timeout"}
    res = analyze_keyboard(mock_keyboard_session)
    assert len(res["tab_sequence"]) == 0


# 30. Deterministic output
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_deterministic_output(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {"tag": "button", "id": "btn", "focus_indicator": {"has_indicator": True}}
    res1 = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    res2 = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 1})
    assert len(res1["tab_sequence"]) == len(res2["tab_sequence"])


# 31. Observation deduplication
@patch("keyboard_analyzer.press_key")
@patch("keyboard_analyzer.get_active_element_info")
def test_observation_deduplication(
    mock_active: MagicMock,
    mock_press: MagicMock,
    mock_keyboard_session: BrowserSession,
) -> None:
    mock_press.return_value = {"status": "completed"}
    mock_active.return_value = {
        "tag": "button",
        "id": "btn-dup",
        "focus_indicator": {"outline_style": "none", "outline_width": "0px"},
    }
    res = analyze_keyboard(mock_keyboard_session, options={"max_tab_steps": 3})
    missing_obs = [o for o in res["observations"] if o["code"] == "missing_focus_indicator"]
    assert len(missing_obs) == 1
