"""
Regression test suite for network/transport failure false positive elimination.
Tests TEST A through TEST H as specified in Round 3 requirements:

TEST A: robots.txt returns 404 -> robots missing finding may be emitted.
TEST B: robots.txt DNS failure -> no robots-missing finding.
TEST C: robots.txt timeout -> no robots-missing finding.
TEST D: sitemap returns 404 -> sitemap missing finding may be emitted.
TEST E: sitemap DNS failure -> no sitemap-missing finding.
TEST F: page returns 404 -> page-not-found behavior is allowed (HTTP-404).
TEST G: page DNS failure -> not treated as a 404 or missing page (HTTP-DNS-FAIL).
TEST H: browser launch failure -> visual checks unavailable/partial; no fabricated geometry.
"""

from __future__ import annotations

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Ensure skill scripts are in path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
for sp in [
    os.path.join(ROOT_DIR, "skills", "crawl-render-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "audit-orchestrator", "scripts"),
    os.path.join(ROOT_DIR, "skills", "visual-accessibility-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "freshness-corroboration-audit", "scripts"),
]:
    if sp not in sys.path:
        sys.path.insert(0, sp)

from crawl_finding_builder import build_findings, build_findings_qualitative_llm
from robots_checker import check_robots
from sitemap_checker import check_sitemap
from crawl_crawler import crawl_url
from audit_orchestrator import build_final_report



# TEST A: robots.txt returns 404

def test_a_robots_404_finding_allowed():
    """TEST A: robots.txt returns 404 -> robots missing finding may be emitted."""
    audit_data = {
        "crawler": {
            "success": True,
            "status_code": 200,
            "resource_state": "present",
            "final_url": "https://example.com/",
        },
        "robots": {
            "status": "404",
            "resource_state": "missing",
            "robots_txt": {
                "found": False,
                "status": "404",
                "resource_state": "missing",
                "url": "https://example.com/robots.txt",
            },
        },
        "sitemap": {
            "summary": {"sitemaps_successful": 1, "sitemap_found": True},
            "sitemaps": [{"requested_url": "https://example.com/sitemap.xml", "status_code": 200}],
            "resource_state": "present",
        },
    }
    findings = build_findings_qualitative_llm(audit_data)
    robots_findings = [f for f in findings if "CR-ROBOTS-001" in f["id"] or "robots" in f["id"].lower()]
    assert len(robots_findings) == 1
    assert "HTTP 404" in robots_findings[0]["evidence"][0]



# TEST B: robots.txt DNS failure

def test_b_robots_dns_failure_no_missing_finding():
    """TEST B: robots.txt DNS failure -> NO robots-missing finding emitted."""
    audit_data = {
        "crawler": {
            "success": True,
            "status_code": 200,
            "resource_state": "present",
            "final_url": "https://example.com/",
        },
        "robots": {
            "status": "unavailable",
            "resource_state": "unavailable",
            "error_classification": "dns_failure",
            "robots_txt": {
                "found": False,
                "status": "unavailable",
                "resource_state": "unavailable",
                "url": "https://example.com/robots.txt",
            },
        },
        "sitemap": {
            "summary": {"sitemaps_successful": 1, "sitemap_found": True},
            "resource_state": "present",
        },
    }
    # Both qualitative LLM and deterministic builder must NEVER emit missing robots
    findings_llm = build_findings_qualitative_llm(audit_data)
    robots_missing_llm = [
        f for f in findings_llm
        if "CR-ROBOTS-001" in f["id"] or ("robots" in f["title"].lower() and "missing" in f["title"].lower())
    ]
    assert len(robots_missing_llm) == 0

    findings_rule = build_findings(audit_data)
    robots_missing_rule = [
        f for f in findings_rule
        if "robots" in f["id"].lower() or ("robots" in f["title"].lower() and "missing" in f["title"].lower())
    ]
    assert len(robots_missing_rule) == 0



# TEST C: robots.txt timeout

def test_c_robots_timeout_no_missing_finding():
    """TEST C: robots.txt timeout -> NO robots-missing finding emitted."""
    audit_data = {
        "crawler": {
            "success": True,
            "status_code": 200,
            "resource_state": "present",
            "final_url": "https://example.com/",
        },
        "robots": {
            "status": "unavailable",
            "resource_state": "unavailable",
            "error_classification": "timeout",
            "robots_txt": {
                "found": False,
                "status": "unavailable",
                "resource_state": "unavailable",
                "url": "https://example.com/robots.txt",
            },
        },
        "sitemap": {
            "summary": {"sitemaps_successful": 1, "sitemap_found": True},
            "resource_state": "present",
        },
    }
    findings_llm = build_findings_qualitative_llm(audit_data)
    robots_missing_llm = [
        f for f in findings_llm
        if "CR-ROBOTS-001" in f["id"] or ("robots" in f["title"].lower() and "missing" in f["title"].lower())
    ]
    assert len(robots_missing_llm) == 0



# TEST D: sitemap returns 404

def test_d_sitemap_404_finding_allowed():
    """TEST D: sitemap returns 404 -> sitemap missing finding may be emitted."""
    audit_data = {
        "crawler": {
            "success": True,
            "status_code": 200,
            "resource_state": "present",
            "final_url": "https://example.com/",
        },
        "robots": {
            "status": "200",
            "resource_state": "present",
            "robots_txt": {"found": True, "status": 200, "url": "https://example.com/robots.txt"},
        },
        "sitemap": {
            "summary": {"sitemaps_successful": 0, "sitemap_found": False, "resource_state": "missing"},
            "sitemaps": [{"requested_url": "https://example.com/sitemap.xml", "status_code": 404, "resource_state": "missing"}],
            "status_code": 404,
            "resource_state": "missing",
        },
    }
    findings = build_findings_qualitative_llm(audit_data)
    sitemap_findings = [f for f in findings if f["id"] == "CR-SITE-001" or "sitemap" in f["title"].lower()]
    assert len(sitemap_findings) == 1
    assert "HTTP 404" in sitemap_findings[0]["evidence"][0]



# TEST E: sitemap DNS failure

def test_e_sitemap_dns_failure_no_missing_finding():
    """TEST E: sitemap DNS failure -> NO sitemap-missing finding emitted."""
    audit_data = {
        "crawler": {
            "success": True,
            "status_code": 200,
            "resource_state": "present",
            "final_url": "https://example.com/",
        },
        "robots": {
            "status": "200",
            "resource_state": "present",
            "robots_txt": {"found": True, "status": 200, "url": "https://example.com/robots.txt"},
        },
        "sitemap": {
            "summary": {"sitemaps_successful": 0, "sitemap_found": False, "resource_state": "unavailable"},
            "sitemaps": [{"requested_url": "https://example.com/sitemap.xml", "status": "network_error", "resource_state": "unavailable"}],
            "resource_state": "unavailable",
            "error_classification": "dns_failure",
        },
    }
    findings_llm = build_findings_qualitative_llm(audit_data)
    sitemap_missing_llm = [
        f for f in findings_llm
        if f["id"] == "CR-SITE-001" or ("sitemap" in f["title"].lower() and "missing" in f["title"].lower())
    ]
    assert len(sitemap_missing_llm) == 0

    findings_rule = build_findings(audit_data)
    sitemap_missing_rule = [
        f for f in findings_rule
        if f["id"] == "CR-SITE-001" or ("sitemap" in f["title"].lower() and "missing" in f["title"].lower())
    ]
    assert len(sitemap_missing_rule) == 0



# TEST F: page returns 404

def test_f_page_404_allowed():
    """TEST F: page returns 404 -> page-not-found behavior is allowed (HTTP-404)."""
    audit_data = {
        "crawler": {
            "success": False,
            "status_code": 404,
            "resource_state": "missing",
            "final_url": "https://example.com/nonexistent",
        },
        "robots": {},
        "sitemap": {},
    }
    findings = build_findings(audit_data)
    http_findings = [f for f in findings if f["id"] == "HTTP-404"]
    assert len(http_findings) == 1
    assert http_findings[0]["severity"] == "high"
    # Document content findings must NOT be generated
    assert not any(f["id"].startswith("SEM-") or f["id"].startswith("META-") for f in findings)



# TEST G: page DNS failure

def test_g_page_dns_failure_not_treated_as_404():
    """TEST G: page DNS failure -> not treated as a 404 or missing page (emits HTTP-DNS-FAIL)."""
    audit_data = {
        "crawler": {
            "success": False,
            "status_code": None,
            "resource_state": "unavailable",
            "error_classification": "dns_failure",
            "error_message": "Failed to resolve host 'nonexistent.domain'",
            "requested_url": "https://nonexistent.domain/",
        },
        "robots": {"resource_state": "unavailable"},
        "sitemap": {"resource_state": "unavailable"},
    }
    findings = build_findings(audit_data)
    assert not any(f["id"] == "HTTP-404" for f in findings)
    assert not any("404" in f.get("title", "") for f in findings)
    dns_findings = [f for f in findings if f["id"] == "HTTP-DNS-FAIL"]
    assert len(dns_findings) == 1
    assert "DNS" in dns_findings[0]["title"]
    assert dns_findings[0]["severity"] == "critical"



# TEST H: browser launch failure

def test_h_browser_launch_failure_no_fake_geometry():
    """TEST H: browser launch failure -> visual checks unavailable/partial; no fabricated geometry."""
    from va_run_audit import run_audit as run_va_audit

    with patch("va_run_audit.launch_browser", side_effect=Exception("Failed to launch headless browser: binary missing")):
        res = run_va_audit("https://example.com", {"timeout_seconds": 5})

    assert res["audit"]["status"] == "failed"
    assert len(res["findings"]["items"]) == 0
    assert any("Failed to launch headless browser" in e.get("error", "") for e in res["errors"])
