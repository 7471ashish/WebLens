"""
DOM Comparator Module (dom_comparator.py)
-----------------------------------------
Deterministic, in-memory differential analyzer comparing RAW HTML observations
against RENDERED DOM observations for the `crawl-render-audit` skill.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            +---- crawler.py
            |       |
            |       v
            |   HTTP response
            |
            +---- robots_checker.py
            |
            +---- sitemap_checker.py
            |
            +---- raw_html_analyzer.py
            |       |
            |       v
            |   RAW HTML observations
            |
            +---- render_analyzer.py
            |       |
            |       v
            |   RENDERED DOM observations
            |
            +---- dom_comparator.py  <-- (THIS MODULE)
            |       |
            |       v
            |   RAW vs RENDERED differences & quantitative evidence
            |
            +---- finding_builder.py
                    |
                    v
            final structured findings (severity + actions)

This module operates strictly in-memory without network calls, Playwright, or LLM
invocations. It produces factual, quantitative difference evidence.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Mapping

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.dom_comparator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default configurable thresholds
DEFAULT_OPTIONS: dict[str, Any] = {
    "min_text_increase_chars": 500,
    "min_text_increase_ratio": 2.0,
    "min_meaningful_rendered_chars": 500,
    "min_link_increase_count": 5,
}

SEMANTIC_TAGS: tuple[str, ...] = (
    "main",
    "article",
    "section",
    "nav",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
)


def _safe_pct_change(raw_val: int | float | None, rend_val: int | float | None) -> float | None:
    """Calculate percentage change safely handling zero and None."""
    if raw_val is None or rend_val is None:
        return None
    if raw_val == 0:
        return 100.0 if rend_val > 0 else 0.0
    return round(((rend_val - raw_val) / raw_val) * 100.0, 2)


def _safe_ratio(raw_val: int | float | None, rend_val: int | float | None) -> float | None:
    """Calculate ratio safely handling zero and None."""
    if raw_val is None or rend_val is None:
        return None
    if raw_val == 0:
        return None
    return round(float(rend_val) / float(raw_val), 2)


def compare_dom(
    raw_result: dict[str, Any] | None,
    rendered_result: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compare raw HTML observations with rendered DOM observations.

    Args:
        raw_result: Observations dictionary from raw_html_analyzer.py.
        rendered_result: Observations dictionary from render_analyzer.py.
        options: Optional configuration thresholds.

    Returns:
        Structured JSON-serializable dictionary with factual comparison metrics and evidence.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    min_text_inc = int(opts.get("min_text_increase_chars", DEFAULT_OPTIONS["min_text_increase_chars"]))
    min_ratio = float(opts.get("min_text_increase_ratio", DEFAULT_OPTIONS["min_text_increase_ratio"]))
    min_meaningful_rendered = int(opts.get("min_meaningful_rendered_chars", DEFAULT_OPTIONS["min_meaningful_rendered_chars"]))
    min_link_inc = int(opts.get("min_link_increase_count", DEFAULT_OPTIONS["min_link_increase_count"]))

    raw = raw_result or {}
    rend = rendered_result or {}
    errors: list[str] = []

    # 1. Comparison Status & Confidence Determination
    raw_present = bool(raw and raw.get("html_metrics") is not None)
    rend_present = bool(rend and rend.get("rendering") is not None)
    rend_status = rend.get("rendering", {}).get("status", "unknown") if rend_present else "missing"

    if not raw_present and not rend_present:
        comparison_status = "failed"
        confidence = "low"
        errors.append("Both raw_result and rendered_result are empty or missing")
    elif not raw_present:
        comparison_status = "partial"
        confidence = "low"
        errors.append("raw_result is missing or incomplete")
    elif not rend_present:
        comparison_status = "partial"
        confidence = "low"
        errors.append("rendered_result is missing or incomplete")
    elif rend_status in ("browser_error", "browser_launch_failure", "navigation_error"):
        comparison_status = "partial"
        confidence = "low"
        errors.append(f"Browser rendering encountered error: {rend_status}")
    elif rend_status == "partial":
        comparison_status = "partial"
        confidence = "medium"
    else:
        comparison_status = "complete"
        confidence = "high"

    # 2. Navigation Consistency
    raw_final_url = raw.get("response", {}).get("final_url") or raw.get("target_url")
    rend_final_url = rend.get("navigation", {}).get("final_url") or rend.get("target_url")
    same_final_url = (raw_final_url == rend_final_url) if (raw_final_url and rend_final_url) else True

    navigation_comp = {
        "raw_final_url": raw_final_url,
        "rendered_final_url": rend_final_url,
        "same_final_url": same_final_url,
    }

    # 3. DOM / HTML Size Comparison
    raw_bytes = raw.get("html_metrics", {}).get("html_bytes")
    rend_bytes = rend.get("dom", {}).get("size_bytes")
    diff_bytes = (rend_bytes - raw_bytes) if (raw_bytes is not None and rend_bytes is not None) else None
    pct_bytes_change = _safe_pct_change(raw_bytes, rend_bytes)

    dom_size_comp = {
        "raw_bytes": raw_bytes,
        "rendered_bytes": rend_bytes,
        "difference_bytes": diff_bytes,
        "percentage_change": pct_bytes_change,
    }

    # 4. Text Content Comparison
    raw_chars = raw.get("text_content", {}).get("character_count")
    rend_chars = rend.get("text_content", {}).get("visible_character_count")
    diff_chars = (rend_chars - raw_chars) if (raw_chars is not None and rend_chars is not None) else None
    pct_text_inc = _safe_pct_change(raw_chars, rend_chars)
    text_ratio = _safe_ratio(raw_chars, rend_chars)

    content_added_after_render = bool(diff_chars is not None and diff_chars > 0)
    substantial_content_added = False
    if diff_chars is not None and rend_chars is not None:
        if rend_chars >= min_meaningful_rendered:
            if diff_chars >= min_text_inc or (text_ratio is not None and text_ratio >= min_ratio) or (raw_chars == 0 and rend_chars >= min_meaningful_rendered):
                substantial_content_added = True

    text_content_comp = {
        "raw_characters": raw_chars,
        "rendered_characters": rend_chars,
        "difference": diff_chars,
        "increase_percentage": pct_text_inc,
        "rendered_to_raw_ratio": text_ratio,
        "content_added_after_render": content_added_after_render,
        "substantial_content_added": substantial_content_added,
    }

    # 5. Headings Comparison
    raw_headings = raw.get("headings", {})
    rend_headings = rend.get("headings", {})
    headings_comp: dict[str, Any] = {}

    for lvl in range(1, 7):
        k = f"h{lvl}"
        r_cnt = raw_headings.get(f"{k}_count")
        rd_cnt = rend_headings.get(f"{k}_count")
        headings_comp[f"raw_{k}_count"] = r_cnt
        headings_comp[f"rendered_{k}_count"] = rd_cnt
        headings_comp[f"{k}_added_after_render"] = bool(r_cnt is not None and rd_cnt is not None and rd_cnt > r_cnt)

    raw_h1_text = raw_headings.get("h1_text", [])
    rend_h1_text = rend_headings.get("h1_text", [])
    headings_comp["raw_h1_text"] = raw_h1_text
    headings_comp["rendered_h1_text"] = rend_h1_text
    headings_comp["h1_text_changed"] = (raw_h1_text != rend_h1_text) if (raw_h1_text and rend_h1_text) else bool(not raw_h1_text and rend_h1_text)

    # 6. Links Comparison
    raw_links = raw.get("links", {})
    rend_links = rend.get("links", {})
    raw_total_links = raw_links.get("total")
    rend_total_links = rend_links.get("total")
    links_added = (rend_total_links - raw_total_links) if (raw_total_links is not None and rend_total_links is not None) else None

    links_comp = {
        "raw_total": raw_total_links,
        "rendered_total": rend_total_links,
        "links_added_after_render": links_added,
        "substantial_link_change": bool(links_added is not None and links_added >= min_link_inc),
        "raw_internal": raw_links.get("internal"),
        "rendered_internal": rend_links.get("internal"),
        "raw_external": raw_links.get("external"),
        "rendered_external": rend_links.get("external"),
        "raw_relative": raw_links.get("relative"),
        "rendered_relative": rend_links.get("relative"),
    }

    # 7. Structured Data Comparison
    raw_sd = raw.get("structured_data", {})
    rend_sd = rend.get("structured_data", {})
    raw_json_ld_blocks = raw_sd.get("valid_json_ld_blocks", raw_sd.get("json_ld_blocks"))
    rend_json_ld_blocks = rend_sd.get("valid_json_ld_blocks", rend_sd.get("json_ld_blocks"))

    raw_types = set(raw_sd.get("types", []))
    rend_types = set(rend_sd.get("types", []))
    types_added = sorted(list(rend_types - raw_types))

    structured_data_comp = {
        "raw_json_ld_blocks": raw_json_ld_blocks,
        "rendered_json_ld_blocks": rend_json_ld_blocks,
        "json_ld_added_after_render": bool(raw_json_ld_blocks is not None and rend_json_ld_blocks is not None and rend_json_ld_blocks > raw_json_ld_blocks),
        "types_added_after_render": types_added,
    }

    # 8. Title Comparison
    raw_title = raw.get("title", {})
    rend_title = rend.get("title", {})
    raw_t_exists = raw_title.get("exists")
    rend_t_exists = rend_title.get("exists")
    raw_t_val = raw_title.get("value")
    rend_t_val = rend_title.get("value")

    title_comp = {
        "raw_exists": raw_t_exists,
        "rendered_exists": rend_t_exists,
        "raw_value": raw_t_val,
        "rendered_value": rend_t_val,
        "added_after_render": bool(not raw_t_exists and rend_t_exists),
        "changed": bool(raw_t_exists and rend_t_exists and raw_t_val != rend_t_val),
    }

    # 9. Meta Description Comparison
    raw_desc = raw.get("meta_description", {})
    rend_desc = rend.get("meta_description", {})
    raw_d_exists = raw_desc.get("exists")
    rend_d_exists = rend_desc.get("exists")
    raw_d_content = raw_desc.get("content")
    rend_d_content = rend_desc.get("content")

    meta_desc_comp = {
        "raw_exists": raw_d_exists,
        "rendered_exists": rend_d_exists,
        "raw_content": raw_d_content,
        "rendered_content": rend_d_content,
        "added_after_render": bool(not raw_d_exists and rend_d_exists),
        "changed": bool(raw_d_exists and rend_d_exists and raw_d_content != rend_d_content),
    }

    # 10. Canonical Comparison
    raw_can = raw.get("canonical", {})
    rend_can = rend.get("canonical", {})
    raw_c_exists = raw_can.get("exists")
    rend_c_exists = rend_can.get("exists")
    raw_c_val = raw_can.get("value")
    rend_c_val = rend_can.get("value")

    canonical_comp = {
        "raw_exists": raw_c_exists,
        "rendered_exists": rend_c_exists,
        "raw_value": raw_c_val,
        "rendered_value": rend_c_val,
        "added_after_render": bool(not raw_c_exists and rend_c_exists),
        "changed": bool(raw_c_exists and rend_c_exists and raw_c_val != rend_c_val),
    }

    # 11. Semantic Structure Comparison
    raw_sem = raw.get("semantic_structure", {})
    rend_sem = rend.get("semantic_structure", {})
    semantic_comp: dict[str, Any] = {}

    for tag in SEMANTIC_TAGS:
        r_val = raw_sem.get(tag)
        rd_val = rend_sem.get(tag)
        semantic_comp[tag] = {
            "raw": r_val,
            "rendered": rd_val,
            "added_after_render": bool(r_val is not None and rd_val is not None and rd_val > r_val),
        }

    # 12. Machine Readability Comparison
    raw_mr = raw.get("machine_readability", {})
    rend_mr = rend.get("machine_readability", {})

    def _comp_mr_field(field_name: str) -> dict[str, Any]:
        r_f = raw_mr.get(field_name)
        rd_f = rend_mr.get(field_name)
        return {
            "raw": r_f,
            "rendered": rd_f,
            "improved_after_render": bool(r_f is False and rd_f is True),
        }

    machine_readability_comp = {
        "meaningful_text": _comp_mr_field("has_meaningful_text"),
        "headings": _comp_mr_field("has_headings"),
        "links": _comp_mr_field("has_links"),
        "structured_data": _comp_mr_field("has_structured_data"),
        "semantic_main": _comp_mr_field("has_semantic_main"),
    }

    # 13. Rendering Dependency & Shell Indicators
    is_raw_shell = bool(raw.get("spa_indicators", {}).get("possible_client_rendered_shell"))
    headings_added = any(headings_comp[f"h{lvl}_added_after_render"] for lvl in range(1, 7))
    links_added_flag = bool(links_added is not None and links_added > 0)
    sd_added = structured_data_comp["json_ld_added_after_render"]
    sem_added = any(v.get("added_after_render") for v in semantic_comp.values())
    mr_improved = any(v.get("improved_after_render") for v in machine_readability_comp.values())

    content_avail_only_after_render = bool(
        substantial_content_added
        or (is_raw_shell and rend_chars is not None and rend_chars >= min_meaningful_rendered)
        or (headings_added and sd_added and substantial_content_added)
    )

    meaningful_content_added = bool(
        substantial_content_added and rend_chars is not None and rend_chars >= min_meaningful_rendered
    )

    rendering_did_not_materially_increase = bool(
        rend_chars is not None
        and rend_chars < min_meaningful_rendered
        and not substantial_content_added
    )

    rendering_dependency = {
        "possible_client_rendered_shell": is_raw_shell,
        "substantial_text_added": substantial_content_added,
        "headings_added": headings_added,
        "links_added": links_added_flag,
        "structured_data_added": sd_added,
        "semantic_content_added": sem_added,
        "machine_readability_improved": mr_improved,
        "content_available_only_after_render": content_avail_only_after_render,
        "meaningful_content_added_after_render": meaningful_content_added,
        "rendering_did_not_materially_increase_content": rendering_did_not_materially_increase,
    }

    # 14. Difference Summary
    diff_summary = {
        "text_content_increased": bool(diff_chars is not None and diff_chars > 0),
        "headings_increased": headings_added,
        "links_increased": links_added_flag,
        "structured_data_increased": sd_added,
        "semantic_structure_increased": sem_added,
        "machine_readability_improved": mr_improved,
    }

    # 15. Rendering Context Summary
    rend_ctx = {
        "status": rend_status,
        "javascript_error_count": rend.get("javascript", {}).get("error_count", 0),
        "failed_resource_count": rend.get("resources", {}).get("failed_count", 0),
    }

    # 16. Quantitative Evidence Generation
    evidence: list[dict[str, Any]] = []

    if raw_chars is not None and rend_chars is not None:
        evidence.append({
            "metric": "visible_text_characters",
            "raw": raw_chars,
            "rendered": rend_chars,
            "difference": diff_chars,
            "unit": "characters",
        })

    raw_h1_c = raw_headings.get("h1_count")
    rend_h1_c = rend_headings.get("h1_count")
    if raw_h1_c is not None and rend_h1_c is not None:
        evidence.append({
            "metric": "h1_count",
            "raw": raw_h1_c,
            "rendered": rend_h1_c,
            "difference": rend_h1_c - raw_h1_c,
            "unit": "count",
        })

    if raw_total_links is not None and rend_total_links is not None:
        evidence.append({
            "metric": "total_links",
            "raw": raw_total_links,
            "rendered": rend_total_links,
            "difference": rend_total_links - raw_total_links,
            "unit": "count",
        })

    if raw_json_ld_blocks is not None and rend_json_ld_blocks is not None:
        evidence.append({
            "metric": "json_ld_blocks",
            "raw": raw_json_ld_blocks,
            "rendered": rend_json_ld_blocks,
            "difference": rend_json_ld_blocks - raw_json_ld_blocks,
            "unit": "count",
        })

    if raw_bytes is not None and rend_bytes is not None:
        evidence.append({
            "metric": "dom_size_bytes",
            "raw": raw_bytes,
            "rendered": rend_bytes,
            "difference": diff_bytes,
            "unit": "bytes",
        })

    return {
        "skill": "crawl-render-audit",
        "component": "dom_comparator",
        "comparison": {
            "status": comparison_status,
            "confidence": confidence,
        },
        "navigation": navigation_comp,
        "dom_size": dom_size_comp,
        "text_content": text_content_comp,
        "headings": headings_comp,
        "links": links_comp,
        "structured_data": structured_data_comp,
        "title": title_comp,
        "meta_description": meta_desc_comp,
        "canonical": canonical_comp,
        "semantic_structure": semantic_comp,
        "machine_readability": machine_readability_comp,
        "rendering_dependency": rendering_dependency,
        "difference_summary": diff_summary,
        "rendering_context": rend_ctx,
        "evidence": evidence,
        "errors": errors,
    }


def _cli_entrypoint() -> None:
    """CLI runner to compare two JSON result files directly."""
    if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python dom_comparator.py <RAW_JSON_PATH> <RENDERED_JSON_PATH>")
        print("Example: python dom_comparator.py raw_output.json rendered_output.json")
        sys.exit(0)

    raw_path = sys.argv[1]
    rend_path = sys.argv[2]

    try:
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        with open(rend_path, "r", encoding="utf-8") as f:
            rend_data = json.load(f)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to load JSON files: {exc}"}, indent=2))
        sys.exit(1)

    result = compare_dom(raw_data, rend_data)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
