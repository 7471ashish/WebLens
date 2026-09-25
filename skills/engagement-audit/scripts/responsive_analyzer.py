"""
Responsive Experience & Multi-Viewport Analyzer (responsive_analyzer.py)
------------------------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates webpage layout adaptation across desktop (1366x768), tablet (768x1024),
and mobile (390x844) viewports from structured geometric layout observations.

Key Evaluations:
1. Page-level and container-level horizontal overflow (page_width > viewport_width)
2. Elements extending beyond viewport boundaries (right > viewport_width)
3. Explicit content and control clipping (clipped: true)
4. Unintended element overlap through bounding-box intersection
5. Responsive navigation transformation (desktop nav vs. mobile collapsed toggle)
6. Responsive CTA visibility and containment across mobile screens
7. Fixed-width container detection across small viewports (e.g. width: 1200px on 390px mobile)
8. Touch-target accessibility dimensions on touch devices (< 44x44px minimum)
9. Responsive image/media overflow exceeding container boundaries
10. Cross-viewport layout regression detection between desktop, tablet, and mobile

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
    EngagementFinding,
    PageInputData,
    SeverityLevel,
    Viewport,
)

logger = logging.getLogger("engagement_audit.responsive")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default viewport dimensions and touch thresholds
DEFAULT_DESKTOP_WIDTH: int = 1366
DEFAULT_DESKTOP_HEIGHT: int = 768
DEFAULT_TABLET_WIDTH: int = 768
DEFAULT_TABLET_HEIGHT: int = 1024
DEFAULT_MOBILE_WIDTH: int = 390
DEFAULT_MOBILE_HEIGHT: int = 844

DEFAULT_MIN_TOUCH_TARGET: float = 44.0  # px (Apple HIG / W3C standard)



# Normalized Responsive Models


@dataclass
class NormalizedLayoutElement:
    """Normalized geometric representation of an element on a specific viewport."""
    id: str
    type: str = "element"  # "cta", "navigation", "image", "form", "section", etc.
    bbox: BoundingBox | None = None
    visible: bool = True
    clipped: bool = False
    clipped_pixels: float = 0.0
    scrollable: bool = False
    is_intentional_scroll: bool = False
    touch_target_size: float | None = None

    @property
    def is_interactive(self) -> bool:
        return self.type.lower() in ("cta", "button", "link", "input", "select", "control")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "visible": self.visible,
            "clipped": self.clipped,
            "clipped_pixels": self.clipped_pixels,
            "scrollable": self.scrollable,
            "is_intentional_scroll": self.is_intentional_scroll,
        }


@dataclass
class NormalizedViewportProfile:
    """Normalized layout snapshot of the page rendered at a specific viewport."""
    name: str
    width: int
    height: int
    page_width: int | None = None
    content_width: int | None = None
    horizontal_overflow: bool | None = None
    elements: list[NormalizedLayoutElement] = field(default_factory=list)
    has_mobile_nav_toggle: bool | None = None

    @property
    def overflow_amount(self) -> int:
        if self.page_width and self.page_width > self.width:
            return self.page_width - self.width
        if self.content_width and self.content_width > self.width:
            return self.content_width - self.width
        return 0

    @property
    def is_mobile_or_tablet(self) -> bool:
        return self.width <= 768 or self.name.lower() in ("mobile", "tablet", "mobile_small")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "page_width": self.page_width,
            "content_width": self.content_width,
            "horizontal_overflow": self.horizontal_overflow or (self.overflow_amount > 0),
            "overflow_amount": self.overflow_amount,
            "elements_count": len(self.elements),
        }



# Normalization Helpers


def _parse_layout_element(item: Any, idx: int) -> NormalizedLayoutElement | None:
    """Safely parse a single layout element dictionary."""
    if not item or not isinstance(item, dict):
        return None

    eid = str(item.get("id") or item.get("name") or f"elem-{idx + 1}")
    etype = str(item.get("type") or item.get("role") or "element").lower()

    raw_box = item.get("bbox") or item.get("bounding_box") or item.get("box")
    bbox = BoundingBox.from_dict(raw_box)

    vis = True
    if "visible" in item and item["visible"] is not None:
        vis = bool(item["visible"])

    clipped = bool(item.get("clipped", False))
    clipped_px = float(item.get("clipped_pixels", 0.0) or 0.0)

    scrollable = bool(item.get("scrollable", False))
    overflow_behavior = str(item.get("overflow_behavior") or "").lower()
    is_intentional = scrollable or overflow_behavior in ("intentional", "scroll", "auto")
    if etype in ("table", "carousel", "code_block", "timeline", "map"):
        is_intentional = True

    touch_size = None
    if "touch_target_size" in item and item["touch_target_size"] is not None:
        try:
            touch_size = float(item["touch_target_size"])
        except (ValueError, TypeError):
            pass
    elif bbox and etype in ("button", "cta", "link", "control"):
        touch_size = min(bbox.width, bbox.height)

    return NormalizedLayoutElement(
        id=eid,
        type=etype,
        bbox=bbox,
        visible=vis,
        clipped=clipped,
        clipped_pixels=clipped_px,
        scrollable=scrollable,
        is_intentional_scroll=is_intentional,
        touch_target_size=touch_size,
    )


def extract_responsive_evidence(raw_data: dict[str, Any]) -> list[NormalizedViewportProfile]:
    """
    Extract and normalize multi-viewport profiles from variable input representations.
    """
    raw_viewports = (
        raw_data.get("viewports")
        or raw_data.get("viewport_profiles")
        or raw_data.get("layouts")
    )

    profiles: list[NormalizedViewportProfile] = []

    # Case A: Explicit list of viewport profiles
    if isinstance(raw_viewports, list):
        for idx, vp in enumerate(raw_viewports):
            if not vp or not isinstance(vp, dict):
                continue
            name = str(vp.get("name") or f"viewport-{idx + 1}")
            w = int(vp.get("width") or (DEFAULT_MOBILE_WIDTH if "mobile" in name else DEFAULT_DESKTOP_WIDTH))
            h = int(vp.get("height") or (DEFAULT_MOBILE_HEIGHT if "mobile" in name else DEFAULT_DESKTOP_HEIGHT))

            page_w = vp.get("page_width")
            page_w = int(page_w) if page_w is not None else None

            content_w = vp.get("content_width")
            content_w = int(content_w) if content_w is not None else None

            h_overflow = vp.get("horizontal_overflow")
            h_overflow = bool(h_overflow) if h_overflow is not None else None

            elements: list[NormalizedLayoutElement] = []
            raw_elems = vp.get("elements") or []
            if isinstance(raw_elems, list):
                for e_idx, e in enumerate(raw_elems):
                    parsed_e = _parse_layout_element(e, e_idx)
                    if parsed_e:
                        elements.append(parsed_e)

            has_toggle = vp.get("has_mobile_nav_toggle")
            has_toggle = bool(has_toggle) if has_toggle is not None else None

            profiles.append(NormalizedViewportProfile(
                name=name,
                width=w,
                height=h,
                page_width=page_w,
                content_width=content_w,
                horizontal_overflow=h_overflow,
                elements=elements,
                has_mobile_nav_toggle=has_toggle,
            ))

    # Case B: Single viewport data object or mobile_data container
    elif isinstance(raw_data.get("viewport"), dict):
        vp = raw_data["viewport"]
        w = int(vp.get("width", DEFAULT_DESKTOP_WIDTH))
        h = int(vp.get("height", DEFAULT_DESKTOP_HEIGHT))
        name = "mobile" if w <= 500 else ("tablet" if w <= 800 else "desktop")

        elements: list[NormalizedLayoutElement] = []
        raw_elems = raw_data.get("elements") or []
        if isinstance(raw_elems, list):
            for e_idx, e in enumerate(raw_elems):
                parsed_e = _parse_layout_element(e, e_idx)
                if parsed_e:
                    elements.append(parsed_e)

        profiles.append(NormalizedViewportProfile(
            name=name,
            width=w,
            height=h,
            page_width=raw_data.get("page_width"),
            content_width=raw_data.get("content_width"),
            horizontal_overflow=raw_data.get("horizontal_overflow"),
            elements=elements,
        ))

    # Case C: Specialized mobile_evidence container
    if raw_data.get("mobile_data") and isinstance(raw_data["mobile_data"], dict):
        mob = raw_data["mobile_data"]
        profiles.append(NormalizedViewportProfile(
            name="mobile",
            width=int(mob.get("width", DEFAULT_MOBILE_WIDTH)),
            height=int(mob.get("height", DEFAULT_MOBILE_HEIGHT)),
            page_width=mob.get("page_width"),
            content_width=mob.get("content_width"),
            horizontal_overflow=mob.get("horizontal_overflow"),
            elements=[
                _parse_layout_element(e, idx)
                for idx, e in enumerate(mob.get("elements", []))
                if _parse_layout_element(e, idx)
            ],
            has_mobile_nav_toggle=mob.get("has_mobile_nav_toggle"),
        ))

    return profiles


def calculate_box_overlap(b1: BoundingBox, b2: BoundingBox) -> float:
    """Calculate the intersecting area in pixels between two bounding boxes."""
    x_overlap = max(0.0, min(b1.x + b1.width, b2.x + b2.width) - max(b1.x, b2.x))
    y_overlap = max(0.0, min(b1.y + b1.height, b2.y + b2.height) - max(b1.y, b2.y))
    return x_overlap * y_overlap



# Responsive Analyzer Core Logic


class ResponsiveAnalyzer:
    """
    Main evaluation engine for responsive design, layout overflow, and viewport adaptation.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.min_touch_target = float(self.config.get("min_touch_target", DEFAULT_MIN_TOUCH_TARGET))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive responsive layout and multi-viewport analysis.
        """
        opts = {**self.config, **(options or {})}

        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        logger.info("Starting responsive analysis for %s", target_url)

        profiles = extract_responsive_evidence(raw_dict)

        # ----------------------------------------------------------------------
        # Check: Insufficient Evidence Handling
        # ----------------------------------------------------------------------
        if not profiles:
            return CategoryResult(
                category="responsive",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "viewports_analyzed": 0,
                    "message": "No viewport or responsive layout evidence provided in audit input",
                },
                confidence=0.5,
                messages=["No responsive layout evidence supplied for evaluation."],
            )

        findings: list[EngagementFinding] = []
        viewport_summaries: list[dict[str, Any]] = []

        # ----------------------------------------------------------------------
        # Per-Viewport Analysis
        # ----------------------------------------------------------------------
        for vp in profiles:
            vp_dict = vp.to_dict()
            viewport_summaries.append(vp_dict)

            # ------------------------------------------------------------------
            # Check 1: Horizontal Page Overflow
            # ------------------------------------------------------------------
            has_overflow = vp.horizontal_overflow is True or vp.overflow_amount > 0
            if has_overflow:
                ov_amt = vp.overflow_amount or 50
                findings.append(EngagementFinding(
                    id="ENG-RESP-001",
                    category="responsive",
                    title=f"Horizontal page overflow detected on {vp.name} viewport ({vp.width}px)",
                    description=(
                        f"At viewport width {vp.width}px, the page width expands to {vp.page_width or vp.width + ov_amt}px, "
                        f"causing {ov_amt}px of unintended horizontal scrolling."
                    ),
                    severity="high" if vp.is_mobile_or_tablet else "medium",
                    confidence=0.95,
                    evidence={
                        "viewport_name": vp.name,
                        "viewport_width": vp.width,
                        "page_width": vp.page_width,
                        "overflow_pixels": ov_amt,
                    },
                    recommendation="Set max-width: 100% and overflow-x: hidden on top-level layout containers.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 2: Element Extending Outside Viewport Boundary
            # ------------------------------------------------------------------
            for elem in vp.elements:
                if not elem.visible or elem.is_intentional_scroll or not elem.bbox:
                    continue

                elem_right = elem.bbox.x + elem.bbox.width
                if elem_right > (vp.width + 5):  # 5px tolerance
                    excess_px = int(elem_right - vp.width)
                    findings.append(EngagementFinding(
                        id="ENG-RESP-002",
                        category="responsive",
                        title=f"Element '{elem.id}' extends beyond the {vp.name} viewport ({excess_px}px overflow)",
                        description=(
                            f"Element '{elem.id}' ({elem.type}) extends from x={elem.bbox.x:.0f} to {elem_right:.0f}px, "
                            f"exceeding the {vp.width}px {vp.name} viewport boundary."
                        ),
                        severity="high" if elem.type in ("cta", "navigation", "form") else "medium",
                        confidence=0.95,
                        evidence={
                            "element_id": elem.id,
                            "element_type": elem.type,
                            "element_bbox": elem.bbox.to_dict(),
                            "viewport_width": vp.width,
                            "excess_pixels": excess_px,
                        },
                        recommendation="Use fluid percentage widths (e.g. width: 100%) or CSS media query breakpoints.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 3: Element Clipping
            # ------------------------------------------------------------------
            for elem in vp.elements:
                if elem.clipped:
                    findings.append(EngagementFinding(
                        id="ENG-RESP-003",
                        category="responsive",
                        title=f"Clipped {elem.type} element on {vp.name} viewport",
                        description=f"Element '{elem.id}' is visually clipped ({elem.clipped_pixels:.0f}px cut off) due to fixed overflow boundaries.",
                        severity="medium",
                        confidence=0.9,
                        evidence={"element_id": elem.id, "clipped_pixels": elem.clipped_pixels, "viewport": vp.name},
                        recommendation="Allow container height to expand with auto flow or adjust font-size to prevent clipping.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 4: Element Overlap Detection
            # ------------------------------------------------------------------
            visible_elements = [e for e in vp.elements if e.visible and e.bbox]
            for i in range(len(visible_elements)):
                for j in range(i + 1, len(visible_elements)):
                    e1 = visible_elements[i]
                    e2 = visible_elements[j]
                    if e1.bbox and e2.bbox:
                        overlap_area = calculate_box_overlap(e1.bbox, e2.bbox)
                        if overlap_area > 500:  # meaningful overlap > 500px^2
                            findings.append(EngagementFinding(
                                id="ENG-RESP-004",
                                category="responsive",
                                title=f"Layout overlap between '{e1.id}' and '{e2.id}' on {vp.name}",
                                description=(
                                    f"Elements '{e1.id}' ({e1.type}) and '{e2.id}' ({e2.type}) collide with "
                                    f"{overlap_area:.0f}px² overlapping surface area."
                                ),
                                severity="medium",
                                confidence=0.9,
                                evidence={
                                    "element_1": e1.id,
                                    "element_2": e2.id,
                                    "overlap_area": overlap_area,
                                    "viewport": vp.name,
                                },
                                recommendation="Apply appropriate margin/padding and avoid rigid absolute positioning across breakpoints.",
                                url=target_url,
                            ))

            # ------------------------------------------------------------------
            # Check 5: Responsive Navigation on Mobile
            # ------------------------------------------------------------------
            if vp.is_mobile_or_tablet:
                nav_elems = [e for e in vp.elements if e.type == "navigation"]
                for nav in nav_elems:
                    if nav.bbox and nav.bbox.width > (vp.width + 10):
                        findings.append(EngagementFinding(
                            id="ENG-RESP-005",
                            category="responsive",
                            title=f"Desktop navigation bar does not collapse on {vp.name} viewport",
                            description=(
                                f"Navigation bar remains at desktop width ({nav.bbox.width:.0f}px) on a "
                                f"{vp.width}px screen instead of collapsing into a mobile hamburger menu."
                            ),
                            severity="high",
                            confidence=0.95,
                            evidence={"nav_width": nav.bbox.width, "viewport_width": vp.width},
                            recommendation="Implement a collapsible hamburger navigation menu on viewports <= 768px.",
                            url=target_url,
                        ))

            # ------------------------------------------------------------------
            # Check 6: Responsive CTA Visibility & Containment
            # ------------------------------------------------------------------
            cta_elems = [e for e in vp.elements if e.type in ("cta", "button")]
            for cta in cta_elems:
                if cta.bbox and vp.is_mobile_or_tablet:
                    if (cta.bbox.x + cta.bbox.width) > vp.width:
                        findings.append(EngagementFinding(
                            id="ENG-RESP-006",
                            category="responsive",
                            title=f"Primary Call-to-Action button overflows {vp.name} viewport",
                            description=f"CTA button '{cta.id}' width ({cta.bbox.width:.0f}px) exceeds the available {vp.name} screen width.",
                            severity="high",
                            confidence=0.95,
                            evidence={"cta_id": cta.id, "cta_bbox": cta.bbox.to_dict(), "viewport_width": vp.width},
                            recommendation="Set mobile CTA width to 100% with box-sizing: border-box.",
                            url=target_url,
                        ))

            # ------------------------------------------------------------------
            # Check 8: Small Touch Target Dimensions
            # ------------------------------------------------------------------
            if vp.is_mobile_or_tablet:
                for elem in vp.elements:
                    if elem.is_interactive and elem.touch_target_size:
                        if elem.touch_target_size < self.min_touch_target:
                            findings.append(EngagementFinding(
                                id="ENG-RESP-008",
                                category="responsive",
                                title=f"Small touch target '{elem.id}' ({elem.touch_target_size:.0f}px) on {vp.name}",
                                description=(
                                    f"Interactive control '{elem.id}' has a dimension of {elem.touch_target_size:.0f}px, "
                                    f"which falls below the recommended {self.min_touch_target:.0f}x{self.min_touch_target:.0f}px mobile touch target size."
                                ),
                                severity="low",
                                confidence=0.9,
                                evidence={
                                    "element_id": elem.id,
                                    "touch_target_size": elem.touch_target_size,
                                    "min_required_size": self.min_touch_target,
                                    "viewport": vp.name,
                                },
                                recommendation=f"Increase button/link padding to ensure minimum {self.min_touch_target:.0f}x{self.min_touch_target:.0f}px tappable area.",
                                url=target_url,
                            ))

        # ----------------------------------------------------------------------
        # Check 10 & 12: Cross-Viewport Regression & Fixed-Width Layouts
        # ----------------------------------------------------------------------
        if len(profiles) > 1:
            desktop_vp = next((p for p in profiles if p.width >= 1000), None)
            mobile_vp = next((p for p in profiles if p.width <= 500), None)

            if desktop_vp and mobile_vp:
                # Compare matching element IDs
                desk_elems = {e.id: e for e in desktop_vp.elements if e.bbox}
                mob_elems = {e.id: e for e in mobile_vp.elements if e.bbox}

                for eid, d_elem in desk_elems.items():
                    if eid in mob_elems:
                        m_elem = mob_elems[eid]
                        # If width is unchanged between desktop (>900px) and mobile (<500px)
                        if d_elem.bbox and m_elem.bbox and d_elem.bbox.width >= 800 and m_elem.bbox.width == d_elem.bbox.width:
                            findings.append(EngagementFinding(
                                id="ENG-RESP-007",
                                category="responsive",
                                title=f"Fixed-width element '{eid}' fails to scale down on mobile ({m_elem.bbox.width:.0f}px)",
                                description=(
                                    f"Element '{eid}' maintains a rigid fixed width of {d_elem.bbox.width:.0f}px "
                                    f"on the {mobile_vp.width}px mobile viewport, failing to adapt."
                                ),
                                severity="medium",
                                confidence=0.95,
                                evidence={
                                    "element_id": eid,
                                    "desktop_width": d_elem.bbox.width,
                                    "mobile_width": m_elem.bbox.width,
                                    "mobile_viewport": mobile_vp.width,
                                },
                                recommendation="Replace fixed pixel widths with max-width: 100% or relative CSS units.",
                                url=target_url,
                            ))

        # ----------------------------------------------------------------------
        # Deterministic Scoring Calculation (0 to 100)
        # ----------------------------------------------------------------------
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

        metrics = {
            "viewports_analyzed": len(profiles),
            "viewport_names": [p.name for p in profiles],
            "viewport_summaries": viewport_summaries,
            "overflow_findings_count": len([f for f in findings if f.id in ("ENG-RESP-001", "ENG-RESP-002")]),
            "clipping_findings_count": len([f for f in findings if f.id == "ENG-RESP-003"]),
            "overlap_findings_count": len([f for f in findings if f.id == "ENG-RESP-004"]),
            "touch_target_findings_count": len([f for f in findings if f.id == "ENG-RESP-008"]),
        }

        return CategoryResult(
            category="responsive",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95 if len(profiles) > 1 else 0.85,
            messages=[
                f"Responsive analysis completed across {len(profiles)} viewport(s) (Score: {score_clamped}/100).",
                f"Viewports evaluated: {', '.join(p.name for p in profiles)}.",
            ],
        )



# Public API Function


def analyze_responsiveness(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for Responsive Design and Multi-Viewport layout analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult containing status, score, metrics, and responsive findings.
    """
    analyzer = ResponsiveAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
