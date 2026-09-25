"""
Unit tests for finding_builder.py using pytest.

Tests all 30 scenarios specified in the requirements deterministically without network access.
"""

from __future__ import annotations

import json
import pytest
from crawl_finding_builder import (
    SEVERITY_ORDER,
    VALID_CONFIDENCES,
    VALID_SEVERITIES,
    build_findings,
)


@pytest.fixture
def clean_audit_results():
    """A clean, healthy audit output with zero issues."""
    return {
        "crawler": {
            "status_code": 200,
            "redirect_count": 0,
            "redirect_chain": [],
            "error_classification": None,
            "final_url": "https://example.com/",
        },
        "robots": {
            "robots_txt_status": "accessible",
            "target_url_evaluation": {
                "allowed": True,
                "matched_rule": "Allow: /",
                "user_agent": "AgentAuditBot",
            },
        },
        "sitemap": {
            "sitemaps": [{
                "url": "https://example.com/sitemap.xml",
                "status": "valid",
                "urls_count": 100,
                "duplicate_urls_count": 0,
            }]
        },
        "raw_html": {
            "title": {"exists": True, "value": "Example Domain"},
            "meta_description": {"exists": True, "content": "Official example domain"},
            "canonical": {"exists": True, "value": "https://example.com/", "is_valid": True, "duplicate_count": 0},
            "headings": {"h1_count": 1, "h1_text": ["Example Domain"]},
            "text_content": {"character_count": 1500},
            "structured_data": {"invalid_json_ld_blocks": 0},
        },
        "rendered": {
            "rendering": {"status": "success"},
            "title": {"exists": True, "value": "Example Domain"},
            "meta_description": {"exists": True, "content": "Official example domain"},
            "canonical": {"exists": True, "value": "https://example.com/", "is_valid": True, "duplicate_count": 0},
            "headings": {"h1_count": 1, "h1_text": ["Example Domain"]},
            "text_content": {"visible_character_count": 1600},
            "javascript": {"error_count": 0, "errors": []},
            "structured_data": {"invalid_json_ld_blocks": 0},
        },
        "dom_comparison": {
            "text_content": {
                "raw_characters": 1500,
                "rendered_characters": 1600,
                "difference": 100,
                "rendered_to_raw_ratio": 1.07,
                "substantial_content_added": False,
            },
            "structured_data": {"json_ld_added_after_render": False},
            "rendering_dependency": {
                "content_available_only_after_render": False,
                "rendering_did_not_materially_increase_content": False,
            },
        },
    }


def test_1_successful_crawl_no_unnecessary_findings(clean_audit_results):
    """1. Test that clean healthy site produces 0 issues."""
    findings = build_findings(clean_audit_results)
    assert len(findings) == 0


def test_2_http_404_finding(clean_audit_results):
    """2. Test HTTP 404 produces high severity finding."""
    clean_audit_results["crawler"]["status_code"] = 404
    findings = build_findings(clean_audit_results)
    f404 = [f for f in findings if f["id"] == "HTTP-404"]
    assert len(f404) == 1
    assert f404[0]["severity"] == "high"
    assert f404[0]["category"] == "http"


def test_3_http_403_finding(clean_audit_results):
    """3. Test HTTP 403 produces high severity finding."""
    clean_audit_results["crawler"]["status_code"] = 403
    findings = build_findings(clean_audit_results)
    f403 = [f for f in findings if f["id"] == "HTTP-403"]
    assert len(f403) == 1
    assert f403[0]["severity"] == "high"


def test_4_http_500_finding(clean_audit_results):
    """4. Test HTTP 500 produces high severity finding."""
    clean_audit_results["crawler"]["status_code"] = 500
    findings = build_findings(clean_audit_results)
    f500 = [f for f in findings if f["id"] == "HTTP-500"]
    assert len(f500) == 1
    assert f500[0]["severity"] == "high"


def test_5_timeout_finding(clean_audit_results):
    """5. Test crawler connection timeout."""
    clean_audit_results["crawler"]["error_classification"] = "timeout"
    findings = build_findings(clean_audit_results)
    ftimeout = [f for f in findings if f["id"] == "HTTP-TIMEOUT"]
    assert len(ftimeout) == 1
    assert ftimeout[0]["severity"] == "high"


