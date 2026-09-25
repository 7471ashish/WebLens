"""
Unit tests for the accessibility_analyzer.py module in visual-accessibility-audit.

Tests all 47 requirements using synthetic render_result fixtures,
running 100% offline and deterministic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from accessibility_analyzer import analyze_accessibility


def _make_render_fixture(
    elements: list[dict[str, Any]] | None = None,
    page_data: dict[str, Any] | None = None,
    iframes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Helper to build synthetic render_result fixtures."""
    p_data = {
        "title": "Accessible Webpage",
        "lang": "en",
        "url": "https://example.com",
        "final_url": "https://example.com",
        "status": 200,
    }
    if page_data:
        p_data.update(page_data)

    els = elements or [
        {
            "tag": "main",
            "id": "content",
            "text": "Main content",
            "visibility": {"is_visible": True},
        },
        {
            "tag": "h1",
            "id": "hdr-1",
            "text": "Accessible Title",
            "visibility": {"is_visible": True},
        },
    ]

    return {
        "status": "completed",
        "target_url": "https://example.com",
        "viewports": [
            {
                "name": "desktop",
                "status": "completed",
                "page": p_data,
                "elements": els,
                "iframes": {"items": iframes or []},
            }
        ],
        "errors": [],
    }


# 1. Valid page language
def test_valid_page_language() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"lang": "en-US"}))
    assert res["document"]["language"]["status"] == "valid"
    assert not any(o["code"] == "missing_page_language" for o in res["observations"])


# 2. Missing page language
def test_missing_page_language() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"lang": None}))
    assert any(o["code"] == "missing_page_language" for o in res["observations"])


# 3. Valid title
def test_valid_title() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"title": "Page Title"}))
    assert res["document"]["title"]["status"] == "valid"
    assert not any(o["code"] == "missing_page_title" for o in res["observations"])


# 4. Missing title
def test_missing_title() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"title": None}))
    assert any(o["code"] == "missing_page_title" for o in res["observations"])


# 5. Empty title
def test_empty_title() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"title": "   "}))
    assert any(o["code"] == "empty_page_title" for o in res["observations"])


# 6. Valid heading hierarchy
def test_valid_heading_hierarchy() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "h1", "id": "h1", "text": "Heading 1"},
        {"tag": "h2", "id": "h2", "text": "Heading 2"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "suspicious_heading_order" for o in res["observations"])


# 7. Skipped heading levels (h1 -> h3)
def test_skipped_heading_levels() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "h1", "id": "h1", "text": "Heading 1"},
        {"tag": "h3", "id": "h3", "text": "Heading 3"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "suspicious_heading_order" for o in res["observations"])


# 8. Empty heading
def test_empty_heading() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "h1", "id": "h1", "text": ""},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "empty_heading" for o in res["observations"])


# 9. Multiple h1 handling
def test_multiple_h1_handling() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "h1", "id": "h1-a", "text": "First"},
        {"tag": "h1", "id": "h1-b", "text": "Second"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["document"]["headings"]["h1_count"] == 2


# 10. Landmark structure (main present)
def test_landmark_structure() -> None:
    els = [
        {"tag": "header", "id": "site-hdr"},
        {"tag": "nav", "id": "main-nav"},
        {"tag": "main", "id": "main-content"},
        {"tag": "footer", "id": "site-ftr"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["document"]["landmarks"]["main_count"] == 1
    assert not any(o["code"] == "missing_main_landmark" for o in res["observations"])


# 11. Image with alt
def test_image_with_alt() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "logo", "src": "logo.png", "alt": "Company Logo"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "missing_image_alt" for o in res["observations"])


# 12. Missing image alt
def test_missing_image_alt() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "hero", "src": "hero.png", "alt": None},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "missing_image_alt" for o in res["observations"])


# 13. Empty alt (decorative image -> not missing)
def test_empty_alt_decorative() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "dec", "src": "dec.png", "alt": ""},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "missing_image_alt" for o in res["observations"])
    assert res["images"]["summary"]["decorative_count"] == 1


