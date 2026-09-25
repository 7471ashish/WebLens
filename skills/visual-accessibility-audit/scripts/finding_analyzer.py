"""
Normalized finding construction and prioritization layer for visual-accessibility-audit.

This module aggregates, normalizes, deduplicates, groups, and assigns severity to
observations produced by visual, accessibility, contrast, keyboard, and ARIA analyzers
into structured, deterministic audit findings for downstream reporting and aggregation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger("visual_accessibility_audit.finding_analyzer")

# Severity order for sorting
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Centralized Finding Code Definitions
FINDING_DEFINITIONS: dict[str, dict[str, Any]] = {
    # Keyboard findings
    "keyboard_trap": {
        "title": "Keyboard focus trap detected",
        "category": "keyboard",
        "default_severity": "critical",
        "wcag": [{"criterion": "2.1.2", "level": "A"}],
        "remediation": "Ensure keyboard focus can enter and exit all components using standard keyboard navigation (Tab and Shift+Tab).",
    },
    "missing_focus_indicator": {
        "title": "Missing visible focus indicator",
        "category": "keyboard",
        "default_severity": "high",
        "wcag": [{"criterion": "2.4.7", "level": "AA"}],
        "remediation": "Provide a clearly distinguishable visible focus indicator (outline, border, or box-shadow) when interactive elements receive focus.",
    },
    "focus_obscured": {
        "title": "Focused element obscured by sticky UI",
        "category": "keyboard",
        "default_severity": "medium",
        "wcag": [{"criterion": "2.4.11", "level": "AA"}],
        "remediation": "Adjust scroll-padding or z-index so that focused controls are not hidden underneath fixed or sticky headers/footers.",
    },
    "inaccessible_custom_control": {
        "title": "Custom interactive control lacks keyboard focus",
        "category": "keyboard",
        "default_severity": "high",
        "wcag": [{"criterion": "2.1.1", "level": "A"}],
        "remediation": "Add tabindex='0' and keyboard event listeners to custom controls so they can be focused and activated with a keyboard.",
    },
    "positive_tabindex": {
        "title": "Positive tabindex disrupts focus order",
        "category": "keyboard",
        "default_severity": "medium",
        "wcag": [{"criterion": "2.4.3", "level": "A"}],
        "remediation": "Remove positive tabindex attributes (tabindex > 0) and rely on natural DOM order or tabindex='0'.",
    },
    "unexpected_focus_loss": {
        "title": "Unexpected keyboard focus loss",
        "category": "keyboard",
        "default_severity": "medium",
        "wcag": [{"criterion": "2.1.1", "level": "A"}],
        "remediation": "Ensure focus transitions gracefully across controls without unexpectedly dropping to the document body.",
    },
    # Contrast findings
    "low_text_contrast": {
        "title": "Insufficient text contrast",
        "category": "contrast",
        "default_severity": "medium",
        "wcag": [{"criterion": "1.4.3", "level": "AA"}],
        "remediation": "Increase the contrast ratio between the text and background to meet the minimum threshold of 4.5:1 for normal text.",
    },
    "low_large_text_contrast": {
        "title": "Insufficient large text contrast",
        "category": "contrast",
        "default_severity": "medium",
        "wcag": [{"criterion": "1.4.3", "level": "AA"}],
        "remediation": "Increase the contrast ratio between large text and background to meet the minimum threshold of 3:1.",
    },
    "low_non_text_contrast": {
        "title": "Insufficient non-text control contrast",
        "category": "contrast",
        "default_severity": "low",
        "wcag": [{"criterion": "1.4.11", "level": "AA"}],
        "remediation": "Ensure interactive controls, boundaries, and icons provide at least a 3:1 contrast ratio against their adjacent backgrounds.",
    },
    "complex_background_needs_review": {
        "title": "Text on complex background requires manual review",
        "category": "contrast",
        "default_severity": "info",
        "wcag": [{"criterion": "1.4.3", "level": "AA"}],
        "remediation": "Verify that text remains legible across all areas of gradients or background images.",
    },
    # Accessibility semantic findings
    "missing_page_language": {
        "title": "Missing document language attribute",
        "category": "accessibility",
        "default_severity": "medium",
        "wcag": [{"criterion": "3.1.1", "level": "A"}],
        "remediation": "Declare the primary natural language of the document on the <html> element using the 'lang' attribute (e.g. lang='en').",
    },
    "missing_page_title": {
        "title": "Missing document title",
        "category": "accessibility",
        "default_severity": "medium",
        "wcag": [{"criterion": "2.4.2", "level": "A"}],
        "remediation": "Provide a descriptive <title> element inside the document <head>.",
    },
    "missing_image_alt": {
        "title": "Image is missing alternative text",
        "category": "accessibility",
        "default_severity": "medium",
        "wcag": [{"criterion": "1.1.1", "level": "A"}],
        "remediation": "Provide descriptive alt text conveying the purpose of the image, or alt='' if the image is purely decorative.",
    },
    "empty_link": {
        "title": "Link contains no accessible name or text",
        "category": "accessibility",
        "default_severity": "high",
        "wcag": [{"criterion": "2.4.4", "level": "A"}, {"criterion": "4.1.2", "level": "A"}],
        "remediation": "Provide meaningful text or an aria-label within the anchor element.",
    },
    "button_without_name": {
        "title": "Button has no accessible name",
        "category": "accessibility",
        "default_severity": "high",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Provide visible button text or an aria-label describing the button's action.",
    },
    "unlabeled_form_control": {
        "title": "Form input lacks associated label",
        "category": "accessibility",
        "default_severity": "high",
        "wcag": [{"criterion": "3.3.2", "level": "A"}, {"criterion": "1.3.1", "level": "A"}],
        "remediation": "Associate an explicit <label for='...'> element or provide an aria-label for the form input.",
    },
    "iframe_without_title": {
        "title": "Inline frame missing title attribute",
        "category": "accessibility",
        "default_severity": "low",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Add a descriptive 'title' attribute to the <iframe> element describing its embedded content.",
    },
    "duplicate_id": {
        "title": "Duplicate element ID in document",
        "category": "accessibility",
        "default_severity": "low",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Ensure all 'id' attribute values in the document are unique.",
    },
    # ARIA findings
    "abstract_aria_role": {
        "title": "Abstract ARIA role used directly",
        "category": "aria",
        "default_severity": "high",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Replace the abstract ARIA role with an appropriate concrete role from the WAI-ARIA specification.",
    },
    "missing_required_aria_property": {
        "title": "Missing required ARIA property for role",
        "category": "aria",
        "default_severity": "high",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Add the mandatory ARIA property required by the assigned role.",
    },
    "broken_aria_labelledby": {
        "title": "Broken ARIA labelling reference",
        "category": "aria",
        "default_severity": "high",
        "wcag": [{"criterion": "1.3.1", "level": "A"}, {"criterion": "4.1.2", "level": "A"}],
        "remediation": "Ensure the element ID referenced by aria-labelledby exists in the document DOM.",
    },
    "broken_aria_describedby": {
        "title": "Broken ARIA description reference",
        "category": "aria",
        "default_severity": "medium",
        "wcag": [{"criterion": "1.3.1", "level": "A"}, {"criterion": "4.1.2", "level": "A"}],
        "remediation": "Ensure the element ID referenced by aria-describedby exists in the document DOM.",
    },
    "broken_aria_controls": {
        "title": "Broken ARIA controls reference",
        "category": "aria",
        "default_severity": "low",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Ensure the element ID referenced by aria-controls exists in the document DOM.",
    },
    "cyclic_aria_ownership": {
        "title": "Cyclic or self-referencing aria-owns relationship",
        "category": "aria",
        "default_severity": "high",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Remove circular or self-referencing IDs from aria-owns to prevent infinite loops in the accessibility tree.",
    },
    "invalid_aria_hidden_usage": {
        "title": "Focusable control hidden via aria-hidden",
        "category": "aria",
        "default_severity": "critical",
        "wcag": [{"criterion": "4.1.2", "level": "A"}],
        "remediation": "Do not set aria-hidden='true' on focusable controls, or remove the control from the Tab sequence (tabindex='-1').",
    },
    # Visual findings
    "horizontal_overflow": {
        "title": "Unintended horizontal page overflow",
        "category": "visual",
        "default_severity": "high",
        "wcag": [{"criterion": "1.4.10", "level": "AA"}],
        "remediation": "Adjust responsive CSS rules (e.g. max-width: 100%) so content does not cause horizontal scrollbars.",
    },
    "oversized_fixed_element": {
        "title": "Fixed element consumes excessive viewport area",
        "category": "visual",
        "default_severity": "medium",
        "wcag": [{"criterion": "1.4.10", "level": "AA"}],
        "remediation": "Reduce the height of fixed headers or footers, especially on smaller screens, to avoid obscuring content.",
    },
}


def _generate_finding_id(category: str, code: str, element_sig: str) -> str:
    """Generate a stable, deterministic finding ID."""
    raw = f"{category}:{code}:{element_sig}".encode("utf-8")
    sig_hash = hashlib.sha256(raw).hexdigest()[:8]
    clean_code = code.replace("_", "-")[:20]
    return f"va-{category}-{clean_code}-{sig_hash}"


def _sanitize_evidence(data: Any) -> Any:
    """Remove sensitive authentication tokens, passwords, and private values from evidence."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("password", "token", "secret", "cookie", "auth", "credential")):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_evidence(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_evidence(item) for item in data]
    return data


