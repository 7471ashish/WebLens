"""
Core semantic and structural accessibility analysis layer for visual-accessibility-audit.

This module inspects rendered page and DOM evidence produced by page_renderer.py,
evaluating document semantics, language, titles, headings, landmarks, image alternatives,
links, buttons, form controls, tables, lists, and duplicate IDs against WCAG 2.2 Level A/AA criteria.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("visual_accessibility_audit.accessibility_analyzer")

# Default bounded analysis limits
DEFAULT_MAX_ELEMENTS = 5_000
DEFAULT_MAX_OBSERVATIONS = 500
DEFAULT_MAX_HEADING_ITEMS = 500
DEFAULT_MAX_FORM_CONTROLS = 1_000
DEFAULT_MAX_LINKS = 2_000
DEFAULT_MAX_IMAGES = 2_000

# Generic link text blacklist
GENERIC_LINK_TEXTS = {
    "click here",
    "click this",
    "here",
    "read more",
    "more",
    "learn more",
    "link",
    "info",
    "details",
    "continue",
    "go",
}


def _get_accessible_name(el: dict[str, Any]) -> tuple[str | None, str]:
    """
    Determine primary accessible name and source signal.
    Returns: (accessible_name, source_type)
    """
    aria_attrs = el.get("aria_attributes") or {}

    # 1. aria-labelledby
    if aria_attrs.get("aria-labelledby"):
        return aria_attrs["aria-labelledby"], "aria_labelledby"

    # 2. aria-label
    if aria_attrs.get("aria-label"):
        return aria_attrs["aria-label"].strip(), "aria_label"

    # 3. alt for img
    if el.get("tag") == "img" and el.get("alt") is not None:
        return el["alt"].strip(), "alt"

    # 4. visible text
    text = (el.get("text") or "").strip()
    if text:
        return text, "visible_text"

    # 5. title attribute
    title = (el.get("title") or "").strip()
    if title:
        return title, "title"

    return None, "none"


def _is_element_hidden(el: dict[str, Any]) -> bool:
    """Check whether element is hidden from viewport or assistive technology."""
    if el.get("hidden"):
        return True
    vis = el.get("visibility") or {}
    if not vis.get("is_visible", True):
        return True
    if vis.get("display") == "none" or vis.get("visibility") == "hidden":
        return True
    aria_attrs = el.get("aria_attributes") or {}
    if aria_attrs.get("aria-hidden") == "true":
        return True
    return False


def analyze_accessibility(
    render_result: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate rendered webpage evidence for semantic and structural accessibility compliance.

    Args:
        render_result: Output dictionary produced by page_renderer.render_page().
        options: Optional configuration dictionary.

    Returns:
        Structured JSON-serializable dictionary with summary, categorized components,
        and structured accessibility observations mapped to WCAG 2.2 criteria.
    """
    opts = options or {}
    max_obs = int(opts.get("max_observations", DEFAULT_MAX_OBSERVATIONS))

    viewports = render_result.get("viewports") or []
    completed_viewports = [v for v in viewports if v.get("status") == "completed"]

    if not completed_viewports:
        return {
            "summary": {
                "pages_analyzed": 0,
                "viewports_analyzed": 0,
                "checks_performed": 0,
                "passed_checks": 0,
                "potential_issues": 0,
                "needs_manual_review": 0,
                "not_testable": 1,
            },
            "document": {"title": {}, "language": {}, "headings": {}, "landmarks": {}},
            "images": {"summary": {}, "items": []},
            "links": {"summary": {}, "items": []},
            "buttons": {"summary": {}, "items": []},
            "forms": {"summary": {}, "items": []},
            "tables": {"summary": {}, "items": []},
            "lists": {"summary": {}, "items": []},
            "observations": [],
            "errors": render_result.get("errors", []),
        }

    # Use desktop viewport as baseline, or the first available viewport
    baseline_vp = next((v for v in completed_viewports if v.get("name") == "desktop"), completed_viewports[0])
    vp_names = [str(v.get("name")) for v in completed_viewports]

    page_info = baseline_vp.get("page") or {}
    elements = baseline_vp.get("elements") or []

    raw_observations: list[dict[str, Any]] = []

    # ---------------------------------------------------------
    # 1. DOCUMENT LANGUAGE & TITLE
    # ---------------------------------------------------------
    doc_lang = page_info.get("lang")
    lang_info: dict[str, Any] = {"declared_lang": doc_lang}
    if not doc_lang:
        raw_observations.append({
            "code": "missing_page_language",
            "category": "document",
            "status": "potential_issue",
            "confidence": "high",
            "description": "The <html> element does not declare a language attribute ('lang').",
            "evidence": {"declared_lang": None, "affected_viewports": vp_names},
            "wcag": {"version": "2.2", "principle": "Understandable", "criterion": "3.1.1", "level": "A"},
        })
        lang_info["status"] = "missing"
    elif not doc_lang.strip():
        raw_observations.append({
            "code": "invalid_page_language",
            "category": "document",
            "status": "potential_issue",
            "confidence": "high",
            "description": "The <html> element declares an empty language attribute.",
            "evidence": {"declared_lang": doc_lang, "affected_viewports": vp_names},
            "wcag": {"version": "2.2", "principle": "Understandable", "criterion": "3.1.1", "level": "A"},
        })
        lang_info["status"] = "empty"
    else:
        lang_info["status"] = "valid"

    page_title = page_info.get("title")
    title_info: dict[str, Any] = {"title": page_title}
    if page_title is None:
        raw_observations.append({
            "code": "missing_page_title",
            "category": "document",
            "status": "potential_issue",
            "confidence": "high",
            "description": "The page lacks a <title> element.",
            "evidence": {"title": None, "affected_viewports": vp_names},
            "wcag": {"version": "2.2", "principle": "Operable", "criterion": "2.4.2", "level": "A"},
        })
        title_info["status"] = "missing"
    elif not page_title.strip():
        raw_observations.append({
            "code": "empty_page_title",
            "category": "document",
            "status": "potential_issue",
            "confidence": "high",
            "description": "The page <title> element contains no text or only whitespace.",
            "evidence": {"title": page_title, "affected_viewports": vp_names},
            "wcag": {"version": "2.2", "principle": "Operable", "criterion": "2.4.2", "level": "A"},
        })
        title_info["status"] = "empty"
    else:
        title_info["status"] = "valid"

    # Index elements by tag and collect IDs
    headings_list: list[dict[str, Any]] = []
    landmarks_list: list[dict[str, Any]] = []
    images_list: list[dict[str, Any]] = []
    links_list: list[dict[str, Any]] = []
    buttons_list: list[dict[str, Any]] = []
    inputs_list: list[dict[str, Any]] = []
    tables_list: list[dict[str, Any]] = []
    lists_list: list[dict[str, Any]] = []
    custom_interactive: list[dict[str, Any]] = []

    id_occurrences: dict[str, list[dict[str, Any]]] = {}

    for el in elements:
        tag = (el.get("tag") or "").lower()
        el_id = el.get("id")

        if el_id:
            id_occurrences.setdefault(el_id, []).append(el)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            headings_list.append(el)
        elif tag in ("header", "nav", "main", "footer", "aside"):
            landmarks_list.append(el)
        elif tag == "img":
            images_list.append(el)
        elif tag == "a":
            links_list.append(el)
        elif tag == "button":
            buttons_list.append(el)
        elif tag in ("input", "select", "textarea"):
            inputs_list.append(el)
        elif tag in ("table", "thead", "tbody"):
            tables_list.append(el)
        elif tag in ("ul", "ol", "dl"):
            lists_list.append(el)

        role = (el.get("role") or "").lower()
        if tag not in ("button", "a", "input", "select", "textarea") and role in ("button", "link"):
            custom_interactive.append(el)

    # ---------------------------------------------------------
    # 2. HEADINGS ANALYSIS (WCAG 1.3.1, 2.4.6)
    # ---------------------------------------------------------
    h1_count = 0
    prev_level = 0
    heading_items: list[dict[str, Any]] = []

    for h in headings_list:
        tag = h["tag"]
        level = int(tag[1])
        text = (h.get("text") or "").strip()

        if level == 1:
            h1_count += 1

        heading_items.append({
            "level": level,
            "id": h.get("id"),
            "text": text[:100],
        })

        # Check empty heading
        if not text:
            raw_observations.append({
                "code": "empty_heading",
                "category": "headings",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Heading <{tag}> contains no accessible text.",
                "evidence": {"tag": tag, "id": h.get("id"), "affected_viewports": vp_names},
                "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "1.3.1", "level": "A"},
                "element": h,
            })

        # Check skipped levels (e.g. h1 followed by h3)
        if prev_level > 0 and level > prev_level + 1:
            raw_observations.append({
                "code": "suspicious_heading_order",
                "category": "headings",
                "status": "needs_manual_review",
                "confidence": "medium",
                "description": f"Heading hierarchy skips from <h{prev_level}> to <h{level}>.",
                "evidence": {"from_level": prev_level, "to_level": level, "heading_id": h.get("id")},
                "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "1.3.1", "level": "A"},
                "element": h,
            })
        prev_level = level

    headings_summary = {
        "total_headings": len(headings_list),
        "h1_count": h1_count,
        "items": heading_items[:50],
    }

    # ---------------------------------------------------------
    # 3. LANDMARKS ANALYSIS (WCAG 1.3.1)
    # ---------------------------------------------------------
    main_count = sum(1 for el in landmarks_list if el["tag"] == "main")
    if main_count == 0:
        raw_observations.append({
            "code": "missing_main_landmark",
            "category": "landmarks",
            "status": "needs_manual_review",
            "confidence": "medium",
            "description": "Page does not contain an explicit <main> landmark element.",
            "evidence": {"main_count": 0, "total_landmarks": len(landmarks_list)},
            "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "1.3.1", "level": "A"},
        })

    landmarks_summary = {
        "total_landmarks": len(landmarks_list),
        "main_count": main_count,
        "nav_count": sum(1 for el in landmarks_list if el["tag"] == "nav"),
        "header_count": sum(1 for el in landmarks_list if el["tag"] == "header"),
        "footer_count": sum(1 for el in landmarks_list if el["tag"] == "footer"),
    }

    # ---------------------------------------------------------
    # 4. IMAGES ANALYSIS (WCAG 1.1.1)
    # ---------------------------------------------------------
    images_items: list[dict[str, Any]] = []
    missing_alt_count = 0
    decorative_count = 0

    for img in images_list:
        alt_val = img.get("alt")
        src = img.get("src") or ""
        img_entry = {"id": img.get("id"), "src": src[:100], "alt": alt_val}

        if alt_val is None:
            missing_alt_count += 1
            raw_observations.append({
                "code": "missing_image_alt",
                "category": "images",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Image missing required 'alt' attribute: {src[:60]}.",
                "evidence": {"id": img.get("id"), "src": src[:100], "affected_viewports": vp_names},
                "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "1.1.1", "level": "A"},
                "element": img,
            })
        elif alt_val == "":
            decorative_count += 1
        elif alt_val.lower().strip() in ("image", "photo", "picture", "icon", "graphic"):
            raw_observations.append({
                "code": "suspicious_image_alt",
                "category": "images",
                "status": "needs_manual_review",
                "confidence": "medium",
                "description": f"Image uses non-descriptive generic alt text '{alt_val}'.",
                "evidence": {"id": img.get("id"), "alt": alt_val, "src": src[:100]},
                "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "1.1.1", "level": "A"},
                "element": img,
            })

        images_items.append(img_entry)

    images_summary = {
        "total_images": len(images_list),
        "missing_alt_count": missing_alt_count,
        "decorative_count": decorative_count,
    }

    # ---------------------------------------------------------
    # 5. LINKS ANALYSIS (WCAG 2.4.4, 4.1.2)
    # ---------------------------------------------------------
    links_items: list[dict[str, Any]] = []
    empty_links_count = 0

    for link in links_list:
        href = link.get("href")
        acc_name, name_source = _get_accessible_name(link)
        link_entry = {"id": link.get("id"), "href": href, "accessible_name": acc_name, "source": name_source}

        if not href:
            raw_observations.append({
                "code": "link_without_href",
                "category": "links",
                "status": "potential_issue",
                "confidence": "high",
                "description": "Anchor element <a> lacks an 'href' destination attribute.",
                "evidence": {"id": link.get("id"), "text": link.get("text")},
                "wcag": {"version": "2.2", "principle": "Operable", "criterion": "4.1.2", "level": "A"},
                "element": link,
            })

        if not acc_name:
            empty_links_count += 1
            raw_observations.append({
                "code": "empty_link",
                "category": "links",
                "status": "potential_issue",
                "confidence": "high",
                "description": "Link contains no accessible name, text, or label.",
                "evidence": {"id": link.get("id"), "href": href},
                "wcag": {"version": "2.2", "principle": "Operable", "criterion": "2.4.4", "level": "A"},
                "element": link,
            })
        elif acc_name.lower().strip() in GENERIC_LINK_TEXTS:
            raw_observations.append({
                "code": "suspicious_generic_link_name",
                "category": "links",
                "status": "needs_manual_review",
                "confidence": "medium",
                "description": f"Link uses non-descriptive generic text '{acc_name}'.",
                "evidence": {"id": link.get("id"), "text": acc_name, "href": href},
                "wcag": {"version": "2.2", "principle": "Operable", "criterion": "2.4.4", "level": "A"},
                "element": link,
            })

        links_items.append(link_entry)

    links_summary = {
        "total_links": len(links_list),
        "empty_links_count": empty_links_count,
    }

    # ---------------------------------------------------------
    # 6. BUTTONS ANALYSIS (WCAG 4.1.2)
    # ---------------------------------------------------------
    buttons_items: list[dict[str, Any]] = []
    unnamed_button_count = 0

    for btn in buttons_list:
        acc_name, name_src = _get_accessible_name(btn)
        btn_entry = {"id": btn.get("id"), "accessible_name": acc_name, "source": name_src}

        if not acc_name:
            unnamed_button_count += 1
            raw_observations.append({
                "code": "button_without_name",
                "category": "buttons",
                "status": "potential_issue",
                "confidence": "high",
                "description": "Button element has no accessible name, text content, or aria-label.",
                "evidence": {"id": btn.get("id")},
                "wcag": {"version": "2.2", "principle": "Robust", "criterion": "4.1.2", "level": "A"},
                "element": btn,
            })

        buttons_items.append(btn_entry)

    buttons_summary = {
        "total_buttons": len(buttons_list),
        "unnamed_button_count": unnamed_button_count,
    }

    # ---------------------------------------------------------
    # 7. FORMS ANALYSIS (WCAG 3.3.2, 4.1.2)
    # ---------------------------------------------------------
    forms_items: list[dict[str, Any]] = []
    unlabeled_controls_count = 0

    for inp in inputs_list:
        inp_type = (inp.get("type") or "text").lower()
        if inp_type in ("hidden", "submit", "button", "reset"):
            continue

        acc_name, name_src = _get_accessible_name(inp)
        has_id = bool(inp.get("id"))
        is_labeled = bool(acc_name)

        if not is_labeled:
            unlabeled_controls_count += 1
            raw_observations.append({
                "code": "unlabeled_form_control",
                "category": "forms",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Form control <input type='{inp_type}'> lacks an associated accessible label or aria-label.",
                "evidence": {"id": inp.get("id"), "name": inp.get("name"), "type": inp_type},
                "wcag": {"version": "2.2", "principle": "Perceivable", "criterion": "3.3.2", "level": "A"},
                "element": inp,
            })

        forms_items.append({
            "id": inp.get("id"),
            "type": inp_type,
            "labeled": is_labeled,
            "source": name_src,
        })

    forms_summary = {
        "total_controls": len(inputs_list),
        "unlabeled_controls_count": unlabeled_controls_count,
    }

    # ---------------------------------------------------------
    # 8. IFRAMES (WCAG 4.1.2)
    # ---------------------------------------------------------
    iframes_meta = baseline_vp.get("iframes", {})
    iframe_items = iframes_meta.get("items") or []
    for ifr in iframe_items:
        title = ifr.get("title")
        if not title or not title.strip():
            raw_observations.append({
                "code": "iframe_without_title",
                "category": "document",
                "status": "potential_issue",
                "confidence": "high",
                "description": "Inline frame <iframe> missing descriptive 'title' attribute.",
                "evidence": {"src": ifr.get("src")},
                "wcag": {"version": "2.2", "principle": "Operable", "criterion": "4.1.2", "level": "A"},
            })

    # ---------------------------------------------------------
    # 9. DUPLICATE IDS (WCAG 4.1.2)
    # ---------------------------------------------------------
    for el_id, occurrences in id_occurrences.items():
        if len(occurrences) > 1:
            raw_observations.append({
                "code": "duplicate_id",
                "category": "document",
                "status": "potential_issue",
                "confidence": "high",
                "description": f"Document contains duplicate id='{el_id}' defined on {len(occurrences)} elements.",
                "evidence": {"id": el_id, "occurrences": len(occurrences)},
                "wcag": {"version": "2.2", "principle": "Robust", "criterion": "4.1.2", "level": "A"},
            })

    # ---------------------------------------------------------
    # 10. CUSTOM INTERACTIVE ELEMENTS (WCAG 4.1.2)
    # ---------------------------------------------------------
    for ci in custom_interactive:
        raw_observations.append({
            "code": "suspicious_custom_interactive",
            "category": "buttons",
            "status": "needs_manual_review",
            "confidence": "medium",
            "description": f"Custom control <{ci.get('tag')}> implements role='{ci.get('role')}'; verify native semantics and keyboard operation.",
            "evidence": {"tag": ci.get("tag"), "id": ci.get("id"), "role": ci.get("role")},
            "wcag": {"version": "2.2", "principle": "Robust", "criterion": "4.1.2", "level": "A"},
            "element": ci,
        })

    # Deduplicate observations
    deduped_obs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for o in raw_observations:
        el_id = o.get("evidence", {}).get("id") or ""
        key = f"{o.get('code')}:{o.get('category')}:{el_id}:{o.get('description')[:30]}"
        if key not in seen:
            seen.add(key)
            deduped_obs.append(o)

    # Sort deterministically
    deduped_obs.sort(key=lambda x: (x.get("category", ""), x.get("code", ""), str(x.get("evidence", {}).get("id", ""))))
    bounded_obs = deduped_obs[:max_obs]

    potential_count = sum(1 for o in bounded_obs if o.get("status") == "potential_issue")
    review_count = sum(1 for o in bounded_obs if o.get("status") == "needs_manual_review")

    return {
        "summary": {
            "pages_analyzed": 1,
            "viewports_analyzed": len(completed_viewports),
            "checks_performed": len(bounded_obs),
            "passed_checks": 0,
            "potential_issues": potential_count,
            "needs_manual_review": review_count,
            "not_testable": 0,
        },
        "document": {
            "title": title_info,
            "language": lang_info,
            "headings": headings_summary,
            "landmarks": landmarks_summary,
        },
        "images": {
            "summary": images_summary,
            "items": images_items[:50],
        },
        "links": {
            "summary": links_summary,
            "items": links_items[:50],
        },
        "buttons": {
            "summary": buttons_summary,
            "items": buttons_items[:50],
        },
        "forms": {
            "summary": forms_summary,
            "items": forms_items[:50],
        },
        "tables": {
            "summary": {"total_tables": len(tables_list)},
            "items": tables_list[:20],
        },
        "lists": {
            "summary": {"total_lists": len(lists_list)},
            "items": lists_list[:20],
        },
        "observations": bounded_obs,
    }
