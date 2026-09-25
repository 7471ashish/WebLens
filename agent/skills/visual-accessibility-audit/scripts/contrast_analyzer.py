"""
Automated color-contrast analysis layer for visual-accessibility-audit.

This module consumes rendered page and DOM evidence produced by page_renderer.py,
calculating relative luminance and WCAG 2.2 contrast ratios for text and UI components
against Levels AA and AAA criteria without making any network calls or browser interactions.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Any

logger = logging.getLogger("visual_accessibility_audit.contrast_analyzer")

# Default analysis bounds
DEFAULT_MAX_ELEMENTS = 5_000
DEFAULT_MAX_TEXT_CANDIDATES = 3_000
DEFAULT_MAX_NON_TEXT_CANDIDATES = 2_000
DEFAULT_MAX_OBSERVATIONS = 500
DEFAULT_MAX_ANCESTOR_DEPTH = 20
DEFAULT_MAX_CSS_COLOR_PARSE_LEN = 256

# Standard WCAG 2.2 Contrast Thresholds
THRESHOLD_TEXT_NORMAL_AA = 4.5
THRESHOLD_TEXT_NORMAL_AAA = 7.0
THRESHOLD_TEXT_LARGE_AA = 3.0
THRESHOLD_TEXT_LARGE_AAA = 4.5
THRESHOLD_NON_TEXT_AA = 3.0

# Standard CSS Named Colors (common subset)
NAMED_COLORS: dict[str, tuple[int, int, int, float]] = {
    "black": (0, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "red": (255, 0, 0, 1.0),
    "green": (0, 128, 0, 1.0),
    "blue": (0, 0, 255, 1.0),
    "gray": (128, 128, 128, 1.0),
    "grey": (128, 128, 128, 1.0),
    "silver": (192, 192, 192, 1.0),
    "darkgray": (169, 169, 169, 1.0),
    "darkgrey": (169, 169, 169, 1.0),
    "lightgray": (211, 211, 211, 1.0),
    "lightgrey": (211, 211, 211, 1.0),
    "yellow": (255, 255, 0, 1.0),
    "orange": (255, 165, 0, 1.0),
    "purple": (128, 0, 128, 1.0),
    "navy": (0, 0, 128, 1.0),
    "teal": (0, 128, 128, 1.0),
    "aqua": (0, 255, 255, 1.0),
    "cyan": (0, 255, 255, 1.0),
    "fuchsia": (255, 0, 255, 1.0),
    "magenta": (255, 0, 255, 1.0),
    "maroon": (128, 0, 0, 1.0),
    "olive": (128, 128, 0, 1.0),
    "transparent": (0, 0, 0, 0.0),
}


def parse_css_color(color_str: str | None) -> tuple[int, int, int, float] | None:
    """
    Parse CSS color string (hex, rgb, rgba, named colors) into (r, g, b, alpha).
    Returns None if color cannot be parsed or is complex/unsupported.
    """
    if not color_str or not isinstance(color_str, str):
        return None

    s = color_str.strip().lower()
    if len(s) > DEFAULT_MAX_CSS_COLOR_PARSE_LEN:
        return None

    # Check named colors
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]

    # Hex formats: #rgb, #rgba, #rrggbb, #rrggbbaa
    if s.startswith("#"):
        hex_val = s[1:]
        try:
            if len(hex_val) == 3:
                r = int(hex_val[0] * 2, 16)
                g = int(hex_val[1] * 2, 16)
                b = int(hex_val[2] * 2, 16)
                return (r, g, b, 1.0)
            elif len(hex_val) == 4:
                r = int(hex_val[0] * 2, 16)
                g = int(hex_val[1] * 2, 16)
                b = int(hex_val[2] * 2, 16)
                a = round(int(hex_val[3] * 2, 16) / 255.0, 3)
                return (r, g, b, a)
            elif len(hex_val) == 6:
                r = int(hex_val[0:2], 16)
                g = int(hex_val[2:4], 16)
                b = int(hex_val[4:6], 16)
                return (r, g, b, 1.0)
            elif len(hex_val) == 8:
                r = int(hex_val[0:2], 16)
                g = int(hex_val[2:4], 16)
                b = int(hex_val[4:6], 16)
                a = round(int(hex_val[6:8], 16) / 255.0, 3)
                return (r, g, b, a)
        except ValueError:
            return None

    # rgb(...) format
    rgb_match = re.match(r"^rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", s)
    if rgb_match:
        try:
            r = max(0, min(255, int(rgb_match.group(1))))
            g = max(0, min(255, int(rgb_match.group(2))))
            b = max(0, min(255, int(rgb_match.group(3))))
            return (r, g, b, 1.0)
        except (ValueError, IndexError):
            return None

    # rgba(...) format
    rgba_match = re.match(r"^rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d\.]+)\s*\)$", s)
    if rgba_match:
        try:
            r = max(0, min(255, int(rgba_match.group(1))))
            g = max(0, min(255, int(rgba_match.group(2))))
            b = max(0, min(255, int(rgba_match.group(3))))
            a = max(0.0, min(1.0, float(rgba_match.group(4))))
            return (r, g, b, a)
        except (ValueError, IndexError):
            return None

    return None


def calculate_relative_luminance(r: int, g: int, b: int) -> float:
    """
    Calculate WCAG relative luminance from standard 8-bit sRGB color channels.
    Formula: L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    """
    channels = []
    for c in (r, g, b):
        c_norm = c / 255.0
        if c_norm <= 0.04045:
            linear = c_norm / 12.92
        else:
            linear = ((c_norm + 0.055) / 1.055) ** 2.4
        channels.append(linear)

    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def calculate_contrast_ratio(lum1: float, lum2: float) -> float:
    """
    Calculate WCAG contrast ratio between two relative luminances.
    Formula: (L1 + 0.05) / (L2 + 0.05) where L1 >= L2.
    """
    l1 = max(lum1, lum2)
    l2 = min(lum1, lum2)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


def composite_color(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]) -> tuple[int, int, int, float]:
    """
    Perform simple alpha compositing of semi-transparent foreground over opaque/semi-opaque background.
    """
    fg_r, fg_g, fg_b, fg_a = fg
    bg_r, bg_g, bg_b, bg_a = bg

    if fg_a >= 1.0:
        return fg
    if fg_a <= 0.0:
        return bg

    out_a = fg_a + bg_a * (1.0 - fg_a)
    if out_a <= 0.0:
        return (0, 0, 0, 0.0)

    out_r = round((fg_r * fg_a + bg_r * bg_a * (1.0 - fg_a)) / out_a)
    out_g = round((fg_g * fg_a + bg_g * bg_a * (1.0 - fg_a)) / out_a)
    out_b = round((fg_b * fg_a + bg_b * bg_a * (1.0 - fg_a)) / out_a)

    return (max(0, min(255, out_r)), max(0, min(255, out_g)), max(0, min(255, out_b)), round(out_a, 3))


def is_large_text(font_size_px: float | None, font_weight: int | str | None) -> bool | None:
    """
    Determine whether text qualifies as 'large' under WCAG 2.2:
    - >= 24px (18pt) normal weight, OR
    - >= 18.5px (14pt) bold weight (font-weight >= 700 or 'bold').
    Returns True/False, or None if size/weight cannot be determined.
    """
    if font_size_px is None:
        return None

    # Parse weight
    weight_num = 400
    if isinstance(font_weight, (int, float)):
        weight_num = int(font_weight)
    elif isinstance(font_weight, str):
        w_str = font_weight.strip().lower()
        if w_str in ("bold", "bolder"):
            weight_num = 700
        elif w_str in ("normal", "lighter"):
            weight_num = 400
        elif w_str.isdigit():
            weight_num = int(w_str)

    is_bold = weight_num >= 700
    if is_bold:
        return font_size_px >= 18.5
    else:
        return font_size_px >= 24.0


def _parse_font_size(size_val: Any) -> float | None:
    """Parse font-size string or number to pixel float."""
    if size_val is None:
        return None
    if isinstance(size_val, (int, float)):
        return float(size_val)
    if isinstance(size_val, str):
        match = re.match(r"^([\d\.]+)\s*px$", size_val.strip().lower())
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        # Handle pt
        match_pt = re.match(r"^([\d\.]+)\s*pt$", size_val.strip().lower())
        if match_pt:
            try:
                return float(match_pt.group(1)) * (4.0 / 3.0)
            except ValueError:
                return None
    return None


def analyze_contrast(
    render_result: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Perform automated color-contrast analysis on pre-rendered webpage evidence.

    Args:
        render_result: Structured output dictionary produced by page_renderer.render_page().
        options: Optional configuration dictionary:
            - evaluate_aaa: bool (default: False).
            - deadline: monotonic timestamp deadline.
            - max_observations: int (default: 500).

    Returns:
        Structured JSON-serializable dictionary with summary, thresholds, and observations.
    """
    opts = options or {}
    evaluate_aaa = bool(opts.get("evaluate_aaa", False))
    max_obs = int(opts.get("max_observations", DEFAULT_MAX_OBSERVATIONS))
    deadline = opts.get("deadline")

    viewports = render_result.get("viewports") or []
    observations: list[dict[str, Any]] = []

    text_candidates_count = 0
    text_tested_count = 0
    non_text_candidates_count = 0
    non_text_tested_count = 0
    potential_issues_count = 0
    manual_review_count = 0
    not_testable_count = 0

    seen_candidate_keys: set[str] = set()

    for vp_data in viewports:
        if deadline is not None and time.monotonic() >= deadline:
            observations.append({
                "code": "skipped_due_to_budget",
                "category": "system",
                "status": "skipped_due_to_budget",
                "confidence": "high",
                "description": "Contrast analysis deadline exceeded; remaining viewports skipped.",
                "element": {"viewport": vp_data.get("name")},
                "wcag": ["1.4.3"],
            })
            break

        if vp_data.get("status") != "completed":
            continue

        vp_name = str(vp_data.get("name", "desktop"))
        elements = vp_data.get("elements") or []

        # Find document/body background fallback if available
        doc_bg_str = None
        for el in elements:
            if el.get("tag") in ("body", "html"):
                doc_bg_str = el.get("computed_styles", {}).get("background-color") or el.get("background_color")
                if doc_bg_str and doc_bg_str != "transparent":
                    break

        for el in elements:
            if deadline is not None and time.monotonic() >= deadline:
                break

            tag = (el.get("tag") or "").lower()
            el_id = el.get("id") or ""
            bounds = el.get("bounds") or {}
            w = float(bounds.get("width", 0))
            h = float(bounds.get("height", 0))

            # Skip hidden, zero-size, or non-visible elements
            vis = el.get("visibility") or {}
            if not vis.get("is_visible", True) or el.get("hidden"):
                continue
            if w <= 0.0 or h <= 0.0:
                continue

            # Check if element is a disabled control (WCAG exception applies)
            is_disabled = bool(el.get("disabled"))

            styles = el.get("computed_styles") or {}
            fg_color_str = styles.get("color") or el.get("color")
            bg_color_str = styles.get("background-color") or el.get("background_color") or doc_bg_str
            bg_image_str = styles.get("background-image") or el.get("background_image")

            text_content = (el.get("text") or "").strip()
            is_text_element = bool(text_content) and tag not in ("svg", "path", "img")
            is_ui_control = tag in ("button", "input", "select", "a") or el.get("role") in ("button", "link")

            if not is_text_element and not is_ui_control:
                continue

            # Candidate key for deduplication
            cand_key = f"{vp_name}:{tag}:{el_id}:{fg_color_str}:{bg_color_str}:{text_content[:20]}"
            if cand_key in seen_candidate_keys:
                continue
            seen_candidate_keys.add(cand_key)

            # ---------------------------------------------------------
            # Complex background: gradient or background image
            # ---------------------------------------------------------
            if bg_image_str and any(k in bg_image_str.lower() for k in ("gradient", "url(", "image")):
                manual_review_count += 1
                observations.append({
                    "code": "complex_background_needs_review",
                    "category": "text_contrast" if is_text_element else "non_text_contrast",
                    "status": "needs_manual_review",
                    "confidence": "medium",
                    "description": f"Element <{tag}> is rendered over a complex background or gradient; manual review required.",
                    "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                    "foreground": {"color": fg_color_str, "source": "computed_style"},
                    "background": {"color": bg_image_str, "source": "background_image"},
                    "contrast_ratio": None,
                    "required_ratio": THRESHOLD_TEXT_NORMAL_AA if is_text_element else THRESHOLD_NON_TEXT_AA,
                    "wcag": ["1.4.3"] if is_text_element else ["1.4.11"],
                })
                continue

            # ---------------------------------------------------------
            # Foreground color resolution
            # ---------------------------------------------------------
            fg_parsed = parse_css_color(fg_color_str)
            if not fg_parsed or fg_parsed[3] <= 0.0:
                not_testable_count += 1
                observations.append({
                    "code": "insufficient_foreground_evidence",
                    "category": "text_contrast" if is_text_element else "non_text_contrast",
                    "status": "not_testable",
                    "confidence": "high",
                    "description": f"Foreground color for <{tag}> could not be reliably determined from computed styles.",
                    "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                    "foreground": {"color": fg_color_str, "source": "unknown"},
                    "background": {"color": bg_color_str, "source": "unknown"},
                    "contrast_ratio": None,
                    "required_ratio": THRESHOLD_TEXT_NORMAL_AA,
                    "wcag": ["1.4.3"],
                })
                continue

            # ---------------------------------------------------------
            # Background color resolution
            # ---------------------------------------------------------
            bg_parsed = parse_css_color(bg_color_str)
            if not bg_parsed or bg_parsed[3] <= 0.0:
                # If background is transparent and no opaque ancestor found
                not_testable_count += 1
                observations.append({
                    "code": "insufficient_background_evidence",
                    "category": "text_contrast" if is_text_element else "non_text_contrast",
                    "status": "not_testable",
                    "confidence": "high",
                    "description": f"Effective opaque background color for <{tag}> could not be determined without guessing.",
                    "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                    "foreground": {"color": fg_color_str, "source": "computed_style"},
                    "background": {"color": bg_color_str, "source": "unresolved"},
                    "contrast_ratio": None,
                    "required_ratio": THRESHOLD_TEXT_NORMAL_AA,
                    "wcag": ["1.4.3"],
                })
                continue

            # Compositing if foreground is semi-transparent
            effective_fg = fg_parsed
            if fg_parsed[3] < 1.0:
                effective_fg = composite_color(fg_parsed, bg_parsed)

            # Calculate relative luminances and contrast ratio
            fg_lum = calculate_relative_luminance(effective_fg[0], effective_fg[1], effective_fg[2])
            bg_lum = calculate_relative_luminance(bg_parsed[0], bg_parsed[1], bg_parsed[2])
            ratio = calculate_contrast_ratio(fg_lum, bg_lum)

            # ---------------------------------------------------------
            # Text Contrast Evaluation (WCAG 1.4.3)
            # ---------------------------------------------------------
            if is_text_element:
                text_candidates_count += 1
                text_tested_count += 1

                font_size_px = _parse_font_size(styles.get("font-size") or el.get("font_size"))
                font_weight = styles.get("font-weight") or el.get("font_weight")
                is_large = is_large_text(font_size_px, font_weight)

                req_ratio = THRESHOLD_TEXT_LARGE_AA if is_large else THRESHOLD_TEXT_NORMAL_AA
                req_aaa_ratio = THRESHOLD_TEXT_LARGE_AAA if is_large else THRESHOLD_TEXT_NORMAL_AAA

                if is_disabled:
                    # Disabled controls are exempt under WCAG 1.4.3
                    observations.append({
                        "code": "contrast_pass",
                        "category": "text_contrast",
                        "status": "passed",
                        "confidence": "high",
                        "description": f"Disabled control <{tag}> is exempt from minimum contrast requirements under WCAG 1.4.3.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name, "disabled": True},
                        "contrast_ratio": ratio,
                        "required_ratio": req_ratio,
                        "is_large_text": is_large,
                        "wcag": ["1.4.3"],
                    })
                    continue

                if ratio < req_ratio:
                    potential_issues_count += 1
                    code = "low_large_text_contrast" if is_large else "low_text_contrast"
                    observations.append({
                        "code": code,
                        "category": "text_contrast",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Text in <{tag}> has insufficient contrast of {ratio}:1 (required minimum {req_ratio}:1).",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                        "foreground": {"color": fg_color_str, "source": "computed_style"},
                        "background": {"color": bg_color_str, "source": "computed_style"},
                        "contrast_ratio": ratio,
                        "required_ratio": req_ratio,
                        "text_size": font_size_px,
                        "text_weight": font_weight,
                        "is_large_text": is_large,
                        "wcag": ["1.4.3"],
                    })
                elif evaluate_aaa and ratio < req_aaa_ratio:
                    observations.append({
                        "code": "low_text_contrast",
                        "category": "text_contrast",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Text in <{tag}> meets AA ({ratio}:1 >= {req_ratio}:1) but fails AAA target ({req_aaa_ratio}:1).",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                        "foreground": {"color": fg_color_str, "source": "computed_style"},
                        "background": {"color": bg_color_str, "source": "computed_style"},
                        "contrast_ratio": ratio,
                        "required_ratio": req_aaa_ratio,
                        "is_large_text": is_large,
                        "wcag": ["1.4.6"],
                    })
                else:
                    observations.append({
                        "code": "contrast_pass",
                        "category": "text_contrast",
                        "status": "passed",
                        "confidence": "high",
                        "description": f"Text in <{tag}> has sufficient contrast of {ratio}:1 (>= {req_ratio}:1).",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "contrast_ratio": ratio,
                        "required_ratio": req_ratio,
                        "is_large_text": is_large,
                        "wcag": ["1.4.3"],
                    })

            # ---------------------------------------------------------
            # Non-Text UI Control Contrast (WCAG 1.4.11)
            # ---------------------------------------------------------
            elif is_ui_control and not is_disabled:
                non_text_candidates_count += 1
                non_text_tested_count += 1

                req_ratio = THRESHOLD_NON_TEXT_AA
                if ratio < req_ratio:
                    potential_issues_count += 1
                    observations.append({
                        "code": "low_non_text_contrast",
                        "category": "non_text_contrast",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Interactive control <{tag}> has contrast of {ratio}:1 against background (required {req_ratio}:1).",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name, "bounds": bounds},
                        "foreground": {"color": fg_color_str, "source": "computed_style"},
                        "background": {"color": bg_color_str, "source": "computed_style"},
                        "contrast_ratio": ratio,
                        "required_ratio": req_ratio,
                        "wcag": ["1.4.11"],
                    })

    # Sort observations deterministically: viewport -> category -> element id -> code -> ratio
    observations.sort(
        key=lambda o: (
            str(o.get("element", {}).get("viewport", "")),
            str(o.get("category", "")),
            str(o.get("element", {}).get("id", "")),
            str(o.get("code", "")),
            float(o.get("contrast_ratio") or 0.0),
        )
    )

    bounded_obs = observations[:max_obs]

    overall_status = "passed"
    if potential_issues_count > 0:
        overall_status = "potential_issue"
    elif manual_review_count > 0:
        overall_status = "needs_manual_review"
    elif not_testable_count > 0 and text_tested_count == 0:
        overall_status = "not_testable"

    return {
        "summary": {
            "status": overall_status,
            "text_candidates": text_candidates_count,
            "text_tested": text_tested_count,
            "non_text_candidates": non_text_candidates_count,
            "non_text_tested": non_text_tested_count,
            "potential_issues": potential_issues_count,
            "manual_review": manual_review_count,
            "not_testable": not_testable_count,
        },
        "thresholds": {
            "text_normal_aa": THRESHOLD_TEXT_NORMAL_AA,
            "text_large_aa": THRESHOLD_TEXT_LARGE_AA,
            "text_normal_aaa": THRESHOLD_TEXT_NORMAL_AAA,
            "text_large_aaa": THRESHOLD_TEXT_LARGE_AAA,
            "non_text_aa": THRESHOLD_NON_TEXT_AA,
        },
        "observations": bounded_obs,
    }
