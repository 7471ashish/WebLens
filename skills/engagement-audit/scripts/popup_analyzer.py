"""
Popup & Modal Intrusiveness Analyzer (popup_analyzer.py)
--------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates intrusive popups, modals, interstitials, promotional overlays,
and sticky backdrops that disrupt user browsing, obstruct key actions, or trap focus.

Key Evaluations:
1. Popup / modal presence and classification (newsletter, promo, consent, login)
2. Viewport screen coverage ratio (large coverage > 70%)
3. Content and primary CTA obstruction
4. Navigation obstruction and interaction trapping
5. Close control availability, visibility, and dismissibility
6. Timing (immediate popups on load vs. delayed/exit-intent overlays)
7. Repetitive popup frequency and multiple simultaneous overlapping modals
8. Context-aware consent / login / user-triggered modal handling
9. Mobile-specific interstitial and viewport obstruction signals

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
    PopupEvidence,
    SeverityLevel,
    Viewport,
)

logger = logging.getLogger("engagement_audit.popups")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default analysis thresholds
DEFAULT_LARGE_COVERAGE_THRESHOLD: float = 0.70  # 70% of viewport area
DEFAULT_IMMEDIATE_POPUP_THRESHOLD_MS: float = 1000.0  # Under 1 second after load
DEFAULT_MOBILE_COVERAGE_THRESHOLD: float = 0.50  # 50% on mobile viewports

# Modal types that are functional/required rather than promotional
FUNCTIONAL_MODAL_TYPES: tuple[str, ...] = (
    "cookie",
    "consent",
    "cookie_banner",
    "login",
    "auth",
    "system",
    "confirmation",
    "security",
)



# Normalized Popup Internal Models


@dataclass
class NormalizedPopup:
    """Normalized popup/modal representation."""
    id: str
    name: str | None = None
    type: str = "unknown"  # "newsletter", "promotional", "cookie", "login", etc.
    visible: bool | None = None
    modal: bool = False
    bbox: BoundingBox | None = None
    overlay_present: bool = False
    overlay_opacity: float | None = None
    delay_ms: float | None = None
    trigger: str = "unknown"  # "on_load", "scroll", "exit_intent", "user_click"
    frequency: str = "unknown"  # "once", "repeated", "every_page"
    has_close_button: bool | None = None
    close_button_visible: bool | None = None
    dismissible: bool | None = None
    blocks_interaction: bool = False
    covered_elements: list[str] = field(default_factory=list)
    user_triggered: bool = False
    is_mobile: bool = False

    @property
    def is_functional(self) -> bool:
        """Check if modal is a required system, login, or cookie consent dialog."""
        return self.type.lower() in FUNCTIONAL_MODAL_TYPES or self.user_triggered

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "visible": self.visible,
            "modal": self.modal,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "delay_ms": self.delay_ms,
            "has_close_button": self.has_close_button,
            "blocks_interaction": self.blocks_interaction,
            "covered_elements": self.covered_elements,
            "is_functional": self.is_functional,
        }



# Normalization Helper


def normalize_popup_evidence(raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract and normalize popup/modal records from variable input dictionary shapes.
    """
    popups_container = None
    for key in ("popups", "popup", "modals", "overlays", "interstitials"):
        if key in raw_data:
            popups_container = raw_data[key]
            break

    result: dict[str, Any] = {
        "has_popup_data": False,
        "items": [],
        "mobile_popups": [],
    }

    if popups_container is None:
        return result

    result["has_popup_data"] = True

    raw_items = popups_container if isinstance(popups_container, list) else [popups_container]
    parsed_popups: list[NormalizedPopup] = []

    for idx, item in enumerate(raw_items):
        if not item or not isinstance(item, (dict, PopupEvidence)):
            continue

        if isinstance(item, PopupEvidence):
            parsed_popups.append(NormalizedPopup(
                id=f"popup-{idx + 1}",
                name=item.name,
                type=item.type,
                visible=True,
                modal=item.is_blocking,
                bbox=item.bounding_box,
                overlay_present=item.is_blocking,
                delay_ms=item.delay_ms if item.immediate else (item.delay_ms if item.delay_ms > 0 else None),
                trigger="on_load" if item.immediate else "delayed",
                has_close_button=item.has_close_button,
                dismissible=item.has_close_button,
                blocks_interaction=item.is_blocking,
                covered_elements=list(item.affected_content),
            ))
            continue

        pid = str(item.get("id") or f"popup-{idx + 1}")
        name = item.get("name") or item.get("title")
        ptype = str(item.get("type") or item.get("role") or "unknown").lower()

        vis = None
        if "visible" in item and item["visible"] is not None:
            vis = bool(item["visible"])

        is_modal = bool(item.get("modal") or item.get("is_modal") or item.get("is_blocking", False))

        raw_box = item.get("bbox") or item.get("bounding_box") or item.get("box")
        bbox = BoundingBox.from_dict(raw_box)

        # Overlay data
        raw_overlay = item.get("overlay", {})
        overlay_present = is_modal
        overlay_opacity = None
        if isinstance(raw_overlay, dict):
            overlay_present = bool(raw_overlay.get("present", is_modal))
            if "opacity" in raw_overlay:
                try:
                    overlay_opacity = float(raw_overlay["opacity"])
                except (ValueError, TypeError):
                    pass
        elif isinstance(raw_overlay, bool):
            overlay_present = raw_overlay

        # Timing & trigger
        raw_timing = item.get("timing")
        delay_ms = None
        if isinstance(raw_timing, dict) and "delay_ms" in raw_timing:
            try:
                delay_ms = float(raw_timing["delay_ms"])
            except (ValueError, TypeError):
                pass
        elif "delay_ms" in item and item["delay_ms"] is not None:
            try:
                delay_ms = float(item["delay_ms"])
            except (ValueError, TypeError):
                pass

        if item.get("immediate") is True:
            delay_ms = 0.0
            trigger = "on_load"
        elif item.get("immediate") is False:
            if delay_ms == 0.0:
                delay_ms = None
            trigger = str(item.get("trigger") or "delayed").lower()
        else:
            trigger = str(item.get("trigger") or ("on_load" if (delay_ms is not None and delay_ms < 1000) else "unknown")).lower()
        frequency = str(item.get("frequency") or "unknown").lower()

        # Close control
        has_close = None
        close_vis = None
        raw_close = item.get("close_control") or item.get("close_button")
        if isinstance(raw_close, dict):
            has_close = bool(raw_close.get("present", True))
            if "visible" in raw_close:
                close_vis = bool(raw_close["visible"])
        elif isinstance(raw_close, bool):
            has_close = raw_close
        elif "has_close_button" in item and item["has_close_button"] is not None:
            has_close = bool(item["has_close_button"])

        if "close_button_visible" in item and item["close_button_visible"] is not None:
            close_vis = bool(item["close_button_visible"])

        dismissible = None
        if "dismissible" in item and item["dismissible"] is not None:
            dismissible = bool(item["dismissible"])
        elif has_close is not None:
            dismissible = has_close

        blocks_interaction = bool(item.get("blocks_interaction", is_modal or overlay_present))
        covered_elements = list(item.get("covered_elements") or item.get("affected_content") or [])
        user_triggered = bool(item.get("user_triggered", False))
        is_mobile = bool(item.get("is_mobile") or item.get("mobile", False))

        parsed_popups.append(NormalizedPopup(
            id=pid,
            name=name,
            type=ptype,
            visible=vis,
            modal=is_modal,
            bbox=bbox,
            overlay_present=overlay_present,
            overlay_opacity=overlay_opacity,
            delay_ms=delay_ms,
            trigger=trigger,
            frequency=frequency,
            has_close_button=has_close,
            close_button_visible=close_vis,
            dismissible=dismissible,
            blocks_interaction=blocks_interaction,
            covered_elements=covered_elements,
            user_triggered=user_triggered,
            is_mobile=is_mobile,
        ))

    result["items"] = parsed_popups
    return result



