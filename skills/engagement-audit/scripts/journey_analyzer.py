"""
User Journey & Funnel Friction Analyzer (journey_analyzer.py)
-------------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates multi-step conversion flows, user progression paths, task completeness,
dead ends, broken state transitions, repetitive form inputs, and error recovery mechanisms
from structured journey evidence.

Key Evaluations:
1. Journey goal clarity and definition
2. Step completeness and expected vs. actual step complexity
3. Unclear next actions and missing progression affordances
4. Dead ends (incomplete non-terminal states with no outgoing pathways)
5. Broken transitions and invalid step target references
6. Unnecessary journey restarts and progress loss
7. Repetitive / duplicate form input fields across steps
8. Error recovery availability and clear recovery pathways
9. Success / confirmation state availability upon goal completion
10. Unavailable / disabled required actions blocking completion
11. Unexpected redirects breaking user progression
12. Structural step consistency and duplicate step ID detection

Architectural Constraint:
    Operates strictly in-memory on structured page evidence. Does not spawn browsers,
    make network calls, or import from crawl-render-audit.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from engagement_state import (
    CategoryResult,
    CategoryStatus,
    EngagementFinding,
    PageInputData,
    SeverityLevel,
)

logger = logging.getLogger("engagement_audit.journey")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default journey friction thresholds
DEFAULT_MAX_REASONABLE_STEPS: int = 7
DEFAULT_MAX_FIELDS_PER_STEP: int = 10



# Normalized Journey Internal Models


@dataclass
class NormalizedJourneyStep:
    """Normalized representation of a single step in a user journey."""
    id: str
    type: str = "step"  # "landing_page", "signup", "form", "checkout", "confirmation", etc.
    label: str = ""
    url: str | None = None
    action: str | None = None
    target: str | None = None
    status: str = "pending"  # "completed", "pending", "failed", "blocked"
    dead_end: bool = False
    restart_required: bool = False
    unavailable_action: bool = False
    error: str | None = None
    recovery_action: str | None = None
    fields: list[str] = field(default_factory=list)
    unexpected_redirect: bool = False
    is_terminal: bool = False
    is_intentional_backtrack: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "url": self.url,
            "action": self.action,
            "target": self.target,
            "status": self.status,
            "dead_end": self.dead_end,
            "restart_required": self.restart_required,
            "unavailable_action": self.unavailable_action,
            "error": self.error,
            "recovery_action": self.recovery_action,
            "fields_count": len(self.fields),
        }


@dataclass
class NormalizedJourney:
    """Normalized multi-step user journey."""
    name: str = "unnamed_journey"
    goal: str | None = None
    steps: list[NormalizedJourneyStep] = field(default_factory=list)
    goal_reached: bool | None = None
    expected_steps: int | None = None
    actual_steps: int | None = None

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "step_count": self.step_count,
            "goal_reached": self.goal_reached,
            "expected_steps": self.expected_steps,
            "actual_steps": self.actual_steps or self.step_count,
            "steps": [s.to_dict() for s in self.steps],
        }



# Normalization Helper


def normalize_journey_evidence(raw_data: dict[str, Any]) -> NormalizedJourney | None:
    """
    Extract and normalize journey records from varying dictionary representations.
    """
    journey_container = None
    for key in ("journey", "journeys", "flow", "funnel", "user_journey"):
        if key in raw_data:
            journey_container = raw_data[key]
            break

    if journey_container is None:
        return None

    # If list of journeys, take the primary / first one
    if isinstance(journey_container, list):
        if not journey_container:
            return NormalizedJourney(name="empty_journey", steps=[])
        journey_dict = journey_container[0] if isinstance(journey_container[0], dict) else {}
    elif isinstance(journey_container, dict):
        journey_dict = journey_container
    else:
        return None

    name = str(journey_dict.get("name") or journey_dict.get("id") or "user_journey")
    goal = journey_dict.get("goal") or journey_dict.get("target_goal")
    if goal:
        goal = str(goal).strip()

    goal_reached = journey_dict.get("goal_reached")
    if goal_reached is not None:
        goal_reached = bool(goal_reached)

    exp_steps = journey_dict.get("expected_steps")
    exp_steps = int(exp_steps) if exp_steps is not None else None

    act_steps = journey_dict.get("actual_steps")
    act_steps = int(act_steps) if act_steps is not None else None

    raw_steps = journey_dict.get("steps") or journey_dict.get("actions") or []
    parsed_steps: list[NormalizedJourneyStep] = []

    if isinstance(raw_steps, list):
        for idx, step_item in enumerate(raw_steps):
            if not step_item or not isinstance(step_item, dict):
                continue

            sid = str(step_item.get("id") or f"step-{idx + 1}")
            stype = str(step_item.get("type") or step_item.get("role") or "step").lower()
            label = str(step_item.get("label") or step_item.get("name") or step_item.get("title") or "")
            url = step_item.get("url") or step_item.get("href")
            action = step_item.get("action") or step_item.get("next_action")
            target = step_item.get("target") or step_item.get("target_step") or step_item.get("next_step")
            status = str(step_item.get("status") or "pending").lower()

            dead_end = bool(step_item.get("dead_end", False))
            restart_req = bool(step_item.get("restart_required", False))
            unavail = bool(step_item.get("unavailable_action") or step_item.get("disabled_action", False))
            err = step_item.get("error")
            recov = step_item.get("recovery_action") or step_item.get("recovery_path")

            # Extract form fields
            raw_fields = step_item.get("fields") or step_item.get("form_fields") or []
            fields_list = [str(f.get("name") or f.get("id") if isinstance(f, dict) else f) for f in raw_fields if f]

            unexp_redir = bool(step_item.get("unexpected_redirect", False))
            is_term = bool(step_item.get("is_terminal") or stype in ("confirmation", "success", "thank_you"))
            is_back = bool(step_item.get("is_intentional_backtrack", False))

            parsed_steps.append(NormalizedJourneyStep(
                id=sid,
                type=stype,
                label=label,
                url=url,
                action=action,
                target=target,
                status=status,
                dead_end=dead_end,
                restart_required=restart_req,
                unavailable_action=unavail,
                error=err,
                recovery_action=recov,
                fields=fields_list,
                unexpected_redirect=unexp_redir,
                is_terminal=is_term,
                is_intentional_backtrack=is_back,
            ))

    return NormalizedJourney(
        name=name,
        goal=goal,
        steps=parsed_steps,
        goal_reached=goal_reached,
        expected_steps=exp_steps,
        actual_steps=act_steps,
    )



# Journey Analyzer Core Logic


class JourneyAnalyzer:
    """
    Main evaluation engine for user journey friction and funnel integrity.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.max_steps = int(self.config.get("max_reasonable_steps", DEFAULT_MAX_REASONABLE_STEPS))
        self.max_fields = int(self.config.get("max_fields_per_step", DEFAULT_MAX_FIELDS_PER_STEP))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive user journey friction analysis.
        """
        opts = {**self.config, **(options or {})}

        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        logger.info("Starting user journey friction analysis for %s", target_url)

        journey = normalize_journey_evidence(raw_dict)

        # ----------------------------------------------------------------------
        # Check: Insufficient Evidence Handling
        # ----------------------------------------------------------------------
        if journey is None or not journey.steps:
            return CategoryResult(
                category="journey",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "journey_name": None,
                    "steps_analyzed": 0,
                    "message": "No structured user journey or conversion flow provided in audit input",
                },
                confidence=0.5,
                messages=["No journey evidence supplied for evaluation."],
            )

        findings: list[EngagementFinding] = []
        steps = journey.steps
        step_ids = [s.id for s in steps]
        step_id_set = set(step_ids)

        # ----------------------------------------------------------------------
        # Check 1: Goal Clarity & Definition
        # ----------------------------------------------------------------------
        if not journey.goal and len(steps) > 1:
            findings.append(EngagementFinding(
                id="ENG-JOURNEY-001",
                category="journey",
                title="User journey has no explicitly defined completion goal",
                description="The journey contains multiple steps but lacks an explicit target goal objective.",
                severity="low",
                confidence=0.8,
                evidence={"journey_name": journey.name, "step_count": len(steps)},
                recommendation="Define an explicit conversion goal (e.g. 'Complete Account Registration' or 'Download Product').",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 2: Journey Length & Excessive Step Count
        # ----------------------------------------------------------------------
        if journey.expected_steps and len(steps) > journey.expected_steps:
            excess_steps = len(steps) - journey.expected_steps
            findings.append(EngagementFinding(
                id="ENG-JOURNEY-002",
                category="journey",
                title=f"Excessive journey steps ({len(steps)} actual vs {journey.expected_steps} expected)",
                description=(
                    f"Journey '{journey.name}' required {len(steps)} steps to reach completion, "
                    f"which exceeds the expected {journey.expected_steps} steps by {excess_steps} extra screen(s)."
                ),
                severity="low",
                confidence=0.9,
                evidence={
                    "actual_steps": len(steps),
                    "expected_steps": journey.expected_steps,
                    "excess_steps": excess_steps,
                },
                recommendation="Consolidate intermediate confirmation or survey screens to shorten the critical path.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 12: Duplicate Step IDs
        # ----------------------------------------------------------------------
        seen_ids: set[str] = set()
        dup_ids: list[str] = []
        for sid in step_ids:
            if sid in seen_ids:
                dup_ids.append(sid)
            seen_ids.add(sid)

        if dup_ids:
            findings.append(EngagementFinding(
                id="ENG-JOURNEY-012",
                category="journey",
                title=f"Duplicate step identifiers in journey flow ({len(dup_ids)} duplicates)",
                description=f"Journey definition contains duplicate step IDs ({', '.join(dup_ids[:3])}), which creates state ambiguity.",
                severity="low",
                confidence=0.95,
                evidence={"duplicate_step_ids": dup_ids},
                recommendation="Ensure every step in the journey has a unique deterministic identifier.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Per-Step Evaluations
        # ----------------------------------------------------------------------
        for idx, step in enumerate(steps):
            is_last_step = (idx == len(steps) - 1)

            # ------------------------------------------------------------------
            # Check 3 & 4: Unclear Next Action & Dead Ends
            # ------------------------------------------------------------------
            if step.dead_end:
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-004",
                    category="journey",
                    title=f"Dead end detected at step '{step.id}' ({step.label or step.type})",
                    description=(
                        f"Step '{step.id}' is marked as a dead end with no forward progression "
                        f"pathway before the goal ('{journey.goal or 'complete'}') was achieved."
                    ),
                    severity="high",
                    confidence=0.95,
                    evidence={"step_id": step.id, "dead_end": True, "goal_reached": journey.goal_reached},
                    recommendation="Provide a clear forward call-to-action or fallback navigation link from this step.",
                    url=target_url,
                ))
            elif not is_last_step and not step.is_terminal:
                # Missing action / target on non-terminal intermediate step
                if not step.action and not step.target:
                    findings.append(EngagementFinding(
                        id="ENG-JOURNEY-003",
                        category="journey",
                        title=f"Unclear next action at step '{step.id}'",
                        description=f"Step '{step.id}' has no defined next action or target step, leaving the user with no clear path forward.",
                        severity="medium",
                        confidence=0.9,
                        evidence={"step_id": step.id, "has_action": bool(step.action), "target": step.target},
                        recommendation="Attach a prominent next-step action button directing users to the subsequent step.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 5: Broken Transitions (Invalid Target)
            # ------------------------------------------------------------------
            if step.target and step.target not in step_id_set:
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-005",
                    category="journey",
                    title=f"Broken transition from step '{step.id}' to non-existent target '{step.target}'",
                    description=f"Action at step '{step.id}' references target '{step.target}', which does not exist in the journey tree.",
                    severity="high",
                    confidence=0.95,
                    evidence={"step_id": step.id, "invalid_target": step.target},
                    recommendation="Update the transition target to resolve to an existing valid journey step.",
                    url=target_url,
                ))
            elif step.status in ("failed", "broken"):
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-005",
                    category="journey",
                    title=f"Failed transition at step '{step.id}'",
                    description=f"Transition execution failed at step '{step.id}' (status='{step.status}'), disrupting user progression.",
                    severity="critical" if is_last_step or journey.goal_reached is False else "high",
                    confidence=0.95,
                    evidence={"step_id": step.id, "status": step.status},
                    recommendation="Fix backend endpoint errors and ensure seamless page transitions.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 6: Unnecessary Restarts / Progress Loss
            # ------------------------------------------------------------------
            if step.restart_required:
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-006",
                    category="journey",
                    title=f"Forced journey restart at step '{step.id}'",
                    description=(
                        f"Step '{step.id}' requires the user to restart the journey from the beginning, "
                        "causing complete loss of prior input and severe user frustration."
                    ),
                    severity="high",
                    confidence=0.95,
                    evidence={"step_id": step.id, "restart_required": True},
                    recommendation="Preserve user session state and form draft inputs across unexpected errors or step resets.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 8: Error Recovery
            # ------------------------------------------------------------------
            if step.error:
                if not step.recovery_action:
                    findings.append(EngagementFinding(
                        id="ENG-JOURNEY-008",
                        category="journey",
                        title=f"Unrecoverable error at step '{step.id}'",
                        description=f"Step '{step.id}' encountered error '{step.error}' with no actionable recovery pathway or retry mechanism.",
                        severity="high",
                        confidence=0.95,
                        evidence={"step_id": step.id, "error": step.error, "has_recovery": False},
                        recommendation="Provide an inline retry button or specific troubleshooting instructions.",
                        url=target_url,
                    ))

            # ------------------------------------------------------------------
            # Check 10: Unavailable / Disabled Required Actions
            # ------------------------------------------------------------------
            if step.unavailable_action:
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-010",
                    category="journey",
                    title=f"Required action unavailable or disabled at step '{step.id}'",
                    description=f"Primary action required to proceed from step '{step.id}' is unavailable or rendered inoperable.",
                    severity="high",
                    confidence=0.95,
                    evidence={"step_id": step.id, "unavailable_action": True},
                    recommendation="Ensure required conversion buttons remain enabled or clearly explain pending validation criteria.",
                    url=target_url,
                ))

            # ------------------------------------------------------------------
            # Check 11: Unexpected Redirects
            # ------------------------------------------------------------------
            if step.unexpected_redirect:
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-011",
                    category="journey",
                    title=f"Unexpected redirect at step '{step.id}'",
                    description=f"User was redirected away from the intended flow path at step '{step.id}'.",
                    severity="medium",
                    confidence=0.9,
                    evidence={"step_id": step.id, "unexpected_redirect": True},
                    recommendation="Prevent unannounced off-path redirects and maintain user context within the funnel.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Check 7: Repetitive / Duplicate Form Input Fields Across Steps
        # ----------------------------------------------------------------------
        all_field_occurrences: dict[str, list[str]] = {}
        for s in steps:
            for f in s.fields:
                f_norm = f.strip().lower()
                all_field_occurrences.setdefault(f_norm, []).append(s.id)

        repeated_fields = {k: v for k, v in all_field_occurrences.items() if len(v) > 1}
        if repeated_fields:
            findings.append(EngagementFinding(
                id="ENG-JOURNEY-007",
                category="journey",
                title=f"Repetitive form inputs requested across steps ({len(repeated_fields)} duplicate fields)",
                description=(
                    f"Fields ({', '.join(list(repeated_fields.keys())[:3])}) were requested multiple times "
                    f"across separate steps in the journey."
                ),
                severity="low",
                confidence=0.9,
                evidence={"repeated_fields": repeated_fields},
                recommendation="Autofill or carry forward previously entered values across multi-step forms.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 9: Missing Success / Confirmation State
        # ----------------------------------------------------------------------
        if len(steps) >= 2 and journey.goal_reached is False:
            last_step = steps[-1]
            if not last_step.is_terminal and last_step.status != "completed":
                findings.append(EngagementFinding(
                    id="ENG-JOURNEY-009",
                    category="journey",
                    title="Journey terminated without confirmation or goal completion",
                    description=f"The journey ended at step '{last_step.id}' without reaching the confirmation state (goal_reached=false).",
                    severity="medium",
                    confidence=0.9,
                    evidence={"last_step": last_step.id, "goal_reached": False},
                    recommendation="Provide an explicit success/confirmation page with next steps and transaction receipt details.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Deterministic Scoring Calculation (0 to 100)
        # ----------------------------------------------------------------------
        score = 100.0
        for f in findings:
            if f.severity == "critical":
                score -= 40.0 * f.confidence
            elif f.severity == "high":
                score -= 20.0 * f.confidence
            elif f.severity == "medium":
                score -= 10.0 * f.confidence
            elif f.severity == "low":
                score -= 5.0 * f.confidence

        score_clamped = max(0, min(100, int(round(score))))

        status: CategoryStatus = "passed"
        if any(f.severity in ("critical", "high") for f in findings):
            status = "failed"
        elif findings:
            status = "warning"

        metrics = {
            "journey_name": journey.name,
            "goal": journey.goal,
            "steps_analyzed": len(steps),
            "goal_reached": journey.goal_reached,
            "expected_steps": journey.expected_steps,
            "friction_count": len(findings),
            "critical_issues_count": len([f for f in findings if f.severity in ("critical", "high")]),
            "dead_ends_count": len([f for f in findings if f.id == "ENG-JOURNEY-004"]),
            "broken_transitions_count": len([f for f in findings if f.id == "ENG-JOURNEY-005"]),
        }

        return CategoryResult(
            category="journey",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95,
            messages=[
                f"Journey analysis completed: {len(steps)} steps evaluated for '{journey.name}' (Score: {score_clamped}/100).",
                f"Friction points identified: {len(findings)}.",
            ],
        )



# Public API Function


def analyze_journey(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for User Journey & Friction analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult containing status, score, metrics, and journey friction findings.
    """
    analyzer = JourneyAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
