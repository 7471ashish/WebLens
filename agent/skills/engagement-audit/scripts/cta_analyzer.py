"""
Call-to-Action (CTA) Analyzer (cta_analyzer.py)
-----------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates the visibility, clarity, placement, prominence, tap-target size,
action specificity, and usability signals of calls-to-action on a webpage.

Key Evaluations:
1. CTA presence & identification of primary vs. secondary actions
2. CTA visibility, clipping, and hidden state detection
3. Above-the-fold CTA placement at target viewports (1366x768)
4. CTA prominence and visual hierarchy differentiation
5. CTA label clarity, specificity, and action orientation
6. CTA destination and action targets (detecting dead-end '#' links)
7. CTA obstruction by modals, sticky banners, or overlays
8. Multiple competing primary CTAs causing decision paralysis
9. CTA tap target size and accessibility dimensions
10. Disabled primary CTA state detection

Architectural Constraint:
    Operates strictly in-memory on structured page evidence. Does not spawn browsers,
    make network calls, or import from crawl-render-audit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from engagement_state import (
    BoundingBox,
    CategoryResult,
    CategoryStatus,
    CTAElement,
    EngagementFinding,
    PageInputData,
    SeverityLevel,
    Viewport,
)

logger = logging.getLogger("engagement_audit.cta")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default analysis thresholds
DEFAULT_MIN_TAP_HEIGHT: float = 36.0  # px
DEFAULT_MIN_TAP_WIDTH: float = 44.0   # px
DEFAULT_MIN_TAP_AREA: float = 1600.0  # px^2 (e.g. 40x40)

# Action-oriented strong CTA patterns
STRONG_ACTION_PATTERNS: tuple[str, ...] = (
    r"\b(get started|start free|start trial|free trial|try free)\b",
    r"\b(buy now|purchase|order now|checkout|add to cart)\b",
    r"\b(sign up|register|create account|join free|join now)\b",
    r"\b(contact us|talk to sales|book a demo|request demo|schedule call|get in touch)\b",
    r"\b(download|get app|install now)\b",
    r"\b(subscribe|join newsletter)\b",
    r"\b(claim offer|redeem|get quote|request quote|estimate)\b",
)

# Vague / ambiguous action phrases
VAGUE_ACTION_TERMS: tuple[str, ...] = (
    "click here",
    "click",
    "here",
    "learn more",
    "more",
    "read more",
    "submit",
    "go",
    "continue",
    "proceed",
    "view",
    "check",
)



# Normalized CTA Internal Model


@dataclass
class NormalizedCTA:
    """Normalized CTA record ensuring safe typing across variable input shapes."""
    id: str
    text: str
    role: str = "unknown"  # "primary", "secondary", "tertiary", "unknown"
    href: str | None = None
    visible: bool | None = None
    enabled: bool = True
    bbox: BoundingBox | None = None
    in_viewport: bool | None = None
    partially_visible: bool | None = None
    obstructed: bool | None = None
    contrast_ratio: float | None = None
    font_size: float | None = None
    area: float | None = None
    is_primary_inferred: bool = False

    @property
    def clean_text(self) -> str:
        return self.text.strip()

    def is_action_specific(self) -> bool:
        """Check if CTA copy contains explicit, high-intent action phrasing."""
        low = self.clean_text.lower()
        return any(re.search(pat, low) for pat in STRONG_ACTION_PATTERNS)

    def is_vague_label(self) -> bool:
        """Check if CTA copy matches known vague or ambiguous words."""
        low = self.clean_text.lower()
        return low in VAGUE_ACTION_TERMS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "role": self.role,
            "href": self.href,
            "visible": self.visible,
            "enabled": self.enabled,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "in_viewport": self.in_viewport,
            "partially_visible": self.partially_visible,
            "obstructed": self.obstructed,
            "area": self.area,
        }



# Normalization Helper


def normalize_cta_entries(raw_entries: Sequence[Any]) -> list[NormalizedCTA]:
    """
    Safely extract and normalize CTA items from varying dictionary, CTAElement, or string shapes.
    """
    normalized: list[NormalizedCTA] = []

    for idx, item in enumerate(raw_entries):
        if not item:
            continue

        cid = f"cta-{idx + 1}"
        text = ""
        role = "unknown"
        href = None
        visible = None
        enabled = True
        bbox = None
        in_vp = None
        partially_vis = None
        obstructed = None
        contrast = None
        font_sz = None
        area = None

        if isinstance(item, str):
            text = item
        elif isinstance(item, CTAElement):
            text = item.text
            href = item.href
            bbox = item.bounding_box
            visible = item.visible
            role = "primary" if item.is_primary else "unknown"
            if item.above_fold is not None:
                in_vp = item.above_fold
        elif isinstance(item, dict):
            cid = str(item.get("id") or cid)
            text = str(item.get("text") or item.get("label") or item.get("title") or "")
            href = item.get("href") or item.get("url") or item.get("action")
            role = str(item.get("role") or item.get("type") or "unknown").lower()
            if item.get("is_primary"):
                role = "primary"

            if "visible" in item and item["visible"] is not None:
                visible = bool(item["visible"])

            if "enabled" in item and item["enabled"] is not None:
                enabled = bool(item["enabled"])
            elif "disabled" in item:
                enabled = not bool(item["disabled"])

            # Extract bounding box from varying aliases
            raw_box = item.get("bbox") or item.get("bounding_box") or item.get("box") or item.get("coordinates")
            bbox = BoundingBox.from_dict(raw_box)

            if "in_viewport" in item and item["in_viewport"] is not None:
                in_vp = bool(item["in_viewport"])
            elif "above_fold" in item and item["above_fold"] is not None:
                in_vp = bool(item["above_fold"])

            if "partially_visible" in item and item["partially_visible"] is not None:
                partially_vis = bool(item["partially_visible"])

            if "obstructed" in item and item["obstructed"] is not None:
                obstructed = bool(item["obstructed"])

            try:
                if "contrast_ratio" in item and item["contrast_ratio"] is not None:
                    contrast = float(item["contrast_ratio"])
                if "font_size" in item and item["font_size"] is not None:
                    font_sz = float(item["font_size"])
                if "area" in item and item["area"] is not None:
                    area = float(item["area"])
            except (ValueError, TypeError):
                pass

        if bbox:
            area = area or bbox.area

        # Infer primary role if strong action verb present and role was unspecified
        inferred_primary = False
        if role == "unknown" and text:
            if any(re.search(pat, text.strip().lower()) for pat in STRONG_ACTION_PATTERNS):
                inferred_primary = True

        normalized.append(NormalizedCTA(
            id=cid,
            text=text,
            role=role if role != "unknown" else ("primary" if inferred_primary else "unknown"),
            href=href,
            visible=visible,
            enabled=enabled,
            bbox=bbox,
            in_viewport=in_vp,
            partially_visible=partially_vis,
            obstructed=obstructed,
            contrast_ratio=contrast,
            font_size=font_sz,
            area=area,
            is_primary_inferred=inferred_primary,
        ))

    return normalized



# CTA Analyzer Core Logic


class CTAAnalyzer:
    """
    Main evaluation engine for call-to-action effectiveness and usability.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.min_tap_height = float(self.config.get("min_tap_height", DEFAULT_MIN_TAP_HEIGHT))
        self.min_tap_width = float(self.config.get("min_tap_width", DEFAULT_MIN_TAP_WIDTH))
        self.min_tap_area = float(self.config.get("min_tap_area", DEFAULT_MIN_TAP_AREA))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive CTA audit on structured page observations.
        """
        opts = {**self.config, **(options or {})}

        if page_data is None:
            return CategoryResult(
                category="cta",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={"reason": "Missing CTA input data"},
            )

        # Normalize raw input into standard dict structure
        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        vp_dict = raw_dict.get("viewport") or {"width": 1366, "height": 768}
        viewport = Viewport.from_dict(vp_dict)

        # Gather CTA list from multiple possible key aliases
        raw_ctas = (
            raw_dict.get("ctas")
            or raw_dict.get("calls_to_action")
            or raw_dict.get("buttons")
            or []
        )

        logger.info("Starting CTA analysis for %s (%d raw CTA candidates)", target_url, len(raw_ctas))

        if not raw_ctas:
            # Check if this is a commercial, pricing, or product landing page where missing CTA is a critical engagement defect
            page_title = str(getattr(page_data, "title", "") or raw_dict.get("title") or "").lower()
            content_dict = getattr(page_data, "content", {}) if hasattr(page_data, "content") else raw_dict.get("content", {})
            main_text = str((content_dict or {}).get("main_text", "")).lower() if isinstance(content_dict, dict) else ""
            url_lower = target_url.lower()

            has_pricing_path = any(k in url_lower for k in ["/pricing", "/plan", "/checkout", "/signup"])
            has_pricing_content = bool(main_text and ("pricing" in main_text or "/mo" in main_text or "$/" in main_text))
            is_pricing_or_conversion = has_pricing_path or ("pricing" in page_title and "example" not in page_title) or has_pricing_content

            if is_pricing_or_conversion:
                finding = EngagementFinding(
                    id="ENG-CTA-001",
                    category="cta",
                    title="Missing Call-to-Action (CTA) on commercial/pricing page",
                    description=f"The page at '{target_url}' presents commercial pricing or product tiers but contains no actionable Call-to-Action (CTA) buttons or conversion links.",
                    severity="high",
                    confidence=0.95,
                    evidence={"target_url": target_url, "ctas_found": 0, "page_type": "pricing_or_conversion"},
                    recommendation="Add distinct, prominent conversion action buttons (e.g. 'Start Free Trial', 'Choose Plan', 'Contact Sales') to each pricing option.",
                    url=target_url,
                )
                return CategoryResult(
                    category="cta",
                    status="failed",
                    score=40,
                    findings=[finding],
                    metrics={
                        "evaluated": True,
                        "total_ctas": 0,
                        "primary_ctas": 0,
                        "reason": "Commercial page missing Call-to-Action buttons",
                    },
                    confidence=0.95,
                    messages=["Commercial pricing page lacks any actionable CTA buttons."],
                )

            return CategoryResult(
                category="cta",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "ctas_analyzed": 0,
                    "primary_cta": None,
                    "message": "No CTA or button elements provided in audit input",
                },
                confidence=0.5,
                messages=["No CTA evidence supplied for evaluation."],
            )


        ctas = normalize_cta_entries(raw_ctas)
        findings: list[EngagementFinding] = []

        primary_ctas = [c for c in ctas if c.role == "primary"]
        primary_cta = primary_ctas[0] if primary_ctas else (ctas[0] if len(ctas) == 1 else None)

        visible_ctas: list[dict[str, Any]] = []
        below_fold_ctas: list[dict[str, Any]] = []
        obstructed_ctas: list[dict[str, Any]] = []
        vague_ctas: list[dict[str, Any]] = []
        undersized_ctas: list[dict[str, Any]] = []

        # ----------------------------------------------------------------------
        # Check 1: CTA Visibility & Hidden States
        # ----------------------------------------------------------------------
        for c in ctas:
            if c.visible is False:
                if c.role == "primary" or c == primary_cta:
                    findings.append(EngagementFinding(
                        id="ENG-CTA-001",
                        category="cta",
                        title="Primary Call-to-Action is hidden or rendered invisible",
                        description=f"Primary CTA '{c.clean_text or c.id}' has visible=false, preventing users from viewing the primary action.",
                        severity="high",
                        confidence=0.95,
                        evidence={"cta_id": c.id, "cta_text": c.text, "visible": False},
                        recommendation="Ensure the primary CTA is rendered visibly in the DOM without display:none or zero opacity.",
                        url=target_url,
                    ))
            elif c.visible is True or c.visible is None:
                visible_ctas.append(c.to_dict())

        # ----------------------------------------------------------------------
        # Check 2: Above-the-Fold Placement
        # ----------------------------------------------------------------------
        for c in ctas:
            # Check bounding box relative to viewport
            if c.bbox:
                if c.bbox.y >= viewport.height:
                    below_fold_ctas.append(c.to_dict())
                    if c.role == "primary" or c == primary_cta:
                        findings.append(EngagementFinding(
                            id="ENG-CTA-002",
                            category="cta",
                            title="Primary Call-to-Action is positioned below the fold",
                            description=(
                                f"Primary CTA '{c.clean_text}' is located at y={c.bbox.y}px, "
                                f"which is below the {viewport.height}px viewport fold line."
                            ),
                            severity="high",
                            confidence=0.95,
                            evidence={
                                "cta_text": c.text,
                                "cta_y": c.bbox.y,
                                "viewport_height": viewport.height,
                            },
                            recommendation="Elevate the primary action button into the above-the-fold hero section.",
                            url=target_url,
                        ))
                elif c.bbox.y < viewport.height < c.bbox.bottom:
                    # Partially cut off at fold
                    if c.role == "primary" or c == primary_cta:
                        findings.append(EngagementFinding(
                            id="ENG-CTA-003",
                            category="cta",
                            title="Primary Call-to-Action is cut off at the fold line",
                            description=f"Primary CTA '{c.clean_text}' partially intersects the bottom of the viewport at y={c.bbox.y}px.",
                            severity="medium",
                            confidence=0.9,
                            evidence={
                                "cta_text": c.text,
                                "cta_bbox": c.bbox.to_dict(),
                                "viewport": viewport.to_dict(),
                            },
                            recommendation="Adjust hero layout padding so the primary CTA button is fully enclosed above 768px.",
                            url=target_url,
                        ))
            elif c.in_viewport is False:
                below_fold_ctas.append(c.to_dict())
                if c.role == "primary" or c == primary_cta:
                    findings.append(EngagementFinding(
                        id="ENG-CTA-002",
                        category="cta",
                        title="Primary Call-to-Action is not in the initial viewport",
                        description=f"Primary CTA '{c.clean_text}' is not visible within the initial above-the-fold viewport.",
                        severity="high",
                        confidence=0.85,
                        evidence={"cta_text": c.text, "in_viewport": False},
                        recommendation="Position the primary CTA within the initial 1366x768 viewport.",
                        url=target_url,
                    ))

        # ----------------------------------------------------------------------
        # Check 3: CTA Prominence & Visual Differentiation
        # ----------------------------------------------------------------------
        if primary_cta and primary_cta.area and len(ctas) > 1:
            # Check if any secondary CTA is significantly larger than primary CTA
            secondary_ctas = [c for c in ctas if c != primary_cta and c.area]
            for sec in secondary_ctas:
                if sec.area and sec.area > (primary_cta.area * 1.5):
                    findings.append(EngagementFinding(
                        id="ENG-CTA-004",
                        category="cta",
                        title="Primary CTA lacks visual dominance over secondary actions",
                        description=(
                            f"Primary CTA '{primary_cta.clean_text}' (area: {primary_cta.area:.0f}px²) is smaller than "
                            f"secondary action '{sec.clean_text}' (area: {sec.area:.0f}px²), distorting visual hierarchy."
                        ),
                        severity="low",
                        confidence=0.85,
                        evidence={
                            "primary_cta_area": primary_cta.area,
                            "secondary_cta_area": sec.area,
                        },
                        recommendation="Ensure the primary CTA button has the largest visual weight, prominent fill color, and sufficient padding.",
                        url=target_url,
                    ))
                    break

        # ----------------------------------------------------------------------
        # Check 4: CTA Copy Clarity & Action Orientation
        # ----------------------------------------------------------------------
        for c in ctas:
            if not c.clean_text:
                findings.append(EngagementFinding(
                    id="ENG-CTA-006",
                    category="cta",
                    title="Empty or unlabelled Call-to-Action element detected",
                    description=f"CTA button (id: {c.id}) has no visible text label or accessible name.",
                    severity="medium",
                    confidence=0.95,
                    evidence={"cta_id": c.id, "text": ""},
                    recommendation="Provide explicit action text or an aria-label attribute for the CTA button.",
                    url=target_url,
                ))
            elif c.is_vague_label():
                vague_ctas.append(c.to_dict())
                findings.append(EngagementFinding(
                    id="ENG-CTA-005",
                    category="cta",
                    title=f"Vague or ambiguous CTA copy: '{c.clean_text}'",
                    description=(
                        f"The CTA label '{c.clean_text}' is generic and does not communicate what happens upon clicking "
                        "(e.g., whether it downloads a file, registers an account, or initiates a trial)."
                    ),
                    severity="low",
                    confidence=0.9,
                    evidence={"cta_text": c.text, "cta_id": c.id},
                    recommendation="Use an outcome-specific action verb (e.g., 'Download Guide' or 'Start 14-Day Trial' instead of generic copy).",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Check 5: Action Targets (Dead-ends)
        # ----------------------------------------------------------------------
        for c in ctas:
            if c.href in ("#", "", "javascript:void(0)"):
                if c.role == "primary" or c == primary_cta:
                    findings.append(EngagementFinding(
                        id="ENG-CTA-007",
                        category="cta",
                        title="Primary CTA points to an empty or dummy destination ('#')",
                        description=f"Primary CTA '{c.clean_text}' has href='{c.href}', creating an inoperable dead-end for users expecting navigation.",
                        severity="medium",
                        confidence=0.9,
                        evidence={"cta_text": c.text, "href": c.href},
                        recommendation="Attach a valid destination URL or bind a functional JavaScript event handler to the button.",
                        url=target_url,
                    ))

        # ----------------------------------------------------------------------
        # Check 6: CTA Obstruction
        # ----------------------------------------------------------------------
        for c in ctas:
            if c.obstructed is True:
                obstructed_ctas.append(c.to_dict())
                findings.append(EngagementFinding(
                    id="ENG-CTA-008",
                    category="cta",
                    title=f"Call-to-Action '{c.clean_text or c.id}' is obstructed by an overlay",
                    description="The CTA element is physically covered by an overlapping modal, banner, or floating overlay.",
                    severity="high" if (c.role == "primary" or c == primary_cta) else "medium",
                    confidence=0.95,
                    evidence={"cta_id": c.id, "cta_text": c.text, "obstructed": True},
                    recommendation="Adjust z-index layering or dismiss intrusive overlays so conversion CTAs remain unobstructed.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Check 7: Multiple Competing Primary CTAs
        # ----------------------------------------------------------------------
        if len(primary_ctas) >= 3:
            distinct_texts = list({c.clean_text for c in primary_ctas if c.clean_text})
            findings.append(EngagementFinding(
                id="ENG-CTA-009",
                category="cta",
                title=f"Multiple competing primary Call-to-Actions ({len(primary_ctas)} primary CTAs)",
                description=(
                    f"Found {len(primary_ctas)} distinct primary CTAs ({', '.join(distinct_texts[:4])}), "
                    "which creates visual noise and decision friction for the user."
                ),
                severity="low",
                confidence=0.85,
                evidence={"primary_cta_count": len(primary_ctas), "cta_labels": distinct_texts},
                recommendation="Designate a single clear primary conversion action and demote alternative paths to secondary/ghost button styles.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 8: CTA Size / Tap Target Dimensions
        # ----------------------------------------------------------------------
        for c in ctas:
            if c.bbox:
                if c.bbox.height < self.min_tap_height or c.bbox.width < self.min_tap_width:
                    undersized_ctas.append(c.to_dict())
                    findings.append(EngagementFinding(
                        id="ENG-CTA-010",
                        category="cta",
                        title=f"CTA button '{c.clean_text or c.id}' is undersized for touch/mouse interaction",
                        description=(
                            f"CTA dimensions ({c.bbox.width:.0f}x{c.bbox.height:.0f}px) fall below the "
                            f"recommended minimum interactive tap size ({self.min_tap_width:.0f}x{self.min_tap_height:.0f}px)."
                        ),
                        severity="low",
                        confidence=0.9,
                        evidence={
                            "cta_width": c.bbox.width,
                            "cta_height": c.bbox.height,
                            "min_required_width": self.min_tap_width,
                            "min_required_height": self.min_tap_height,
                        },
                        recommendation=f"Increase button padding to achieve at least {self.min_tap_width:.0f}x{self.min_tap_height:.0f}px tap target size.",
                        url=target_url,
                    ))

        # ----------------------------------------------------------------------
        # Check 9: Disabled Primary CTA
        # ----------------------------------------------------------------------
        for c in ctas:
            if c.enabled is False and (c.role == "primary" or c == primary_cta):
                findings.append(EngagementFinding(
                    id="ENG-CTA-011",
                    category="cta",
                    title=f"Primary Call-to-Action '{c.clean_text}' is disabled on page load",
                    description="The primary conversion button is rendered with disabled=true, preventing users from clicking it upon landing.",
                    severity="medium",
                    confidence=0.9,
                    evidence={"cta_id": c.id, "cta_text": c.text, "enabled": False},
                    recommendation="Keep primary conversion buttons enabled or provide inline validation instructions explaining why it is inactive.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Calculate CTA Category Score (0 to 100)
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
            "ctas_analyzed": len(ctas),
            "primary_cta": primary_cta.to_dict() if primary_cta else None,
            "primary_ctas_count": len(primary_ctas),
            "visible_ctas_count": len(visible_ctas),
            "below_fold_ctas_count": len(below_fold_ctas),
            "obstructed_ctas_count": len(obstructed_ctas),
            "vague_ctas_count": len(vague_ctas),
            "undersized_ctas_count": len(undersized_ctas),
        }

        return CategoryResult(
            category="cta",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95 if any(c.bbox for c in ctas) else 0.85,
            messages=[
                f"CTA analysis completed: {len(ctas)} CTAs evaluated (Score: {score_clamped}/100).",
                f"Primary CTA detected: '{primary_cta.clean_text if primary_cta else 'None'}'.",
            ],
        )



# Public API Function


def analyze_ctas(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for CTA analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult instance containing findings, metrics, and score.
    """
    analyzer = CTAAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