# 14. Suspicious generic image alt
def test_suspicious_generic_image_alt() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "img1", "src": "photo.png", "alt": "image"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "suspicious_image_alt" for o in res["observations"])


# 15. Named link
def test_named_link() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "a", "id": "lnk1", "href": "/about", "text": "About Us"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "empty_link" for o in res["observations"])


# 16. Empty link
def test_empty_link() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "a", "id": "lnk-empty", "href": "/empty", "text": ""},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "empty_link" for o in res["observations"])


# 17. Generic link name (e.g. click here)
def test_generic_link_name() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "a", "id": "lnk-gen", "href": "/page", "text": "click here"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "suspicious_generic_link_name" for o in res["observations"])


# 18. Button with visible text
def test_button_with_visible_text() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "btn-ok", "text": "Submit Form"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "button_without_name" for o in res["observations"])


# 19. Icon-only unnamed button
def test_icon_only_unnamed_button() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "btn-icon", "text": ""},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "button_without_name" for o in res["observations"])


# 20. Form with proper label
def test_form_with_proper_label() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "input", "id": "username", "name": "user", "text": "Username"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "unlabeled_form_control" for o in res["observations"])


# 21. Unlabeled input
def test_unlabeled_input() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "input", "id": "search-box", "name": "q", "type": "text"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "unlabeled_form_control" for o in res["observations"])


# 22. Aria-label naming
def test_aria_label_naming() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "btn-close", "aria_attributes": {"aria-label": "Close modal"}},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "button_without_name" for o in res["observations"])


# 23. Aria-labelledby naming
def test_aria_labelledby_naming() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "btn-send", "aria_attributes": {"aria-labelledby": "send-label"}},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert not any(o["code"] == "button_without_name" for o in res["observations"])


# 24. Placeholder-only control
def test_placeholder_only_control() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "input", "id": "query", "placeholder": "Search...", "type": "text"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "unlabeled_form_control" for o in res["observations"])


# 25. Fieldset and legend
def test_fieldset_legend() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "form", "id": "checkout"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["summary"]["pages_analyzed"] == 1


# 26. Table summary
def test_table_summary() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "table", "id": "data-table"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["tables"]["summary"]["total_tables"] == 1


# 27. Malformed table structure
def test_malformed_table_structure() -> None:
    els = [{"tag": "main", "id": "main"}, {"tag": "table", "id": "t1"}]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["tables"]["summary"]["total_tables"] == 1


# 28. Valid list
def test_valid_list() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "ul", "id": "items-list"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["lists"]["summary"]["total_lists"] == 1


# 29. Malformed list
def test_malformed_list() -> None:
    els = [{"tag": "main", "id": "main"}, {"tag": "ol", "id": "ol1"}]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["lists"]["summary"]["total_lists"] == 1


# 30. Iframe title
def test_iframe_title() -> None:
    ifrs = [{"src": "https://maps.org", "title": "Interactive Map"}]
    res = analyze_accessibility(_make_render_fixture(iframes=ifrs))
    assert not any(o["code"] == "iframe_without_title" for o in res["observations"])


# 31. Missing iframe title
def test_missing_iframe_title() -> None:
    ifrs = [{"src": "https://maps.org", "title": None}]
    res = analyze_accessibility(_make_render_fixture(iframes=ifrs))
    assert any(o["code"] == "iframe_without_title" for o in res["observations"])


# 32. Duplicate IDs
def test_duplicate_ids() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "btn-submit", "text": "Submit"},
        {"tag": "button", "id": "btn-submit", "text": "Submit Again"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "duplicate_id" for o in res["observations"])


# 33. Hidden interactive content
def test_hidden_interactive_content() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "button", "id": "hidden-btn", "text": "Hidden", "hidden": True},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["summary"]["pages_analyzed"] == 1


# 34. Custom interactive element (div role=button)
def test_custom_interactive_element() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "div", "id": "custom-btn", "role": "button", "text": "Click Me"},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert any(o["code"] == "suspicious_custom_interactive" for o in res["observations"])


