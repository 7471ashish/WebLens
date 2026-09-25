"""
Visual analysis layer for visual-accessibility-audit.

This module evaluates rendered page evidence produced by page_renderer.py across
desktop, tablet, and mobile viewports to identify measurable layout defects,
horizontal overflow, content clipping, component overlaps, oversized fixed elements,
and responsive anomalies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("visual_accessibility_audit.visual_analyzer")

# Default analysis bounds
DEFAULT_MAX_ELEMENTS = 5_000
DEFAULT_MAX_OBSERVATIONS = 500
DEFAULT_MAX_OVERLAP_COMPARISONS = 50_000
HORIZONTAL_OVERFLOW_TOLERANCE_PX = 5.0
VIEWPORT_OVERFLOW_TOLERANCE_PX = 5.0
FIXED_ELEMENT_MAX_VIEWPORT_RATIO = 0.30  # > 30% of viewport height flagged as oversized

# Legitimate scroll/carousel tags or classes to minimize false positives
INTENTIONAL_OVERFLOW_INDICATORS = {
    "carousel",
    "slider",
    "swiper",
    "table",
    "pre",
    "code",
    "scroll",
    "overflow-x",
}


def _is_intentional_overflow(el: dict[str, Any]) -> bool:
    """Detect whether element is an intentional scroll container (carousel, code block, table)."""
    tag = (el.get("tag") or "").lower()
    if tag in ("pre", "code", "table"):
        return True

    classes = " ".join(el.get("classes") or []).lower()
    el_id = (el.get("id") or "").lower()
    role = (el.get("role") or "").lower()

    for indicator in INTENTIONAL_OVERFLOW_INDICATORS:
        if indicator in classes or indicator in el_id or indicator in role:
            return True

    return False


def _is_meaningful_ui_element(el: dict[str, Any]) -> bool:
    """Filter out non-visible, tracking, SVG, or structural empty tags."""
    tag = (el.get("tag") or "").lower()
    if tag in ("script", "style", "meta", "link", "noscript", "svg", "path", "defs"):
        return False
    return True


def _do_rects_intersect(r1: dict[str, float], r2: dict[str, float], min_overlap_area: float = 100.0) -> bool:
    """Determine whether two bounding boxes meaningfully intersect."""
    x_left = max(r1.get("x", 0), r2.get("x", 0))
    y_top = max(r1.get("y", 0), r2.get("y", 0))
    x_right = min(r1.get("right", r1.get("x", 0) + r1.get("width", 0)), r2.get("right", r2.get("x", 0) + r2.get("width", 0)))
    y_bottom = min(r1.get("bottom", r1.get("y", 0) + r1.get("height", 0)), r2.get("bottom", r2.get("y", 0) + r2.get("height", 0)))

    if x_right > x_left and y_bottom > y_top:
        overlap_area = (x_right - x_left) * (y_bottom - y_top)
        return overlap_area >= min_overlap_area
    return False


def _detect_horizontal_overflow(
    viewport_data: dict[str, Any],
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect horizontal document overflow and pinpoint offending elements."""
    obs: list[dict[str, Any]] = []
    vp_spec = viewport_data.get("viewport", {})
    doc_geo = viewport_data.get("document", {})

    vp_width = float(vp_spec.get("width", 1440))
    scroll_width = float(doc_geo.get("scroll_width", vp_width))
    client_width = float(doc_geo.get("client_width", vp_width))

    excess_width = scroll_width - max(vp_width, client_width)
    if excess_width > HORIZONTAL_OVERFLOW_TOLERANCE_PX:
        # Locate elements whose right edge extends beyond the viewport
        culprits: list[dict[str, Any]] = []
        for el in elements:
            if not el.get("visibility", {}).get("is_visible", True):
                continue
            if _is_intentional_overflow(el):
                continue

            bounds = el.get("bounds", {})
            r_edge = float(bounds.get("right", bounds.get("x", 0) + bounds.get("width", 0)))
            if r_edge > vp_width + HORIZONTAL_OVERFLOW_TOLERANCE_PX:
                culprits.append({
                    "tag": el.get("tag"),
                    "id": el.get("id"),
                    "bounds": bounds,
                    "right_edge": r_edge,
                    "overflow_px": r_edge - vp_width,
                })

        obs.append({
            "code": "horizontal_overflow",
            "type": "layout",
            "status": "potential_issue",
            "confidence": "high",
            "description": f"Document has horizontal overflow of {round(excess_width, 1)}px beyond viewport width of {vp_width}px.",
            "evidence": {
                "viewport": vp_spec,
                "document": {
                    "scroll_width": scroll_width,
                    "client_width": client_width,
                    "excess_width_px": round(excess_width, 1),
                },
                "culprit_elements": culprits[:5],
            },
        })

    return obs