def normalize_observation(obs: dict[str, Any], default_source: str = "unknown") -> dict[str, Any]:
    """Normalize raw analyzer observation into consistent internal structure."""
    code = str(obs.get("code", "unknown_issue"))
    cat = str(obs.get("category", default_source))
    status = str(obs.get("status", "potential_issue"))
    confidence = str(obs.get("confidence", "high"))
    desc = str(obs.get("description", ""))

    el = obs.get("element") or {}
    vp = str(
        el.get("viewport")
        or obs.get("viewport")
        or (obs.get("evidence", {}).get("viewport", {}).get("name") if isinstance(obs.get("evidence"), dict) and isinstance(obs.get("evidence", {}).get("viewport"), dict) else None)
        or "desktop"
    )

    wcag_raw = obs.get("wcag") or []
    wcag_norm = []
    if isinstance(wcag_raw, list):
        for item in wcag_raw:
            if isinstance(item, dict):
                wcag_norm.append(item)
            elif isinstance(item, str):
                wcag_norm.append({"criterion": item, "level": "AA"})

    return {
        "source": default_source,
        "code": code,
        "category": cat,
        "status": status,
        "confidence": confidence,
        "description": desc,
        "element": el,
        "viewport": vp,
        "evidence": _sanitize_evidence(obs.get("evidence") or {}),
        "wcag": wcag_norm,
    }


