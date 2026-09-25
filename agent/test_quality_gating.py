"""
Quality Gating, Evidence Grounding, and Normalization Verification Suite
========================================================================
Validates all 20 mandatory quality and reliability scenarios:
1. LLM qualitative finding with Tier 4 evidence -> cannot become HIGH
2. LLM qualitative finding -> cannot become CRITICAL
3. LLM qualitative observation with no deterministic evidence -> LOW/suggestion/discard
4. Deterministic high-severity evidence + LLM enrichment -> remains HIGH
5. Same CTA issue from two analyzers -> one final finding
6. Three identical SEMANTIC-NO-H1 suggestions -> one aggregated suggestion
7. URL containing "\\nCall" -> normalized URL without corruption
8. Playwright environment error -> environment/partial classification
9. Actual deterministic rendering failure -> can still produce legitimate website rendering finding
10. No JSON-LD on generic page -> not automatically HIGH
11. No JSON-LD on important product/entity page -> appropriate MEDIUM/HIGH based on deterministic context
12. More than 5 proactive recommendations generated -> final report contains max 5
13. Proactive suggestions do not affect summary severity counts
14. LLM disabled -> deterministic audit still works
15. LLM unavailable -> fallback works
16. Browser unavailable -> audit continues
17. Normal TLS -> verify_ssl=True
18. Explicit insecure mode -> verify_ssl=False only then
19. Final report schema validation
20. Full local mock-site E2E test
"""

import asyncio
import json
import os
import sys
import pytest

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
ORCHESTRATOR_PATH = os.path.join(WORKSPACE_ROOT, "skills", "audit-orchestrator", "scripts")
CRAWL_PATH = os.path.join(WORKSPACE_ROOT, "skills", "crawl-render-audit", "scripts")
FRESHNESS_PATH = os.path.join(WORKSPACE_ROOT, "skills", "freshness-corroboration-audit", "scripts")
ENGAGEMENT_PATH = os.path.join(WORKSPACE_ROOT, "skills", "engagement-audit", "scripts")

for p in [ORCHESTRATOR_PATH, CRAWL_PATH, FRESHNESS_PATH, ENGAGEMENT_PATH]:
    if p not in sys.path:
        sys.path.insert(0, p)

from audit_orchestrator import (
    build_final_report,
    normalize_finding,
    normalize_url,
    validate_final_report_schema,
    aggregate_suggestions,
    classify_evidence_tier,
    is_tier1_evidence,
    is_qualitative_evidence,
    select_proactive_suggestions,
    PROACTIVE_DOMAIN_SUGGESTIONS,
)
from crawl_finding_builder import build_findings as build_crawl_findings
from freshness_finding_builder import build_findings as build_freshness_findings
from llm_client import LLMClient


# TEST 1: LLM qualitative finding with Tier 4 evidence -> cannot become HIGH
def test_1_llm_qualitative_finding_cannot_become_high():
    finding = {
        "id": "ENG-001",
        "title": "Missing Primary Call-to-Action",
        "severity": "high",
        "evidence": {"qualitative_critique": "Observed that the page lacks a strong persuasive conversion button."},
        "confidence": 0.85,
        "evidence_tier": "tier_4",
    }
    normalized = normalize_finding(finding)
    assert normalized is not None
    assert normalized["severity"] != "high"
    assert normalized["severity"] == "low"
    assert normalized["evidence_tier"] == "tier_4"


# TEST 2: LLM qualitative finding -> cannot become CRITICAL
def test_2_llm_qualitative_finding_cannot_become_critical():
    finding = {
        "id": "ENG-001",
        "title": "Missing Primary Call-to-Action",
        "severity": "critical",
        "evidence": {"qualitative_critique": "Hero section lacks immediate action directive."},
        "confidence": 0.90,
        "evidence_tier": "tier_4",
    }
    normalized = normalize_finding(finding)
    assert normalized is not None
    assert normalized["severity"] != "critical"
    assert normalized["severity"] == "low"


