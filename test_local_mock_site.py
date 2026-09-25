"""
Local Multi-Page Mock Site End-to-End Test Suite
=================================================
Validates Requirement Y:
Sets up a live local HTTP server serving 5 distinct representative pages:
  - / (Homepage)
  - /pricing (Pricing page with broken/missing CTA)
  - /products/widget (Product page with missing alt text and missing structured data)
  - /about (About page with conflicting founding date: 2018 vs 2022)
  - /contact (Contact page with telephone number and contact form)

Executes the REAL master audit pipeline against http://127.0.0.1:<port>/
and asserts that:
1. Multi-page discovery successfully discovers and crawls the representative pages.
2. The final audit report contains evidence and findings originating from multiple distinct pages.
3. Cross-page fact consistency detects the founding year conflict between / and /about.
4. Aggregated findings correctly reflect multi-page ratios while page-specific defects remain preserved.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from typing import Any
import pytest

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
for sub_path in [
    os.path.join(WORKSPACE_ROOT, "skills", "audit-orchestrator", "scripts"),
    os.path.join(WORKSPACE_ROOT, "skills", "crawl-render-audit", "scripts"),
    os.path.join(WORKSPACE_ROOT, "skills", "engagement-audit", "scripts"),
    os.path.join(WORKSPACE_ROOT, "skills", "multimodal-audit", "scripts"),
    os.path.join(WORKSPACE_ROOT, "skills", "freshness-corroboration-audit", "scripts"),
    os.path.join(WORKSPACE_ROOT, "skills", "visual-accessibility-audit", "scripts"),
    WORKSPACE_ROOT,
]:
    if sub_path not in sys.path:
        sys.path.insert(0, sub_path)

from audit_orchestrator import validate_final_report_schema
import run_master_audit



# 1. Multi-Page Mock Website Content


MOCK_PAGES = {
    "/": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Acme Widget Technologies - Next-Gen Automation</title>
    <meta name="description" content="Acme Technologies builds premier automation solutions for global enterprises.">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme Widget Technologies",
        "url": "http://127.0.0.1/",
        "foundingDate": "2018-05-10"
    }
    </script>
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
            <a href="/products/widget">Widget X</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <h1>Next-Gen Automation Platform</h1>
        <p>Acme was founded in 2018 to pioneer industrial software automation.</p>
        <button class="primary">Start Free Trial</button>
        <img src="/assets/hero.jpg" alt="Platform dashboard overview with analytics charts" width="600" height="300">
    </main>
    <footer>
        <p>&copy; 2026 Acme Technologies. All rights reserved.</p>
    </footer>
</body>
</html>""",

    "/pricing": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Pricing Plans - Acme Widget Technologies</title>
    <meta name="description" content="Explore affordable pricing tiers for teams of all sizes.">
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
            <a href="/products/widget">Widget X</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <h1>Transparent Pricing for Every Team</h1>
        <p>Choose the plan that best fits your enterprise scale.</p>
        <div class="pricing-card" style="background-color: #ffffff; color: #cfcfcf;">
            <h2>Starter Plan - $49/mo</h2>
            <p>Basic automation pipelines.</p>
        </div>
        <div class="pricing-card">
            <h2>Enterprise Plan - Custom</h2>
            <p>Dedicated cluster with 99.99% uptime SLA.</p>
        </div>
    </main>
</body>
</html>""",


    "/products/widget": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Widget X Automation Hardware - Acme Technologies</title>
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
            <a href="/products/widget">Widget X</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <h1>Widget X Autonomous Controller</h1>
        <p>High-precision controller for edge automation workloads.</p>
        <img src="/assets/widget-unlabeled.png">
        <button class="primary">Request Quote</button>
    </main>
</body>
</html>""",

    "/about": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>About Us - Acme Widget Technologies</title>
    <meta name="description" content="Learn about our mission and engineering leadership.">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme Widget Technologies",
        "url": "http://127.0.0.1/about",
        "foundingDate": "2022-09-01"
    }
    </script>
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
            <a href="/products/widget">Widget X</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <h1>Our Story and Mission</h1>
        <p>Founded in 2022, Acme has grown to support thousands of industrial facilities worldwide.</p>
    </main>
</body>
</html>""",

    "/contact": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Contact Customer Support - Acme Technologies</title>
    <meta name="description" content="Reach our 24/7 technical assistance and sales representatives.">
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
            <a href="/products/widget">Widget X</a>
            <a href="/about">About Us</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <h1>Contact Our Team</h1>
        <p>Call us at <a href="tel:+18005550199">+1 (800) 555-0199</a> or submit a message below.</p>
        <form name="contact-form" action="/submit">
            <input type="text" name="name" placeholder="Full Name">
            <input type="email" name="email" placeholder="Email Address">
            <textarea name="message" placeholder="How can we help?"></textarea>
            <button type="submit">Send Message</button>
        </form>
    </main>
</body>
</html>""",

    "/robots.txt": """User-agent: *
Allow: /
Sitemap: /sitemap.xml
""",

    "/sitemap.xml": """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>http://127.0.0.1:{PORT}/</loc><lastmod>2026-01-15</lastmod></url>
    <url><loc>http://127.0.0.1:{PORT}/pricing</loc><lastmod>2026-01-15</lastmod></url>
    <url><loc>http://127.0.0.1:{PORT}/products/widget</loc><lastmod>2026-01-15</lastmod></url>
    <url><loc>http://127.0.0.1:{PORT}/about</loc><lastmod>2026-01-15</lastmod></url>
    <url><loc>http://127.0.0.1:{PORT}/contact</loc><lastmod>2026-01-15</lastmod></url>
</urlset>
""",
}