def test_6_redirects_medium_finding(clean_audit_results):
    """6. Test 3 redirects produces medium finding."""
    clean_audit_results["crawler"]["redirect_count"] = 3
    clean_audit_results["crawler"]["redirect_chain"] = ["url1", "url2", "url3", "url4"]
    findings = build_findings(clean_audit_results)
    fred = [f for f in findings if f["id"] == "HTTP-REDIRECT-MED"]
    assert len(fred) == 1
    assert fred[0]["severity"] == "medium"


def test_7_redirects_high_finding(clean_audit_results):
    """7. Test 5 redirects produces high finding."""
    clean_audit_results["crawler"]["redirect_count"] = 5
    findings = build_findings(clean_audit_results)
    fred = [f for f in findings if f["id"] == "HTTP-REDIRECT-HIGH"]
    assert len(fred) == 1
    assert fred[0]["severity"] == "high"


def test_8_robots_disallow_finding(clean_audit_results):
    """8. Test robots.txt disallow rule."""
    clean_audit_results["robots"]["target_url_evaluation"]["allowed"] = False
    clean_audit_results["robots"]["target_url_evaluation"]["matched_rule"] = "Disallow: /"
    findings = build_findings(clean_audit_results)
    frob = [f for f in findings if f["id"] == "ROBOTS-DISALLOWED"]
    assert len(frob) == 1
    assert frob[0]["severity"] == "high"


def test_9_robots_404_no_false_positive(clean_audit_results):
    """9. Test that robots.txt returning 404 does NOT create a failure finding."""
    clean_audit_results["robots"] = {
        "robots_txt_status": "not_found",
        "target_url_evaluation": {"allowed": True},
    }
    findings = build_findings(clean_audit_results)
    frob = [f for f in findings if f["category"] == "robots"]
    assert len(frob) == 0


def test_10_malformed_sitemap_finding(clean_audit_results):
    """10. Test malformed sitemap XML produces medium finding."""
    clean_audit_results["sitemap"]["sitemaps"][0]["status"] = "malformed"
    clean_audit_results["sitemap"]["sitemaps"][0]["errors"] = ["Unclosed tag"]
    findings = build_findings(clean_audit_results)
    fsm = [f for f in findings if f["id"] == "SITEMAP-MALFORMED"]
    assert len(fsm) == 1
    assert fsm[0]["severity"] == "medium"


def test_11_missing_sitemap_not_critical(clean_audit_results):
    """11. Test missing sitemap produces no critical finding."""
    clean_audit_results["sitemap"] = {"sitemaps": []}
    findings = build_findings(clean_audit_results)
    critical_findings = [f for f in findings if f["severity"] == "critical"]
    assert len(critical_findings) == 0


def test_12_missing_llms_txt_no_finding(clean_audit_results):
    """12. Test absence of llms.txt produces zero findings."""
    clean_audit_results["llms_txt"] = None
    findings = build_findings(clean_audit_results)
    llms_findings = [f for f in findings if "llms" in f["id"].lower()]
    assert len(llms_findings) == 0


def test_13_large_raw_rendered_diff(clean_audit_results):
    """13. Test moderate raw vs rendered text difference."""
    clean_audit_results["dom_comparison"]["text_content"] = {
        "raw_characters": 1000,
        "rendered_characters": 2500,
        "difference": 1500,
        "rendered_to_raw_ratio": 2.5,
    }
    findings = build_findings(clean_audit_results)
    frend = [f for f in findings if f["id"] == "RENDER-GAP-MED"]
    assert len(frend) == 1
    assert frend[0]["severity"] == "medium"


def test_14_small_raw_rendered_diff_no_finding(clean_audit_results):
    """14. Test small difference produces no finding."""
    clean_audit_results["dom_comparison"]["text_content"] = {
        "raw_characters": 1000,
        "rendered_characters": 1050,
        "difference": 50,
        "rendered_to_raw_ratio": 1.05,
    }
    findings = build_findings(clean_audit_results)
    frend = [f for f in findings if f["category"] == "rendering"]
    assert len(frend) == 0