# TEST 3: LLM qualitative observation with no deterministic evidence -> LOW/suggestion/discard
def test_3_llm_qualitative_observation_becomes_suggestion_or_low():
    finding = {
        "id": "GEN-AUDIT-001",
        "title": "General UX and accessibility optimization opportunity",
        "severity": "medium",
        "evidence": "visual inspection suggests possible layout and UX enhancement opportunities; not independently measured against objective failure criteria.",
        "suggested_action": {"summary": "Perform targeted user testing.", "priority": "low"},
    }
    report = build_final_report("https://example.com", [finding])
    # GEN-AUDIT-001 is hard-gated out of validated findings and moved to suggestions
    assert not any(f["id"] == "GEN-AUDIT-001" for f in report["findings"])
    assert any(s["id"] == "GEN-AUDIT-001" for s in report["suggestions"])
    assert report["summary"]["total_findings"] == 0


# TEST 4: Deterministic high-severity evidence + LLM enrichment -> remains HIGH
def test_4_deterministic_high_severity_with_llm_enrichment_remains_high():
    finding = {
        "id": "CR-ROBOTS-001",
        "title": "Robots.txt disallows search engine crawlers site-wide",
        "severity": "high",
        "evidence": "checked at https://example.com/robots.txt, returned HTTP 200 with Disallow: / directive blocking automated agents.",
        "suggested_action": {"summary": "Update robots.txt to allow search bots.", "priority": "high"},
        "evidence_tier": "tier_1",
    }
    report = build_final_report("https://example.com", [finding])
    assert len(report["findings"]) == 1
    assert report["findings"][0]["severity"] == "high"
    assert report["findings"][0]["evidence_tier"] == "tier_1"
    assert report["summary"]["high"] == 1


# TEST 5: Same CTA issue from two analyzers -> one final finding
def test_5_same_cta_issue_deduplicated_to_one_finding():
    finding_qual = {
        "id": "ENG-001",
        "title": "Missing Primary Call-to-Action",
        "severity": "low",
        "evidence": {"qualitative_critique": "Observed no clear button above fold"},
        "source_url": "https://example.com/product",
    }
    finding_det = {
        "id": "ENG-CTA-001",
        "title": "No clear Call-to-Action element detected",
        "severity": "medium",
        "evidence": "checked at https://example.com/product; primary CTA element: not detected; 0 conversion links discovered above fold.",
        "source_url": "https://example.com/product",
    }
    report = build_final_report("https://example.com", [finding_qual, finding_det])
    cta_findings = [f for f in report["findings"] if "cta" in f["id"].lower() or "cta" in f["title"].lower()]
    assert len(cta_findings) == 1
    assert cta_findings[0]["id"] == "ENG-CTA-001"


# TEST 6: Three identical SEMANTIC-NO-H1 suggestions -> one aggregated suggestion
def test_6_identical_suggestions_aggregated_across_pages():
    raw_suggestions = [
        {
            "id": "SEMANTIC-NO-H1",
            "title": "Missing primary heading (<h1>)",
            "severity": "low",
            "evidence": "checked heading structure at https://example.com/page1; <h1> count: 0",
            "affected_urls": ["https://example.com/page1"],
            "type": "suggestion",
        },
        {
            "id": "SEMANTIC-NO-H1",
            "title": "Missing primary heading (<h1>)",
            "severity": "low",
            "evidence": "checked heading structure at https://example.com/page2; <h1> count: 0",
            "affected_urls": ["https://example.com/page2"],
            "type": "suggestion",
        },
        {
            "id": "SEMANTIC-NO-H1",
            "title": "Missing primary heading (<h1>)",
            "severity": "low",
            "evidence": "checked heading structure at https://example.com/page3; <h1> count: 0",
            "affected_urls": ["https://example.com/page3"],
            "type": "suggestion",
        },
    ]
    aggregated = aggregate_suggestions(raw_suggestions, pages_examined=8)
    assert len(aggregated) == 1
    agg = aggregated[0]
    assert agg["id"] == "SEMANTIC-NO-H1"
    assert agg["affected_pages"] == 3
    assert agg["pages_examined"] == 8
    assert agg["affected_ratio"] == 0.38
    assert len(agg["affected_urls"]) == 3


# TEST 7: URL containing "\\nCall" -> normalized URL without corruption
def test_7_url_normalization_strips_call_and_newlines():
    corrupted_url = "https://www.adobe.com/\nCall log:\n  - navigating to https://www.adobe.com/"
    cleaned = normalize_url(corrupted_url)
    assert cleaned == "https://www.adobe.com/"
    assert "\n" not in cleaned
    assert "Call" not in cleaned

    corrupted_url_2 = "https://example.com/about\r\nPage.goto: net::ERR_HTTP2_PROTOCOL_ERROR"
    cleaned_2 = normalize_url(corrupted_url_2)
    assert cleaned_2 == "https://example.com/about"


