"""
Engagement Audit State & Data Contract (engagement_state.py)
------------------------------------------------------------
Defines the state representation, data schemas, category results, and normalized
findings contracts for the Engagement Audit subagent.

Design Principles:
1. Strong Typing: Uses Python 3.10+ dataclasses, Optional, and Literal types.
2. Zero External Dependencies: Completely decoupled from other subagents (no imports from crawl-render-audit).
3. Serializable: All models convert cleanly to/from standard JSON/dictionaries.
4. Fault-Tolerant: Supports partial audit executions and explicitly models 'insufficient_evidence'.
5. LangGraph & Orchestrator Ready: Pure state container suitable for StateGraph(EngagementAuditState).

This module defines DATA STRUCTURES ONLY. It contains no business analysis logic,
crawling routines, or scoring algorithms.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

# Allowed severity levels for findings
SeverityLevel = Literal["critical", "high", "medium", "low", "info"]

# Allowed category result execution statuses
CategoryStatus = Literal["passed", "failed", "warning", "insufficient_evidence", "error"]

# Core engagement evaluation dimensions
EngagementCategory = Literal[
    "above_fold",
    "cta",
    "navigation",
    "popups",
    "readability",
    "responsiveness",
    "user_journey",
]



# 1. Geometry & Layout Data Models


@dataclass
class Viewport:
    """Represents browser viewport dimensions in pixels."""
    width: float = 1366.0
    height: float = 768.0

    def to_dict(self) -> dict[str, float]:
        return {"width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Viewport:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            width=float(data.get("width", 1366.0)),
            height=float(data.get("height", 768.0)),
        )


@dataclass
class BoundingBox:
    """Represents a 2D bounding rectangle on the rendered page."""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def is_above_fold(self, viewport: Viewport) -> bool:
        """Check if any portion of the bounding box is inside the initial viewport."""
        return self.y < viewport.height and self.x < viewport.width and self.height > 0

    def to_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BoundingBox | None:
        if data is None or not isinstance(data, dict):
            return None
        try:
            return cls(
                x=float(data.get("x", data.get("left", 0.0))),
                y=float(data.get("y", data.get("top", 0.0))),
                width=float(data.get("width", 0.0)),
                height=float(data.get("height", 0.0)),
            )
        except (ValueError, TypeError):
            return None



# 2. Structured Page Evidence Models


@dataclass
class PageElement:
    """Generic representation of a visible or interactive DOM element."""
    tag: str
    text: str = ""
    role: str | None = None
    selector: str | None = None
    bounding_box: BoundingBox | None = None
    visible: bool = True
    enabled: bool = True
    href: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    aria: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.bounding_box:
            res["bounding_box"] = self.bounding_box.to_dict()
        return res


@dataclass
class HeadingElement:
    """Represents an HTML heading tag (h1-h6)."""
    level: int
    text: str = ""
    bounding_box: BoundingBox | None = None
    above_fold: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.bounding_box:
            res["bounding_box"] = self.bounding_box.to_dict()
        return res


@dataclass
class CTAElement:
    """Represents a Call-to-Action button or high-intent action link."""
    text: str
    type: str = "button"  # "button", "link", "input_submit"
    href: str | None = None
    bounding_box: BoundingBox | None = None
    visible: bool = True
    above_fold: bool | None = None
    is_primary: bool = False
    context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.bounding_box:
            res["bounding_box"] = self.bounding_box.to_dict()
        return res


@dataclass
class NavigationItem:
    """Represents a link or node in the website navigation hierarchy."""
    text: str
    href: str | None = None
    depth: int = 1
    children: list[NavigationItem] = field(default_factory=list)
    visible: bool = True
    bounding_box: BoundingBox | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "href": self.href,
            "depth": self.depth,
            "visible": self.visible,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class PopupEvidence:
    """Represents evidence of a modal, dialog, banner, or full-page overlay."""
    name: str | None = None
    type: str = "modal"  # "modal", "dialog", "cookie_banner", "newsletter", "toast"
    is_blocking: bool = False
    immediate: bool = False
    delay_ms: float = 0.0
    has_close_button: bool = True
    bounding_box: BoundingBox | None = None
    affected_content: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        if self.bounding_box:
            res["bounding_box"] = self.bounding_box.to_dict()
        return res


@dataclass
class FormElement:
    """Represents a form or conversion input container."""
    name: str | None = None
    id: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)
    field_count: int = 0
    action: str | None = None
    method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MobileViewportEvidence:
    """Represents responsiveness observations on mobile viewports."""
    viewport: Viewport = field(default_factory=lambda: Viewport(390.0, 844.0))
    horizontal_overflow: bool = False
    scroll_width: float | None = None
    client_width: float | None = None
    small_touch_targets_count: int = 0
    overlapping_elements_count: int = 0
    cta_hidden_on_mobile: bool = False
    overflow_elements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["viewport"] = self.viewport.to_dict()
        return res


@dataclass
class JourneyEvidence:
    """Represents multi-step conversion pathway and interaction flow evidence."""
    starting_page: str | None = None
    destination: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    dead_end_links_count: int = 0
    excessive_fields_count: int = 0
    friction_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



# 3. Input Page Data Container


@dataclass
class PageInputData:
    """
    Standard input container holding structured observations supplied to the Engagement Audit.
    """
    url: str = "https://example.com"
    title: str | None = None
    language: str | None = None
    viewport: Viewport = field(default_factory=Viewport)
    text: str = ""
    headings: list[HeadingElement] = field(default_factory=list)
    ctas: list[CTAElement] = field(default_factory=list)
    buttons: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    navigation: list[NavigationItem] = field(default_factory=list)
    popups: list[PopupEvidence] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    forms: list[FormElement] = field(default_factory=list)
    mobile: MobileViewportEvidence | None = None
    journey: JourneyEvidence | None = None
    raw_html: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "language": self.language,
            "viewport": self.viewport.to_dict(),
            "text": self.text,
            "headings": [h.to_dict() for h in self.headings],
            "ctas": [c.to_dict() for c in self.ctas],
            "buttons": self.buttons,
            "links": self.links,
            "navigation": [n.to_dict() for n in self.navigation],
            "popups": [p.to_dict() for p in self.popups],
            "paragraphs": self.paragraphs,
            "forms": [f.to_dict() for f in self.forms],
            "mobile": self.mobile.to_dict() if self.mobile else None,
            "journey": self.journey.to_dict() if self.journey else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PageInputData:
        if not data or not isinstance(data, dict):
            return cls()

        vp = Viewport.from_dict(data.get("viewport"))

        # Parse headings
        raw_h = data.get("headings", [])
        parsed_headings = []
        for h in raw_h:
            if isinstance(h, dict):
                parsed_headings.append(HeadingElement(
                    level=int(h.get("level", 1)),
                    text=str(h.get("text", "")),
                    bounding_box=BoundingBox.from_dict(h.get("bounding_box") or h.get("box")),
                    above_fold=h.get("above_fold"),
                ))

        # Parse CTAs
        raw_cta = data.get("ctas", [])
        parsed_ctas = []
        for c in raw_cta:
            if isinstance(c, dict):
                parsed_ctas.append(CTAElement(
                    text=str(c.get("text", "")),
                    type=str(c.get("type", "button")),
                    href=c.get("href"),
                    bounding_box=BoundingBox.from_dict(c.get("bounding_box") or c.get("box")),
                    visible=bool(c.get("visible", True)),
                    above_fold=c.get("above_fold"),
                    is_primary=bool(c.get("is_primary", False)),
                    context=c.get("context"),
                ))

        # Parse Popups
        raw_pop = data.get("popups", [])
        parsed_popups = []
        for p in raw_pop:
            if isinstance(p, dict):
                parsed_popups.append(PopupEvidence(
                    name=p.get("name"),
                    type=str(p.get("type", "modal")),
                    is_blocking=bool(p.get("is_blocking", False)),
                    immediate=bool(p.get("immediate", False)),
                    delay_ms=float(p.get("delay_ms", 0.0)),
                    has_close_button=bool(p.get("has_close_button", True)),
                    bounding_box=BoundingBox.from_dict(p.get("bounding_box")),
                    affected_content=list(p.get("affected_content", [])),
                ))

        # Parse Mobile Evidence
        raw_mob = data.get("mobile")
        parsed_mob = None
        if isinstance(raw_mob, dict):
            parsed_mob = MobileViewportEvidence(
                viewport=Viewport.from_dict(raw_mob.get("viewport")) if raw_mob.get("viewport") else Viewport(390.0, 844.0),
                horizontal_overflow=bool(raw_mob.get("horizontal_overflow", False)),
                scroll_width=raw_mob.get("scroll_width"),
                client_width=raw_mob.get("client_width"),
                small_touch_targets_count=int(raw_mob.get("small_touch_targets_count", 0)),
                overlapping_elements_count=int(raw_mob.get("overlapping_elements_count", 0)),
                cta_hidden_on_mobile=bool(raw_mob.get("cta_hidden_on_mobile", False)),
                overflow_elements=list(raw_mob.get("overflow_elements", [])),
            )

        # Parse Forms
        raw_forms = data.get("forms", [])
        parsed_forms = []
        for f in raw_forms:
            if isinstance(f, dict):
                parsed_forms.append(FormElement(
                    name=f.get("name"),
                    id=f.get("id"),
                    fields=list(f.get("fields", [])),
                    field_count=int(f.get("field_count", len(f.get("fields", [])))),
                    action=f.get("action"),
                    method=f.get("method"),
                ))

        # Parse Journey
        raw_jrn = data.get("journey")
        parsed_jrn = None
        if isinstance(raw_jrn, dict):
            parsed_jrn = JourneyEvidence(
                starting_page=raw_jrn.get("starting_page"),
                destination=raw_jrn.get("destination"),
                steps=list(raw_jrn.get("steps", [])),
                dead_end_links_count=int(raw_jrn.get("dead_end_links_count", 0)),
                excessive_fields_count=int(raw_jrn.get("excessive_fields_count", 0)),
                friction_notes=list(raw_jrn.get("friction_notes", [])),
            )

        return cls(
            url=str(data.get("url") or data.get("target_url") or "https://example.com"),
            title=data.get("title"),
            language=data.get("language"),
            viewport=vp,
            text=str(data.get("text") or data.get("content") or ""),
            headings=parsed_headings,
            ctas=parsed_ctas,
            buttons=list(data.get("buttons", [])),
            links=list(data.get("links", [])),
            popups=parsed_popups,
            paragraphs=list(data.get("paragraphs", [])),
            forms=parsed_forms,
            mobile=parsed_mob,
            journey=parsed_jrn,
            raw_html=data.get("raw_html"),
            metadata=dict(data.get("metadata", {})),
        )



# 4. Finding & Category Result Models


@dataclass
class EngagementFinding:
    """
    Standardized, evidence-backed finding generated by an engagement check.
    """
    id: str
    category: str
    title: str
    description: str
    severity: SeverityLevel
    confidence: float  # 0.0 to 1.0
    evidence: dict[str, Any]
    recommendation: str
    url: str

    def __post_init__(self) -> None:
        # Validate confidence clamp
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        # Validate severity
        valid_sevs = ("critical", "high", "medium", "low", "info")
        if self.severity not in valid_sevs:
            self.severity = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngagementFinding:
        return cls(
            id=str(data["id"]),
            category=str(data.get("category", "general")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            severity=data.get("severity", "medium"),
            confidence=float(data.get("confidence", 1.0)),
            evidence=dict(data.get("evidence", {})),
            recommendation=str(data.get("recommendation", "")),
            url=str(data.get("url", "")),
        )


@dataclass
class CategoryResult:
    """
    Result contract produced independently by each of the seven engagement analysis branches.
    """
    category: EngagementCategory
    status: CategoryStatus = "passed"
    score: int | None = None  # 0-100 or None if insufficient evidence
    findings: list[EngagementFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "status": self.status,
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "metrics": self.metrics,
            "confidence": round(self.confidence, 2),
            "messages": self.messages,
        }


@dataclass
class AuditError:
    """Structured error record for component-level failures."""
    component: str
    error_message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditMetadata:
    """Metadata detailing execution parameters and environment."""
    agent_name: str = "engagement-audit"
    agent_version: str = "1.0.0"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    target_viewport: Viewport = field(default_factory=Viewport)
    processing_duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "timestamp": self.timestamp,
            "target_viewport": self.target_viewport.to_dict(),
            "processing_duration_ms": self.processing_duration_ms,
        }



# 5. Top-Level State Container (LangGraph & Orchestrator Ready)


@dataclass
class EngagementAuditState:
    """
    Root State Container for the Engagement Audit Subagent.

    Can be directly used as the state schema for LangGraph StateGraph workflows:
        workflow = StateGraph(EngagementAuditState)
    """
    # 1. Input Data
    input_data: PageInputData = field(default_factory=PageInputData)

    # 2. Independent Branch Execution Results
    above_fold_result: CategoryResult | None = None
    cta_result: CategoryResult | None = None
    navigation_result: CategoryResult | None = None
    popup_result: CategoryResult | None = None
    readability_result: CategoryResult | None = None
    responsive_result: CategoryResult | None = None
    journey_result: CategoryResult | None = None

    # 3. Global Aggregated Findings & Scores
    findings: list[EngagementFinding] = field(default_factory=list)
    category_scores: dict[str, int | None] = field(default_factory=dict)
    overall_score: int | None = None
    summary: str | None = None

    # 4. Error Logging & Diagnostics
    errors: list[AuditError] = field(default_factory=list)
    metadata: AuditMetadata = field(default_factory=AuditMetadata)

    # --------------------------------------------------------------------------
    # Helper & Serialization Methods
    # --------------------------------------------------------------------------

    def add_finding(self, finding: EngagementFinding) -> None:
        """Add a finding to the global state."""
        self.findings.append(finding)

    def add_error(self, component: str, message: str, details: dict[str, Any] | None = None) -> None:
        """Record an error from an analysis branch without crashing."""
        self.errors.append(AuditError(
            component=component,
            error_message=message,
            details=details or {},
        ))

    def get_findings_by_category(self, category: str) -> list[EngagementFinding]:
        """Retrieve findings filtered by category."""
        return [f for f in self.findings if f.category == category]

    def has_critical_findings(self) -> bool:
        """Check if any critical-severity findings were flagged."""
        return any(f.severity == "critical" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        """Convert complete state to a JSON-serializable dictionary."""
        return {
            "skill": "engagement-audit",
            "target_url": self.input_data.url,
            "overall_score": self.overall_score,
            "category_scores": self.category_scores,
            "summary": self.summary,
            "findings_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "category_results": {
                "above_fold": self.above_fold_result.to_dict() if self.above_fold_result else None,
                "cta": self.cta_result.to_dict() if self.cta_result else None,
                "navigation": self.navigation_result.to_dict() if self.navigation_result else None,
                "popups": self.popup_result.to_dict() if self.popup_result else None,
                "readability": self.readability_result.to_dict() if self.readability_result else None,
                "responsiveness": self.responsive_result.to_dict() if self.responsive_result else None,
                "user_journey": self.journey_result.to_dict() if self.journey_result else None,
            },
            "errors": [e.to_dict() for e in self.errors],
            "metadata": self.metadata.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize state directly to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EngagementAuditState:
        """Construct state instance from a raw dictionary."""
        if not data:
            return cls()
        input_data = PageInputData.from_dict(data.get("input_data") or data)
        state = cls(input_data=input_data)
        state.overall_score = data.get("overall_score") or data.get("score")
        state.summary = data.get("summary")
        state.category_scores = dict(data.get("category_scores", {}))

        # Rehydrate findings
        raw_f = data.get("findings", [])
        state.findings = [EngagementFinding.from_dict(f) for f in raw_f if isinstance(f, dict)]

        return state
