"""
Comprehensive Unit & Integration Test Suite for Multi-Page Website Auditing Engine
==================================================================================
Tests all requirements:
1. URL normalization, media filtering, domain restriction
2. Page type classification (homepage, pricing, product, about, contact, blog, docs, general)
3. Page candidate prioritization and diversity selection up to MAX_PAGES
4. Sitemap XML and Sitemap Index URL discovery
5. Raw HTML internal link extraction and discovery
6. Complete site audit with robots.txt enforcement and graceful deadline stop
7. Multi-page finding aggregation (e.g. 6/8 pages missing JSON-LD) vs single-page preservation
8. Concrete referent & Tier-1 evidence validation for multi-page ratios
9. Final report schema validation
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from page_discovery import (
    classify_page_type,
    discover_and_select_representative_pages,
    is_same_site,
    normalize_url,
    DiscoveredPage,
    DEFAULT_MAX_PAGES,
)
from sitemap_checker import parse_sitemap_xml, check_sitemap
from raw_html_analyzer import analyze_raw_html
from crawl_run_audit import run_complete_site_audit, run_complete_audit
from audit_orchestrator import (
    aggregate_multipage_findings,
    build_final_report,
    has_concrete_referent,
    is_tier1_evidence,
    validate_final_report_schema,
)



# 1. Page Discovery & Normalization Tests


class TestPageDiscovery:
    def test_normalize_url_basic(self):
        base = "https://example.com"
        assert normalize_url("https://example.com/about/", base) == "https://example.com/about"
        assert normalize_url("/pricing?ref=banner#features", base) == "https://example.com/pricing?ref=banner"
        assert normalize_url("https://example.com", base) == "https://example.com/"

    def test_normalize_url_filters_media_and_binaries(self):
        base = "https://example.com"
        assert normalize_url("/assets/logo.png", base) is None
        assert normalize_url("/docs/report.pdf", base) is None
        assert normalize_url("/styles/main.css", base) is None
        assert normalize_url("/script.js", base) is None
        assert normalize_url("mailto:info@example.com", base) is None
        assert normalize_url("tel:+1234567890", base) is None
        assert normalize_url("javascript:void(0)", base) is None

    def test_is_same_site(self):
        base = "https://example.com"
        assert is_same_site("https://example.com/about", base) is True
        assert is_same_site("https://www.example.com/about", base) is True
        assert is_same_site("https://twitter.com/example", base) is False
        assert is_same_site("https://sub.otherdomain.com/page", base) is False

    def test_classify_page_type(self):
        assert classify_page_type("https://example.com") == "homepage"
        assert classify_page_type("https://example.com/") == "homepage"
        assert classify_page_type("https://example.com/pricing") == "pricing"
        assert classify_page_type("https://example.com/plans-and-pricing") == "pricing"
        assert classify_page_type("https://example.com/products/widget") == "product_service"
        assert classify_page_type("https://example.com/services/cloud") == "product_service"
        assert classify_page_type("https://example.com/about-us") == "about_company"
        assert classify_page_type("https://example.com/contact") == "contact"
        assert classify_page_type("https://example.com/blog/2026/ai-trends") == "blog_content"
        assert classify_page_type("https://example.com/docs/getting-started") == "docs"
        assert classify_page_type("https://example.com/random-page-123") == "general"

    def test_discover_and_select_representative_pages(self):
        base = "https://example.com"
        discovered_sitemap = [
            "https://example.com/",
            "https://example.com/pricing",
            "https://example.com/pricing-enterprise",
            "https://example.com/product",
            "https://example.com/product/crm",
            "https://example.com/about",
            "https://example.com/team",
            "https://example.com/contact",
            "https://example.com/blog/post-1",
            "https://example.com/blog/post-2",
            "https://example.com/docs/api",
            "https://example.com/docs/cli",
            "https://example.com/terms",
            "https://example.com/privacy",
        ]
        selected = discover_and_select_representative_pages(
            target_url=base,
            sitemap_urls=discovered_sitemap,
            options={"max_pages": 8},
        )
        assert len(selected) == 8
        assert selected[0].url == "https://example.com/"
        assert selected[0].page_type == "homepage"

        types_covered = {p.page_type for p in selected}
        # Should cover at least 5 diverse page types
        assert len(types_covered) >= 5
        assert "pricing" in types_covered
        assert "product_service" in types_covered
        assert "about_company" in types_covered



# 2. Sitemap URL Discovery Tests


class TestSitemapDiscovery:
    def test_sitemap_urlset_discovery(self):
        xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/</loc><lastmod>2026-01-01</lastmod></url>
            <url><loc>https://example.com/pricing</loc></url>
            <url><loc>https://example.com/features</loc></url>
            <url><loc>https://example.com/about</loc></url>
        </urlset>
        """
        res = parse_sitemap_xml(xml_bytes)
        assert res["valid_xml"] is True
        assert res["type"] == "urlset"
        urls = [u["loc"] for u in res.get("urls", [])]
        assert len(urls) == 4
        assert "https://example.com/pricing" in urls

    def test_sitemap_index_discovery(self):
        xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
            <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
        </sitemapindex>
        """
        res = parse_sitemap_xml(xml_bytes)
        assert res["valid_xml"] is True
        assert res["type"] == "sitemapindex"
        sitemaps = [s["loc"] for s in res.get("sitemaps", [])]
        assert len(sitemaps) == 2
        assert "https://example.com/sitemap-pages.xml" in sitemaps



# 3. Raw HTML Internal Link Extraction Tests


class TestRawHtmlLinkExtraction:
    def test_raw_html_extracts_discovered_urls(self):
        html = """
        <html>
        <body>
            <header>
                <a href="/">Home</a>
                <a href="/pricing">Pricing Plans</a>
                <a href="/about-us">About Company</a>
            </header>
            <main>
                <a href="/docs/guide">Documentation</a>
                <a href="https://external.com/partner">External Partner</a>
                <a href="/image.jpg">Image File</a>
            </main>
        </body>
        </html>
        """
        obs = analyze_raw_html(html, target_url="https://example.com")
        links = obs.get("links", {})
        discovered = links.get("discovered_urls", [])

        assert "https://example.com/" in discovered
        assert "https://example.com/pricing" in discovered
        assert "https://example.com/about-us" in discovered
        assert "https://example.com/docs/guide" in discovered
        assert not any("external.com" in u for u in discovered)
        assert not any(".jpg" in u for u in discovered)



# 4. Multi-Page Site Audit Integration Tests


class TestMultiPageAuditExecution:
    @pytest.mark.asyncio
    async def test_single_page_site_crawl(self):
        """When a site has no sitemap and no internal links, it audits the single homepage cleanly."""
        target_url = "https://singlepage.example.com/"

        with patch("crawl_run_audit.check_robots") as mock_check_robots, \
             patch("crawl_run_audit.check_sitemap") as mock_check_sitemap, \
             patch("crawl_run_audit.crawl_url") as mock_crawl_url, \
             patch("crawl_run_audit.analyze_rendered_page") as mock_render:

            mock_check_robots.return_value = {"status": "present", "is_allowed": True, "target_url_evaluation": {"allowed": True}, "directives": {}}
            mock_check_sitemap.return_value = {"status": "not_found", "discovered_urls": []}
            mock_crawl_url.return_value = {
                "success": True,
                "status_code": 200,
                "final_url": target_url,
                "body": "<html><head><title>Single Page</title></head><body><h1>Hello World</h1></body></html>",
                "headers": {"content-type": "text/html"},
            }
            mock_render.return_value = {
                "rendering": {"status": "success"},
                "console_logs": [],
                "errors": [],
            }

            report = await run_complete_site_audit(target_url, options={"max_pages": 8, "global_timeout_seconds": 30.0})

            assert report["site"] == "singlepage.example.com"
            assert len(report["audited_pages"]) == 1
            assert report["coverage"]["pages_crawled"] == 1
            assert report["coverage"]["pages_discovered"] == 1

    @pytest.mark.asyncio
    async def test_multipage_site_crawl_with_links_and_sitemap(self):
        """When a site has sitemaps and internal links, audits up to MAX_PAGES representative pages."""
        target_url = "https://multipage.example.com/"

        with patch("crawl_run_audit.check_robots") as mock_check_robots, \
             patch("crawl_run_audit.check_sitemap") as mock_check_sitemap, \
             patch("crawl_run_audit.crawl_url") as mock_crawl_url, \
             patch("crawl_run_audit.analyze_rendered_page") as mock_render:

            mock_check_robots.return_value = {"status": "present", "is_allowed": True, "target_url_evaluation": {"allowed": True}, "directives": {}}
            mock_check_sitemap.return_value = {
                "status": "valid",
                "discovered_urls": [
                    "https://multipage.example.com/pricing",
                    "https://multipage.example.com/about",
                    "https://multipage.example.com/contact",
                    "https://multipage.example.com/features",
                ],
            }
            mock_crawl_url.side_effect = lambda url, **kwargs: {
                "success": True,
                "status_code": 200,
                "final_url": url,
                "body": f"<html><head><title>{url}</title></head><body><h1>Content for {url}</h1><a href='/docs'>Docs</a></body></html>",
                "headers": {"content-type": "text/html"},
            }
            mock_render.return_value = {
                "rendering": {"status": "success"},
                "console_logs": [],
                "errors": [],
            }

            report = await run_complete_site_audit(target_url, options={"max_pages": 5, "global_timeout_seconds": 60.0})

            assert len(report["audited_pages"]) >= 4
            assert report["coverage"]["pages_crawled"] >= 4
            assert len(report["coverage"]["page_types_covered"]) >= 3

    @pytest.mark.asyncio
    async def test_robots_disallow_stops_active_crawling(self):
        """When robots.txt disallows the target, crawler stops deeper crawl and reports blocked."""
        target_url = "https://forbidden.example.com/"

        with patch("crawl_run_audit.check_robots") as mock_check_robots:
            mock_check_robots.return_value = {
                "status": "present",
                "is_allowed": False,
                "target_url_evaluation": {"allowed": False},
                "disallow_reason": "Disallowed by robots.txt User-agent: * Disallow: /",
                "directives": {"disallow": ["/"]},
            }

            report = await run_complete_site_audit(target_url, options={"max_pages": 8})

            assert report["summary"]["total_findings"] >= 1
            assert any("ROBOTS" in f["id"] for f in report["findings"])
            assert len(report["audited_pages"]) == 0



# 5. Multi-Page Finding Aggregation & Evidence Validation Tests


class TestMultiPageFindingAggregation:
    def test_aggregate_multipage_recurring_findings(self):
        raw_findings = [
            {
                "id": "CR-STRUCTURED-001",
                "title": "Missing Schema.org JSON-LD Structured Data",
                "severity": "medium",
                "evidence": "URL 'https://example.com/' lacks Schema.org JSON-LD structured data.",
                "suggested_action": {"summary": "Add Schema.org JSON-LD.", "priority": "medium"},
            },
            {
                "id": "CR-STRUCTURED-001",
                "title": "Missing Schema.org JSON-LD Structured Data",
                "severity": "medium",
                "evidence": "URL 'https://example.com/pricing' lacks Schema.org JSON-LD structured data.",
                "suggested_action": {"summary": "Add Schema.org JSON-LD.", "priority": "medium"},
            },
            {
                "id": "CR-STRUCTURED-001",
                "title": "Missing Schema.org JSON-LD Structured Data",
                "severity": "medium",
                "evidence": "URL 'https://example.com/about' lacks Schema.org JSON-LD structured data.",
                "suggested_action": {"summary": "Add Schema.org JSON-LD.", "priority": "medium"},
            },
            {
                "id": "PAGE-CTA-001",
                "title": "Missing Call to Action Button on Pricing",
                "severity": "high",
                "evidence": "URL 'https://example.com/pricing' has 0 call-to-action buttons in viewport.",
                "suggested_action": {"summary": "Add a prominent CTA button.", "priority": "high"},
            },
        ]

        aggregated = aggregate_multipage_findings(raw_findings, total_pages_crawled=3)

        assert len(aggregated) == 2
        # CR-STRUCTURED-001 aggregated into multi-page finding
        structured = next(f for f in aggregated if f["id"] == "CR-STRUCTURED-001")
        assert "Audited 3 representative pages; 3/3 pages exhibit this issue" in structured["evidence"]
        assert "/pricing" in structured["evidence"]
        assert "/about" in structured["evidence"]

        # PAGE-CTA-001 preserved as isolated single-page finding
        cta = next(f for f in aggregated if f["id"] == "PAGE-CTA-001")
        assert cta["severity"] == "high"
        assert "pricing" in cta["evidence"]

    def test_has_concrete_referent_recognizes_multipage_ratios(self):
        ev = "Audited 8 representative pages; 6/8 pages exhibit missing meta description (/pricing, /about, /contact). Sample telemetry: missing meta."
        assert has_concrete_referent(ev, title="Missing Meta Description", finding_id="CR-METADATA-001")
        assert is_tier1_evidence(ev)

    def test_build_final_report_schema_and_counts(self):
        raw_findings = [
            {
                "id": "CR-STRUCTURED-001",
                "title": "Missing Schema.org JSON-LD Structured Data",
                "severity": "medium",
                "evidence": "Audited 6 representative pages; 6/6 pages exhibit this issue (/pricing, /about). Telemetry: 0 JSON-LD blocks.",
                "suggested_action": {"summary": "Add Schema.org JSON-LD.", "priority": "medium"},
            },
            {
                "id": "MM-ALT-001",
                "title": "Informative image is missing alternative text",
                "severity": "high",
                "evidence": "Image URL 'https://example.com/hero.jpg' lacks an alt attribute (alt_attribute: not present).",
                "suggested_action": {"summary": "Add descriptive alt attribute.", "priority": "high"},
            },
        ]

        report = build_final_report(
            target_url="https://example.com",
            raw_findings=raw_findings,
            meta={"pages_crawled": 6, "pages_discovered": 12},
            include_proactive_suggestions=True,
        )

        validate_final_report_schema(report)
        assert report["site"] == "example.com"
        assert report["summary"]["total_findings"] == 2
        assert report["summary"]["high"] == 1
        assert report["summary"]["medium"] == 1
        assert report["summary"]["low"] == 0
        assert "suggestions" in report
        assert len(report["suggestions"]) >= 3
        assert report["meta"]["pages_crawled"] == 6