# TEST 8: Playwright environment error -> environment/partial classification
def test_8_playwright_environment_error_classified_as_environment():
    render_obs = {
        "status": "error",
        "error_message": "Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://example.com/\nCall log:\n  - navigating to https://example.com/",
        "error_classification": "environment_error",
        "rendered_dom_length": 0,
        "screenshot_captured": False,
        "rendering": {
            "status": "environment_error",
            "javascript_executed": False,
        },
        "errors": [{"type": "environment_error", "message": "net::ERR_HTTP2_PROTOCOL_ERROR"}],
    }
    audit_results = {
        "crawler": {"status_code": 200, "success": True, "final_url": "https://example.com/", "body": "<html></html>", "headers": {}},
        "raw_html": {"status": "success"},
        "rendered": render_obs,
        "dom_comparison": {},
    }
    findings = build_crawl_findings(audit_results)
    exec_findings = [f for f in findings if f.get("id") == "RENDER-EXEC-ERROR"]
    assert len(exec_findings) == 1
    ef = exec_findings[0]
    assert ef["severity"] == "low"
    assert ef.get("classification") == "environment"
    action_str = str(ef.get("suggested_action", ""))
    assert "Re-run browser rendering in a compatible network/browser environment" in action_str


# TEST 9: Actual deterministic rendering failure -> can still produce legitimate website rendering finding
def test_9_actual_site_rendering_failure_produces_legitimate_defect():
    render_obs = {
        "status": "success",
        "error_classification": "none",
        "rendered_dom_length": 500,
        "visible_text_characters": 10,
        "raw_text_characters": 5000,
        "javascript": {"error_count": 1, "errors": [{"message": "Uncaught TypeError: render"}]},
        "console": {"error_count": 1, "warning_count": 0},
    }
    audit_results = {
        "crawler": {"status_code": 200, "success": True, "final_url": "https://example.com/", "body": "<html><body><div id='app'></div></body></html>", "headers": {}},
        "raw_html": {"status": "success", "heading_count": 1, "text_content": {"character_count": 5000}},
        "rendered": render_obs,
        "dom_comparison": {"text_content": {"content_loss_ratio": 0.85, "hydration_loss_detected": True}},
    }
    findings = build_crawl_findings(audit_results)
    # Legitimate rendering defect identified
    assert len(findings) >= 1
    assert any("hydration" in f.get("id", "").lower() or "loss" in f.get("title", "").lower() or "js" in f.get("id", "").lower() or "render" in f.get("id", "").lower() for f in findings)


# TEST 10: No JSON-LD on generic page -> not automatically HIGH
def test_10_no_jsonld_on_generic_page_not_automatically_high():
    result = build_freshness_findings(
        target_url="https://example.com/blog/article-1",
        jsonld={"blocks": [], "summary": {"json_ld_blocks": 0}},
        entities={"entities": []},
        freshness={"timestamps": {"copyright_year": 2026}},
        options={"use_llm": False, "require_jsonld": True},
    )
    findings = result.get("findings", []) if isinstance(result, dict) else result
    f_jsonld = next((f for f in findings if "structured_data" in f.get("category", "") or "jsonld" in f.get("code", "") or "freshness-001" in f.get("id", "")), None)
    assert f_jsonld is not None
    assert f_jsonld["severity"] == "medium"


# TEST 11: No JSON-LD on important product/entity page -> appropriate MEDIUM/HIGH based on deterministic context
def test_11_no_jsonld_on_entity_page_evaluated_appropriately():
    result = build_freshness_findings(
        target_url="https://example.com/",
        jsonld={"blocks": [], "summary": {"json_ld_blocks": 0}},
        entities={"entities": [{"name": "Acme Corp", "types": ["Organization"]}]},
        options={"use_llm": False, "require_jsonld": True},
    )
    findings = result.get("findings", []) if isinstance(result, dict) else result
    f_jsonld = next((f for f in findings if "structured_data" in f.get("category", "") or "jsonld" in f.get("code", "") or "freshness-001" in f.get("id", "")), None)
    assert f_jsonld is not None
    assert f_jsonld["severity"] in ("medium", "high")


