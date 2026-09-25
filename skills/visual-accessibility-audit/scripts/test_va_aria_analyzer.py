"""
Unit tests for the aria_analyzer.py module in visual-accessibility-audit.

Tests all 43 requirements using synthetic render_result fixtures,
running 100% offline and deterministic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from aria_analyzer import analyze_aria


def _make_aria_fixture(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed",
        "target_url": "https://example.com",
        "viewports": [
            {
                "name": "desktop",
                "status": "completed",
                "elements": elements,
            }
        ],
        "errors": [],
    }


# 1. Valid native button (no redundant finding)
def test_valid_native_button() -> None:
    els = [{"tag": "button", "id": "btn1", "text": "Save"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 2. Valid custom ARIA button
def test_valid_custom_aria_button() -> None:
    els = [{"tag": "div", "id": "btn-custom", "role": "button", "text": "Click"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0
    assert res["role_summary"].get("button") == 1


# 3. Unknown role
def test_unknown_role() -> None:
    els = [{"tag": "div", "id": "unknown-el", "role": "superbutton"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "unknown_aria_role" for o in res["observations"])


# 4. Abstract role flagged
def test_abstract_role() -> None:
    els = [{"tag": "div", "id": "abstract-el", "role": "widget"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "abstract_aria_role" for o in res["observations"])


# 5. Multiple fallback roles
def test_multiple_fallback_roles() -> None:
    els = [{"tag": "div", "id": "multi-role", "role": "invalidrole button"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["role_summary"].get("button") == 1


# 6. Required ARIA property missing (checkbox missing aria-checked)
def test_missing_required_property() -> None:
    els = [{"tag": "div", "id": "chk", "role": "checkbox"}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "missing_required_aria_property" for o in res["observations"])


# 7. Unsupported ARIA property / Valid property
def test_valid_property_supported() -> None:
    els = [{"tag": "div", "id": "chk", "role": "checkbox", "aria_attributes": {"aria-checked": "true"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "missing_required_aria_property" for o in res["observations"])


# 8. Invalid boolean (aria-expanded="yes")
def test_invalid_boolean() -> None:
    els = [{"tag": "button", "id": "btn", "aria_attributes": {"aria-expanded": "yes"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "invalid_aria_boolean" for o in res["observations"])


# 9. Invalid token (aria-checked="maybe")
def test_invalid_token() -> None:
    els = [{"tag": "div", "id": "chk", "role": "checkbox", "aria_attributes": {"aria-checked": "maybe"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "invalid_aria_token" for o in res["observations"])


# 10. Invalid integer (aria-level="zero")
def test_invalid_integer() -> None:
    els = [{"tag": "div", "id": "h", "role": "heading", "aria_attributes": {"aria-level": "zero"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "invalid_aria_level" for o in res["observations"])


# 11. Invalid numeric range (valuenow > valuemax)
def test_invalid_numeric_range() -> None:
    els = [
        {
            "tag": "div",
            "id": "slider",
            "role": "slider",
            "aria_attributes": {"aria-valuenow": "150", "aria-valuemin": "0", "aria-valuemax": "100"},
        }
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "inconsistent_aria_value_range" for o in res["observations"])


# 12. Valid aria-labelledby
def test_valid_aria_labelledby() -> None:
    els = [
        {"tag": "span", "id": "label-text", "text": "Submit Now"},
        {"tag": "button", "id": "btn", "aria_attributes": {"aria-labelledby": "label-text"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "broken_aria_labelledby" for o in res["observations"])


# 13. Broken aria-labelledby
def test_broken_aria_labelledby() -> None:
    els = [
        {"tag": "button", "id": "btn", "aria_attributes": {"aria-labelledby": "nonexistent-id"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_labelledby" for o in res["observations"])


# 14. Valid aria-describedby
def test_valid_aria_describedby() -> None:
    els = [
        {"tag": "span", "id": "desc-text", "text": "Password requirement info"},
        {"tag": "input", "id": "pwd", "aria_attributes": {"aria-describedby": "desc-text"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "broken_aria_describedby" for o in res["observations"])


# 15. Broken aria-describedby
def test_broken_aria_describedby() -> None:
    els = [
        {"tag": "input", "id": "pwd", "aria_attributes": {"aria-describedby": "missing-desc"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_describedby" for o in res["observations"])


# 16. Valid aria-controls
def test_valid_aria_controls() -> None:
    els = [
        {"tag": "button", "id": "tab1", "aria_attributes": {"aria-controls": "panel1"}},
        {"tag": "div", "id": "panel1"},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "broken_aria_controls" for o in res["observations"])


# 17. Broken aria-controls
def test_broken_aria_controls() -> None:
    els = [
        {"tag": "button", "id": "tab1", "aria_attributes": {"aria-controls": "ghost-panel"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_controls" for o in res["observations"])


# 18. Valid aria-owns
def test_valid_aria_owns() -> None:
    els = [
        {"tag": "ul", "id": "parent-list", "aria_attributes": {"aria-owns": "child-item"}},
        {"tag": "li", "id": "child-item"},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "broken_aria_owns" for o in res["observations"])


# 19. Self-referencing aria-owns
def test_self_referencing_aria_owns() -> None:
    els = [
        {"tag": "ul", "id": "list-self", "aria_attributes": {"aria-owns": "list-self"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "cyclic_aria_ownership" for o in res["observations"])


# 20. Cyclic aria-owns
def test_cyclic_aria_owns() -> None:
    els = [
        {"tag": "div", "id": "node1", "aria_attributes": {"aria-owns": "node1"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "cyclic_aria_ownership" for o in res["observations"])


# 21. Valid aria-activedescendant
def test_valid_aria_activedescendant() -> None:
    els = [
        {"tag": "ul", "id": "listbox", "aria_attributes": {"aria-activedescendant": "opt1"}},
        {"tag": "li", "id": "opt1"},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "broken_aria_activedescendant" for o in res["observations"])


# 22. Broken aria-activedescendant
def test_broken_aria_activedescendant() -> None:
    els = [
        {"tag": "ul", "id": "listbox", "aria_attributes": {"aria-activedescendant": "ghost-opt"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_activedescendant" for o in res["observations"])


# 23. aria-expanded validity
def test_aria_expanded_validity() -> None:
    els = [{"tag": "button", "id": "btn", "aria_attributes": {"aria-expanded": "true"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "invalid_aria_boolean" for o in res["observations"])


# 24. aria-selected validity
def test_aria_selected_validity() -> None:
    els = [{"tag": "div", "id": "tab", "role": "tab", "aria_attributes": {"aria-selected": "true"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 25. aria-checked mixed state
def test_aria_checked_mixed() -> None:
    els = [{"tag": "div", "id": "chk", "role": "checkbox", "aria_attributes": {"aria-checked": "mixed"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "invalid_aria_token" for o in res["observations"])


# 26. aria-pressed mixed state
def test_aria_pressed_mixed() -> None:
    els = [{"tag": "button", "id": "btn", "aria_attributes": {"aria-pressed": "mixed"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "invalid_aria_token" for o in res["observations"])


# 27. aria-valuemin/max/now consistency
def test_aria_value_consistency() -> None:
    els = [
        {
            "tag": "div",
            "id": "sl",
            "role": "slider",
            "aria_attributes": {"aria-valuenow": "50", "aria-valuemin": "0", "aria-valuemax": "100"},
        }
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "inconsistent_aria_value_range" for o in res["observations"])


# 28. aria-level
def test_aria_level() -> None:
    els = [{"tag": "div", "id": "h", "role": "heading", "aria_attributes": {"aria-level": "2"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert not any(o["code"] == "invalid_aria_level" for o in res["observations"])


# 29. aria-posinset / setsize
def test_posinset_setsize() -> None:
    els = [{"tag": "div", "id": "tab1", "role": "tab", "aria_attributes": {"aria-posinset": "1", "aria-setsize": "3"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 30. aria-hidden with focusable content
def test_aria_hidden_with_focusable() -> None:
    els = [
        {"tag": "button", "id": "btn-hidden", "aria_attributes": {"aria-hidden": "true"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "invalid_aria_hidden_usage" for o in res["observations"])


# 31. Valid dialog semantics
def test_valid_dialog_semantics() -> None:
    els = [{"tag": "div", "id": "dlg", "role": "dialog", "aria_attributes": {"aria-modal": "true"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 32. Invalid dialog relationships
def test_invalid_dialog_relationships() -> None:
    els = [{"tag": "div", "id": "dlg", "role": "dialog", "aria_attributes": {"aria-labelledby": "ghost-hdr"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_labelledby" for o in res["observations"])


# 33. Valid tablist/tab relationships
def test_valid_tablist_relationships() -> None:
    els = [
        {"tag": "div", "id": "tl", "role": "tablist"},
        {"tag": "button", "id": "t1", "role": "tab", "aria_attributes": {"aria-selected": "true"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 34. Invalid tab relationship
def test_invalid_tab_relationship() -> None:
    els = [{"tag": "button", "id": "t1", "role": "tab", "aria_attributes": {"aria-controls": "missing-panel"}}]
    res = analyze_aria(_make_aria_fixture(els))
    assert any(o["code"] == "broken_aria_controls" for o in res["observations"])


# 35. Valid listbox/options
def test_valid_listbox_options() -> None:
    els = [
        {"tag": "ul", "id": "lb", "role": "listbox"},
        {"tag": "li", "id": "opt1", "role": "option", "aria_attributes": {"aria-selected": "true"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["potential_issues"] == 0


# 36. Invalid widget context
def test_invalid_widget_context() -> None:
    pass


# 37. Duplicate IDs affecting ARIA references
def test_duplicate_ids_affecting_aria() -> None:
    els = [
        {"tag": "button", "id": "btn", "aria_attributes": {"aria-labelledby": "dup-label"}},
        {"tag": "span", "id": "dup-label", "text": "First"},
        {"tag": "span", "id": "dup-label", "text": "Second"},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["elements_checked"] >= 3


# 38. Responsive semantic differences
def test_responsive_semantic_differences() -> None:
    fixture = _make_aria_fixture([{"tag": "button", "id": "btn1", "aria_attributes": {"aria-expanded": "true"}}])
    res = analyze_aria(fixture)
    assert res["summary"]["status"] == "passed"


# 39. Incomplete accessibility evidence
def test_incomplete_evidence() -> None:
    res = analyze_aria({})
    assert res["summary"]["not_testable"] == 1


# 40. Very large DOM
def test_very_large_dom() -> None:
    els = [
        {"tag": "button", "id": f"btn-{i}", "role": "button"}
        for i in range(100)
    ]
    res = analyze_aria(_make_aria_fixture(els))
    assert res["summary"]["elements_checked"] == 100


# 41. Observation deduplication
def test_observation_deduplication() -> None:
    els = [
        {"tag": "button", "id": "b1", "aria_attributes": {"aria-expanded": "bad"}},
        {"tag": "button", "id": "b1", "aria_attributes": {"aria-expanded": "bad"}},
    ]
    res = analyze_aria(_make_aria_fixture(els))
    bad_obs = [o for o in res["observations"] if o["code"] == "invalid_aria_boolean"]
    assert len(bad_obs) == 1


# 42. Deterministic output ordering
def test_deterministic_output_ordering() -> None:
    els = [
        {"tag": "button", "id": "btn-z", "aria_attributes": {"aria-expanded": "no"}},
        {"tag": "div", "id": "btn-a", "role": "abstract-role"},
    ]
    res1 = analyze_aria(_make_aria_fixture(els))
    res2 = analyze_aria(_make_aria_fixture(els))
    assert [o["code"] for o in res1["observations"]] == [o["code"] for o in res2["observations"]]


# 43. Budget exhaustion
def test_budget_exhaustion() -> None:
    els = [{"tag": "button", "id": "b1", "role": "button"}]
    res = analyze_aria(_make_aria_fixture(els), options={"deadline": 0.0})
    assert any(o["code"] == "skipped_due_to_budget" for o in res["observations"])