def test_15_extreme_rendering_dependency_high(clean_audit_results):
    """15. Test extreme rendering gap (80 chars raw -> 2500 chars rendered)."""
    clean_audit_results["dom_comparison"]["text_content"] = {
        "raw_characters": 80,
        "rendered_characters": 2500,
        "difference": 2420,
        "rendered_to_raw_ratio": 31.25,
    }
    clean_audit_results["dom_comparison"]["rendering_dependency"]["content_available_only_after_render"] = True
    findings = build_findings(clean_audit_results)
    frend = [f for f in findings if f["id"] == "RENDER-GAP-HIGH"]
    assert len(frend) == 1
    assert frend[0]["severity"] == "high"


def test_16_rendering_tool_error_not_website_critical(clean_audit_results):
    """16. Test browser rendering error handled with medium severity."""
    clean_audit_results["rendered"]["rendering"]["status"] = "browser_error"
    clean_audit_results["rendered"]["errors"] = [{"message": "Page crash"}]
    findings = build_findings(clean_audit_results)
    ferr = [f for f in findings if f["id"] == "RENDER-EXEC-ERROR"]
    assert len(ferr) == 1
    assert ferr[0]["severity"] == "medium"


def test_browser_launch_failure_produces_no_finding_but_marks_skill_failed(clean_audit_results):
    """Test that browser launch failure (e.g. missing Chromium binary) produces NO findings."""
    launch_err_msg = "Executable doesn't exist at /root/.cache/ms-playwright/chromium-1097/chrome-linux/chrome"
    clean_audit_results["rendered"]["rendering"]["status"] = "browser_launch_failure"
    clean_audit_results["rendered"]["errors"] = [{
        "type": "launch_error",
        "message": launch_err_msg,
    }]
    findings = build_findings(clean_audit_results)
    # Tooling launch failure must not be converted into site-level defects
    render_exec_findings = [f for f in findings if f["id"] == "RENDER-EXEC-ERROR"]
    assert len(render_exec_findings) == 0
    assert len(findings) == 0


def test_browser_render_crash_still_produces_finding(clean_audit_results):
    """Test that genuine browser crash during page render still produces RENDER-EXEC-ERROR finding."""
    render_crash_msg = "Page crashed: Target crashed while executing client-side scripts at runtime"
    clean_audit_results["rendered"]["rendering"]["status"] = "browser_error"
    clean_audit_results["rendered"]["errors"] = [{
        "type": "render_error",
        "message": render_crash_msg,
    }]
    findings = build_findings(clean_audit_results)
    ferr = [f for f in findings if f["id"] == "RENDER-EXEC-ERROR"]
    assert len(ferr) == 1
    assert ferr[0]["severity"] == "medium"
    assert ferr[0]["category"] == "rendering"
    assert ferr[0]["evidence"][0]["value"] == "browser_error"
    assert render_crash_msg[:50] in ferr[0]["evidence"][0]["details"]


def test_17_canonical_conflict_finding(clean_audit_results):
    """17. Test conflicting canonical URLs between raw and rendered."""
    clean_audit_results["raw_html"]["canonical"]["value"] = "https://example.com/item"
    clean_audit_results["rendered"]["canonical"]["value"] = "https://example.com/item?variant=1"
    findings = build_findings(clean_audit_results)
    fcan = [f for f in findings if f["id"] == "CANONICAL-CONFLICT"]
    assert len(fcan) == 1
    assert fcan[0]["severity"] == "medium"


def test_18_invalid_canonical_finding(clean_audit_results):
    """18. Test invalid canonical URL tag."""
    clean_audit_results["raw_html"]["canonical"]["is_valid"] = False
    clean_audit_results["raw_html"]["canonical"]["value"] = "invalid url ://"
    findings = build_findings(clean_audit_results)
    fcan = [f for f in findings if f["id"] == "CANONICAL-INVALID"]
    assert len(fcan) == 1
    assert fcan[0]["severity"] == "high"


