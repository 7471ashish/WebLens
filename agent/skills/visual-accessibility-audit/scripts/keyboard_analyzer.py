"""
Automated keyboard accessibility analysis layer for visual-accessibility-audit.

This module performs controlled, non-destructive keyboard interactions (Tab, Shift+Tab, Escape)
via browser.py primitives to evaluate keyboard reachability, Tab navigation sequences, focus visibility,
focus traps, positive tabindex anomalies, and focus obscuration against WCAG 2.2 criteria.
"""

from __future__ import annotations

import logging
import time
from typing import Any

# Sibling browser imports
try:
    from .browser import (
        BrowserSession,
        get_active_element_info,
        get_computed_style,
        get_element_info,
        press_key,
    )
except ImportError:
    from browser import (
        BrowserSession,
        get_active_element_info,
        get_computed_style,
        get_element_info,
        press_key,
    )

logger = logging.getLogger("visual_accessibility_audit.keyboard_analyzer")

# Default bounded limits
DEFAULT_MAX_TAB_STEPS = 100
DEFAULT_MAX_FOCUSABLE_ELEMENTS = 5_000
DEFAULT_MAX_OBSERVATIONS = 500
DEFAULT_MAX_FOCUS_CYCLE_LENGTH = 15
DEFAULT_ACTION_TIMEOUT_MS = 10_000


def _is_focus_indicator_visible(active_info: dict[str, Any]) -> tuple[bool, str]:
    """
    Evaluate whether active element has a visible focus indicator.
    Checks outline, box-shadow, border, background, or active indicator state.
    Returns: (is_visible, indicator_type)
    """
    indicator = active_info.get("focus_indicator") or {}
    has_ind = indicator.get("has_indicator")
    outline_style = indicator.get("outline_style", "")
    outline_width = indicator.get("outline_width", "")

    # 1. Direct outline detection from browser.py
    if has_ind is True:
        return True, "outline"

    if outline_style and outline_style not in ("none", "hidden") and outline_width and outline_width != "0px":
        return True, "outline"

    # 2. Check box-shadow
    box_shadow = indicator.get("box_shadow", "")
    if box_shadow and box_shadow != "none":
        return True, "box_shadow"

    # 3. Check border or background change signals if present
    border_style = indicator.get("border_style", "")
    if border_style and border_style not in ("none", "hidden"):
        return True, "border"

    # If outline is explicitly none and no other indicator found
    if outline_style == "none" or outline_width == "0px":
        return False, "none"

    return False, "none"


def _check_focus_obscured(
    active_info: dict[str, Any],
    fixed_elements: list[dict[str, Any]],
    viewport_height: float,
    viewport_width: float,
) -> tuple[bool, str | None]:
    """
    Determine if focused element bounding box is obscured by fixed/sticky headers or footers.
    """
    bounds = active_info.get("bounds") or {}
    f_top = float(bounds.get("top", bounds.get("y", 0)))
    f_bottom = float(bounds.get("bottom", bounds.get("y", 0) + bounds.get("height", 0)))
    f_left = float(bounds.get("left", bounds.get("x", 0)))
    f_right = float(bounds.get("right", bounds.get("x", 0) + bounds.get("width", 0)))

    # Ignore zero-sized or non-rendered elements
    if f_right <= f_left or f_bottom <= f_top:
        return False, None

    for fe in fixed_elements:
        fb = fe.get("bounds") or {}
        fe_top = float(fb.get("top", fb.get("y", 0)))
        fe_bottom = float(fb.get("bottom", fb.get("y", 0) + fb.get("height", 0)))
        fe_left = float(fb.get("left", fb.get("x", 0)))
        fe_right = float(fb.get("right", fb.get("x", 0) + fb.get("width", 0)))

        # Check rectangular intersection with fixed element
        x_overlap = max(0.0, min(f_right, fe_right) - max(f_left, fe_left))
        y_overlap = max(0.0, min(f_bottom, fe_bottom) - max(f_top, fe_top))

        if x_overlap > 20.0 and y_overlap > 20.0:
            tag = fe.get("tag", "fixed-element")
            fe_id = fe.get("id") or ""
            return True, f"Obscured by <{tag} id='{fe_id}'>"

    return False, None


