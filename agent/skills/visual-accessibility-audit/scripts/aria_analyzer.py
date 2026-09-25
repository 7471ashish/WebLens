"""
Automated ARIA analysis layer for visual-accessibility-audit.

This module validates ARIA roles, attributes, states, properties, and IDREF/IDREFS relationships
using rendered DOM evidence produced by page_renderer.py against WAI-ARIA 1.2/1.3 and WCAG 2.2 specifications.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("visual_accessibility_audit.aria_analyzer")

# Centralized ARIA Specification Metadata
ARIA_SPEC_VERSION = "WAI-ARIA 1.2"

# Centralized Abstract Roles
ABSTRACT_ROLES: set[str] = {
    "command",
    "composite",
    "input",
    "landmark",
    "range",
    "roletype",
    "section",
    "sectionhead",
    "select",
    "structure",
    "widget",
    "window",
}

# Global ARIA Properties applicable to all elements and roles
GLOBAL_ARIA_PROPERTIES: set[str] = {
    "aria-atomic",
    "aria-busy",
    "aria-controls",
    "aria-current",
    "aria-describedby",
    "aria-description",
    "aria-details",
    "aria-disabled",
    "aria-dropeffect",
    "aria-errormessage",
    "aria-flowto",
    "aria-grabbed",
    "aria-haspopup",
    "aria-hidden",
    "aria-invalid",
    "aria-keyshortcuts",
    "aria-label",
    "aria-labelledby",
    "aria-live",
    "aria-owns",
    "aria-relevant",
    "aria-roledescription",
}

# Centralized Concrete Role Registry
ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "alert": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded"},
    },
    "alertdialog": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded", "aria-modal"},
    },
    "button": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded", "aria-pressed"},
    },
    "checkbox": {
        "required_properties": {"aria-checked"},
        "supported_properties": {"aria-checked", "aria-readonly"},
    },
    "combobox": {
        "required_properties": {"aria-expanded"},
        "supported_properties": {"aria-autocomplete", "aria-expanded", "aria-required", "aria-activedescendant"},
    },
    "dialog": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded", "aria-modal"},
    },
    "heading": {
        "required_properties": set(),
        "supported_properties": {"aria-level"},
    },
    "link": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded"},
    },
    "listbox": {
        "required_properties": set(),
        "supported_properties": {"aria-multiselectable", "aria-required", "aria-activedescendant", "aria-orientation"},
        "required_owned": ["option"],
    },
    "menu": {
        "required_properties": set(),
        "supported_properties": {"aria-activedescendant", "aria-orientation"},
        "required_owned": ["menuitem", "menuitemcheckbox", "menuitemradio"],
    },
    "menuitem": {
        "required_properties": set(),
        "supported_properties": {"aria-posinset", "aria-setsize"},
        "required_context": ["menu", "menubar"],
    },
    "option": {
        "required_properties": {"aria-selected"},
        "supported_properties": {"aria-checked", "aria-posinset", "aria-selected", "aria-setsize"},
        "required_context": ["listbox"],
    },
    "progressbar": {
        "required_properties": set(),
        "supported_properties": {"aria-valuenow", "aria-valuemin", "aria-valuemax", "aria-valuetext"},
    },
    "radio": {
        "required_properties": {"aria-checked"},
        "supported_properties": {"aria-checked", "aria-posinset", "aria-setsize"},
        "required_context": ["radiogroup"],
    },
    "radiogroup": {
        "required_properties": set(),
        "supported_properties": {"aria-required", "aria-activedescendant", "aria-orientation"},
        "required_owned": ["radio"],
    },
    "scrollbar": {
        "required_properties": {"aria-valuenow"},
        "supported_properties": {"aria-controls", "aria-orientation", "aria-valuemax", "aria-valuemin", "aria-valuenow"},
    },
    "searchbox": {
        "required_properties": set(),
        "supported_properties": {"aria-activedescendant", "aria-autocomplete", "aria-placeholder", "aria-readonly", "aria-required"},
    },
    "slider": {
        "required_properties": {"aria-valuenow"},
        "supported_properties": {"aria-orientation", "aria-valuemax", "aria-valuemin", "aria-valuenow", "aria-valuetext"},
    },
    "spinbutton": {
        "required_properties": {"aria-valuenow"},
        "supported_properties": {"aria-required", "aria-valuemax", "aria-valuemin", "aria-valuenow", "aria-valuetext"},
    },
    "switch": {
        "required_properties": {"aria-checked"},
        "supported_properties": {"aria-checked", "aria-readonly"},
    },
    "tab": {
        "required_properties": set(),
        "supported_properties": {"aria-selected", "aria-posinset", "aria-setsize"},
        "required_context": ["tablist"],
    },
    "tablist": {
        "required_properties": set(),
        "supported_properties": {"aria-activedescendant", "aria-multiselectable", "aria-orientation"},
        "required_owned": ["tab"],
    },
    "tabpanel": {
        "required_properties": set(),
        "supported_properties": {"aria-expanded"},
    },
    "textbox": {
        "required_properties": set(),
        "supported_properties": {"aria-activedescendant", "aria-autocomplete", "aria-multiline", "aria-placeholder", "aria-readonly", "aria-required"},
    },
    "tooltip": {
        "required_properties": set(),
        "supported_properties": set(),
    },
    "tree": {
        "required_properties": set(),
        "supported_properties": {"aria-activedescendant", "aria-multiselectable", "aria-orientation", "aria-required"},
        "required_owned": ["treeitem"],
    },
    "treeitem": {
        "required_properties": set(),
        "supported_properties": {"aria-checked", "aria-expanded", "aria-level", "aria-posinset", "aria-selected", "aria-setsize"},
        "required_context": ["tree", "group"],
    },
    "presentation": {"required_properties": set(), "supported_properties": set()},
    "none": {"required_properties": set(), "supported_properties": set()},
}


def _validate_boolean_attribute(attr_name: str, value: Any) -> bool:
    """Validate true/false boolean attribute values."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "false")
    return False