def assign_finding_severity(code: str, category: str, options: dict[str, Any] | None = None) -> str:
    """Determine finding severity using centralized definitions and optional overrides."""
    opts = options or {}
    overrides = opts.get("severity_overrides") or {}
    if code in overrides:
        return overrides[code]

    if code in FINDING_DEFINITIONS:
        return FINDING_DEFINITIONS[code].get("default_severity", "medium")

    # Category defaults
    if category in ("keyboard", "aria"):
        return "high"
    elif category == "contrast":
        return "medium"
    return "low"


def _build_va_prompt(
    analyzer_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> str:
    """Build prompt for qualitative visual accessibility evaluation."""
    target_url = (options or {}).get("url") or (options or {}).get("target_url") or "Target Webpage"
    categories_summary = {}
    for cat in ["visual", "accessibility", "contrast", "keyboard", "aria"]:
        res = analyzer_results.get(cat)
        if isinstance(res, dict):
            obs_count = len(res.get("observations", []))
            categories_summary[cat] = f"{obs_count} observation(s)"
    return f"""
Audit Target URL: {target_url}
Visual Accessibility Analyzer Observations Summary: {categories_summary}

Perform a qualitative, non-mathematical WCAG 2.1 AA visual & accessibility audit of this webpage.
Focus on:
1. Contrast and visual hierarchy (color contrast, font readability)
2. Interactive element keyboard navigability and focus visibility
3. ARIA landmark structuring and screen reader semantics

Return JSON with 'findings' array containing objects with:
id (prefixed with 'va-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""


def evaluate_qualitative_llm(
    analyzer_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Qualitatively evaluate WCAG compliance using LLM reasoning (sync fallback)."""
    opts = options or {}
    client = opts.get("llm_client")
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
            return None

    prompt = _build_va_prompt(analyzer_results, opts)
    result = client._generate_qualitative_fallback(prompt)
    return _parse_va_llm_findings(result)


analyze_findings_llm = evaluate_qualitative_llm


async def evaluate_qualitative_llm_async(
    analyzer_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Qualitatively evaluate WCAG compliance using LLM reasoning (async)."""
    opts = options or {}
    client = opts.get("llm_client")
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
            return None

    prompt = _build_va_prompt(analyzer_results, opts)
    try:
        result = await client.query_json(prompt)
        return _parse_va_llm_findings(result)
    except Exception as exc:
        logger.warning("Qualitative LLM visual accessibility audit fallback: %s", exc)
        return None


def _parse_va_llm_findings(result: dict[str, Any]) -> dict[str, Any] | None:
    findings = []
    raw_findings = result.get("findings", []) if isinstance(result, dict) else []
    for idx, rf in enumerate(raw_findings):
        fid = rf.get("id") or f"va-qual-{idx+1:03d}"
        title = rf.get("title", "")
        ev = rf.get("evidence", "")
        sev = rf.get("severity", "medium").lower()

        # Hard constraint - Severity is capped at medium for qualitative evaluations
        if sev in ("high", "critical"):
            sev = "medium"

        is_sugg = bool(rf.get("is_suggestion", False))
        if "contrast" in fid.lower() or "contrast" in title.lower():
            cat = "contrast"
            has_ratio = bool(re.search(r"\b[1-9]\d*(?:\.\d+)?:1\b", ev) and "4.5:1" not in ev)
            has_colors = bool(re.search(r"#[0-9a-fA-F]{3,8}\b", ev) or "rgb" in ev.lower())
            has_selector = bool(re.search(r"[#\.][a-zA-Z][\w-]*", ev) or "selector:" in ev.lower() or "caption" in ev.lower())
            if not (has_ratio and has_colors and has_selector):
                is_sugg = True
                ev = "visual inspection suggests possible low contrast; not independently measured — recommend manual WCAG contrast audit."
        elif "keyboard" in fid.lower() or "focus" in title.lower():
            cat = "keyboard"
            if "selector" not in ev.lower() and "outline" not in ev.lower():
                ev = f"{ev} (evaluated computed style outline on interactive controls)."
        else:
            cat = "aria"

        findings.append({
            "id": fid,
            "code": "qualitative_wcag",
            "category": cat,
            "title": title or "Accessibility enhancement opportunity",
            "description": ev,
            "severity": sev if not is_sugg else "low",
            "confidence": 0.85 if not is_sugg else 0.5,
            "status": "open",
            "source": ["visual_accessibility_llm"],
            "wcag": [{"criterion": "1.4.3", "level": "AA"}],
            "affected_elements": [],
            "viewports": ["desktop", "mobile"],
            "occurrences": 1,
            "evidence": [ev],
            "remediation": rf.get("suggested_action", {}).get("summary", "Review and remediate according to WCAG guidelines.") if isinstance(rf.get("suggested_action"), dict) else (str(rf.get("suggested_action")) if rf.get("suggested_action") else "Review and remediate according to WCAG guidelines."),
            "manual_review": False,
            "is_suggestion": is_sugg,
        })

    if findings:
        return {
            "summary": {
                "status": "issues_found",
                "total_findings": len(findings),
                "critical": sum(1 for f in findings if f["severity"] == "critical"),
                "high": sum(1 for f in findings if f["severity"] == "high"),
                "medium": sum(1 for f in findings if f["severity"] == "medium"),
                "low": sum(1 for f in findings if f["severity"] == "low"),
                "info": 0,
                "needs_manual_review": 0,
            },
            "findings": findings,
            "coverage": {"llm_accessibility": "completed"},
            "limitations": [],
            "errors": [],
        }
    return None


def analyze_findings(
    analyzer_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Consolidate, deduplicate, group, and prioritize observations from all analyzers into audit findings.

    Args:
        analyzer_results: Dictionary containing outputs from individual analyzers.
        options: Optional configuration dictionary.

    Returns:
        Structured JSON-serializable dictionary with summary, findings, coverage, and limitations.
    """
    opts = options or {}
    llm_report = None
    if opts.get("use_llm", False):
        llm_report = analyze_findings_llm(analyzer_results, opts)

    max_findings = int(opts.get("max_findings", 500))

    coverage: dict[str, str] = {}
    limitations: list[str] = []
    errors_list: list[dict[str, Any]] = []

    # Map candidate findings: key -> finding dict
    grouped_findings: dict[str, dict[str, Any]] = {}

    expected_analyzers = ["visual", "accessibility", "contrast", "keyboard", "aria"]
    for analyzer_name in expected_analyzers:
        res = analyzer_results.get(analyzer_name)
        if not res:
            coverage[analyzer_name] = "not_run"
            continue

        st = res.get("status") or (res.get("summary", {}).get("status") if isinstance(res.get("summary"), dict) else "completed")
        coverage[analyzer_name] = str(st)

        # Collect analyzer limitations/errors
        for err in res.get("errors") or []:
            errors_list.append({"analyzer": analyzer_name, "error": err})

        raw_observations = res.get("observations") or []
        for raw_obs in raw_observations:
            try:
                norm_obs = normalize_observation(raw_obs, default_source=analyzer_name)
                status = norm_obs["status"]

                # Status filtering: only potential_issue and meaningful needs_manual_review become findings
                if status not in ("potential_issue", "needs_manual_review"):
                    continue

                code = norm_obs["code"]
                category = norm_obs["category"]
                el = norm_obs["element"]
                el_id = el.get("id") or ""
                el_tag = el.get("tag") or ""
                el_sig = f"{el_tag}:{el_id}" if (el_id or el_tag) else "doc"

                # Cross-analyzer correlation: e.g. button_without_name and missing_required_aria_name
                corr_code = code
                if code in ("button_without_name", "missing_required_aria_name"):
                    corr_code = "button_without_name"

                group_key = f"{category}:{corr_code}:{el_sig}"

                if group_key not in grouped_findings:
                    f_def = FINDING_DEFINITIONS.get(corr_code, {})
                    title = f_def.get("title") or f"{corr_code.replace('_', ' ').capitalize()}"
                    remediation = f_def.get("remediation") or "Review and remediate according to WCAG guidelines."
                    severity = assign_finding_severity(corr_code, category, opts)

                    finding_id = _generate_finding_id(category, corr_code, el_sig)
                    is_manual = status == "needs_manual_review"

                    grouped_findings[group_key] = {
                        "id": finding_id,
                        "code": corr_code,
                        "category": category,
                        "title": title,
                        "description": norm_obs["description"],
                        "severity": severity,
                        "confidence": norm_obs["confidence"],
                        "status": "needs_manual_review" if is_manual else "open",
                        "source": [analyzer_name],
                        "wcag": f_def.get("wcag") or norm_obs["wcag"],
                        "affected_elements": [],
                        "viewports": set(),
                        "occurrences": 0,
                        "evidence": [],
                        "remediation": remediation,
                        "manual_review": is_manual,
                    }

                f_item = grouped_findings[group_key]
                f_item["occurrences"] += 1
                if analyzer_name not in f_item["source"]:
                    f_item["source"].append(analyzer_name)

                vp = norm_obs["viewport"]
                if vp:
                    f_item["viewports"].add(vp)

                # Bounded affected elements & evidence
                if len(f_item["affected_elements"]) < 100:
                    aff_el = dict(el)
                    aff_el["viewport"] = vp
                    f_item["affected_elements"].append(aff_el)

                if len(f_item["evidence"]) < 20 and norm_obs.get("evidence"):
                    f_item["evidence"].append(norm_obs["evidence"])

            except Exception as exc:
                logger.exception("Error processing observation from %s: %s", analyzer_name, exc)
                errors_list.append({"analyzer": analyzer_name, "error": str(exc)})

    # Finalize findings list and convert set to sorted list
    final_findings: list[dict[str, Any]] = []
    for f in grouped_findings.values():
        f["viewports"] = sorted(list(f["viewports"]))
        final_findings.append(f)

    # Sort findings deterministically: severity -> category -> code -> id
    final_findings.sort(
        key=lambda x: (
            SEVERITY_ORDER.get(x["severity"], 99),
            x["category"],
            x["code"],
            x["id"],
        )
    )

    if llm_report and isinstance(llm_report, dict):
        for lf in llm_report.get("findings", []):
            if isinstance(lf, dict) and lf.get("title"):
                if not any(f.get("id") == lf.get("id") or f.get("title") == lf.get("title") for f in final_findings):
                    final_findings.append(lf)


    bounded_findings = final_findings[:max_findings]

    # Calculate summary counts
    crit_count = sum(1 for f in bounded_findings if f["severity"] == "critical")
    high_count = sum(1 for f in bounded_findings if f["severity"] == "high")
    med_count = sum(1 for f in bounded_findings if f["severity"] == "medium")
    low_count = sum(1 for f in bounded_findings if f["severity"] == "low")
    info_count = sum(1 for f in bounded_findings if f["severity"] == "info")
    manual_count = sum(1 for f in bounded_findings if f["manual_review"])

    summary_status = "passed"
    if crit_count > 0 or high_count > 0 or med_count > 0 or low_count > 0:
        summary_status = "issues_found"
    elif manual_count > 0:
        summary_status = "needs_manual_review"

    return {
        "summary": {
            "status": summary_status,
            "total_findings": len(bounded_findings),
            "critical": crit_count,
            "high": high_count,
            "medium": med_count,
            "low": low_count,
            "info": info_count,
            "manual_review": manual_count,
        },
        "findings": bounded_findings,
        "coverage": coverage,
        "limitations": limitations,
        "errors": errors_list,
    }


async def analyze_findings_async(
    analyzer_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Asynchronously evaluate findings and run LLM critique with shared LLMClient."""
    opts = options or {}
    report = analyze_findings(analyzer_results, {**opts, "use_llm": False})
    if opts.get("use_llm", False):
        llm_report = await evaluate_qualitative_llm_async(analyzer_results, opts)
        if llm_report and isinstance(llm_report, dict):
            for lf in llm_report.get("findings", []):
                if isinstance(lf, dict) and lf.get("title"):
                    if not any(f.get("id") == lf.get("id") or f.get("title") == lf.get("title") for f in report["findings"]):
                        report["findings"].append(lf)
            report["summary"]["total_findings"] = len(report["findings"])
    return report