# Popup Analyzer Core Logic


class PopupAnalyzer:
    """
    Main evaluation engine for intrusive popups, modals, overlays, and interstitials.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.large_coverage_thresh = float(self.config.get("large_coverage_threshold", DEFAULT_LARGE_COVERAGE_THRESHOLD))
        self.immediate_thresh_ms = float(self.config.get("immediate_threshold_ms", DEFAULT_IMMEDIATE_POPUP_THRESHOLD_MS))
        self.mobile_coverage_thresh = float(self.config.get("mobile_coverage_threshold", DEFAULT_MOBILE_COVERAGE_THRESHOLD))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive popup/modal intrusiveness analysis.
        """
        opts = {**self.config, **(options or {})}

        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        vp_dict = raw_dict.get("viewport") or {"width": 1366, "height": 768}
        viewport = Viewport.from_dict(vp_dict)

        logger.info("Starting popup intrusiveness analysis for %s", target_url)

        normalized_data = normalize_popup_evidence(raw_dict)

        # ----------------------------------------------------------------------
        # Check 1: Popup Presence & Insufficient Evidence Handling
        # ----------------------------------------------------------------------
        if not normalized_data["has_popup_data"]:
            return CategoryResult(
                category="popups",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "popup_presence": "unknown",
                    "popups_analyzed": 0,
                    "message": "No popup or overlay evidence provided in audit input",
                },
                confidence=0.5,
                messages=["No popup evidence supplied for evaluation."],
            )

        popups: list[NormalizedPopup] = normalized_data["items"]

        # Explicitly zero popups detected -> Perfect pass
        if not popups:
            return CategoryResult(
                category="popups",
                status="passed",
                score=100,
                findings=[],
                metrics={
                    "popup_presence": "absent",
                    "popups_analyzed": 0,
                    "visible_popups_count": 0,
                },
                confidence=1.0,
                messages=["No intrusive popups or blocking overlays detected on page."],
            )

        findings: list[EngagementFinding] = []
        visible_popups: list[dict[str, Any]] = []
        obstructing_popups: list[dict[str, Any]] = []
        cta_obstructions: list[str] = []
        nav_obstructions: list[str] = []
        content_obstructions: list[str] = []
        dismissal_issues: list[str] = []

        active_visible_count = 0

        for p in popups:
            # Skip hidden popups from intrusive scoring
            if p.visible is False:
                continue

            active_visible_count += 1
            visible_popups.append(p.to_dict())

            # ------------------------------------------------------------------
            # Check 3: Screen Coverage Ratio
            # ------------------------------------------------------------------
            coverage_ratio = None
            if p.bbox and viewport.width > 0 and viewport.height > 0:
                vp_area = viewport.width * viewport.height
                p_area = p.bbox.area
                coverage_ratio = round(p_area / vp_area, 2)

                if coverage_ratio >= self.large_coverage_thresh and not p.is_functional:
                    findings.append(EngagementFinding(
                        id="ENG-POP-001",
                        category="popups",
                        title=f"Excessive screen coverage by overlay ({int(coverage_ratio * 100)}% of viewport)",
                        description=(
                            f"Overlay '{p.name or p.id}' consumes {int(coverage_ratio * 100)}% of the initial "
                            f"{viewport.width:.0f}x{viewport.height:.0f}px viewport, dominating the browsing experience."
                        ),
                        severity="medium",
                        confidence=0.95,
                        evidence={
                            "popup_id": p.id,
                            "coverage_ratio": coverage_ratio,
                            "popup_dimensions": f"{p.bbox.width:.0f}x{p.bbox.height:.0f}px",
                            "viewport": viewport.to_dict(),
                        },
                        recommendation="Reduce overlay dimensions to a non-intrusive modal or banner that preserves context.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 4, 5, 6: Element & CTA Obstruction
            # ------------------------------------------------------------------
            covered_low = [elem.lower().strip() for elem in p.covered_elements]
            obstructs_cta = any("cta" in elem or "button" in elem or "action" in elem for elem in covered_low)
            obstructs_nav = any("nav" in elem or "header" in elem or "menu" in elem for elem in covered_low)
            obstructs_content = any("content" in elem or "heading" in elem or "main" in elem for elem in covered_low)

            if obstructs_cta:
                cta_obstructions.append(p.id)
                findings.append(EngagementFinding(
                    id="ENG-POP-003",
                    category="popups",
                    title="Modal overlay obstructs primary Call-to-Action",
                    description=f"Overlay '{p.name or p.id}' physically covers the primary conversion CTA, preventing user action.",
                    severity="high",
                    confidence=0.95,
                    evidence={"popup_id": p.id, "covered_elements": p.covered_elements},
                    recommendation="Ensure overlays do not cover primary conversion buttons or provide easy inline dismissal.",
                    url=target_url,
                ))

            if obstructs_nav:
                nav_obstructions.append(p.id)
                findings.append(EngagementFinding(
                    id="ENG-POP-004",
                    category="popups",
                    title="Overlay obstructs top website navigation",
                    description=f"Overlay '{p.name or p.id}' blocks access to primary header navigation links.",
                    severity="medium",
                    confidence=0.95,
                    evidence={"popup_id": p.id, "covered_elements": p.covered_elements},
                    recommendation="Position banners below navigation or ensure modal z-index allows top bar navigation access.",
                    url=target_url,
                ))

            if obstructs_content:
                content_obstructions.append(p.id)
                findings.append(EngagementFinding(
                    id="ENG-POP-002",
                    category="popups",
                    title="Modal blocks main page body content",
                    description=f"Overlay '{p.name or p.id}' blocks main readable text and headings before user interaction.",
                    severity="medium",
                    confidence=0.9,
                    evidence={"popup_id": p.id, "covered_elements": p.covered_elements},
                    recommendation="Allow visitors to read the initial value proposition before triggering promotional overlays.",
                    url=target_url,
                ))

            if obstructs_cta or obstructs_nav or obstructs_content or p.blocks_interaction:
                obstructing_popups.append(p.to_dict())

            # ------------------------------------------------------------------
            # Check 7 & 8: Close Control Availability & Visibility
            # ------------------------------------------------------------------
            # Functional dialogs like cookie consent or login may not have a simple "X" close,
            # but promotional/marketing modals MUST have an explicit close control.
            if not p.is_functional:
                if p.has_close_button is False:
                    dismissal_issues.append(p.id)
                    findings.append(EngagementFinding(
                        id="ENG-POP-005",
                        category="popups",
                        title=f"Promotional modal '{p.name or p.id}' lacks a close/dismiss button",
                        description=(
                            f"The '{p.type}' modal has no identifiable close button ('X' or 'Close'), "
                            "trapping user interaction and forcing engagement."
                        ),
                        severity="high",
                        confidence=0.95,
                        evidence={"popup_id": p.id, "has_close_button": False, "type": p.type},
                        recommendation="Provide an explicit, easily accessible close button in the top-right corner of the modal.",
                        url=target_url,
                    ))
                elif p.close_button_visible is False:
                    dismissal_issues.append(p.id)
                    findings.append(EngagementFinding(
                        id="ENG-POP-006",
                        category="popups",
                        title=f"Close button on modal '{p.name or p.id}' is hidden or off-screen",
                        description="The close control exists in the DOM but is rendered hidden or outside the viewport boundary.",
                        severity="high",
                        confidence=0.95,
                        evidence={"popup_id": p.id, "close_button_visible": False},
                        recommendation="Ensure modal close controls are fully visible and within the active viewport.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 9: Immediate Page Load Interruption
            # ------------------------------------------------------------------
            is_immediate = (
                p.delay_ms is not None and p.delay_ms <= self.immediate_thresh_ms
            ) or p.trigger == "on_load"

            if is_immediate and not p.is_functional and p.blocks_interaction:
                findings.append(EngagementFinding(
                    id="ENG-POP-007",
                    category="popups",
                    title=f"Immediate blocking overlay displayed on page load ({p.delay_ms or 0:.0f}ms delay)",
                    description=(
                        f"A promotional '{p.type}' modal triggers immediately upon landing ({p.delay_ms or 0:.0f}ms), "
                        "interrupting the user before they can view page content."
                    ),
                    severity="medium",
                    confidence=0.9,
                    evidence={"popup_id": p.id, "delay_ms": p.delay_ms, "trigger": p.trigger},
                    recommendation="Delay promotional popups until after 10-15 seconds of engagement or on exit-intent.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 10: Frequency & Repetition
            # ------------------------------------------------------------------
            if p.frequency in ("repeated", "every_page") and not p.is_functional:
                findings.append(EngagementFinding(
                    id="ENG-POP-008",
                    category="popups",
                    title=f"Repetitive popup interruptions ({p.frequency})",
                    description=f"Modal '{p.name or p.id}' is configured to re-trigger repeatedly across browsing sessions.",
                    severity="medium",
                    confidence=0.85,
                    evidence={"popup_id": p.id, "frequency": p.frequency},
                    recommendation="Set a session cookie to suppress dismissed popups for at least 7-14 days.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 14: Mobile Interstitial Signals
            # ------------------------------------------------------------------
            if p.is_mobile:
                if coverage_ratio and coverage_ratio >= self.mobile_coverage_thresh and not p.is_functional:
                    findings.append(EngagementFinding(
                        id="ENG-POP-010",
                        category="popups",
                        title=f"Intrusive mobile interstitial ({int(coverage_ratio * 100)}% mobile screen coverage)",
                        description=(
                            f"Mobile overlay consumes {int(coverage_ratio * 100)}% of mobile screen area, "
                            "violating Google's intrusive interstitial guidelines."
                        ),
                        severity="high",
                        confidence=0.95,
                        evidence={"popup_id": p.id, "mobile_coverage": coverage_ratio},
                        recommendation="Use small bottom banner sheets instead of full-screen interstitials on mobile viewports.",
                        url=target_url,
                    ))

        # ----------------------------------------------------------------------
        # Check 13: Multiple Simultaneous Overlapping Popups
        # ----------------------------------------------------------------------
        if active_visible_count > 1:
            findings.append(EngagementFinding(
                id="ENG-POP-009",
                category="popups",
                title=f"Multiple simultaneous overlays active ({active_visible_count} overlays)",
                description=(
                    f"Page displays {active_visible_count} simultaneous popup dialogs/banners at the same time, "
                    "overwhelming the user with competing dismissals."
                ),
                severity="high",
                confidence=0.9,
                evidence={"simultaneous_popups_count": active_visible_count},
                recommendation="Queue overlays sequentially and never display more than one modal at a time.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Scoring Calculation (0 to 100)
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
            "popups_analyzed": len(popups),
            "visible_popups_count": len(visible_popups),
            "obstructing_popups_count": len(obstructing_popups),
            "cta_obstructions_count": len(cta_obstructions),
            "nav_obstructions_count": len(nav_obstructions),
            "content_obstructions_count": len(content_obstructions),
            "dismissal_issues_count": len(dismissal_issues),
        }

        return CategoryResult(
            category="popups",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95,
            messages=[
                f"Popup analysis completed: {len(popups)} popups evaluated (Score: {score_clamped}/100).",
                f"Obstructing overlays detected: {len(obstructing_popups)}.",
            ],
        )



# Public API Function


def analyze_popups(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for Popup and Modal intrusiveness analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult containing status, score, metrics, and popup findings.
    """
    analyzer = PopupAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