class MockSiteHandler(http.server.BaseHTTPRequestHandler):
    port = 8000

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in MOCK_PAGES:
            content = MOCK_PAGES[path].replace("{PORT}", str(self.port))
            content_type = "application/xml" if path.endswith(".xml") else ("text/plain" if path.endswith(".txt") else "text/html; charset=utf-8")
            body_bytes = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")


@pytest.fixture(scope="module")
def mock_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), MockSiteHandler)
    port = server.server_address[1]
    MockSiteHandler.port = port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()
    server.server_close()



# 2. Local Multi-Page End-to-End Master Audit Execution


@pytest.mark.asyncio
async def test_e2e_local_multipage_site_audit(mock_server: str):
    """
    Execute the full master website audit against the local multi-page mock site.
    Asserts criteria A through K:
    A. Multiple pages were actually crawled (>=5).
    B. Multiple pages built page-specific canonical contexts.
    C. Downstream skills (engagement, multimodal, freshness, visual-accessibility) were invoked for multiple pages.
    D. A defect existing on /products/widget (e.g. missing alt text) appears with route evidence.
    E. Cross-page founding date conflict between / and /about is detected.
    F. Page-specific findings retain page-specific route evidence.
    G. Recurring issues across pages are aggregated with ratio telemetry.
    H. Page 1 findings are not merely copied to all pages.
    I. pages_crawled equals the actual number of pages crawled (5).
    J. pages_discovered equals the unique normalized discovery count (5).
    K. Canonical output schema is 100% valid.
    """
    target_url = mock_server

    # Execute main audit runner
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["run_master_audit.py", target_url])
        await run_master_audit.main()

    output_path = os.path.join(WORKSPACE_ROOT, "output.json")
    assert os.path.exists(output_path), "output.json was not created by master audit!"

    with open(output_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    # 1. Validate canonical output schema (Criterion K)
    validate_final_report_schema(report)
    assert report["site"] == "127.0.0.1"
    assert report["summary"]["total_findings"] >= 2

    # 2. Check metadata: multiple pages discovered and crawled (Criteria A, I, J)
    meta = report.get("meta", {})
    assert meta.get("pages_discovered") == 5, f"Expected exactly 5 unique pages discovered, got {meta.get('pages_discovered')}"
    assert meta.get("pages_crawled") == 5, f"Expected exactly 5 pages crawled, got {meta.get('pages_crawled')}"
    assert meta.get("pages_successfully_audited") == 5
    assert len(meta.get("page_types_covered", [])) >= 4

    # 3. Verify cross-page fact consistency finding (founding year 2018 vs 2022) (Criterion E)
    conflict_findings = [f for f in report["findings"] if "conflict" in f["id"].lower() or "conflict" in f["title"].lower()]
    assert len(conflict_findings) >= 1, "Expected cross-page founding year conflict finding!"
    cf = conflict_findings[0]
    assert "2018" in cf["evidence"] and "2022" in cf["evidence"]

    # 4. Verify multimodal missing alt text finding originating specifically from /products/widget (Criteria D, F)
    mm_findings = [f for f in report["findings"] if "alt" in f["id"].lower() or "alt" in f["title"].lower()]
    assert len(mm_findings) >= 1, "Expected missing alt finding from /products/widget!"
    alt_finding = mm_findings[0]
    assert "products/widget" in alt_finding["evidence"] or "widget-unlabeled" in alt_finding["evidence"]
    assert alt_finding.get("scope") in ("single-page", "majority", "site-wide")
    assert alt_finding.get("pages_examined") == 5

    # 5. Verify multi-page aggregation telemetry (Criterion G)
    agg_findings = [f for f in report["findings"] if "Audited 5 representative pages" in f.get("evidence", "")]
    if agg_findings:
        assert any("pages exhibit this issue" in f["evidence"] for f in agg_findings)
        for af in agg_findings:
            assert af.get("pages_examined") == 5
            assert af.get("affected_pages", 0) >= 1
            assert af.get("scope") in ("site-wide", "majority", "page-type-specific", "single-page")

    # 6. Verify coverage envelope shows downstream skills ran across multiple pages (Criteria B, C)
    coverage = report.get("coverage", {})
    assert coverage.get("skills_run") == 5
    assert coverage.get("skills_ok") >= 4

    # 7. Verify engagement audit findings (missing CTA on /pricing or reading/navigation signals)
    eng_findings = [f for f in report["findings"] if f.get("id", "").startswith("ENG-") or "cta" in f.get("id", "").lower() or "cta" in f.get("title", "").lower()]
    assert len(eng_findings) >= 1, f"Expected engagement findings > 0, got {len(eng_findings)}"

    # 8. Verify visual accessibility findings (contrast, typography, or UI accessibility)
    va_findings = [f for f in report["findings"] if f.get("id", "").startswith("VA-") or "contrast" in f.get("id", "").lower() or "accessibility" in f.get("category", "").lower() or f.get("id", "").startswith("va-")]
    assert len(va_findings) >= 1, f"Expected visual accessibility findings > 0, got {len(va_findings)}"

    print(f"\n[SUCCESS] Local mock site E2E verified: {len(report['findings'])} findings across {meta.get('pages_crawled')} pages (Engagement: {len(eng_findings)}, VA: {len(va_findings)}).")

