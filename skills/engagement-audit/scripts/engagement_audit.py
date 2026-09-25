"""
Engagement Audit Subagent (engagement_audit.py)
-----------------------------------------------
Deterministic user engagement and UX friction analyzer for the
Adobe Website Audit / AI Agent Skills Marketplace project.

This module evaluates how effectively a website engages human visitors and automated
agents, identifying interaction friction across seven core UX dimensions:
1. Above-the-fold experience (at 1366x768 viewport)
2. CTA (Call-to-Action) visibility and effectiveness
3. Navigation structure and information hierarchy
4. Intrusive popups, modals, and obstructive overlays
5. Content readability (including Flesch Reading Ease)
6. Mobile responsiveness and viewport adaptability
7. User journey friction and conversion pathway barriers

Architectural Constraint:
    This subagent is completely decoupled and standalone. It communicates with other
    skills only via structured input dictionaries/dataclasses and produces normalized
    engagement findings and category scores without importing or depending on other subagents.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

# Configure module-level logger
logger = logging.getLogger("engagement_audit")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Standard CTA action verbs and phrases (case-insensitive regex patterns)
COMMON_CTA_PATTERNS: tuple[str, ...] = (
    r"\b(get started|start free|start trial|free trial)\b",
    r"\b(buy now|purchase|order now|checkout)\b",
    r"\b(sign up|register|create account|join now)\b",
    r"\b(contact us|talk to sales|book a demo|request demo|schedule call)\b",
    r"\b(download|get app|install)\b",
    r"\b(subscribe|join newsletter)\b",
    r"\b(learn more|explore|view details|discover)\b",
    r"\b(claim offer|redeem|get quote|request quote)\b",
)

# Standard category weights for overall engagement score calculation
DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    "above_fold": 0.20,
    "cta": 0.20,
    "navigation": 0.15,
    "popups": 0.15,
    "readability": 0.10,
    "responsiveness": 0.10,
    "user_journey": 0.10,
}



# Data Models


@dataclass
class BoundingBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BoundingBox | None:
        if not data or not isinstance(data, dict):
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


@dataclass
class EngagementFinding:
    id: str
    category: str
    title: str
    description: str
    severity: str  # "critical", "high", "medium", "low", "info"
    confidence: float  # 0.0 to 1.0
    evidence: dict[str, Any]
    recommendation: str
    url: str
    is_suggestion: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



# Helper Functions: Readability & Text Analysis


def count_syllables_in_word(word: str) -> int:
    """
    Estimate the syllable count of an English word using standard phonological rules.
    """
    w = word.lower().strip()
    if not w:
        return 0
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 1

    # Single-letter words
    if len(w) <= 3:
        return 1

    # Discard silent 'e' at end of words (e.g., 'make', 'game')
    if w.endswith("e") and not w.endswith("le") and not w.endswith("ee"):
        w = w[:-1]

    # Count vowel sequences
    vowel_runs = re.findall(r"[aeiouy]+", w)
    count = len(vowel_runs)

    # Specific common suffix adjustments
    if w.endswith("ed") and not (w.endswith("ted") or w.endswith("ded")):
        count = max(1, count - 1)
    if w.endswith("es") and not (w.endswith("ses") or w.endswith("zes") or w.endswith("ches") or w.endswith("shes")):
        count = max(1, count - 1)

    return max(1, count)


def calculate_flesch_reading_ease(text: Any) -> dict[str, Any]:
    """
    Calculate the Flesch Reading Ease score and readability metrics.
    Formula: 206.835 - 1.015 * (total_words / total_sentences) - 84.6 * (total_syllables / total_words)
    """
    if not isinstance(text, str) or not text.strip():
        return {
            "score": None,
            "status": "insufficient_text",
            "word_count": 0,
            "sentence_count": 0,
            "syllable_count": 0,
            "avg_sentence_length": 0.0,
            "avg_syllables_per_word": 0.0,
            "reading_level": "Unknown",
        }

    # Extract sentences
    raw_sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = max(1, len(raw_sentences))

    # Extract words
    words = re.findall(r"\b[a-zA-Z0-9'-]+\b", text)
    word_count = len(words)

    if word_count < 10:
        return {
            "score": None,
            "status": "insufficient_text",
            "word_count": word_count,
            "sentence_count": sentence_count,
            "syllable_count": sum(count_syllables_in_word(w) for w in words),
            "avg_sentence_length": round(word_count / sentence_count, 2),
            "avg_syllables_per_word": 0.0,
            "reading_level": "Insufficient Text (under 10 words)",
        }

    total_syllables = sum(count_syllables_in_word(w) for w in words)
    asl = word_count / sentence_count
    asw = total_syllables / word_count

    score = 206.835 - (1.015 * asl) - (84.6 * asw)
    score_clamped = max(0.0, min(100.0, round(score, 1)))

    if score_clamped >= 90:
        level = "Very Easy (5th grade)"
    elif score_clamped >= 80:
        level = "Easy (6th grade)"
    elif score_clamped >= 70:
        level = "Fairly Easy (7th grade)"
    elif score_clamped >= 60:
        level = "Standard (8th-9th grade)"
    elif score_clamped >= 50:
        level = "Fairly Difficult (10th-12th grade)"
    elif score_clamped >= 30:
        level = "Difficult (College)"
    else:
        level = "Very Confusing / Academic (College Graduate)"

    return {
        "score": score_clamped,
        "status": "calculated",
        "word_count": word_count,
        "sentence_count": sentence_count,
        "syllable_count": total_syllables,
        "avg_sentence_length": round(asl, 1),
        "avg_syllables_per_word": round(asw, 2),
        "reading_level": level,
    }



# Main Engagement Audit Class


class EngagementAudit:
    """
    Main controller for the independent Engagement Audit subagent.
    """

    def __init__(self, config: dict[str, Any] | None = None, llm_client: Any = None) -> None:
        self.config = config or {}
        self.llm_client = llm_client or self.config.get("llm_client")
        self.default_viewport = self.config.get("default_viewport", {"width": 1366, "height": 768})
        self.weights = {**DEFAULT_CATEGORY_WEIGHTS, **self.config.get("category_weights", {})}

    def audit(self, page_data: dict[str, Any] | None) -> dict[str, Any]:
        """
        Execute full engagement audit on supplied page data.

        Args:
            page_data: Dictionary of structured page observations.

        Returns:
            Normalized engagement audit report with scores, findings, and metrics.
        """
        data = page_data or {}
        target_url = str(data.get("url") or data.get("target_url") or "https://example.com")
        viewport = data.get("viewport") or self.default_viewport
        vp_w = float(viewport.get("width", 1366))
        vp_h = float(viewport.get("height", 768))

        logger.info("Starting engagement audit for %s (viewport: %dx%d)", target_url, vp_w, vp_h)

        findings: list[EngagementFinding] = []
        category_evaluations: dict[str, dict[str, Any]] = {}

        # 1. Above-the-fold analysis
        above_fold_res, above_fold_findings = self._analyze_above_the_fold(data, target_url, vp_w, vp_h)
        category_evaluations["above_fold"] = above_fold_res
        findings.extend(above_fold_findings)


        # 2. CTA visibility and effectiveness
        cta_res, cta_findings = self._analyze_cta(data, target_url, vp_w, vp_h)
        category_evaluations["cta"] = cta_res
        findings.extend(cta_findings)

        # 3. Navigation and information hierarchy
        nav_res, nav_findings = self._analyze_navigation_hierarchy(data, target_url)
        category_evaluations["navigation"] = nav_res
        findings.extend(nav_findings)

        # 4. Popups and intrusive modals
        popup_res, popup_findings = self._analyze_popups_modals(data, target_url, vp_w, vp_h)
        category_evaluations["popups"] = popup_res
        findings.extend(popup_findings)

        # 5. Readability & text density
        read_res, read_findings = self._analyze_readability(data, target_url)
        category_evaluations["readability"] = read_res
        findings.extend(read_findings)

        # 6. Mobile responsiveness
        mobile_res, mobile_findings = self._analyze_mobile_responsiveness(data, target_url)
        category_evaluations["responsiveness"] = mobile_res
        findings.extend(mobile_findings)

        # 7. User journey friction
        journey_res, journey_findings = self._analyze_user_journey_friction(data, target_url)
        category_evaluations["user_journey"] = journey_res
        findings.extend(journey_findings)

        # 8. Optional Qualitative LLM enrichment (merged & grounded)
        use_llm = bool(data.get("use_llm", self.config.get("use_llm", False)))
        if use_llm:
            llm_findings = self._analyze_with_llm(data, target_url)
            if llm_findings:
                findings.extend(llm_findings)

        # Calculate scores
        scores, overall_score = self._calculate_scores(category_evaluations, findings)


        # Format structured findings output
        findings_dicts = [f.to_dict() for f in findings]
        findings_dicts.sort(
            key=lambda f: (
                {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}.get(f["severity"], 99),
                f["category"],
                f["id"],
            )
        )

        summary_text = (
            f"Overall engagement score: {overall_score}/100. "
            f"Identified {len(findings)} interaction friction points "
            f"({sum(1 for f in findings if f.severity in ('critical', 'high'))} high/critical priority)."
        )

        return {
            "skill": "engagement-audit",
            "component": "engagement_audit",
            "target_url": target_url,
            "score": overall_score,
            "category_scores": scores,
            "category_metrics": category_evaluations,
            "findings_count": len(findings_dicts),
            "findings": findings_dicts,
            "summary": summary_text,
            "metadata": {
                "evaluated_viewport": {"width": vp_w, "height": vp_h},
                "total_checks_evaluated": 7,
            },
        }

    # ==========================================================================
    # Check 1: Above-the-Fold Experience
    # ==========================================================================


    def _analyze_above_the_fold(
        self,
        data: dict[str, Any],
        url: str,
        vp_w: float,
        vp_h: float,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        headings = data.get("headings", [])
        ctas = data.get("ctas", []) or data.get("buttons", [])
        elements = data.get("elements", []) or data.get("above_fold_elements", [])

        # Check if coordinates are available
        has_coords = False
        h1_above_fold = False
        h1_element = None
        for h in headings:
            if isinstance(h, dict) and (h.get("level") == 1 or h.get("tag") == "h1"):
                h1_element = h
                box = BoundingBox.from_dict(h.get("bounding_box") or h.get("box"))
                if box:
                    has_coords = True
                    if box.y < vp_h and box.x < vp_w:
                        h1_above_fold = True
                elif h.get("above_fold") is not None:
                    h1_above_fold = bool(h.get("above_fold"))
                else:
                    h1_above_fold = True  # fallback if headings present in document

        # Evaluate hero CTA visibility
        primary_cta_above_fold = False
        primary_cta = None
        for c in ctas:
            if isinstance(c, dict):
                is_primary = c.get("is_primary") or self._is_primary_cta_text(c.get("text", ""))
                if is_primary and primary_cta is None:
                    primary_cta = c
                    box = BoundingBox.from_dict(c.get("bounding_box") or c.get("box"))
                    if box:
                        has_coords = True
                        if box.y < vp_h and box.x < vp_w and box.height > 0:
                            primary_cta_above_fold = True
                    elif c.get("above_fold") is not None:
                        primary_cta_above_fold = bool(c.get("above_fold"))
                    else:
                        primary_cta_above_fold = True

        # Finding: Main H1 is pushed below the fold
        if h1_element and not h1_above_fold and has_coords:
            box = BoundingBox.from_dict(h1_element.get("bounding_box"))
            findings.append(EngagementFinding(
                id="ENG-FOLD-001",
                category="above_fold",
                title="Primary page heading is pushed below the fold",
                description=f"The primary <h1> heading '{h1_element.get('text', '')}' is positioned at y={box.y if box else 'below fold'}px, requiring user scroll to see page topic.",
                severity="medium",
                confidence=0.9,
                evidence={
                    "heading_text": h1_element.get("text"),
                    "y_position": box.y if box else None,
                    "viewport_height": vp_h,
                },
                recommendation="Position the main value proposition and primary headline within the initial 1366x768 hero area.",
                url=url,
            ))

        # Finding: Primary CTA is missing above the fold
        if primary_cta and not primary_cta_above_fold and has_coords:
            box = BoundingBox.from_dict(primary_cta.get("bounding_box"))
            findings.append(EngagementFinding(
                id="ENG-FOLD-002",
                category="above_fold",
                title="Primary Call-to-Action is not visible above the fold",
                description=f"The primary action button '{primary_cta.get('text', '')}' is located below the 768px fold line.",
                severity="high",
                confidence=0.95,
                evidence={
                    "cta_text": primary_cta.get("text"),
                    "y_position": box.y if box else None,
                    "viewport_height": vp_h,
                },
                recommendation="Elevate the primary action button into the above-the-fold hero section.",
                url=url,
            ))

        metrics = {
            "evaluated": True,
            "has_coordinates": has_coords,
            "h1_above_fold": h1_above_fold if (h1_element or has_coords) else None,
            "primary_cta_above_fold": primary_cta_above_fold if (primary_cta or has_coords) else None,
            "elements_evaluated_count": len(elements),
        }
        return metrics, findings

    # ==========================================================================
    # Check 2: Call-to-Action (CTA) Visibility & Effectiveness
    # ==========================================================================

    def _analyze_cta(
        self,
        data: dict[str, Any],
        url: str,
        vp_w: float,
        vp_h: float,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        raw_ctas = data.get("ctas", []) or data.get("buttons", [])
        links = data.get("links", [])

        extracted_ctas: list[dict[str, Any]] = []
        for item in raw_ctas:
            if isinstance(item, dict):
                extracted_ctas.append(item)
            elif isinstance(item, str):
                extracted_ctas.append({"text": item})

        # Also search links for explicit CTA phrasing if raw_ctas is empty
        if not extracted_ctas and links:
            for l in links:
                if isinstance(l, dict):
                    txt = l.get("text", "")
                    if self._is_primary_cta_text(txt):
                        extracted_ctas.append({**l, "is_link_cta": True})

        if not extracted_ctas:
            # Check if this is a landing/marketing page where CTA is expected
            findings.append(EngagementFinding(
                id="ENG-CTA-001",
                category="cta",
                title="No clear Call-to-Action (CTA) element detected",
                description="The page lacks an identifiable primary conversion action or button (e.g. 'Get Started', 'Contact Us').",
                severity="medium",
                confidence=0.8,
                evidence={"cta_count": 0, "buttons_evaluated": len(raw_ctas)},
                recommendation="Add a clear, prominent primary CTA button guiding the user toward the next step.",
                url=url,
            ))
            return {"evaluated": True, "total_ctas": 0, "primary_ctas": 0}, findings

        primary_ctas = [c for c in extracted_ctas if self._is_primary_cta_text(c.get("text", ""))]
        vague_ctas = [c for c in extracted_ctas if self._is_vague_cta_text(c.get("text", ""))]

        # Finding: Missing primary CTA (only secondary/vague CTAs exist)
        if not primary_ctas and extracted_ctas:
            findings.append(EngagementFinding(
                id="ENG-CTA-001",
                category="cta",
                title="No clear primary Call-to-Action (CTA) element detected",
                description="The page lacks an identifiable primary conversion action or prominent CTA button.",
                severity="medium",
                confidence=0.85,
                evidence={"cta_count": len(extracted_ctas), "primary_ctas": 0},
                recommendation="Add a clear, prominent primary CTA button guiding the user toward the next step.",
                url=url,
            ))

        # Finding: Vague CTA label
        if vague_ctas:
            sample_vague = vague_ctas[0]
            findings.append(EngagementFinding(
                id="ENG-CTA-002",
                category="cta",
                title="Vague or generic Call-to-Action copy detected",
                description=f"CTA button contains generic label '{sample_vague.get('text', '')}', which provides low context regarding what occurs upon clicking.",
                severity="low",
                confidence=0.85,
                evidence={"vague_ctas": [c.get("text") for c in vague_ctas[:3]]},
                recommendation="Use specific, outcome-oriented action verbs (e.g., 'Download Whitepaper' instead of 'Click Here').",
                url=url,
            ))

        # Finding: Competing multiple primary CTAs
        if len(primary_ctas) > 3:
            findings.append(EngagementFinding(
                id="ENG-CTA-003",
                category="cta",
                title="Excessive competing primary Call-to-Actions",
                description=f"Page defines {len(primary_ctas)} simultaneous primary CTAs with differing intent, causing user decision fatigue.",
                severity="low",
                confidence=0.8,
                evidence={"primary_cta_texts": [c.get("text") for c in primary_ctas[:5]]},
                recommendation="Establish a clear visual hierarchy with one primary CTA and demote secondary options to outlined/ghost styles.",
                url=url,
            ))

        metrics = {
            "evaluated": True,
            "total_ctas": len(extracted_ctas),
            "primary_ctas": len(primary_ctas),
            "vague_ctas": len(vague_ctas),
            "ctas_detected": [c.get("text") for c in extracted_ctas[:10]],
        }
        return metrics, findings

    # ==========================================================================
    # Check 3: Navigation and Information Hierarchy
    # ==========================================================================

    def _analyze_navigation_hierarchy(
        self,
        data: dict[str, Any],
        url: str,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        nav_items = data.get("navigation", []) or data.get("nav_links", [])
        headings = data.get("headings", [])

        # Check navigation item count
        if nav_items:
            nav_count = len(nav_items)
            if nav_count > 10:
                findings.append(EngagementFinding(
                    id="ENG-NAV-001",
                    category="navigation",
                    title=f"Cluttered top-level navigation menu ({nav_count} items)",
                    description=f"Primary navigation menu contains {nav_count} items, exceeding the recommended Miller's Law cognitive load limit (7±2 items).",
                    severity="low",
                    confidence=0.9,
                    evidence={"nav_item_count": nav_count, "items": [str(i) for i in nav_items[:12]]},
                    recommendation="Group related navigation destinations into intuitive dropdown categories to simplify choices.",
                    url=url,
                ))

        # Check for empty or generic navigation links
        empty_navs = [n for n in nav_items if isinstance(n, dict) and not str(n.get("text", "")).strip()]
        if empty_navs:
            findings.append(EngagementFinding(
                id="ENG-NAV-002",
                category="navigation",
                title="Empty or unlabelled navigation links detected",
                description=f"Found {len(empty_navs)} navigation element(s) without accessible text labels.",
                severity="medium",
                confidence=0.95,
                evidence={"empty_nav_count": len(empty_navs)},
                recommendation="Ensure all navigation links contain descriptive anchor text or aria-label attributes.",
                url=url,
            ))

        # Check heading structure hierarchy
        h_levels = []
        for h in headings:
            if isinstance(h, dict) and h.get("level"):
                h_levels.append(int(h.get("level")))
            elif isinstance(h, str) and h.startswith("h") and h[1:].isdigit():
                h_levels.append(int(h[1]))

        # Detect skipped heading levels (e.g. h1 directly to h4)
        skipped_hierarchy = False
        for i in range(len(h_levels) - 1):
            if h_levels[i + 1] - h_levels[i] > 1:
                skipped_hierarchy = True
                break

        if skipped_hierarchy:
            findings.append(EngagementFinding(
                id="ENG-NAV-003",
                category="navigation",
                title="Broken heading level hierarchy",
                description="Document skips heading levels (e.g. <h1> directly to <h3> or <h4>), disrupting content structure and scanning for readers.",
                severity="low",
                confidence=0.85,
                evidence={"heading_level_sequence": h_levels[:10]},
                recommendation="Nest headings sequentially (<h1> $\\to$ <h2> $\\to$ <h3>) to maintain semantic visual hierarchy.",
                url=url,
            ))

        metrics = {
            "evaluated": True,
            "nav_item_count": len(nav_items),
            "heading_count": len(headings),
            "skipped_hierarchy": skipped_hierarchy,
        }
        return metrics, findings

    # ==========================================================================
    # Check 4: Popups and Intrusive Modals
    # ==========================================================================

    def _analyze_popups_modals(
        self,
        data: dict[str, Any],
        url: str,
        vp_w: float,
        vp_h: float,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        popups = data.get("popups", []) or data.get("modals", []) or data.get("overlays", [])

        if not popups:
            return {"evaluated": True, "popup_count": 0, "blocking_popups": 0}, findings

        blocking_count = 0
        immediate_count = 0
        no_close_count = 0

        for p in popups:
            if not isinstance(p, dict):
                continue
            is_blocking = bool(p.get("is_blocking") or p.get("backdrop") or p.get("modal"))
            is_immediate = bool(p.get("immediate") or (p.get("delay_ms", 0) < 1000))
            has_close = bool(p.get("has_close_button", True) and p.get("has_close_button") is not False)

            if is_blocking:
                blocking_count += 1
            if is_immediate:
                immediate_count += 1
            if not has_close:
                no_close_count += 1

        # Finding: Immediate full-screen blocking popup
        if immediate_count > 0:
            findings.append(EngagementFinding(
                id="ENG-POP-001",
                category="popups",
                title="Intrusive immediate overlay/modal detected on load",
                description="A modal or popup displays immediately upon page load, obstructing user interaction before they can inspect the page content.",
                severity="medium",
                confidence=0.9,
                evidence={"immediate_popup_count": immediate_count, "total_popups": len(popups)},
                recommendation="Trigger promotional or newsletter overlays based on scroll depth or exit intent rather than immediate page load.",
                url=url,
            ))

        # Finding: Modal without visible dismiss/close button
        if no_close_count > 0:
            findings.append(EngagementFinding(
                id="ENG-POP-002",
                category="popups",
                title="Overlay/modal lacking an obvious dismissal mechanism",
                description="Detected an overlay element without an identifiable close button or dismiss action, trapping user focus.",
                severity="high",
                confidence=0.85,
                evidence={"modals_without_close": no_close_count},
                recommendation="Provide an explicit, easily tappable close button (with aria-label='Close') and support Escape key dismissal.",
                url=url,
            ))

        metrics = {
            "evaluated": True,
            "popup_count": len(popups),
            "blocking_popups": blocking_count,
            "immediate_popups": immediate_count,
            "no_close_popups": no_close_count,
        }
        return metrics, findings

    # ==========================================================================
    # Check 5: Readability & Content Density
    # ==========================================================================

    def _analyze_readability(
        self,
        data: dict[str, Any],
        url: str,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        raw_text = data.get("text") or data.get("main_text") or data.get("body_text") or ""
        if not raw_text and isinstance(data.get("content"), dict):
            raw_content = data["content"]
            raw_text = raw_content.get("main_text") or raw_content.get("text") or ""
            if not raw_text and raw_content.get("paragraphs"):
                raw_text = "\n\n".join(str(p.get("text") if isinstance(p, dict) else p) for p in raw_content["paragraphs"])
        elif isinstance(data.get("content"), str):
            raw_text = data["content"]
        elif not raw_text and data.get("paragraphs"):
            raw_text = "\n\n".join(str(p.get("text") if isinstance(p, dict) else p) for p in data["paragraphs"])

        paragraphs = data.get("paragraphs", [])

        readability_data = calculate_flesch_reading_ease(raw_text)
        score = readability_data.get("score")
        asl = readability_data.get("avg_sentence_length", 0.0)

        # Finding: Extremely difficult readability (Score < 40)
        if score is not None and score < 35.0:
            findings.append(EngagementFinding(
                id="ENG-READ-001",
                category="readability",
                title=f"High reading difficulty (Flesch Score: {score}/100)",
                description=f"Page text requires college-graduate level reading comprehension ({readability_data.get('reading_level')}), with an average of {asl} words per sentence.",
                severity="low",
                confidence=0.9,
                evidence={
                    "flesch_reading_ease": score,
                    "reading_level": readability_data.get("reading_level"),
                    "avg_sentence_length": asl,
                    "avg_syllables_per_word": readability_data.get("avg_syllables_per_word"),
                    "word_count": readability_data.get("word_count"),
                },
                recommendation="Shorten complex sentences, break long paragraphs, and use simpler conversational terminology.",
                url=url,
            ))

        # Check for overly dense 'walls of text'
        long_paragraphs = [p for p in paragraphs if isinstance(p, str) and len(p.split()) > 100]
        if len(long_paragraphs) >= 2:
            findings.append(EngagementFinding(
                id="ENG-READ-002",
                category="readability",
                title=f"Dense walls of text detected ({len(long_paragraphs)} oversized paragraphs)",
                description=f"Found {len(long_paragraphs)} paragraph blocks exceeding 100 words without sub-headings or bullet lists.",
                severity="low",
                confidence=0.85,
                evidence={"long_paragraph_count": len(long_paragraphs)},
                recommendation="Break paragraphs exceeding 3-4 sentences into smaller blocks with visual scannability aids (bullet points, bold highlights).",
                url=url,
            ))

        return readability_data, findings

    # ==========================================================================
    # Check 6: Mobile Responsiveness
    # ==========================================================================

    def _analyze_mobile_responsiveness(
        self,
        data: dict[str, Any],
        url: str,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        mobile = data.get("mobile")

        if not mobile or not isinstance(mobile, dict):
            return {
                "evaluated": False,
                "status": "insufficient_evidence",
                "message": "No mobile viewport data provided in audit input",
            }, findings

        has_overflow = bool(mobile.get("horizontal_overflow") or mobile.get("has_horizontal_scroll"))
        small_touch_targets = mobile.get("small_touch_targets_count", 0)
        hidden_mobile_cta = bool(mobile.get("cta_hidden_on_mobile"))
        overlapping_elements = mobile.get("overlapping_elements_count", 0)

        # Finding: Horizontal scrolling on mobile
        if has_overflow:
            scroll_w = mobile.get("scroll_width")
            client_w = mobile.get("client_width")
            findings.append(EngagementFinding(
                id="ENG-MOB-001",
                category="responsiveness",
                title="Horizontal viewport overflow detected on mobile",
                description=f"Mobile viewport triggers horizontal scrolling (content width {scroll_w}px > viewport {client_w}px), causing broken layouts.",
                severity="high",
                confidence=0.95,
                evidence={
                    "scroll_width": scroll_w,
                    "client_width": client_w,
                    "overflow_elements": mobile.get("overflow_elements", []),
                },
                recommendation="Ensure container max-width is set to 100% and wrap oversized tables, code blocks, or wide images with responsive containers.",
                url=url,
            ))

        # Finding: Small touch targets
        if small_touch_targets > 3:
            findings.append(EngagementFinding(
                id="ENG-MOB-002",
                category="responsiveness",
                title=f"Touch targets too small for mobile fingers ({small_touch_targets} elements)",
                description=f"Found {small_touch_targets} interactive elements under the recommended 44x44px minimum tap size.",
                severity="medium",
                confidence=0.9,
                evidence={"small_touch_targets_count": small_touch_targets},
                recommendation="Increase button padding and touch targets to at least 44x44px with adequate spacing between clickable links.",
                url=url,
            ))

        # Finding: Primary CTA disappears on mobile
        if hidden_mobile_cta:
            findings.append(EngagementFinding(
                id="ENG-MOB-003",
                category="responsiveness",
                title="Primary Call-to-Action is hidden or truncated on mobile viewport",
                description="Primary desktop CTA button is hidden (display:none) or pushed off-screen on mobile viewports.",
                severity="high",
                confidence=0.9,
                evidence={"cta_hidden_on_mobile": True},
                recommendation="Ensure the primary conversion CTA remains accessible on mobile viewports, optionally using a sticky bottom action bar.",
                url=url,
            ))

        metrics = {
            "evaluated": True,
            "horizontal_overflow": has_overflow,
            "small_touch_targets": small_touch_targets,
            "overlapping_elements": overlapping_elements,
            "mobile_viewport": mobile.get("viewport", {"width": 390, "height": 844}),
        }
        return metrics, findings

    # ==========================================================================
    # Check 7: User Journey Friction
    # ==========================================================================

    def _analyze_user_journey_friction(
        self,
        data: dict[str, Any],
        url: str,
    ) -> tuple[dict[str, Any], list[EngagementFinding]]:
        findings: list[EngagementFinding] = []
        journey = data.get("journey") or data.get("conversion_funnel") or {}
        forms = data.get("forms", [])

        # Check forms for excessive fields
        for f in forms:
            if isinstance(f, dict):
                field_count = len(f.get("fields", [])) or f.get("field_count", 0)
                form_name = f.get("name") or f.get("id") or "Lead Form"
                if field_count >= 8:
                    findings.append(EngagementFinding(
                        id="ENG-JRN-001",
                        category="user_journey",
                        title=f"Excessive form friction ({field_count} fields in {form_name})",
                        description=f"Form contains {field_count} input fields, which significantly increases abandonment rates for conversion funnels.",
                        severity="medium",
                        confidence=0.85,
                        evidence={"form_name": form_name, "field_count": field_count},
                        recommendation="Streamline initial conversion forms to 3-4 essential fields, deferring non-critical data collection.",
                        url=url,
                    ))

        # Check for dead-end links or empty anchor targets
        links = data.get("links", [])
        empty_hrefs = [l for l in links if isinstance(l, dict) and l.get("href") in ("#", "", "javascript:void(0)", None)]
        if len(empty_hrefs) >= 3:
            findings.append(EngagementFinding(
                id="ENG-JRN-002",
                category="user_journey",
                title=f"Dead-end anchor links detected ({len(empty_hrefs)} links)",
                description=f"Found {len(empty_hrefs)} links pointing to dummy destinations ('#' or empty hrefs), confusing users expecting navigation.",
                severity="low",
                confidence=0.9,
                evidence={"dead_end_links_count": len(empty_hrefs)},
                recommendation="Replace dummy anchor links with functional destination URLs or proper accessible button elements.",
                url=url,
            ))

        # Check for multi-step journey dropoffs if journey metrics are provided
        steps = journey.get("steps", [])
        if steps and len(steps) > 5:
            findings.append(EngagementFinding(
                id="ENG-JRN-003",
                category="user_journey",
                title=f"Excessive checkout / conversion steps ({len(steps)} steps)",
                description=f"The conversion funnel requires {len(steps)} distinct steps, adding unnecessary user friction.",
                severity="low",
                confidence=0.75,
                evidence={"step_count": len(steps), "steps": [str(s) for s in steps[:8]]},
                recommendation="Consolidate conversion flows into fewer steps with clear progress indicators.",
                url=url,
            ))

        metrics = {
            "evaluated": bool(journey or forms or links),
            "form_count": len(forms),
            "dead_end_links": len(empty_hrefs),
            "journey_steps": len(steps) if steps else 0,
        }
        return metrics, findings

    # ==========================================================================
    # Scoring Model (0 to 100)
    # ==========================================================================

    def _calculate_scores(
        self,
        category_metrics: dict[str, dict[str, Any]],
        findings: list[EngagementFinding],
    ) -> tuple[dict[str, Any], int]:
        """
        Calculate deterministic scores per category (0-100) and weighted overall score.
        """
        severity_penalties = {
            "critical": 35.0,
            "high": 20.0,
            "medium": 10.0,
            "low": 4.0,
            "info": 0.0,
        }

        category_scores: dict[str, Any] = {}
        weighted_sum = 0.0
        total_weight_available = 0.0

        for cat_name, weight in self.weights.items():
            metrics = category_metrics.get(cat_name, {})
            is_evaluated = metrics.get("evaluated", True)

            if not is_evaluated:
                category_scores[cat_name] = {
                    "score": None,
                    "status": "insufficient_evidence",
                    "findings_count": 0,
                }
                continue

            # Start at 100 and apply penalty deductions
            cat_score = 100.0
            cat_findings = [f for f in findings if f.category == cat_name]
            for f in cat_findings:
                penalty = severity_penalties.get(f.severity, 5.0)
                # Weight penalty by confidence
                cat_score -= penalty * f.confidence

            cat_score_clamped = max(0, min(100, int(round(cat_score))))
            category_scores[cat_name] = {
                "score": cat_score_clamped,
                "status": "evaluated",
                "findings_count": len(cat_findings),
            }

            weighted_sum += cat_score_clamped * weight
            total_weight_available += weight

        if total_weight_available > 0:
            overall_score = max(0, min(100, int(round(weighted_sum / total_weight_available))))
        else:
            overall_score = 100

        return category_scores, overall_score

    # ==========================================================================
    # Internal CTA Detection Heuristics
    # ==========================================================================

    @staticmethod
    def _is_primary_cta_text(text: str) -> bool:
        if not text:
            return False
        clean = text.strip().lower()
        return any(re.search(pat, clean) for pat in COMMON_CTA_PATTERNS)

    @staticmethod
    def _is_vague_cta_text(text: str) -> bool:
        if not text:
            return False
        cleaned = text.strip().lower()
        vague_patterns = [
            "click here", "read more", "learn more", "more", "details",
            "here", "continue", "submit", "go", "link"
        ]
        return cleaned in vague_patterns or any(cleaned == p for p in vague_patterns)

    async def audit_async(self, page_data: dict[str, Any] | None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Asynchronously execute full engagement audit on supplied page data with shared LLMClient.
        """
        opts = {**self.config, **(options or {})}
        data = page_data or {}
        target_url = str(data.get("url") or data.get("target_url") or "https://example.com")
        viewport = data.get("viewport") or self.default_viewport
        vp_w = float(viewport.get("width", 1366))
        vp_h = float(viewport.get("height", 768))

        findings: list[EngagementFinding] = []
        category_evaluations: dict[str, dict[str, Any]] = {}

        # 1. Above-the-fold analysis
        above_fold_res, above_fold_findings = self._analyze_above_the_fold(data, target_url, vp_w, vp_h)
        category_evaluations["above_fold"] = above_fold_res
        findings.extend(above_fold_findings)

        # 2. CTA visibility and effectiveness
        cta_res, cta_findings = self._analyze_cta(data, target_url, vp_w, vp_h)
        category_evaluations["cta"] = cta_res
        findings.extend(cta_findings)

        # 3. Navigation and information hierarchy
        nav_res, nav_findings = self._analyze_navigation_hierarchy(data, target_url)
        category_evaluations["navigation"] = nav_res
        findings.extend(nav_findings)

        # 4. Popups and intrusive modals
        popup_res, popup_findings = self._analyze_popups_modals(data, target_url, vp_w, vp_h)
        category_evaluations["popups"] = popup_res
        findings.extend(popup_findings)

        # 5. Readability & text density
        read_res, read_findings = self._analyze_readability(data, target_url)
        category_evaluations["readability"] = read_res
        findings.extend(read_findings)

        # 6. Mobile responsiveness
        mobile_res, mobile_findings = self._analyze_mobile_responsiveness(data, target_url)
        category_evaluations["responsiveness"] = mobile_res
        findings.extend(mobile_findings)

        # 7. User journey friction
        journey_res, journey_findings = self._analyze_user_journey_friction(data, target_url)
        category_evaluations["user_journey"] = journey_res
        findings.extend(journey_findings)

        # 8. Optional Qualitative LLM enrichment
        use_llm = bool(data.get("use_llm", opts.get("use_llm", False)))
        if use_llm:
            llm_client = opts.get("llm_client") or self.llm_client
            llm_findings = await self._analyze_with_llm_async(data, target_url, client=llm_client)
            if llm_findings:
                findings.extend(llm_findings)

        # Calculate scores
        scores, overall_score = self._calculate_scores(category_evaluations, findings)

        findings_dicts = [f.to_dict() for f in findings]
        findings_dicts.sort(
            key=lambda f: (
                {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}.get(f["severity"], 99),
                f["category"],
                f["id"],
            )
        )

        return {
            "skill": "engagement-audit",
            "component": "engagement_audit",
            "target_url": target_url,
            "score": overall_score,
            "category_scores": scores,
            "category_metrics": category_evaluations,
            "findings": findings_dicts,
            "summary": {
                "overall_score": overall_score,
                "total_findings": len(findings),
                "critical_findings": sum(1 for f in findings if f.severity == "critical"),
                "high_findings": sum(1 for f in findings if f.severity == "high"),
                "medium_findings": sum(1 for f in findings if f.severity == "medium"),
                "low_findings": sum(1 for f in findings if f.severity == "low"),
            },
        }

    def _analyze_with_llm(self, data: dict[str, Any], target_url: str) -> list[EngagementFinding]:
        """Perform qualitative, semantic LLM evaluation of user engagement (sync fallback)."""
        client = self.llm_client
        if client is None:
            import os, sys
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            for candidate_root in [
                os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..")),
            ]:
                if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                    sys.path.insert(0, candidate_root)
            try:
                from llm_client import LLMClient
                client = LLMClient()
            except Exception:
                return []

        title = data.get("title", "")
        headings = [h.get("text") if isinstance(h, dict) else str(h) for h in data.get("headings", [])[:10]]
        ctas = [c.get("text") if isinstance(c, dict) else str(c) for c in data.get("ctas", [])[:10]]
        nav = data.get("navigation", {})
        paragraphs = data.get("content", {}).get("paragraphs", [])[:5]
        popups = data.get("popups", [])
        journey = data.get("journey", {})

        prompt = f"""
Audit Target URL: {target_url}
Page Title: {title}
Main Headings: {headings}
Primary CTAs: {ctas}
Navigation Menu Items: {nav}
Sample Copy Paragraphs: {paragraphs}
Active Popups/Modals: {popups}
Conversion Journey Steps: {journey}

Perform a qualitative UX and user engagement review based strictly on the supplied telemetry above.
Critique:
1. Is the value proposition clear and compelling to a visitor?
2. Are the CTAs clear, action-oriented, and positioned without cognitive friction?
3. Is the copy readable, authentic, and appropriately toned?
4. Are navigation and user journeys smooth, or are there barriers or dead ends?

Return JSON with 'findings' array containing objects with:
id (prefixed with 'ENG-'), title, severity ('medium'|'low'), evidence, and suggested_action.
"""
        result = client._generate_qualitative_fallback(prompt)
        return self._parse_llm_findings(result, target_url, ctas)

    async def _analyze_with_llm_async(self, data: dict[str, Any], target_url: str, client: Any = None) -> list[EngagementFinding]:
        """Perform qualitative, semantic LLM evaluation of user engagement (async)."""
        llm_c = client or self.llm_client
        if llm_c is None:
            import os, sys
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            for candidate_root in [
                os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..")),
            ]:
                if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                    sys.path.insert(0, candidate_root)
            try:
                from llm_client import LLMClient
                llm_c = LLMClient()
            except Exception:
                return []

        title = data.get("title", "")
        headings = [h.get("text") if isinstance(h, dict) else str(h) for h in data.get("headings", [])[:10]]
        ctas = [c.get("text") if isinstance(c, dict) else str(c) for c in data.get("ctas", [])[:10]]
        nav = data.get("navigation", {})
        paragraphs = data.get("content", {}).get("paragraphs", [])[:5]
        popups = data.get("popups", [])
        journey = data.get("journey", {})

        prompt = f"""
Audit Target URL: {target_url}
Page Title: {title}
Main Headings: {headings}
Primary CTAs: {ctas}
Navigation Menu Items: {nav}
Sample Copy Paragraphs: {paragraphs}
Active Popups/Modals: {popups}
Conversion Journey Steps: {journey}

Perform a qualitative UX and user engagement review based strictly on the supplied telemetry above.
Critique:
1. Is the value proposition clear and compelling to a visitor?
2. Are the CTAs clear, action-oriented, and positioned without cognitive friction?
3. Is the copy readable, authentic, and appropriately toned?
4. Are navigation and user journeys smooth, or are there barriers or dead ends?

Return JSON with 'findings' array containing objects with:
id (prefixed with 'ENG-'), title, severity ('medium'|'low'), evidence, and suggested_action.
"""
        try:
            result = await llm_c.query_json(prompt)
            return self._parse_llm_findings(result, target_url, ctas)
        except Exception as ex:
            logger.warning("Qualitative LLM engagement audit fallback: %s", ex)
            return []

    def _parse_llm_findings(self, result: dict[str, Any], target_url: str, ctas: list[Any]) -> list[EngagementFinding]:
        llm_findings = []
        raw_findings = (result or {}).get("findings", [])
        for rf in raw_findings:
            fid = rf.get("id", "ENG-UX-001")
            cat = "cta" if "cta" in fid.lower() else ("readability" if "read" in fid.lower() else ("navigation" if "nav" in fid.lower() else "user_journey"))
            is_sugg = bool(rf.get("is_suggestion", False) or rf.get("type") == "suggestion" or "suggestion" in fid.lower())
            recom = rf.get("suggested_action", {}).get("summary", "Review user experience.") if isinstance(rf.get("suggested_action"), dict) else (str(rf.get("suggested_action")) if rf.get("suggested_action") else "Review user experience.")
            sev_hint = str(rf.get("severity", "low")).lower()
            if sev_hint in ("critical", "high"):
                sev_hint = "low"

            # Prevent contradiction: if no CTAs exist in page telemetry, suppress suggestions claiming a CTA was examined
            if not ctas and ("cta" in fid.lower() or "button" in str(rf.get("evidence", "")).lower() or "button" in str(rf.get("title", "")).lower()):
                continue

            llm_findings.append(EngagementFinding(
                id=fid,
                category=cat,
                title=rf.get("title", "UX engagement enhancement opportunity"),
                description=rf.get("evidence", "") or rf.get("description", ""),
                severity=sev_hint if not is_sugg else "low",
                confidence=0.75,
                evidence={"qualitative_critique": rf.get("evidence", "") or rf.get("description", "")},
                recommendation=recom,
                url=target_url,
                is_suggestion=is_sugg,
            ))
        return llm_findings



