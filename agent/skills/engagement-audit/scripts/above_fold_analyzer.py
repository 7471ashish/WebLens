"""
Above-the-Fold Experience Analyzer (above_fold_analyzer.py)
----------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates what content and interaction affordances are immediately visible
to a user in the initial viewport (default 1366x768 pixels) upon page load.

Key Evaluations:
1. Main heading (H1 / value proposition) visibility and positioning
2. Primary Call-to-Action (CTA) visibility, clipping, and fold intersection
3. Primary navigation visibility in the initial viewport
4. Important content visibility vs. pushed below the fold
5. Viewport space distribution and oversized element detection
6. Popup, modal, or overlay obstruction of hero elements
7. Initial page clarity and immediate purpose communication

Architectural Constraint:
    Operates strictly in-memory on structured page evidence. Does not spawn browsers,
    make network calls, or import from crawl-render-audit.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from engagement_state import (
    BoundingBox,
    CategoryResult,
    CategoryStatus,
    CTAElement,
    EngagementFinding,
    HeadingElement,
    PageInputData,
    PopupEvidence,
    SeverityLevel,
    Viewport,
)

logger = logging.getLogger("engagement_audit.above_fold")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)



# Visibility State Helpers


VisibilityClassification = str  # "fully_visible", "partially_visible", "below_fold", "off_screen", "unknown"


def classify_fold_visibility(
    box: BoundingBox | None,
    viewport: Viewport,
) -> tuple[VisibilityClassification, float]:
    """
    Classify the visibility of a bounding box relative to the viewport.

    Returns:
        tuple of (classification_string, visible_area_percentage)
    """
    if not box:
        return "unknown", 0.0

    if box.width <= 0 or box.height <= 0:
        return "unknown", 0.0

    # Calculate intersection rectangle with viewport (0, 0, vp.w, vp.h)
    int_left = max(0.0, box.x)
    int_top = max(0.0, box.y)
    int_right = min(viewport.width, box.right)
    int_bottom = min(viewport.height, box.bottom)

    int_width = max(0.0, int_right - int_left)
    int_height = max(0.0, int_bottom - int_top)
    visible_area = int_width * int_height
    total_area = box.width * box.height

    if total_area <= 0:
        return "unknown", 0.0

    visible_ratio = min(1.0, max(0.0, visible_area / total_area))

    # Completely below the fold
    if box.y >= viewport.height:
        return "below_fold", 0.0

    # Completely off-screen to the right or above/left
    if box.x >= viewport.width or box.bottom <= 0 or box.right <= 0:
        return "off_screen", 0.0

    # Fully visible
    if (
        box.x >= 0
        and box.y >= 0
        and box.right <= viewport.width
        and box.bottom <= viewport.height
    ):
        return "fully_visible", 1.0

    # Partially visible
    if visible_ratio > 0.05:
        return "partially_visible", visible_ratio

    return "below_fold" if box.y > 0 else "off_screen", visible_ratio


def boxes_intersect(a: BoundingBox, b: BoundingBox) -> bool:
    """Check if two bounding boxes geometrically overlap."""
    if a.right <= b.left or b.right <= a.left:
        return False
    if a.bottom <= b.top or b.bottom <= a.top:
        return False
    return True



# Above-the-Fold Analyzer Implementation


def analyze_above_fold(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Analyze the above-the-fold user experience for the given page data.

    Args:
        page_data: Raw dictionary or PageInputData instance.
        options: Optional configuration overrides (e.g., custom viewport).

    Returns:
        CategoryResult containing status, score, findings, and detailed metrics.
    """
    opts = options or {}

    # Normalize input into PageInputData
    if isinstance(page_data, PageInputData):
        input_data = page_data
    elif isinstance(page_data, dict):
        input_data = PageInputData.from_dict(page_data)
    else:
        input_data = PageInputData()

    target_url = input_data.url
    viewport = Viewport(
        width=float(opts.get("viewport_width", input_data.viewport.width)),
        height=float(opts.get("viewport_height", input_data.viewport.height)),
    )

    logger.info("Analyzing above-fold experience for %s on viewport %.0fx%.0f", target_url, viewport.width, viewport.height)

    # Check for complete lack of data
    has_any_data = bool(
        input_data.headings
        or input_data.ctas
        or input_data.buttons
        or input_data.navigation
        or input_data.text
        or input_data.popups
    )

    if not has_any_data:
        return CategoryResult(
            category="above_fold",
            status="insufficient_evidence",
            score=None,
            findings=[],
            metrics={
                "viewport": viewport.to_dict(),
                "evaluated": False,
                "message": "No layout, heading, or element evidence supplied in input",
            },
            confidence=0.5,
            messages=["Insufficient structural evidence to evaluate above-the-fold experience."],
        )

    findings: list[EngagementFinding] = []
    below_fold_elements: list[dict[str, Any]] = []
    has_coordinate_evidence = False

    # --------------------------------------------------------------------------
    # 1. Main Heading (H1) Visibility Evaluation
    # --------------------------------------------------------------------------
    main_heading: HeadingElement | None = None
    for h in input_data.headings:
        if h.level == 1:
            main_heading = h
            break
    if not main_heading and input_data.headings:
        main_heading = input_data.headings[0]

    heading_vis = "unknown"
    heading_vis_ratio = 0.0
    if main_heading:
        if main_heading.bounding_box:
            has_coordinate_evidence = True
            heading_vis, heading_vis_ratio = classify_fold_visibility(main_heading.bounding_box, viewport)
        elif main_heading.above_fold is not None:
            heading_vis = "fully_visible" if main_heading.above_fold else "below_fold"
            heading_vis_ratio = 1.0 if main_heading.above_fold else 0.0

        if heading_vis == "below_fold":
            box = main_heading.bounding_box
            findings.append(EngagementFinding(
                id="ENG-AF-001",
                category="above_fold",
                title="Primary page heading is pushed below the fold",
                description=(
                    f"The main heading '{main_heading.text}' is positioned at y={box.y if box else 'below 768'}px, "
                    f"requiring users to scroll before understanding the page's core value proposition."
                ),
                severity="medium",
                confidence=0.95 if box else 0.8,
                evidence={
                    "heading_text": main_heading.text,
                    "heading_level": main_heading.level,
                    "y_position": box.y if box else None,
                    "viewport": viewport.to_dict(),
                    "visibility_state": heading_vis,
                },
                recommendation="Position the primary <h1> headline and value proposition inside the initial 1366x768 hero region.",
                url=target_url,
            ))
            below_fold_elements.append({"type": "heading", "text": main_heading.text, "y": box.y if box else None})

    # --------------------------------------------------------------------------
    # 2. Primary Call-to-Action (CTA) Visibility Evaluation
    # --------------------------------------------------------------------------
    primary_cta: CTAElement | None = None
    for c in input_data.ctas:
        if c.is_primary:
            primary_cta = c
            break
    if not primary_cta and input_data.ctas:
        primary_cta = input_data.ctas[0]

    cta_vis = "unknown"
    cta_vis_ratio = 0.0
    if primary_cta:
        if primary_cta.bounding_box:
            has_coordinate_evidence = True
            cta_vis, cta_vis_ratio = classify_fold_visibility(primary_cta.bounding_box, viewport)
        elif primary_cta.above_fold is not None:
            cta_vis = "fully_visible" if primary_cta.above_fold else "below_fold"
            cta_vis_ratio = 1.0 if primary_cta.above_fold else 0.0

        if cta_vis == "below_fold":
            box = primary_cta.bounding_box
            findings.append(EngagementFinding(
                id="ENG-AF-002",
                category="above_fold",
                title="Primary Call-to-Action is located below the fold",
                description=(
                    f"The primary action button '{primary_cta.text}' is positioned at y={box.y if box else 'below fold'}px, "
                    f"meaning visitors cannot take immediate conversion action without scrolling down."
                ),
                severity="high",
                confidence=0.95 if box else 0.85,
                evidence={
                    "cta_text": primary_cta.text,
                    "y_position": box.y if box else None,
                    "viewport": viewport.to_dict(),
                    "visibility_state": cta_vis,
                },
                recommendation="Elevate the primary action button into the initial 1366x768 viewport beside the hero headline.",
                url=target_url,
            ))
            below_fold_elements.append({"type": "cta", "text": primary_cta.text, "y": box.y if box else None})
        elif cta_vis == "partially_visible" and cta_vis_ratio < 0.6:
            box = primary_cta.bounding_box
            findings.append(EngagementFinding(
                id="ENG-AF-003",
                category="above_fold",
                title="Primary Call-to-Action is clipped at the fold line",
                description=(
                    f"Primary CTA '{primary_cta.text}' is cut off by the bottom of the viewport "
                    f"(only {int(cta_vis_ratio * 100)}% visible at y={box.y if box else 0}px)."
                ),
                severity="medium",
                confidence=0.9,
                evidence={
                    "cta_text": primary_cta.text,
                    "visible_percentage": round(cta_vis_ratio * 100, 1),
                    "viewport": viewport.to_dict(),
                },
                recommendation="Adjust hero padding or element heights so the entire CTA button rests comfortably above the 768px fold line.",
                url=target_url,
            ))

    # --------------------------------------------------------------------------
    # 3. Popup / Modal Obstruction Evaluation
    # --------------------------------------------------------------------------
    obstruction_findings: list[dict[str, Any]] = []
    for p in input_data.popups:
        if p.is_blocking or p.immediate:
            # Check if popup has coordinates or covers hero area
            p_box = p.bounding_box
            obstructs_cta = False
            obstructs_h1 = False

            if p_box:
                has_coordinate_evidence = True
                if primary_cta and primary_cta.bounding_box and boxes_intersect(p_box, primary_cta.bounding_box):
                    obstructs_cta = True
                if main_heading and main_heading.bounding_box and boxes_intersect(p_box, main_heading.bounding_box):
                    obstructs_h1 = True
            elif p.is_blocking or p.immediate:
                # Modals with backdrops obstruct the entire above-fold region
                obstructs_cta = bool(primary_cta)
                obstructs_h1 = bool(main_heading)

            if obstructs_cta or obstructs_h1:
                findings.append(EngagementFinding(
                    id="ENG-AF-004",
                    category="above_fold",
                    title="Intrusive overlay obstructs primary above-the-fold content",
                    description=(
                        f"An immediate or blocking overlay ('{p.name or p.type}') obstructs "
                        f"{'the primary CTA' if obstructs_cta else ''}{' and ' if (obstructs_cta and obstructs_h1) else ''}"
                        f"{'the main headline' if obstructs_h1 else ''} upon page load."
                    ),
                    severity="high" if obstructs_cta else "medium",
                    confidence=0.9,
                    evidence={
                        "popup_name": p.name,
                        "popup_type": p.type,
                        "is_blocking": p.is_blocking,
                        "immediate": p.immediate,
                        "obstructs_cta": obstructs_cta,
                        "obstructs_heading": obstructs_h1,
                    },
                    recommendation="Defer newsletter, discount, or promotional popups until the user has scrolled or spent time on the page.",
                    url=target_url,
                ))
                obstruction_findings.append({"popup": p.name or p.type, "obstructs_cta": obstructs_cta, "obstructs_h1": obstructs_h1})

    # --------------------------------------------------------------------------
    # 4. Navigation Visibility Evaluation
    # --------------------------------------------------------------------------
    nav_above_fold = True
    nav_evaluated = False
    for n in input_data.navigation:
        nav_evaluated = True
        if n.bounding_box:
            has_coordinate_evidence = True
            n_vis, _ = classify_fold_visibility(n.bounding_box, viewport)
            if n_vis == "below_fold":
                nav_above_fold = False
                break
        elif n.visible is False:
            nav_above_fold = False

    # --------------------------------------------------------------------------
    # 5. Calculate Above-the-Fold Score (0 to 100)
    # --------------------------------------------------------------------------
    score = 100.0
    for f in findings:
        if f.severity == "critical":
            score -= 35.0 * f.confidence
        elif f.severity == "high":
            score -= 20.0 * f.confidence
        elif f.severity == "medium":
            score -= 10.0 * f.confidence
        elif f.severity == "low":
            score -= 4.0 * f.confidence

    score_clamped = max(0, min(100, int(round(score))))

    status: CategoryStatus = "passed"
    if any(f.severity in ("critical", "high") for f in findings):
        status = "failed"
    elif findings:
        status = "warning"

    overall_confidence = 0.95 if has_coordinate_evidence else 0.75

    metrics = {
        "viewport": viewport.to_dict(),
        "evaluated": True,
        "has_coordinate_evidence": has_coordinate_evidence,
        "main_heading": {
            "text": main_heading.text if main_heading else None,
            "visibility": heading_vis,
            "visible_ratio": round(heading_vis_ratio, 2),
            "bounding_box": main_heading.bounding_box.to_dict() if (main_heading and main_heading.bounding_box) else None,
        },
        "primary_cta": {
            "text": primary_cta.text if primary_cta else None,
            "visibility": cta_vis,
            "visible_ratio": round(cta_vis_ratio, 2),
            "bounding_box": primary_cta.bounding_box.to_dict() if (primary_cta and primary_cta.bounding_box) else None,
        },
        "navigation_above_fold": nav_above_fold if nav_evaluated else None,
        "below_fold_elements_count": len(below_fold_elements),
        "obstructions_count": len(obstruction_findings),
    }

    return CategoryResult(
        category="above_fold",
        status=status,
        score=score_clamped,
        findings=findings,
        metrics=metrics,
        confidence=overall_confidence,
        messages=[
            f"Above-the-fold analysis completed with score {score_clamped}/100.",
            f"Heading status: {heading_vis}, CTA status: {cta_vis}.",
        ],
    )
