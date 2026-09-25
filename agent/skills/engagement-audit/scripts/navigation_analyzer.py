"""
Navigation & Information Hierarchy Analyzer (navigation_analyzer.py)
--------------------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates website navigation architecture, menu clarity, information
hierarchy depth, redundant links, active item states, breadcrumbs, and layout discoverability
from structured page observations.

Key Evaluations:
1. Primary navigation presence & discoverability
2. Navigation visibility & obstruction
3. Label clarity, empty text labels, and ambiguous symbols
4. Information hierarchy & parent-child nesting structure
5. Excessive navigation depth (configurable threshold, default max depth = 4)
6. Navigation complexity & cognitive load (Miller's law 7±2 items)
7. Duplicate navigation labels and redundant destinations within the same group
8. Primary vs. secondary vs. footer navigation distinction
9. Active / current section state consistency
10. Breadcrumb hierarchy validation (when breadcrumb evidence is supplied)
11. Mobile navigation availability signals (when mobile evidence is supplied)

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
    NavigationItem,
    PageInputData,
    SeverityLevel,
    Viewport,
)

logger = logging.getLogger("engagement_audit.navigation")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default analysis thresholds
DEFAULT_MAX_NAV_DEPTH: int = 4
DEFAULT_MAX_PRIMARY_ITEMS: int = 10
DEFAULT_MAX_CHILDREN_PER_PARENT: int = 15



# Normalized Navigation Internal Models


@dataclass
class NormalizedNavItem:
    """Normalized navigation item representation."""
    id: str
    label: str
    href: str | None = None
    role: str = "primary"  # "primary", "secondary", "utility", "footer", "unknown"
    visible: bool | None = None
    active: bool | None = None
    enabled: bool = True
    parent_id: str | None = None
    depth: int = 1
    position: int | None = None
    group: str = "primary"
    bbox: BoundingBox | None = None
    obstructed: bool | None = None
    children: list[NormalizedNavItem] = field(default_factory=list)

    @property
    def clean_label(self) -> str:
        return self.label.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "href": self.href,
            "role": self.role,
            "visible": self.visible,
            "active": self.active,
            "enabled": self.enabled,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "position": self.position,
            "group": self.group,
            "obstructed": self.obstructed,
            "children_count": len(self.children),
        }



# Normalization Helpers


def _parse_single_nav_item(item: Any, idx: int, default_group: str = "primary", depth: int = 1) -> NormalizedNavItem | None:
    """Safely parse a single item from varying dictionary, NavigationItem, or string shapes."""
    if not item:
        return None

    if isinstance(item, str):
        return NormalizedNavItem(
            id=f"nav-{default_group}-{idx + 1}",
            label=item,
            group=default_group,
            depth=depth,
        )

    if isinstance(item, NavigationItem):
        child_items = [
            _parse_single_nav_item(c, c_idx, default_group, depth + 1)
            for c_idx, c in enumerate(item.children)
        ]
        return NormalizedNavItem(
            id=f"nav-{default_group}-{idx + 1}",
            label=item.text,
            href=item.href,
            visible=item.visible,
            depth=item.depth or depth,
            bbox=item.bounding_box,
            children=[c for c in child_items if c is not None],
            group=default_group,
        )

    if isinstance(item, dict):
        nid = str(item.get("id") or f"nav-{default_group}-{idx + 1}")
        label = str(item.get("label") or item.get("text") or item.get("title") or item.get("name") or "")
        href = item.get("href") or item.get("url")
        role = str(item.get("role") or default_group).lower()
        group = str(item.get("group") or role or default_group).lower()

        vis = None
        if "visible" in item and item["visible"] is not None:
            vis = bool(item["visible"])

        act = None
        if "active" in item and item["active"] is not None:
            act = bool(item["active"])
        elif "current" in item and item["current"] is not None:
            act = bool(item["current"])

        enab = True
        if "enabled" in item and item["enabled"] is not None:
            enab = bool(item["enabled"])
        elif "disabled" in item and item["disabled"] is not None:
            enab = not bool(item["disabled"])

        parent_id = item.get("parent_id")
        item_depth = int(item.get("depth", depth))
        pos = item.get("position")

        raw_box = item.get("bbox") or item.get("bounding_box") or item.get("box")
        bbox = BoundingBox.from_dict(raw_box)

        obstructed = None
        if "obstructed" in item and item["obstructed"] is not None:
            obstructed = bool(item["obstructed"])

        raw_children = item.get("children") or item.get("sub_items") or []
        children: list[NormalizedNavItem] = []
        if isinstance(raw_children, list):
            for c_idx, c in enumerate(raw_children):
                parsed_c = _parse_single_nav_item(c, c_idx, default_group=group, depth=item_depth + 1)
                if parsed_c:
                    children.append(parsed_c)

        return NormalizedNavItem(
            id=nid,
            label=label,
            href=href,
            role=role,
            visible=vis,
            active=act,
            enabled=enab,
            parent_id=parent_id,
            depth=item_depth,
            position=pos,
            group=group,
            bbox=bbox,
            obstructed=obstructed,
            children=children,
        )

    return None


def normalize_navigation_evidence(raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract, normalize, and organize navigation groups and breadcrumbs from page input.
    """
    nav_container = None
    for key in ("navigation", "nav", "primary_navigation", "menus"):
        if key in raw_data:
            nav_container = raw_data[key]
            break

    result: dict[str, Any] = {
        "has_navigation_data": False,
        "items": [],
        "groups": {},
        "breadcrumbs": [],
        "mobile_nav": raw_data.get("mobile_navigation") or raw_data.get("mobile_nav") or {},
    }

    if nav_container is None:
        return result

    result["has_navigation_data"] = True

    # Case A: nav_container is a list of items
    if isinstance(nav_container, list):
        parsed_items: list[NormalizedNavItem] = []
        for idx, item in enumerate(nav_container):
            p = _parse_single_nav_item(item, idx, default_group="primary")
            if p:
                parsed_items.append(p)
        result["items"] = parsed_items
        result["groups"]["primary"] = parsed_items

    # Case B: nav_container is a dictionary of groups (primary, secondary, footer, breadcrumbs)
    elif isinstance(nav_container, dict):
        all_items: list[NormalizedNavItem] = []

        # Extract breadcrumbs
        raw_bc = nav_container.get("breadcrumbs") or raw_data.get("breadcrumbs") or []
        if isinstance(raw_bc, list):
            result["breadcrumbs"] = [str(b.get("label") or b.get("text") if isinstance(b, dict) else b) for b in raw_bc]

        # Extract categorized groups
        for group_name, group_val in nav_container.items():
            if group_name == "breadcrumbs":
                continue
            if isinstance(group_val, list):
                group_items: list[NormalizedNavItem] = []
                for idx, item in enumerate(group_val):
                    p = _parse_single_nav_item(item, idx, default_group=group_name)
                    if p:
                        group_items.append(p)
                        all_items.append(p)
                result["groups"][group_name] = group_items

        result["items"] = all_items

    return result