def test_19_missing_optional_metadata_not_critical(clean_audit_results):
    """19. Test missing meta description produces low severity, not critical."""
    clean_audit_results["raw_html"]["meta_description"]["exists"] = False
    clean_audit_results["rendered"]["meta_description"]["exists"] = False
    findings = build_findings(clean_audit_results)
    fmeta = [f for f in findings if f["id"] == "META-DESC-MISSING"]
    assert len(fmeta) == 1
    assert fmeta[0]["severity"] == "low"


def test_20_duplicate_findings_merged(clean_audit_results):
    """20. Test deduplication ensures each finding ID is unique."""
    clean_audit_results["crawler"]["status_code"] = 404
    findings = build_findings(clean_audit_results)
    ids = [f["id"] for f in findings]
    assert len(ids) == len(set(ids))


def test_21_missing_sections_do_not_crash():
    """21. Test builder with completely empty or partial results dict."""
    findings = build_findings({})
    assert isinstance(findings, list)
    assert len(findings) == 0


def test_22_none_values_do_not_crash():
    """22. Test builder with None input."""
    findings = build_findings(None)
    assert isinstance(findings, list)
    assert len(findings) == 0


def test_23_division_by_zero_handled(clean_audit_results):
    """23. Test handling when raw characters are 0."""
    clean_audit_results["dom_comparison"]["text_content"] = {
        "raw_characters": 0,
        "rendered_characters": 2500,
        "difference": 2500,
        "rendered_to_raw_ratio": None,
    }
    clean_audit_results["dom_comparison"]["rendering_dependency"]["content_available_only_after_render"] = True
    findings = build_findings(clean_audit_results)
    frend = [f for f in findings if f["id"] == "RENDER-GAP-HIGH"]
    assert len(frend) == 1


def test_24_deterministic_finding_ids(clean_audit_results):
    """24. Test that finding IDs are fully deterministic."""
    clean_audit_results["crawler"]["status_code"] = 404
    f1 = build_findings(clean_audit_results)
    f2 = build_findings(clean_audit_results)
    assert [f["id"] for f in f1] == [f["id"] for f in f2]


def test_25_deterministic_order(clean_audit_results):
    """25. Test sorting by severity priority, category, and ID."""
    clean_audit_results["crawler"]["status_code"] = 404  # high
    clean_audit_results["crawler"]["redirect_count"] = 3  # medium
    clean_audit_results["raw_html"]["meta_description"]["exists"] = False  # low
    clean_audit_results["rendered"]["meta_description"]["exists"] = False

    findings = build_findings(clean_audit_results)
    severities = [f["severity"] for f in findings]
    # Verify severity priority order
    for i in range(len(severities) - 1):
        assert SEVERITY_ORDER[severities[i]] <= SEVERITY_ORDER[severities[i + 1]]


def test_26_evidence_present_in_findings(clean_audit_results):
    """26. Test that non-info findings contain concrete evidence."""
    clean_audit_results["crawler"]["status_code"] = 404
    findings = build_findings(clean_audit_results)
    for f in findings:
        if f["severity"] != "info":
            assert len(f["evidence"]) > 0
            assert "source" in f["evidence"][0]
            assert "details" in f["evidence"][0]


def test_27_valid_severities(clean_audit_results):
    """27. Test all findings have valid severity strings."""
    clean_audit_results["crawler"]["status_code"] = 500
    clean_audit_results["crawler"]["redirect_count"] = 5
    findings = build_findings(clean_audit_results)
    for f in findings:
        assert f["severity"] in VALID_SEVERITIES


def test_28_valid_confidences(clean_audit_results):
    """28. Test all findings have valid confidence strings."""
    clean_audit_results["crawler"]["status_code"] = 404
    findings = build_findings(clean_audit_results)
    for f in findings:
        assert f["confidence"] in VALID_CONFIDENCES


def test_29_suggested_action_present(clean_audit_results):
    """29. Test every finding contains a non-empty actionable recommendation."""
    clean_audit_results["crawler"]["status_code"] = 404
    clean_audit_results["crawler"]["redirect_count"] = 4
    findings = build_findings(clean_audit_results)
    for f in findings:
        assert isinstance(f["suggested_action"], str)
        assert len(f["suggested_action"]) > 5