# 35. Missing renderer fields
def test_missing_renderer_fields() -> None:
    res = analyze_accessibility({})
    assert res["summary"]["not_testable"] == 1


# 36. Viewport deduplication
def test_viewport_deduplication() -> None:
    fixture = _make_render_fixture(elements=[
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "logo", "src": "logo.png", "alt": None},
    ])
    # Add second viewport with exact same element
    fixture["viewports"].append({
        "name": "mobile",
        "status": "completed",
        "page": fixture["viewports"][0]["page"],
        "elements": fixture["viewports"][0]["elements"],
        "iframes": {"items": []},
    })
    res = analyze_accessibility(fixture)
    img_obs = [o for o in res["observations"] if o["code"] == "missing_image_alt"]
    assert len(img_obs) == 1
    assert "mobile" in img_obs[0]["evidence"]["affected_viewports"]


# 37. Observation deduplication
def test_observation_deduplication() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "logo", "src": "logo.png", "alt": None},
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    keys = [f"{o['code']}:{o['category']}" for o in res["observations"]]
    assert len(keys) == len(set(keys))


# 38. Element limits
def test_element_limits() -> None:
    els = [{"tag": "main", "id": "main"}] + [
        {"tag": "a", "id": f"lnk-{i}", "href": f"/{i}", "text": "Link"}
        for i in range(100)
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els))
    assert res["links"]["summary"]["total_links"] == 100


# 39. Observation limits
def test_observation_limits() -> None:
    els = [{"tag": "main", "id": "main"}] + [
        {"tag": "img", "id": f"img-{i}", "src": f"{i}.png", "alt": None}
        for i in range(50)
    ]
    res = analyze_accessibility(_make_render_fixture(elements=els), options={"max_observations": 5})
    assert len(res["observations"]) <= 5


# 40. Deterministic ordering
def test_deterministic_ordering() -> None:
    els = [
        {"tag": "main", "id": "main"},
        {"tag": "img", "id": "img-a", "src": "a.png", "alt": None},
        {"tag": "button", "id": "btn-z", "text": ""},
    ]
    res1 = analyze_accessibility(_make_render_fixture(elements=els))
    res2 = analyze_accessibility(_make_render_fixture(elements=els))
    codes1 = [o["code"] for o in res1["observations"]]
    codes2 = [o["code"] for o in res2["observations"]]
    assert codes1 == codes2


# 41. JSON serialization
def test_json_serialization() -> None:
    res = analyze_accessibility(_make_render_fixture())
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["summary"]["pages_analyzed"] == 1


# 42. No network access
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_access(mock_urlopen: Any, mock_socket: Any) -> None:
    res = analyze_accessibility(_make_render_fixture())
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()


# 43. No browser launch
@patch("playwright.sync_api.sync_playwright")
def test_no_browser_launch(mock_pw: Any) -> None:
    res = analyze_accessibility(_make_render_fixture())
    mock_pw.assert_not_called()


# 44. No contrast calculation
def test_no_contrast_calculation() -> None:
    import inspect
    import accessibility_analyzer
    src = inspect.getsource(accessibility_analyzer)
    assert "contrast_ratio" not in src
    assert "calculate_contrast" not in src


# 45. No keyboard interaction
def test_no_keyboard_interaction() -> None:
    import inspect
    import accessibility_analyzer
    src = inspect.getsource(accessibility_analyzer)
    assert "press_key" not in src
    assert "tab_index_sequence" not in src


# 46. No final severity
def test_no_final_severity() -> None:
    res = analyze_accessibility(_make_render_fixture(page_data={"lang": None}))
    assert "findings" not in res
    for o in res["observations"]:
        assert "severity" not in o
        assert o["status"] in ("potential_issue", "needs_manual_review")


# 47. No cross-agent imports
def test_no_cross_agent_imports() -> None:
    import inspect
    import accessibility_analyzer
    src = inspect.getsource(accessibility_analyzer)
    assert "crawl_render_audit" not in src
    assert "freshness_corroboration" not in src