# CLI Testing Interface & Sample Mock Execution


def _generate_sample_mock_data() -> dict[str, Any]:
    """Generate realistic mock page data for self-contained validation."""
    return {
        "url": "https://example.com/product",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Next-Generation Creative Suite", "bounding_box": {"x": 100, "y": 950, "width": 600, "height": 80}},  # Below fold
            {"level": 3, "text": "Feature Overview", "bounding_box": {"x": 100, "y": 1200, "width": 400, "height": 40}},  # Skipped h2
        ],
        "ctas": [
            {"text": "Get Started", "is_primary": True, "bounding_box": {"x": 100, "y": 1050, "width": 180, "height": 50}},  # Below fold
            {"text": "Click Here", "bounding_box": {"x": 300, "y": 1050, "width": 120, "height": 40}},  # Vague
        ],
        "navigation": [
            {"text": "Home"}, {"text": "Products"}, {"text": "Features"}, {"text": "Pricing"},
            {"text": "Enterprise"}, {"text": "Solutions"}, {"text": "Resources"}, {"text": "Blog"},
            {"text": "Company"}, {"text": "Careers"}, {"text": "Contact"}, {"text": ""},  # 12 items + 1 empty
        ],
        "popups": [
            {"name": "Newsletter Subscribe", "is_blocking": True, "immediate": True, "has_close_button": True}
        ],
        "text": (
            "We offer enterprise-grade computational infrastructure utilizing poly-asynchronous multi-tiered "
            "methodologies to optimize programmatic workflow orchestrations. Our synergistic architectures "
            "facilitate robust high-concurrency micro-service paradigms across distributed multi-tenant environments."
        ),
        "mobile": {
            "viewport": {"width": 390, "height": 844},
            "horizontal_overflow": True,
            "scroll_width": 450,
            "client_width": 390,
            "small_touch_targets_count": 5,
        },
        "forms": [
            {"name": "Registration", "field_count": 9}
        ],
        "links": [
            {"text": "Terms", "href": "#"},
            {"text": "Privacy", "href": "#"},
            {"text": "Security", "href": "#"},
        ],
    }


def main() -> None:
    """CLI runner for direct testing with a JSON input file or self-test sample."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python engagement_audit.py <PAGE_DATA_JSON_PATH | --sample>")
        print("Example: python engagement_audit.py --sample")
        sys.exit(0)

    arg = sys.argv[1]
    if arg == "--sample":
        page_data = _generate_sample_mock_data()
    else:
        try:
            with open(arg, "r", encoding="utf-8") as f:
                page_data = json.load(f)
        except Exception as exc:
            print(json.dumps({"error": f"Failed to load input JSON: {exc}"}, indent=2))
            sys.exit(1)

    auditor = EngagementAudit()
    result = auditor.audit(page_data)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