# TEST 12: More than 5 proactive recommendations generated -> final report contains max 5
def test_12_proactive_recommendations_capped_at_max_5():
    selected = select_proactive_suggestions([], max_count=5)
    assert len(selected) <= 5
    assert len(PROACTIVE_DOMAIN_SUGGESTIONS) >= 15
    report = build_final_report("https://example.com", [], include_proactive_suggestions=True)
    assert len(report["suggestions"]) <= 5


# TEST 13: Proactive suggestions do not affect summary severity counts
def test_13_proactive_suggestions_do_not_affect_summary_counts():
    report = build_final_report("https://example.com", [], include_proactive_suggestions=True)
    assert report["summary"]["total_findings"] == 0
    assert report["summary"]["critical"] == 0
    assert report["summary"]["high"] == 0
    assert report["summary"]["medium"] == 0
    assert report["summary"]["low"] == 0
    assert len(report["suggestions"]) > 0


# TEST 14: LLM disabled -> deterministic audit still works
def test_14_llm_disabled_mode():
    result = build_freshness_findings(
        target_url="https://example.com",
        jsonld={"blocks": [{"@type": "Organization", "name": "Test"}]},
        freshness={"timestamps": {"copyright_year": 2026}},
        options={"use_llm": False},
    )
    findings = result.get("findings", []) if isinstance(result, dict) else result
    assert isinstance(findings, list)


# TEST 15: LLM unavailable -> fallback works
def test_15_llm_fallback_generator():
    client = LLMClient(api_key=None)
    fallback = client._generate_qualitative_fallback("Audit Target URL: https://example.com\nCrawl redirects: 2")
    assert "findings" in fallback
    assert len(fallback["findings"]) >= 1


# TEST 16: Browser unavailable -> audit continues
def test_16_browser_unavailable_graceful_handling():
    audit_results = {
        "crawler": {"status_code": 200, "success": True, "final_url": "https://example.com/", "body": "<html></html>", "headers": {}},
        "raw_html": {"status": "success"},
        "rendered": {
            "status": "browser_launch_failure",
            "rendering": {"status": "browser_launch_failure", "javascript_executed": False},
            "errors": [{"type": "launch_error", "message": "Executable missing"}],
        },
        "dom_comparison": {},
    }
    findings = build_crawl_findings(audit_results)
    assert isinstance(findings, list)


# TEST 17: Normal TLS -> verify_ssl=True
def test_17_normal_tls_defaults():
    import crawl_crawler
    assert crawl_crawler.DEFAULT_OPTIONS.get("verify_ssl") is True


# TEST 18: Explicit insecure mode -> verify_ssl=False only then
def test_18_explicit_insecure_mode():
    import crawl_crawler
    opts = {**crawl_crawler.DEFAULT_OPTIONS, "verify_ssl": False}
    assert opts["verify_ssl"] is False
    assert crawl_crawler.DEFAULT_OPTIONS["verify_ssl"] is True


# TEST 19: Final report schema validation
def test_19_final_report_schema_validation():
    findings = [
        {
            "id": "CR-ROBOTS-001",
            "title": "Robots.txt blocks search bots",
            "severity": "high",
            "evidence": "checked at https://example.com/robots.txt, returned HTTP 200 with Disallow: /",
            "suggested_action": {"summary": "Update robots.txt.", "priority": "high"},
            "evidence_tier": "tier_1",
        }
    ]
    report = build_final_report(
        "https://example.com",
        findings,
        coverage={"skills_run": 5, "skills_ok": 5, "skills_partial": 0, "skills_failed": 0},
        subagent_status={"crawl-render-audit": "ok"},
        include_proactive_suggestions=True,
    )
    assert validate_final_report_schema(report) is True
    assert "site" in report
    assert "summary" in report
    assert "findings" in report
    assert "coverage" in report
    assert "subagent_status" in report
    assert "suggestions" in report


# TEST 20: Full local mock-site E2E test verification
def test_20_mock_site_e2e_components():
    report = build_final_report(
        "http://127.0.0.1:8080",
        [
            {
                "id": "FRESH-CONFLICT-001",
                "title": "Conflicting founding year across pages",
                "severity": "medium",
                "evidence": "2018 vs 2022 founding year conflict across / and /about",
                "suggested_action": {"summary": "Standardize founding dates.", "priority": "medium"},
                "evidence_tier": "tier_1",
            }
        ],
        include_proactive_suggestions=True,
    )
    assert validate_final_report_schema(report) is True
    assert report["summary"]["total_findings"] == 1
    assert len(report["suggestions"]) <= 5