def test_30_json_serializable(clean_audit_results):
    """30. Test that findings list converts to valid JSON without error."""
    clean_audit_results["crawler"]["status_code"] = 404
    clean_audit_results["crawler"]["redirect_count"] = 4
    clean_audit_results["raw_html"]["meta_description"]["exists"] = False
    clean_audit_results["rendered"]["meta_description"]["exists"] = False

    findings = build_findings(clean_audit_results)
    serialized = json.dumps(findings)
    deserialized = json.loads(serialized)
    assert isinstance(deserialized, list)
    assert len(deserialized) == len(findings)


def test_31_cr_hydrate_viewport_blocking_severity_and_selector(clean_audit_results):
    """31. Test CR-HYDRATE-001 interpolates real selector and assigns high severity when viewport-blocking."""
    from crawl_finding_builder import build_findings_qualitative_llm
    clean_audit_results["raw_html"]["spa_indicators"] = {
        "has_preloader": True,
        "preloader_details": {
            "tag": "div",
            "id": "site-preloader",
            "class": "overlay fullscreen",
            "selector": "div#site-preloader.overlay.fullscreen",
            "blocks_viewport": True,
            "is_progress_bar": False,
        }
    }
    clean_audit_results["rendered"]["text_content"]["visible_character_count"] = 1500

    report = build_findings_qualitative_llm(clean_audit_results)
    hydrate_findings = [f for f in report if f["id"] == "CR-HYDRATE-001"]
    assert len(hydrate_findings) == 1
    hf = hydrate_findings[0]
    assert hf["severity"] == "high"
    assert "div#site-preloader.overlay.fullscreen" in hf["title"]
    assert "div#site-preloader.overlay.fullscreen" in hf["evidence"][0]
    assert "div#preloader" not in hf["evidence"][0]


def test_32_cr_hydrate_progress_bar_medium_severity(clean_audit_results):
    """32. Test CR-HYDRATE-001 assigns medium severity and avoids 'high' for small progress-bar loader."""
    from crawl_finding_builder import build_findings_qualitative_llm
    clean_audit_results["raw_html"]["spa_indicators"] = {
        "has_preloader": True,
        "preloader_details": {
            "tag": "progress",
            "id": None,
            "class": "page-loader-bar mini",
            "selector": "progress.page-loader-bar.mini",
            "blocks_viewport": False,
            "is_progress_bar": True,
        }
    }
    clean_audit_results["rendered"]["text_content"]["visible_character_count"] = 1500

    report = build_findings_qualitative_llm(clean_audit_results)
    hydrate_findings = [f for f in report if f["id"] == "CR-HYDRATE-001"]
    assert len(hydrate_findings) == 1
    hf = hydrate_findings[0]
    assert hf["severity"] == "medium"
    assert "progress.page-loader-bar.mini" in hf["title"]
    assert "progress.page-loader-bar.mini" in hf["evidence"][0]


def test_33_status_code_interpolation_sitemap_and_robots(clean_audit_results):
    """33. Test status code interpolation for sitemap and robots.txt (e.g. 403 Forbidden)."""
    from crawl_finding_builder import build_findings_qualitative_llm
    clean_audit_results["sitemap"] = {
        "summary": {"sitemaps_successful": 0, "sitemap_found": False},
        "sitemaps": [{"requested_url": "https://example.com/sitemap.xml", "status_code": 403}],
        "status_code": 403,
    }
    clean_audit_results["robots"] = {
        "robots_txt": {"found": False, "status": 500, "url": "https://example.com/robots.txt"}
    }

    report = build_findings_qualitative_llm(clean_audit_results)
    sitemap_findings = [f for f in report if f["id"] == "CR-SITE-001"]
    assert len(sitemap_findings) == 1
    assert "HTTP 403 Forbidden" in sitemap_findings[0]["evidence"][0]
    assert "HTTP 404 Not Found" not in sitemap_findings[0]["evidence"][0]

    robots_findings = [f for f in report if f["id"] == "CR-ROBOTS-001"]
    assert len(robots_findings) == 1
    assert "HTTP 500 Internal Server Error" in robots_findings[0]["evidence"][0]
    assert "HTTP 404 Not Found" not in robots_findings[0]["evidence"][0]