def _validate_tristate_attribute(attr_name: str, value: Any) -> bool:
    """Validate true/false/mixed tri-state attribute values."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "false", "mixed")
    return False


def analyze_aria(
    render_result: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate rendered webpage evidence for ARIA role and attribute validity,
    required properties, states, and IDREF relationships.

    Args:
        render_result: Output dictionary produced by page_renderer.render_page().
        options: Optional configuration dictionary (max_observations, deadline).

    Returns:
        Structured JSON-serializable dictionary with summary, role summary, relationships,
        and structured ARIA observations.
    """
    opts = options or {}
    max_obs = int(opts.get("max_observations", 500))
    deadline = opts.get("deadline")

    viewports = render_result.get("viewports") or []
    completed_viewports = [v for v in viewports if v.get("status") == "completed"]

    if not completed_viewports:
        return {
            "summary": {
                "status": "not_testable",
                "elements_checked": 0,
                "roles_checked": 0,
                "aria_attributes_checked": 0,
                "relationships_checked": 0,
                "potential_issues": 0,
                "manual_review": 0,
                "not_testable": 1,
            },
            "role_summary": {},
            "relationships": [],
            "observations": [],
        }

    # Baseline viewport for evaluation
    baseline_vp = next((v for v in completed_viewports if v.get("name") == "desktop"), completed_viewports[0])
    vp_name = str(baseline_vp.get("name", "desktop"))
    elements = baseline_vp.get("elements") or []

    # Map of all DOM IDs in the document
    dom_id_map: dict[str, list[dict[str, Any]]] = {}
    for el in elements:
        el_id = el.get("id")
        if el_id:
            dom_id_map.setdefault(el_id, []).append(el)

    observations: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    role_summary: dict[str, int] = {}

    elements_checked = 0
    roles_checked = 0
    aria_attributes_checked = 0
    relationships_checked = 0

    for el in elements:
        if deadline is not None and time.monotonic() >= deadline:
            observations.append({
                "code": "skipped_due_to_budget",
                "category": "system",
                "status": "skipped_due_to_budget",
                "confidence": "high",
                "description": "ARIA analysis deadline reached; remaining elements skipped.",
                "wcag": ["4.1.2"],
            })
            break

        elements_checked += 1
        tag = (el.get("tag") or "").lower()
        el_id = el.get("id") or ""
        role_raw = el.get("role")
        aria_attrs = el.get("aria_attributes") or {}

        # ---------------------------------------------------------
        # 1. ARIA ROLE VALIDATION
        # ---------------------------------------------------------
        active_role = None
        if role_raw and isinstance(role_raw, str):
            roles_checked += 1
            tokens = [t.strip().lower() for t in role_raw.split() if t.strip()]

            for token in tokens:
                if token in ABSTRACT_ROLES:
                    observations.append({
                        "code": "abstract_aria_role",
                        "category": "role",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Element <{tag}> assigns abstract ARIA role='{token}', which cannot be used directly by authors.",
                        "element": {"tag": tag, "id": el_id, "role": token, "viewport": vp_name},
                        "attribute": "role",
                        "value": token,
                        "wcag": ["4.1.2"],
                    })
                elif token in ROLE_DEFINITIONS:
                    if active_role is None:
                        active_role = token
                        role_summary[token] = role_summary.get(token, 0) + 1
                else:
                    observations.append({
                        "code": "unknown_aria_role",
                        "category": "role",
                        "status": "needs_manual_review",
                        "confidence": "medium",
                        "description": f"Element <{tag}> declares unknown role='{token}'.",
                        "element": {"tag": tag, "id": el_id, "role": token, "viewport": vp_name},
                        "attribute": "role",
                        "value": token,
                        "wcag": ["4.1.2"],
                    })

        # ---------------------------------------------------------
        # 2. REQUIRED & PROHIBITED ROLE PROPERTIES
        # ---------------------------------------------------------
        if active_role and active_role in ROLE_DEFINITIONS:
            role_def = ROLE_DEFINITIONS[active_role]
            req_props = role_def.get("required_properties", set())
            for req in req_props:
                if req not in aria_attrs:
                    observations.append({
                        "code": "missing_required_aria_property",
                        "category": "properties",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Element <{tag} role='{active_role}'> is missing required property '{req}'.",
                        "element": {"tag": tag, "id": el_id, "role": active_role, "viewport": vp_name},
                        "attribute": req,
                        "wcag": ["4.1.2"],
                    })

        # ---------------------------------------------------------
        # 3. ARIA ATTRIBUTE VALUE VALIDATION
        # ---------------------------------------------------------
        for attr_name, attr_val in aria_attrs.items():
            aria_attributes_checked += 1
            val_str = str(attr_val).strip()

            # Boolean attributes
            if attr_name in ("aria-expanded", "aria-modal", "aria-hidden", "aria-disabled", "aria-readonly", "aria-required"):
                if not _validate_boolean_attribute(attr_name, val_str):
                    observations.append({
                        "code": "invalid_aria_boolean",
                        "category": "values",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Attribute '{attr_name}' on <{tag}> must be a boolean ('true' or 'false'), got '{val_str}'.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "attribute": attr_name,
                        "value": val_str,
                        "wcag": ["4.1.2"],
                    })

            # Tri-state attributes
            elif attr_name in ("aria-checked", "aria-pressed"):
                if not _validate_tristate_attribute(attr_name, val_str):
                    observations.append({
                        "code": "invalid_aria_token",
                        "category": "values",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Attribute '{attr_name}' on <{tag}> must be 'true', 'false', or 'mixed', got '{val_str}'.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "attribute": attr_name,
                        "value": val_str,
                        "wcag": ["4.1.2"],
                    })

            # Integer / Level attributes
            elif attr_name == "aria-level":
                try:
                    lvl_num = int(val_str)
                    if lvl_num < 1:
                        raise ValueError()
                except ValueError:
                    observations.append({
                        "code": "invalid_aria_level",
                        "category": "values",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Attribute 'aria-level' on <{tag}> must be an integer >= 1, got '{val_str}'.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "attribute": attr_name,
                        "value": val_str,
                        "wcag": ["4.1.2"],
                    })

            # Numeric range attributes
            elif attr_name in ("aria-valuenow", "aria-valuemin", "aria-valuemax"):
                try:
                    float(val_str)
                except ValueError:
                    observations.append({
                        "code": "invalid_aria_number",
                        "category": "values",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Attribute '{attr_name}' on <{tag}> must be numeric, got '{val_str}'.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "attribute": attr_name,
                        "value": val_str,
                        "wcag": ["4.1.2"],
                    })

        # Check numeric range consistency (min <= now <= max)
        if "aria-valuenow" in aria_attrs:
            try:
                now_v = float(aria_attrs["aria-valuenow"])
                min_v = float(aria_attrs.get("aria-valuemin", 0))
                max_v = float(aria_attrs.get("aria-valuemax", 100))
                if min_v > max_v or not (min_v <= now_v <= max_v):
                    observations.append({
                        "code": "inconsistent_aria_value_range",
                        "category": "values",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Range inconsistency on <{tag}>: min={min_v}, now={now_v}, max={max_v}.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "wcag": ["4.1.2"],
                    })
            except ValueError:
                pass

        # ---------------------------------------------------------
        # 4. IDREF / IDREFS RELATIONSHIPS VALIDATION
        # ---------------------------------------------------------
        idref_attrs = ["aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-activedescendant"]
        for rel_attr in idref_attrs:
            if rel_attr in aria_attrs:
                relationships_checked += 1
                ref_val = str(aria_attrs[rel_attr]).strip()
                target_ids = [t.strip() for t in ref_val.split() if t.strip()]

                rel_status = "valid"
                broken_targets: list[str] = []

                for tid in target_ids:
                    # Self-reference check on aria-owns
                    if rel_attr == "aria-owns" and tid == el_id:
                        rel_status = "broken"
                        observations.append({
                            "code": "cyclic_aria_ownership",
                            "category": "relationships",
                            "status": "potential_issue",
                            "confidence": "high",
                            "description": f"Element <{tag} id='{el_id}'> owns itself via aria-owns.",
                            "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                            "attribute": rel_attr,
                            "value": ref_val,
                            "wcag": ["4.1.2"],
                        })

                    # Check if target ID exists in DOM
                    if tid not in dom_id_map:
                        broken_targets.append(tid)

                if broken_targets:
                    rel_status = "broken"
                    observations.append({
                        "code": f"broken_{rel_attr.replace('-', '_')}",
                        "category": "relationships",
                        "status": "potential_issue",
                        "confidence": "high",
                        "description": f"Attribute '{rel_attr}' on <{tag}> references nonexistent ID(s): {broken_targets}.",
                        "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                        "attribute": rel_attr,
                        "value": ref_val,
                        "evidence": {"missing_ids": broken_targets},
                        "wcag": ["1.3.1", "4.1.2"],
                    })

                relationships.append({
                    "attribute": rel_attr,
                    "source_id": el_id,
                    "target_ids": target_ids,
                    "status": rel_status,
                })

        # ---------------------------------------------------------
        # 5. STATE CONTRADICTIONS: aria-hidden with focusable control
        # ---------------------------------------------------------
        if aria_attrs.get("aria-hidden") == "true":
            # If element is focusable (button, link, input or tabindex>=0)
            if tag in ("button", "a", "input", "select") or el.get("tabindex") in ("0", 0):
                observations.append({
                    "code": "invalid_aria_hidden_usage",
                    "category": "state",
                    "status": "potential_issue",
                    "confidence": "high",
                    "description": f"Focusable interactive element <{tag}> has aria-hidden='true', hiding it from assistive technology while remaining operable.",
                    "element": {"tag": tag, "id": el_id, "viewport": vp_name},
                    "attribute": "aria-hidden",
                    "wcag": ["4.1.2"],
                })

    # Deduplicate observations
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for o in observations:
        el_id = o.get("element", {}).get("id") or ""
        key = f"{o.get('code')}:{o.get('category')}:{el_id}:{o.get('attribute', '')}"
        if key not in seen:
            seen.add(key)
            deduped.append(o)

    # Sort deterministically
    deduped.sort(
        key=lambda x: (
            str(x.get("element", {}).get("viewport", "")),
            str(x.get("category", "")),
            str(x.get("element", {}).get("id", "")),
            str(x.get("attribute", "")),
            str(x.get("code", "")),
        )
    )

    bounded_obs = deduped[:max_obs]
    potential_count = sum(1 for o in bounded_obs if o.get("status") == "potential_issue")
    manual_count = sum(1 for o in bounded_obs if o.get("status") == "needs_manual_review")

    overall_status = "passed"
    if potential_count > 0:
        overall_status = "potential_issue"
    elif manual_count > 0:
        overall_status = "needs_manual_review"

    return {
        "summary": {
            "status": overall_status,
            "elements_checked": elements_checked,
            "roles_checked": roles_checked,
            "aria_attributes_checked": aria_attributes_checked,
            "relationships_checked": relationships_checked,
            "potential_issues": potential_count,
            "manual_review": manual_count,
            "not_testable": 0,
        },
        "role_summary": role_summary,
        "relationships": relationships[:100],
        "observations": bounded_obs,
    }