# Navigation Analyzer Core Logic


class NavigationAnalyzer:
    """
    Main evaluation engine for website navigation and information hierarchy.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.max_depth = int(self.config.get("max_nav_depth", DEFAULT_MAX_NAV_DEPTH))
        self.max_primary_items = int(self.config.get("max_primary_items", DEFAULT_MAX_PRIMARY_ITEMS))
        self.max_children = int(self.config.get("max_children_per_parent", DEFAULT_MAX_CHILDREN_PER_PARENT))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive navigation and hierarchy analysis.
        """
        opts = {**self.config, **(options or {})}

        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        logger.info("Starting navigation analysis for %s", target_url)

        normalized_nav = normalize_navigation_evidence(raw_dict)

        # ----------------------------------------------------------------------
        # Check 1: Navigation Presence & Insufficient Evidence Handling
        # ----------------------------------------------------------------------
        if not normalized_nav["has_navigation_data"] and not raw_dict.get("nav_links"):
            return CategoryResult(
                category="navigation",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "navigation_present": None,
                    "items_analyzed": 0,
                    "message": "No navigation or menu structure provided in audit input",
                },
                confidence=0.5,
                messages=["No navigation evidence supplied for evaluation."],
            )

        all_items: list[NormalizedNavItem] = normalized_nav["items"]
        primary_items: list[NormalizedNavItem] = normalized_nav["groups"].get("primary", all_items)
        breadcrumbs: list[str] = normalized_nav["breadcrumbs"]
        mobile_evidence: dict[str, Any] = normalized_nav["mobile_nav"]

        if not all_items:
            # Explicitly empty navigation
            return CategoryResult(
                category="navigation",
                status="warning",
                score=70,
                findings=[
                    EngagementFinding(
                        id="ENG-NAV-001",
                        category="navigation",
                        title="Explicitly empty navigation container detected",
                        description="Navigation evidence indicates that zero menu items are present in the navigation container.",
                        severity="medium",
                        confidence=0.9,
                        evidence={"items_count": 0},
                        recommendation="Include essential primary navigation links to key website sections (e.g. Products, Pricing, About).",
                        url=target_url,
                    )
                ],
                metrics={"navigation_present": False, "items_analyzed": 0},
                confidence=0.9,
                messages=["Navigation container exists but contains zero items."],
            )

        findings: list[EngagementFinding] = []

        # Flatten all items including nested children for depth and label checks
        flattened_items: list[NormalizedNavItem] = []
        def _collect_flattened(items: list[NormalizedNavItem]) -> None:
            for item in items:
                flattened_items.append(item)
                if item.children:
                    _collect_flattened(item.children)
        _collect_flattened(all_items)

        # ----------------------------------------------------------------------
        # Check 2: Navigation Visibility & Hidden States
        # ----------------------------------------------------------------------
        for item in primary_items:
            if item.visible is False:
                findings.append(EngagementFinding(
                    id="ENG-NAV-001",
                    category="navigation",
                    title=f"Primary navigation item '{item.clean_label or item.id}' is hidden",
                    description=f"Primary navigation element '{item.clean_label}' has visible=false, hiding standard navigation paths.",
                    severity="high",
                    confidence=0.95,
                    evidence={"item_id": item.id, "label": item.label, "visible": False},
                    recommendation="Ensure main header navigation links are visible on initial page load.",
                    url=target_url,
                ))
            if item.obstructed is True:
                findings.append(EngagementFinding(
                    id="ENG-NAV-002",
                    category="navigation",
                    title=f"Navigation item '{item.clean_label or item.id}' is obstructed by an overlay",
                    description="Navigation bar element is physically covered by a floating banner, popup backdrop, or modal overlay.",
                    severity="high",
                    confidence=0.95,
                    evidence={"item_id": item.id, "label": item.label, "obstructed": True},
                    recommendation="Ensure floating overlays and cookie banners do not obstruct the top navigation bar.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Check 3: Label Clarity & Empty Labels
        # ----------------------------------------------------------------------
        empty_label_items: list[str] = []
        ambiguous_symbol_items: list[str] = []
        for item in flattened_items:
            lbl = item.clean_label
            if not lbl:
                empty_label_items.append(item.id)
            elif lbl in (">", "...", "#", ">>", "->", "icon", "menu_icon"):
                ambiguous_symbol_items.append(lbl)

        if empty_label_items:
            findings.append(EngagementFinding(
                id="ENG-NAV-003",
                category="navigation",
                title=f"Empty or unlabelled navigation links ({len(empty_label_items)} items)",
                description=f"Found {len(empty_label_items)} navigation element(s) without text labels or accessible anchor names.",
                severity="medium",
                confidence=0.95,
                evidence={"empty_nav_ids": empty_label_items[:5]},
                recommendation="Provide clear text labels or aria-label attributes for all navigation links.",
                url=target_url,
            ))

        if ambiguous_symbol_items:
            findings.append(EngagementFinding(
                id="ENG-NAV-004",
                category="navigation",
                title=f"Ambiguous or symbol-only navigation labels ({len(ambiguous_symbol_items)} items)",
                description=f"Navigation contains symbol-only links ({', '.join(ambiguous_symbol_items[:3])}) without semantic meaning.",
                severity="low",
                confidence=0.9,
                evidence={"ambiguous_labels": ambiguous_symbol_items[:5]},
                recommendation="Replace bare symbol characters with descriptive textual labels or screen-reader accessible text.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 4 & 5: Information Hierarchy & Navigation Depth
        # ----------------------------------------------------------------------
        max_observed_depth = max((i.depth for i in flattened_items), default=1)
        total_depth = sum(i.depth for i in flattened_items)
        avg_depth = round(total_depth / max(1, len(flattened_items)), 2)

        if max_observed_depth > self.max_depth:
            findings.append(EngagementFinding(
                id="ENG-NAV-008",
                category="navigation",
                title=f"Excessive navigation hierarchy depth ({max_observed_depth} levels)",
                description=(
                    f"Navigation structure reaches {max_observed_depth} nested levels (exceeding maximum recommended "
                    f"depth of {self.max_depth}), burying content and creating discoverability friction."
                ),
                severity="medium",
                confidence=0.9,
                evidence={
                    "max_depth": max_observed_depth,
                    "configured_max_depth": self.max_depth,
                    "average_depth": avg_depth,
                },
                recommendation="Flatten navigation architecture to a maximum of 3-4 levels and utilize mega-menus or landing hub pages.",
                url=target_url,
            ))

        # Check for broken parent-child references
        all_ids = {i.id for i in flattened_items}
        orphaned_items = [i for i in flattened_items if i.parent_id and i.parent_id not in all_ids]
        if orphaned_items:
            findings.append(EngagementFinding(
                id="ENG-NAV-006",
                category="navigation",
                title=f"Orphaned navigation items with broken parent links ({len(orphaned_items)} items)",
                description=f"Found {len(orphaned_items)} submenu item(s) referencing parent_ids that do not exist in the navigation tree.",
                severity="medium",
                confidence=0.95,
                evidence={"orphaned_ids": [o.id for o in orphaned_items[:4]]},
                recommendation="Ensure all submenu child items resolve to valid, existing parent navigation categories.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 6: Navigation Complexity in Primary Menu
        # ----------------------------------------------------------------------
        if len(primary_items) > self.max_primary_items:
            findings.append(EngagementFinding(
                id="ENG-NAV-009",
                category="navigation",
                title=f"Cluttered top-level primary navigation ({len(primary_items)} items)",
                description=(
                    f"Primary navigation contains {len(primary_items)} top-level items, exceeding the "
                    f"recommended cognitive load limit of {self.max_primary_items} items (Miller's law 7±2)."
                ),
                severity="low",
                confidence=0.9,
                evidence={
                    "primary_items_count": len(primary_items),
                    "labels": [i.clean_label for i in primary_items[:12]],
                },
                recommendation="Consolidate top-level menu items into 5-7 intuitive categories with structured dropdown menus.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 7: Duplicate Labels & Redundant Destinations
        # ----------------------------------------------------------------------
        # Detect duplicate labels within the same group & depth
        label_seen: dict[tuple[str, int, str], int] = {}
        for item in flattened_items:
            if item.clean_label:
                key = (item.group, item.depth, item.clean_label.lower())
                label_seen[key] = label_seen.get(key, 0) + 1

        dup_labels = [k[2] for k, count in label_seen.items() if count > 1]
        if dup_labels:
            findings.append(EngagementFinding(
                id="ENG-NAV-005",
                category="navigation",
                title=f"Duplicate navigation labels detected ({len(dup_labels)} duplicates)",
                description=f"Found duplicate navigation labels ({', '.join(dup_labels[:3])}) within the same menu level, causing user confusion.",
                severity="low",
                confidence=0.9,
                evidence={"duplicate_labels": dup_labels[:5]},
                recommendation="Deduplicate menu items or provide distinguishing subtitles/descriptors.",
                url=target_url,
            ))

        # Detect redundant links with identical href and label in same group
        dest_seen: dict[tuple[str, str], int] = {}
        for item in primary_items:
            if item.href and item.href not in ("/", "#", ""):
                key = (item.group, item.href.lower().rstrip("/"))
                dest_seen[key] = dest_seen.get(key, 0) + 1

        dup_dests = [k[1] for k, count in dest_seen.items() if count > 1]
        if dup_dests:
            findings.append(EngagementFinding(
                id="ENG-NAV-010",
                category="navigation",
                title=f"Redundant destination links in primary menu ({len(dup_dests)} destinations)",
                description=f"Multiple top-level navigation links point to the identical URL ({', '.join(dup_dests[:3])}).",
                severity="low",
                confidence=0.85,
                evidence={"redundant_urls": dup_dests[:4]},
                recommendation="Ensure each primary menu item leads to a unique, distinct page or section.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 8: Active / Current State Consistency
        # ----------------------------------------------------------------------
        active_items = [i for i in flattened_items if i.active is True]
        if len(active_items) > 2:
            findings.append(EngagementFinding(
                id="ENG-NAV-011",
                category="navigation",
                title=f"Multiple contradictory active navigation items ({len(active_items)} active items)",
                description=f"Found {len(active_items)} navigation items simultaneously marked with active/current states.",
                severity="low",
                confidence=0.85,
                evidence={"active_labels": [i.clean_label for i in active_items[:4]]},
                recommendation="Highlight only the single navigation link corresponding to the user's current location.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 9: Breadcrumb Hierarchy Validation
        # ----------------------------------------------------------------------
        if breadcrumbs:
            # Check for empty breadcrumbs or duplicate consecutive items
            empty_bc = [b for b in breadcrumbs if not b.strip()]
            dup_consecutive_bc = any(
                breadcrumbs[i].strip().lower() == breadcrumbs[i + 1].strip().lower()
                for i in range(len(breadcrumbs) - 1)
            )

            if empty_bc or dup_consecutive_bc:
                findings.append(EngagementFinding(
                    id="ENG-NAV-012",
                    category="navigation",
                    title="Malformed breadcrumb trail hierarchy",
                    description="Breadcrumb navigation contains empty items or duplicate consecutive pathway steps.",
                    severity="low",
                    confidence=0.9,
                    evidence={"breadcrumbs_trail": breadcrumbs},
                    recommendation="Ensure breadcrumb trails follow strict linear ancestral steps (Home > Category > Page).",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Check 10: Mobile Navigation Availability
        # ----------------------------------------------------------------------
        if mobile_evidence:
            if mobile_evidence.get("mobile_nav_hidden") or mobile_evidence.get("missing_hamburger_toggle"):
                findings.append(EngagementFinding(
                    id="ENG-NAV-013",
                    category="navigation",
                    title="Mobile navigation menu is missing or inaccessible",
                    description="Mobile viewport lacks an accessible navigation toggle (hamburger menu) or menu is hidden on small screens.",
                    severity="high",
                    confidence=0.95,
                    evidence=mobile_evidence,
                    recommendation="Provide an accessible, tappable mobile navigation toggle button on mobile viewports.",
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
            "navigation_present": True,
            "total_items_count": len(flattened_items),
            "primary_items_count": len(primary_items),
            "groups_count": len(normalized_nav["groups"]),
            "max_depth": max_observed_depth,
            "average_depth": avg_depth,
            "active_items_count": len(active_items),
            "breadcrumbs_count": len(breadcrumbs),
            "duplicate_labels_count": len(dup_labels),
            "redundant_dests_count": len(dup_dests),
        }

        return CategoryResult(
            category="navigation",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95,
            messages=[
                f"Navigation analysis completed: {len(flattened_items)} items across {len(normalized_nav['groups'])} groups.",
                f"Max depth: {max_observed_depth}, Score: {score_clamped}/100.",
            ],
        )



# Public API Function


def analyze_navigation(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for Navigation and Information Hierarchy analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult containing status, score, metrics, and navigation findings.
    """
    analyzer = NavigationAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