def _detect_clipping_and_offscreen(
    viewport_data: dict[str, Any],
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect elements clipped or unexpectedly extending outside the visible viewport."""
    obs: list[dict[str, Any]] = []
    vp_spec = viewport_data.get("viewport", {})
    vp_width = float(vp_spec.get("width", 1440))

    for el in elements:
        if not el.get("visibility", {}).get("is_visible", True):
            continue
        if _is_intentional_overflow(el):
            continue

        bounds = el.get("bounds", {})
        tag = (el.get("tag") or "").lower()
        width = float(bounds.get("width", 0))
        r_edge = float(bounds.get("right", bounds.get("x", 0) + width))
        x_pos = float(bounds.get("x", 0))

        # Check element extending off-screen to the right
        if r_edge > vp_width + VIEWPORT_OVERFLOW_TOLERANCE_PX:
            obs.append({
                "code": "element_outside_viewport",
                "type": "layout",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Element <{tag}> extends {round(r_edge - vp_width, 1)}px past the viewport right boundary.",
                "evidence": {
                    "viewport": vp_spec,
                    "element": {
                        "tag": tag,
                        "id": el.get("id"),
                        "bounds": bounds,
                    },
                    "overflow_px": round(r_edge - vp_width, 1),
                },
                "element": el,
            })

        # Check interactive element with negative x offset
        if tag in ("button", "a", "input", "select") and x_pos < -10.0:
            obs.append({
                "code": "interactive_element_clipped",
                "type": "layout",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Interactive control <{tag}> is placed off-screen at x={x_pos}px.",
                "evidence": {
                    "viewport": vp_spec,
                    "element": {"tag": tag, "id": el.get("id"), "bounds": bounds},
                },
                "element": el,
            })

    return obs


def _detect_overlaps(
    viewport_data: dict[str, Any],
    elements: list[dict[str, Any]],
    max_comparisons: int = DEFAULT_MAX_OVERLAP_COMPARISONS,
) -> list[dict[str, Any]]:
    """Detect suspicious overlapping among sibling/unrelated visible content elements."""
    obs: list[dict[str, Any]] = []
    vp_spec = viewport_data.get("viewport", {})

    # Filter candidates: visible, non-zero dimensions, meaningful tags
    candidates: list[dict[str, Any]] = []
    for el in elements:
        if not el.get("visibility", {}).get("is_visible", True):
            continue
        bounds = el.get("bounds", {})
        w = float(bounds.get("width", 0))
        h = float(bounds.get("height", 0))
        if w >= 20.0 and h >= 20.0 and _is_meaningful_ui_element(el):
            candidates.append(el)

    # Sort candidates by top Y coordinate for sweep-line comparison
    candidates.sort(key=lambda e: float(e.get("bounds", {}).get("y", 0)))

    comparisons_count = 0
    candidate_count = len(candidates)

    for i in range(candidate_count):
        el1 = candidates[i]
        b1 = el1.get("bounds", {})
        b1_bot = float(b1.get("bottom", b1.get("y", 0) + b1.get("height", 0)))

        for j in range(i + 1, candidate_count):
            comparisons_count += 1
            if comparisons_count > max_comparisons:
                break

            el2 = candidates[j]
            b2 = el2.get("bounds", {})
            b2_y = float(b2.get("y", 0))

            # If el2 starts below el1 bottom, break inner loop (sorted)
            if b2_y >= b1_bot:
                break

            # Skip parent/child or container relationships (e.g. div containing p or h1)
            # If one bounding box completely wraps the other, it's likely a container
            b1_x = float(b1.get("x", 0))
            b1_w = float(b1.get("width", 0))
            b2_x = float(b2.get("x", 0))
            b2_w = float(b2.get("width", 0))

            b1_r = float(b1.get("right", b1_x + b1_w))
            b2_r = float(b2.get("right", b2_x + b2_w))
            b2_bot = float(b2.get("bottom", b2_y + float(b2.get("height", 0))))

            is_container = (
                (b1_x <= b2_x and b1_r >= b2_r and float(b1.get("y", 0)) <= b2_y and b1_bot >= b2_bot)
                or (b2_x <= b1_x and b2_r >= b1_r and b2_y <= float(b1.get("y", 0)) and b2_bot >= b1_bot)
            )
            if is_container:
                continue

            # Skip button containing icon/span
            t1, t2 = (el1.get("tag") or "").lower(), (el2.get("tag") or "").lower()
            if {t1, t2} & {"button", "a"} and {t1, t2} & {"span", "svg", "img", "i"}:
                continue

            if _do_rects_intersect(b1, b2, min_overlap_area=200.0):
                obs.append({
                    "code": "suspicious_element_overlap",
                    "type": "layout",
                    "status": "potential_issue",
                    "confidence": "medium",
                    "description": f"Element <{t1}> and <{t2}> have intersecting bounding boxes that may cause content occlusion.",
                    "evidence": {
                        "viewport": vp_spec,
                        "element_a": {"tag": t1, "id": el1.get("id"), "bounds": b1},
                        "element_b": {"tag": t2, "id": el2.get("id"), "bounds": b2},
                    },
                })

        if comparisons_count > max_comparisons:
            break

    return obs


def _detect_fixed_sticky_issues(viewport_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate fixed and sticky elements for excessive viewport height consumption."""
    obs: list[dict[str, Any]] = []
    vp_spec = viewport_data.get("viewport", {})
    vp_height = float(vp_spec.get("height", 900))

    # Fixed elements
    for fe in viewport_data.get("fixed_elements", []):
        bounds = fe.get("bounds", {})
        h = float(bounds.get("height", 0))
        ratio = h / vp_height if vp_height > 0 else 0
        if ratio > FIXED_ELEMENT_MAX_VIEWPORT_RATIO:
            obs.append({
                "code": "oversized_fixed_element",
                "type": "layout",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Fixed element <{fe.get('tag')}> consumes {round(ratio * 100, 1)}% of vertical viewport height.",
                "evidence": {
                    "viewport": vp_spec,
                    "element": fe,
                    "height_ratio": round(ratio, 2),
                },
                "element": fe,
            })

    # Sticky elements
    for se in viewport_data.get("sticky_elements", []):
        bounds = se.get("bounds", {})
        h = float(bounds.get("height", 0))
        ratio = h / vp_height if vp_height > 0 else 0
        if ratio > FIXED_ELEMENT_MAX_VIEWPORT_RATIO:
            obs.append({
                "code": "oversized_sticky_element",
                "type": "layout",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Sticky element <{se.get('tag')}> consumes {round(ratio * 100, 1)}% of vertical viewport height.",
                "evidence": {
                    "viewport": vp_spec,
                    "element": se,
                    "height_ratio": round(ratio, 2),
                },
                "element": se,
            })

    return obs


def _detect_zero_size_anomalies(
    viewport_data: dict[str, Any],
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect meaningful interactive controls rendered with 0x0 dimensions."""
    obs: list[dict[str, Any]] = []
    vp_spec = viewport_data.get("viewport", {})

    for el in elements:
        tag = (el.get("tag") or "").lower()
        bounds = el.get("bounds", {})
        w = float(bounds.get("width", 0))
        h = float(bounds.get("height", 0))

        # Check interactive buttons and inputs claiming visibility with 0 dimensions
        if tag in ("button", "input", "select") and el.get("visibility", {}).get("is_visible", False):
            if w == 0.0 or h == 0.0:
                obs.append({
                    "code": "suspicious_zero_size_element",
                    "type": "layout",
                    "status": "potential_issue",
                    "confidence": "medium",
                    "description": f"Interactive element <{tag}> is visible but has zero width or height.",
                    "evidence": {
                        "viewport": vp_spec,
                        "element": {"tag": tag, "id": el.get("id"), "bounds": bounds},
                    },
                    "element": {"tag": tag, "id": el.get("id"), "bounds": bounds},
                })

    return obs


def _detect_cross_viewport_anomalies(
    viewports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare desktop vs mobile viewports to identify responsive breakpoint anomalies."""
    obs: list[dict[str, Any]] = []
    vp_map = {str(v.get("name")): v for v in viewports if v.get("status") == "completed"}

    desktop = vp_map.get("desktop")
    mobile = vp_map.get("mobile")

    if not desktop or not mobile:
        return obs

    d_doc = desktop.get("document", {})
    m_doc = mobile.get("document", {})
    m_vp = mobile.get("viewport", {})

    m_width = float(m_vp.get("width", 390))
    m_scroll_w = float(m_doc.get("scroll_width", m_width))
    d_scroll_w = float(d_doc.get("scroll_width", 1440))
    d_width = float(desktop.get("viewport", {}).get("width", 1440))

    # Responsive overflow appearing specifically on mobile
    d_overflow = (d_scroll_w - d_width) > HORIZONTAL_OVERFLOW_TOLERANCE_PX
    m_overflow = (m_scroll_w - m_width) > HORIZONTAL_OVERFLOW_TOLERANCE_PX

    if m_overflow and not d_overflow:
        obs.append({
            "code": "responsive_overflow",
            "type": "responsive",
            "status": "potential_issue",
            "confidence": "high",
            "description": f"Horizontal overflow occurs on mobile viewport (scrollWidth={m_scroll_w}px > {m_width}px) but not on desktop.",
            "evidence": {
                "desktop": {"scroll_width": d_scroll_w, "viewport_width": d_width},
                "mobile": {"scroll_width": m_scroll_w, "viewport_width": m_width},
            },
        })

    return obs


def analyze_visual(
    render_result: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Perform deterministic visual and layout analysis across all rendered viewports.

    Args:
        render_result: Output dictionary produced by page_renderer.render_page().
        options: Optional configuration dictionary (max_observations, max_elements).

    Returns:
        Structured JSON-serializable dictionary with per-viewport and cross-viewport observations.
    """
    opts = options or {}
    max_obs = int(opts.get("max_observations", DEFAULT_MAX_OBSERVATIONS))

    viewports = render_result.get("viewports") or []
    analyzed_viewports: list[dict[str, Any]] = []
    all_observations: list[dict[str, Any]] = []
    errors_list: list[dict[str, Any]] = []

    for vp_data in viewports:
        vp_name = str(vp_data.get("name", "unknown"))
        st = vp_data.get("status")

        if st != "completed":
            analyzed_viewports.append({
                "name": vp_name,
                "status": st,
                "observations": [],
            })
            continue

        elements = vp_data.get("elements") or []
        vp_obs: list[dict[str, Any]] = []

        try:
            # 1. Horizontal overflow
            vp_obs.extend(_detect_horizontal_overflow(vp_data, elements))

            # 2. Clipping and off-screen elements
            vp_obs.extend(_detect_clipping_and_offscreen(vp_data, elements))

            # 3. Component overlaps
            vp_obs.extend(_detect_overlaps(vp_data, elements))

            # 4. Oversized fixed and sticky elements
            vp_obs.extend(_detect_fixed_sticky_issues(vp_data))

            # 5. Zero-size interactive elements
            vp_obs.extend(_detect_zero_size_anomalies(vp_data, elements))

            # Deduplicate observations for this viewport
            deduped: list[dict[str, Any]] = []
            seen: set[str] = set()
            for o in vp_obs:
                el_tag = o.get("element", {}).get("tag") or ""
                el_id = o.get("element", {}).get("id") or ""
                key = f"{o.get('code')}:{el_tag}:{el_id}:{o.get('description', '')[:30]}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(o)

            analyzed_viewports.append({
                "name": vp_name,
                "status": "completed",
                "observations": deduped,
            })
            all_observations.extend(deduped)
        except Exception as exc:
            logger.exception("Error analyzing viewport %s: %s", vp_name, exc)
            errors_list.append({
                "stage": "visual_analyzer",
                "viewport": vp_name,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "recoverable": True,
            })

    # Cross-viewport comparison
    cross_obs = _detect_cross_viewport_anomalies(viewports)
    all_observations.extend(cross_obs)

    # Sort observations deterministically: code -> description
    all_observations.sort(key=lambda o: (o.get("code", ""), o.get("description", "")))
    bounded_observations = all_observations[:max_obs]

    potential_issues_count = sum(1 for o in bounded_observations if o.get("status") == "potential_issue")
    review_count = sum(1 for o in bounded_observations if o.get("status") == "needs_manual_review")

    return {
        "summary": {
            "viewports_analyzed": len([v for v in analyzed_viewports if v.get("status") == "completed"]),
            "observations": len(bounded_observations),
            "potential_issues": potential_issues_count,
            "needs_manual_review": review_count,
        },
        "viewports": analyzed_viewports,
        "cross_viewport": {
            "observations": cross_obs,
        },
        "observations": bounded_observations,
        "errors": errors_list,
    }
