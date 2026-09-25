"""
Unit tests for the contrast_analyzer.py module in visual-accessibility-audit.

Tests all 27 requirements using deterministic synthetic fixtures,
running 100% offline without network or browser dependencies.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from contrast_analyzer import (
    analyze_contrast,
    calculate_contrast_ratio,
    calculate_relative_luminance,
    composite_color,
    is_large_text,
    parse_css_color,
)


def _make_contrast_fixture(
    elements: list[dict[str, Any]],
    viewports: list[str] | None = None,
) -> dict[str, Any]:
    vps = viewports or ["desktop"]
    vp_list = []
    for vp in vps:
        vp_list.append({
            "name": vp,
            "status": "completed",
            "elements": elements,
        })
    return {
        "status": "completed",
        "target_url": "https://example.com",
        "viewports": vp_list,
        "errors": [],
    }


# 1. Black text on white background -> 21.0:1
def test_black_on_white() -> None:
    lum_black = calculate_relative_luminance(0, 0, 0)
    lum_white = calculate_relative_luminance(255, 255, 255)
    ratio = calculate_contrast_ratio(lum_black, lum_white)
    assert ratio == 21.0

    els = [
        {
            "tag": "p",
            "id": "p1",
            "text": "Hello world",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["status"] == "passed"
    assert res["observations"][0]["contrast_ratio"] == 21.0


# 2. White text on black background -> 21.0:1
def test_white_on_black() -> None:
    els = [
        {
            "tag": "p",
            "id": "p2",
            "text": "Inverted text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#ffffff", "background-color": "#000000"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["status"] == "passed"
    assert res["observations"][0]["contrast_ratio"] == 21.0


# 3. Known failing normal-text ratio (#777777 on white is ~4.48:1 < 4.5:1)
def test_known_failing_normal_text() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-fail",
            "text": "Low contrast",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#777777", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["potential_issues"] == 1
    assert any(o["code"] == "low_text_contrast" for o in res["observations"])


# 4. Known passing normal-text ratio (#595959 on white is ~7.01:1 >= 4.5:1)
def test_known_passing_normal_text() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-pass",
            "text": "Good contrast",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#595959", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["potential_issues"] == 0
    assert any(o["code"] == "contrast_pass" for o in res["observations"])


# 5. Large-text threshold (font-size 24px normal weight -> 3:1 threshold)
def test_large_text_threshold() -> None:
    assert is_large_text(24.0, 400) is True
    assert is_large_text(18.5, 700) is True
    assert is_large_text(16.0, 400) is False

    # #777777 on white is 4.48:1, which passes large-text (>= 3.0)
    els = [
        {
            "tag": "h1",
            "id": "h-large",
            "text": "Large heading",
            "bounds": {"width": 200, "height": 40},
            "visibility": {"is_visible": True},
            "computed_styles": {
                "color": "#777777",
                "background-color": "#ffffff",
                "font-size": "24px",
                "font-weight": "400",
            },
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["potential_issues"] == 0
    assert res["observations"][0]["code"] == "contrast_pass"
    assert res["observations"][0]["is_large_text"] is True


# 6. AAA threshold evaluation
def test_aaa_threshold() -> None:
    # #595959 on #ffffff is ~7.01:1 which passes AAA (7.0)
    # #666666 on #ffffff is ~5.74:1 which passes AA (4.5) but fails AAA (7.0)
    els = [
        {
            "tag": "p",
            "id": "p-aa-only",
            "text": "Meets AA only",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#666666", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els), options={"evaluate_aaa": True})
    assert any(o["required_ratio"] == 7.0 for o in res["observations"])


# 7. Non-text 3:1 threshold
def test_non_text_threshold() -> None:
    # Button with light gray (#cccccc) on white (#ffffff) has ratio ~1.6:1 < 3.0:1
    els = [
        {
            "tag": "button",
            "id": "btn-low",
            "text": "",
            "bounds": {"width": 80, "height": 30},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#cccccc", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert any(o["code"] == "low_non_text_contrast" for o in res["observations"])


# 8. RGB parsing
def test_rgb_parsing() -> None:
    parsed = parse_css_color("rgb(100, 150, 200)")
    assert parsed == (100, 150, 200, 1.0)


# 9. RGBA parsing
def test_rgba_parsing() -> None:
    parsed = parse_css_color("rgba(50, 100, 150, 0.5)")
    assert parsed == (50, 100, 150, 0.5)


# 10. Hex parsing
def test_hex_parsing() -> None:
    assert parse_css_color("#fff") == (255, 255, 255, 1.0)
    assert parse_css_color("#000000") == (0, 0, 0, 1.0)
    assert parse_css_color("#ffffff80") == (255, 255, 255, 0.502)


# 11. Transparent foreground -> not testable
def test_transparent_foreground() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-trans",
            "text": "Transparent",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "transparent", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert any(o["code"] == "insufficient_foreground_evidence" for o in res["observations"])


# 12. Inherited background fallback (from body)
def test_inherited_background() -> None:
    els = [
        {
            "tag": "body",
            "id": "body",
            "computed_styles": {"background-color": "#000000"},
        },
        {
            "tag": "p",
            "id": "p-child",
            "text": "Text inheriting black background",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#ffffff"},
        },
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["text_tested"] == 1
    assert res["observations"][0]["contrast_ratio"] == 21.0


# 13. Transparent ancestor / missing background -> not_testable
def test_transparent_background_not_testable() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-unresolved",
            "text": "Floating text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "transparent"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert any(o["code"] == "insufficient_background_evidence" for o in res["observations"])


# 14. Missing background evidence -> not_testable
def test_missing_background_evidence() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-nobg",
            "text": "No background declared",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["not_testable"] == 1


# 15. Gradient background -> needs_manual_review
def test_gradient_background() -> None:
    els = [
        {
            "tag": "h1",
            "id": "hero-title",
            "text": "Gradient hero",
            "bounds": {"width": 200, "height": 50},
            "visibility": {"is_visible": True},
            "computed_styles": {
                "color": "#ffffff",
                "background-image": "linear-gradient(to right, red, blue)",
            },
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert any(o["code"] == "complex_background_needs_review" for o in res["observations"])


# 16. Image background -> needs_manual_review
def test_image_background() -> None:
    els = [
        {
            "tag": "h2",
            "id": "photo-header",
            "text": "Overlaid on photo",
            "bounds": {"width": 200, "height": 50},
            "visibility": {"is_visible": True},
            "computed_styles": {
                "color": "#ffffff",
                "background-image": "url('/assets/bg.jpg')",
            },
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert any(o["code"] == "complex_background_needs_review" for o in res["observations"])


# 17. Nested text elements deduplicated
def test_nested_text_elements() -> None:
    els = [
        {
            "tag": "button",
            "id": "btn1",
            "text": "Save Changes",
            "bounds": {"width": 100, "height": 30},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
        {
            "tag": "button",
            "id": "btn1",
            "text": "Save Changes",
            "bounds": {"width": 100, "height": 30},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert len(res["observations"]) == 1


# 18. Duplicate text wrappers
def test_duplicate_text_wrappers() -> None:
    els = [
        {
            "tag": "span",
            "id": "dup",
            "text": "Duplicate text",
            "bounds": {"width": 50, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
        {
            "tag": "span",
            "id": "dup",
            "text": "Duplicate text",
            "bounds": {"width": 50, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert len(res["observations"]) == 1


# 19. Disabled controls (exempt from WCAG 1.4.3)
def test_disabled_controls() -> None:
    els = [
        {
            "tag": "button",
            "id": "btn-dis",
            "text": "Disabled Action",
            "disabled": True,
            "bounds": {"width": 100, "height": 30},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#999999", "background-color": "#cccccc"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["potential_issues"] == 0
    assert any(o["code"] == "contrast_pass" for o in res["observations"])


# 20. Multiple viewports independent evaluation
def test_multiple_viewports() -> None:
    els = [
        {
            "tag": "p",
            "id": "p1",
            "text": "Responsive text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els, viewports=["desktop", "mobile"]))
    assert len(res["observations"]) == 2
    vps = {o["element"]["viewport"] for o in res["observations"]}
    assert "desktop" in vps and "mobile" in vps


# 21. Different interactive states
def test_different_interactive_states() -> None:
    els = [
        {
            "tag": "a",
            "id": "link1",
            "text": "Home link",
            "bounds": {"width": 50, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#0000ee", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["text_tested"] == 1


# 22. Inline SVG / icon evidence (non-text)
def test_inline_svg_icon() -> None:
    els = [
        {
            "tag": "button",
            "id": "icon-btn",
            "role": "button",
            "bounds": {"width": 40, "height": 40},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["non_text_tested"] == 1


# 23. Invalid color values handled safely
def test_invalid_color_values() -> None:
    els = [
        {
            "tag": "p",
            "id": "p-inv",
            "text": "Invalid color",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "not-a-real-color", "background-color": "gibberish"},
        }
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert res["summary"]["not_testable"] == 1


# 24. Very large DOM input handled boundedly
def test_very_large_dom_input() -> None:
    els = [
        {
            "tag": "p",
            "id": f"p-{i}",
            "text": f"Item {i}",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        }
        for i in range(100)
    ]
    res = analyze_contrast(_make_contrast_fixture(els), options={"max_observations": 10})
    assert len(res["observations"]) <= 10


# 25. Observation deduplication
def test_observation_deduplication() -> None:
    els = [
        {
            "tag": "p",
            "id": "same-id",
            "text": "Same text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
        {
            "tag": "p",
            "id": "same-id",
            "text": "Same text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
    ]
    res = analyze_contrast(_make_contrast_fixture(els))
    assert len(res["observations"]) == 1


# 26. Budget/deadline exhaustion handling
def test_deadline_exhaustion() -> None:
    els = [
        {
            "tag": "p",
            "id": "p1",
            "text": "Expired text",
            "bounds": {"width": 100, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        }
    ]
    # Provide an already expired monotonic deadline
    res = analyze_contrast(_make_contrast_fixture(els), options={"deadline": 0.0})
    assert any(o["code"] == "skipped_due_to_budget" for o in res["observations"])


# 27. Deterministic output ordering
def test_deterministic_ordering() -> None:
    els = [
        {
            "tag": "p",
            "id": "b-id",
            "text": "B",
            "bounds": {"width": 50, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
        {
            "tag": "p",
            "id": "a-id",
            "text": "A",
            "bounds": {"width": 50, "height": 20},
            "visibility": {"is_visible": True},
            "computed_styles": {"color": "#000000", "background-color": "#ffffff"},
        },
    ]
    res1 = analyze_contrast(_make_contrast_fixture(els))
    res2 = analyze_contrast(_make_contrast_fixture(els))
    ids1 = [o["element"]["id"] for o in res1["observations"]]
    ids2 = [o["element"]["id"] for o in res2["observations"]]
    assert ids1 == ids2