def analyze_keyboard(
    browser_session: Any,
    render_result: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Perform controlled keyboard accessibility testing using browser interaction.

    Args:
        browser_session: Active BrowserSession instance from browser.py.
        render_result: Optional output from page_renderer.render_page() providing DOM inventory and fixed elements.
        options: Optional configuration dictionary (max_tab_steps, deadline, evaluate_reverse).

    Returns:
        Structured JSON-serializable dictionary with summary, tab sequence, and observations.
    """
    opts = options or {}
    max_steps = int(opts.get("max_tab_steps", DEFAULT_MAX_TAB_STEPS))
    max_obs = int(opts.get("max_observations", DEFAULT_MAX_OBSERVATIONS))
    deadline = opts.get("deadline")
    evaluate_reverse = bool(opts.get("evaluate_reverse", False))

    if not browser_session:
        return {
            "summary": {
                "status": "not_testable",
                "focusable_elements": 0,
                "tab_steps": 0,
                "unique_focus_targets": 0,
                "keyboard_traps": 0,
                "focus_visibility_issues": 0,
                "focus_order_issues": 0,
                "keyboard_operability_issues": 0,
                "manual_review": 0,
            },
            "tab_sequence": [],
            "observations": [
                {
                    "code": "browser_session_unavailable",
                    "category": "keyboard",
                    "status": "not_testable",
                    "confidence": "high",
                    "description": "No active browser session provided for interactive keyboard testing.",
                    "wcag": ["2.1.1"],
                }
            ],
        }

    # Extract inventory and fixed elements from render_result baseline viewport
    fixed_elements: list[dict[str, Any]] = []
    dom_inventory: list[dict[str, Any]] = []
    vp_name = "desktop"
    vp_height = 900.0
    vp_width = 1440.0

    if render_result:
        vps = render_result.get("viewports") or []
        if vps:
            base_vp = vps[0]
            vp_name = str(base_vp.get("name", "desktop"))
            vp_spec = base_vp.get("viewport") or {}
            vp_width = float(vp_spec.get("width", 1440))
            vp_height = float(vp_spec.get("height", 900))
            fixed_elements = (base_vp.get("fixed_elements") or []) + (base_vp.get("sticky_elements") or [])
            dom_inventory = base_vp.get("elements") or []

    # Check for custom controls without keyboard access or positive tabindexes in inventory
    pre_observations: list[dict[str, Any]] = []
    for el in dom_inventory:
        tag = (el.get("tag") or "").lower()
        role = (el.get("role") or "").lower()
        tindex = el.get("tabindex")

        # Positive tabindex risk
        if tindex is not None:
            try:
                ti_val = int(tindex)
                if ti_val > 0:
                    pre_observations.append({
                        "code": "positive_tabindex",
                        "category": "keyboard",
                        "status": "needs_manual_review",
                        "confidence": "medium",
                        "description": f"Element <{tag}> has positive tabindex='{ti_val}', which may disrupt natural reading and Tab focus order.",
                        "element": {"tag": tag, "id": el.get("id"), "tabindex": ti_val, "viewport": vp_name},
                        "wcag": ["2.4.3"],
                    })
            except (ValueError, TypeError):
                pass

        # Custom buttons lacking tabindex
        if tag not in ("button", "a", "input", "select", "textarea") and role == "button":
            if tindex is None or tindex == "-1":
                pre_observations.append({
                    "code": "inaccessible_custom_control",
                    "category": "keyboard",
                    "status": "potential_issue",
                    "confidence": "high",
                    "description": f"Custom control <{tag} role='button'> lacks tabindex='0' and cannot be focused via keyboard.",
                    "element": {"tag": tag, "id": el.get("id"), "role": role, "viewport": vp_name},
                    "wcag": ["2.1.1"],
                })

    tab_sequence: list[dict[str, Any]] = []
    step_observations: list[dict[str, Any]] = list(pre_observations)

    unique_targets: dict[str, dict[str, Any]] = {}
    target_history: list[str] = []

    trapped = False
    focus_visibility_issues_count = 0
    focus_order_issues_count = 0
    keyboard_traps_count = 0

    # Execute Tab sequence
    for step in range(1, max_steps + 1):
        if deadline is not None and time.monotonic() >= deadline:
            step_observations.append({
                "code": "skipped_due_to_budget",
                "category": "keyboard",
                "status": "skipped_due_to_budget",
                "confidence": "high",
                "description": "Keyboard testing deadline exceeded; Tab sequence stopped early.",
                "wcag": ["2.1.1"],
            })
            break

        # Press Tab key
        press_res = press_key(browser_session, "Tab")
        if press_res.get("status") != "completed":
            break

        # Inspect currently focused activeElement
        active_info = get_active_element_info(browser_session)
        target_id = active_info.get("id") or ""
        target_tag = (active_info.get("tag") or "").lower()

        # Check if focus was lost to null or body
        if not target_tag or target_tag == "body":
            # If body is focused multiple times in sequence
            if len(target_history) >= 2 and target_history[-1] == "body":
                step_observations.append({
                    "code": "unexpected_focus_loss",
                    "category": "keyboard",
                    "status": "potential_issue",
                    "confidence": "medium",
                    "description": "Keyboard focus was lost and reverted unexpectedly to the <body> element.",
                    "element": {"tag": "body", "viewport": vp_name},
                    "wcag": ["2.1.1", "2.4.3"],
                })
                break
            target_key = "body"
        else:
            target_key = f"{target_tag}:{target_id}:{active_info.get('text', '')[:20]}"

        # Record target in sequence
        is_visible, ind_type = _is_focus_indicator_visible(active_info)
        tab_seq_entry = {
            "step": step,
            "element": {
                "tag": target_tag,
                "id": target_id,
                "role": active_info.get("role"),
                "text": (active_info.get("text") or "")[:100],
            },
            "viewport": vp_name,
            "focus_visible": is_visible,
            "focused": True,
            "bounding_box": active_info.get("bounds", {}),
            "evidence": {"indicator_type": ind_type},
        }
        tab_sequence.append(tab_seq_entry)

        if target_key not in unique_targets and target_key != "body":
            unique_targets[target_key] = tab_seq_entry

        target_history.append(target_key)

        # Check Focus Visibility (WCAG 2.4.7)
        if target_tag and target_tag != "body" and not is_visible:
            focus_visibility_issues_count += 1
            step_observations.append({
                "code": "missing_focus_indicator",
                "category": "keyboard",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Focused element <{target_tag} id='{target_id}'> lacks a visible outline or focus indicator.",
                "element": {"tag": target_tag, "id": target_id, "viewport": vp_name},
                "wcag": ["2.4.7"],
            })

        # Check Focus Not Obscured (WCAG 2.4.11)
        is_obscured, obscuring_desc = _check_focus_obscured(active_info, fixed_elements, vp_height, vp_width)
        if is_obscured:
            step_observations.append({
                "code": "focus_obscured",
                "category": "keyboard",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Focused element <{target_tag} id='{target_id}'> is visually obscured: {obscuring_desc}.",
                "element": {"tag": target_tag, "id": target_id, "viewport": vp_name},
                "wcag": ["2.4.11"],
            })

        # Keyboard Trap Detection (cycle detection)
        # Check if the last 3 repetitions of period k (k=1..4) are identical
        cycle_detected = False
        for k in (1, 2, 3, 4):
            if len(target_history) >= k * 3:
                c1 = target_history[-k:]
                c2 = target_history[-2 * k : -k]
                c3 = target_history[-3 * k : -2 * k]
                if c1 == c2 == c3 and all(t != "body" for t in c1):
                    cycle_set = set(c1)
                    is_dialog = any(
                        "dialog" in str(t).lower() or "modal" in str(t).lower() for t in cycle_set
                    )
                    if is_dialog:
                        step_observations.append({
                            "code": "modal_focus_trap_review",
                            "category": "keyboard",
                            "status": "needs_manual_review",
                            "confidence": "medium",
                            "description": "Focus is contained within a modal dialog; verify that Escape closes the modal cleanly.",
                            "element": {"cycle_elements": list(cycle_set), "viewport": vp_name},
                            "wcag": ["2.1.2"],
                        })
                    else:
                        trapped = True
                        keyboard_traps_count += 1
                        step_observations.append({
                            "code": "keyboard_trap",
                            "category": "keyboard",
                            "status": "potential_issue",
                            "confidence": "high",
                            "description": f"Keyboard focus is trapped in an inescapable loop between elements: {cycle_set}.",
                            "element": {"cycle_elements": list(cycle_set), "viewport": vp_name},
                            "wcag": ["2.1.2"],
                        })
                    cycle_detected = True
                    break

        if cycle_detected:
            break

    # Optional Reverse Navigation Testing (Shift+Tab)
    if evaluate_reverse and not trapped and tab_sequence:
        rev_res = press_key(browser_session, "Shift+Tab")
        if rev_res.get("status") == "completed":
            rev_active = get_active_element_info(browser_session)
            tab_sequence.append({
                "step": len(tab_sequence) + 1,
                "key": "Shift+Tab",
                "element": {
                    "tag": rev_active.get("tag"),
                    "id": rev_active.get("id"),
                },
                "viewport": vp_name,
            })

    # Deduplicate observations
    deduped_obs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for o in step_observations:
        el_id = o.get("element", {}).get("id") or ""
        el_tag = o.get("element", {}).get("tag") or ""
        obs_key = f"{o.get('code')}:{el_tag}:{el_id}:{o.get('description', '')[:30]}"
        if obs_key not in seen_keys:
            seen_keys.add(obs_key)
            deduped_obs.append(o)

    # Sort deterministically
    deduped_obs.sort(
        key=lambda x: (
            str(x.get("element", {}).get("viewport", "")),
            str(x.get("category", "")),
            str(x.get("element", {}).get("id", "")),
            str(x.get("code", "")),
        )
    )

    bounded_obs = deduped_obs[:max_obs]
    potential_issues_count = sum(1 for o in bounded_obs if o.get("status") == "potential_issue")
    manual_review_count = sum(1 for o in bounded_obs if o.get("status") == "needs_manual_review")

    overall_status = "passed"
    if potential_issues_count > 0:
        overall_status = "potential_issue"
    elif manual_review_count > 0:
        overall_status = "needs_manual_review"

    return {
        "summary": {
            "status": overall_status,
            "focusable_elements": len(dom_inventory),
            "tab_steps": len(tab_sequence),
            "unique_focus_targets": len(unique_targets),
            "keyboard_traps": keyboard_traps_count,
            "focus_visibility_issues": focus_visibility_issues_count,
            "focus_order_issues": focus_order_issues_count,
            "keyboard_operability_issues": potential_issues_count,
            "manual_review": manual_review_count,
        },
        "tab_sequence": tab_sequence,
        "observations": bounded_obs,
    }
