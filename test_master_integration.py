"""
Master Integration Test Suite for Adobe Hackathon 2026 Round 3
===============================================================
Comprehensive validation of:
- Exact Marketplace Entrypoint & Discovery (Section 8)
- Canonical Evidence Pipeline (Section 1)
- Engagement Audit Integration (Section 2)
- Multimodal Evidence Pipeline (Section 3)
- Truthful Failure Status Propagation (Section 4)
- Explicit Evidence Tiers (Section 5)
- Determinism & Safety (Section 9, 10)
- Scenarios A through K (Section 7)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
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

from audit_orchestrator import (
    build_final_report,
    normalize_finding,
    normalize_site_identifier,
    validate_final_report_schema,
    classify_evidence_tier,
)
from canonical_evidence import (
    CanonicalAuditContext,
    build_canonical_context_from_html,
)
import run_master_audit



# Test Fixtures & HTML Payloads


CLEAN_FIXTURE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Enterprise Cloud Infrastructure</title>
    <meta name="description" content="State of the art cloud computing and enterprise intelligence platform.">
    <link rel="canonical" href="https://example.com/">
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "@id": "https://example.com/#org",
      "name": "Enterprise Cloud Inc",
      "url": "https://example.com",
      "sameAs": ["https://linkedin.com/company/enterprise-cloud"]
    }
    </script>
</head>
<body>
    <header>
        <h1>Enterprise Cloud Infrastructure</h1>
        <nav aria-label="Main Navigation">
            <a href="/">Home</a>
            <a href="/solutions">Solutions</a>
            <a href="/pricing">Pricing</a>
            <a href="/contact">Contact</a>
        </nav>
    </header>
    <main>
        <section id="hero">
            <h2>Accelerate Your AI Workloads</h2>
            <p>Deploy mission-critical architectures with sub-millisecond latency and automatic scaling.</p>
            <button class="primary" role="button">Get Started Free</button>
        </section>
        <section id="media">
            <img src="/assets/diagram.png" alt="Architecture deployment diagram illustrating cluster topology" width="800" height="400">
            <img src="/assets/partner.png" alt="Certified Technology Partner Logo" width="160" height="40">
        </section>
        <form id="newsletter-form" name="newsletter">
            <input type="email" name="email" placeholder="Enter work email">
            <button type="submit">Subscribe</button>
        </form>
    </main>
    <footer>
        <p>&copy; 2026 Enterprise Cloud Inc. All rights reserved.</p>
    </footer>
</body>
</html>"""

DEFECT_RICH_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Defective Legacy Portal</title>
</head>
<body>
    <div>
        <h1>Welcome</h1>
        <h4>Skipped Heading Level 4</h4>
        <p>Short paragraph.</p>
        <a href="#section1">Anchor Only Link 1</a>
        <a href="#section2">Anchor Only Link 2</a>
        <a href="javascript:void(0)">Click Here</a>
        <a href="javascript:void(0)">Learn More</a>
        <a href="javascript:void(0)">Click Here</a>
        <a href="javascript:void(0)">Read More</a>
        <img src="/img/broken.jpg">
        <img src="/img/badge.png" alt="">
        <div class="modal-overlay">
            <div class="modal-content">
                <p>Urgent Promo!</p>
            </div>
        </div>
        <form id="mega-form">
            <input name="f1"><input name="f2"><input name="f3"><input name="f4">
            <input name="f5"><input name="f6"><input name="f7"><input name="f8">
            <input name="f9"><input name="f10">
        </form>
    </div>
</body>
</html>"""

MALFORMED_HTML = """<html lang="en"><head><title>Malformed Document<div><p>Broken structure<b>no close tag<a href>bad link"""



# 8. MASTER ENTRYPOINT TESTS


def test_marketplace_manifest_and_single_entrypoint():
    """Verify marketplace.json has exactly one designated entrypoint matching spec."""
    mpath = os.path.join(WORKSPACE_ROOT, "marketplace.json")
    assert os.path.exists(mpath), "marketplace.json missing at root!"

    with open(mpath, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "name" in manifest
    assert "version" in manifest
    assert "skills" in manifest
    assert isinstance(manifest["skills"], list)

    entrypoints = [s for s in manifest["skills"] if s.get("entrypoint") is True]
    assert len(entrypoints) == 1, f"Marketplace must have exactly ONE entrypoint, found {len(entrypoints)}!"
    
    ep = entrypoints[0]
    assert ep["id"] == "audit-orchestrator"
    ep_path = os.path.join(WORKSPACE_ROOT, ep["path"])
    assert os.path.isdir(ep_path), f"Entrypoint path does not exist: {ep_path}"

    skill_md = os.path.join(ep_path, "SKILL.md")
    assert os.path.isfile(skill_md), f"SKILL.md missing in entrypoint {ep_path}"



# 7. INTEGRATION SCENARIOS A - K


@pytest.mark.asyncio
async def test_scenario_a_successful_complete_audit():
    """SCENARIO A: Successful complete audit flows through canonical evidence and outputs valid JSON."""
    target_url = "https://example.com/"
    
    with patch("run_master_audit.run_crawl_audit") as mock_crawl, \
         patch("run_master_audit.run_freshness_audit") as mock_fresh, \
         patch("run_master_audit.run_va_audit") as mock_va:

        mock_crawl.return_value = {
            "status": "ok",
            "findings": [],
            "raw_observations": {
                "crawler": {"status_code": 200, "success": True, "body": CLEAN_FIXTURE_HTML, "final_url": target_url},
                "robots": {"target_url_evaluation": {"allowed": True}},
                "sitemap": {"sitemaps": ["https://example.com/sitemap.xml"]},
                "rendered": {
                    "status": "ok",
                    "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
                },
            }
        }
        mock_fresh.return_value = {
            "audit_metadata": {"status": "completed"},
            "findings": [],
            "structured_data": {"blocks": 1},
        }
        mock_va.return_value = {
            "audit": {"status": "passed"},
            "findings": [],
            "coverage": {"contrast": "completed", "keyboard": "completed"},
        }

        with patch.object(sys, "argv", ["run_master_audit.py", target_url]):
            await run_master_audit.main()

    output_path = os.path.join(WORKSPACE_ROOT, "output.json")
    assert os.path.exists(output_path)
    with open(output_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    validate_final_report_schema(report)
    assert report["site"] == "example.com"
    assert "coverage" in report
    assert report["coverage"]["skills_ok"] >= 4


def test_scenario_b_browser_unavailable_truthful_status():
    """SCENARIO B: When browser rendering is unavailable, geometry checks are NOT fabricated,
    and status is truthfully degraded without claiming unmeasured geometry."""
    ctx = build_canonical_context_from_html(
        target_url="https://example.com/",
        raw_html=DEFECT_RICH_HTML,
        render_observations=None,  # No browser execution
    )

    assert ctx.rendered.has_browser_data is False
    assert ctx.rendered.mobile_evidence is None

    eng_input = ctx.to_engagement_input()
    # Ensure no fabricated bounding boxes are present
    for h in eng_input["headings"]:
        assert "bounding_box" not in h or h["bounding_box"] is None
    for c in eng_input["ctas"]:
        assert "bounding_box" not in c or c["bounding_box"] is None
    assert "mobile" not in eng_input  # Must not invent mobile overflow flag!


@pytest.mark.asyncio
async def test_scenario_c_dns_network_failure_fail_soft():
    """SCENARIO C: Network/DNS resolution failure produces a fail-soft report
    with truthful failed status and no fabricated observations."""
    target_url = "https://unreachable-domain-dns-error.invalid/"

    with patch("run_master_audit.run_crawl_audit") as mock_crawl, \
         patch("run_master_audit.run_freshness_audit") as mock_fresh, \
         patch("run_master_audit.run_va_audit") as mock_va:

        mock_crawl.return_value = {
            "status": "failed",
            "findings": [
                {
                    "id": "CR-SITE-001",
                    "title": "Site unreachable: DNS resolution error",
                    "severity": "high",
                    "evidence": "Network transport error: DNS lookup failed for unreachable-domain-dns-error.invalid",
                    "suggested_action": {"summary": "Verify domain DNS records.", "priority": "high"},
                }
            ],
            "raw_observations": {
                "crawler": {"status_code": None, "success": False, "error_message": "DNS resolution error", "body": ""},
            }
        }
        mock_fresh.return_value = {"audit_metadata": {"status": "failed"}, "findings": []}
        mock_va.return_value = {"audit": {"status": "failed"}, "findings": []}

        with patch.object(sys, "argv", ["run_master_audit.py", target_url]):
            await run_master_audit.main()

    output_path = os.path.join(WORKSPACE_ROOT, "output.json")
    with open(output_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    validate_final_report_schema(report)
    assert report["coverage"]["skills_failed"] >= 1


def test_scenario_d_image_unavailable_no_placeholder_dimensions():
    """SCENARIO D: Images without declared dimensions do NOT receive fake 800x600 placeholders."""
    ctx = build_canonical_context_from_html(
        target_url="https://example.com/",
        raw_html='<img src="/img/no-size.jpg" alt="No size specified">',
    )
    mm_input = ctx.to_multimodal_input()
    assert len(mm_input["images"]) == 1
    img = mm_input["images"][0]
    assert img.get("width") is None, "Did not expect fake width!"
    assert img.get("height") is None, "Did not expect fake height!"
    assert img.get("position") is None, "Did not expect fake coordinates!"


def test_scenario_e_malformed_html_safe_parsing():
    """SCENARIO E: Malformed HTML is parsed safely without crashing."""
    ctx = build_canonical_context_from_html(
        target_url="https://example.com/bad",
        raw_html=MALFORMED_HTML,
    )
    assert ctx.document.title == "Malformed Document"
    eng_input = ctx.to_engagement_input()
    assert isinstance(eng_input, dict)
    assert eng_input["title"] == "Malformed Document"


def test_scenario_g_robots_txt_disallow():
    """SCENARIO G: Robots.txt disallow directives are verified and normalized."""
    finding = {
        "id": "CR-ROBOTS-001",
        "title": "Robots.txt disallows crawler access to essential resources",
        "severity": "high",
        "evidence": "checked at https://example.com/robots.txt; directive 'Disallow: /' blocks all automated indexing agents.",
        "suggested_action": {"summary": "Update robots.txt directives.", "priority": "high"},
    }
    report = build_final_report("https://example.com", [finding])
    validate_final_report_schema(report)
    assert report["summary"]["high"] == 1
    assert report["findings"][0]["id"] == "CR-ROBOTS-001"


def test_scenario_h_redirect_chain_recording():
    """SCENARIO H: Redirect chain correctly records final canonical URL."""
    ctx = build_canonical_context_from_html(
        target_url="http://example.com/",
        final_url="https://example.com/en/home",
        raw_html=CLEAN_FIXTURE_HTML,
    )
    assert ctx.site.requested_url == "http://example.com/"
    assert ctx.site.final_url == "https://example.com/en/home"
    assert ctx.to_engagement_input()["url"] == "https://example.com/en/home"


def test_scenario_i_no_findings_clean_site():
    """SCENARIO I: Clean site produces valid report with 0 findings."""
    report = build_final_report("https://example.com", [])
    validate_final_report_schema(report)
    assert report["summary"]["total_findings"] == 0
    assert report["summary"]["critical"] == 0
    assert report["summary"]["high"] == 0
    assert report["summary"]["medium"] == 0
    assert report["summary"]["low"] == 0
    assert len(report["findings"]) == 0


def test_scenario_j_mixed_severity_summary_invariants():
    """SCENARIO J: Summary counts exactly match verified finding severities."""
    findings = [
        {
            "id": "SEC-001",
            "title": "SSL Certificate Expiry Imminent",
            "severity": "critical",
            "evidence": "checked at https://example.com; certificate expires in 2 days (HTTP 200).",
            "suggested_action": {"summary": "Renew certificate.", "priority": "critical"},
        },
        {
            "id": "MM-001",
            "title": "Missing alt attribute on hero banner",
            "severity": "high",
            "evidence": "checked at https://example.com; image selector: img#hero alt_attribute not present.",
            "suggested_action": {"summary": "Add alt text.", "priority": "high"},
        },
        {
            "id": "ENG-001",
            "title": "Dense wall of text in overview section",
            "severity": "medium",
            "evidence": "Flesch reading ease score 32.1 across 400 words (word_count: 400).",
            "suggested_action": {"summary": "Shorten sentences.", "priority": "medium"},
        },
        {
            "id": "VA-001",
            "title": "Low contrast text in footer copyright",
            "severity": "low",
            "evidence": "checked at https://example.com; selector: footer.copy computed contrast ratio 3.8:1 with fg: #888888 and bg: #ffffff.",
            "suggested_action": {"summary": "Darken font color.", "priority": "low"},
        },
    ]
    report = build_final_report("https://example.com", findings)
    validate_final_report_schema(report)

    summary = report["summary"]
    assert summary["total_findings"] == 4
    assert summary["critical"] == 1
    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["low"] == 1
    assert summary["total_findings"] == len(report["findings"])


def test_scenario_k_unsupported_geometry_checks_not_falsely_passed():
    """SCENARIO K: Spatial fold checks are not evaluated on synthetic coordinates."""
    ctx = build_canonical_context_from_html(
        target_url="https://example.com/",
        raw_html="""<html><body><h1>Headline</h1><button>Buy Now</button></body></html>""",
        render_observations=None,
    )
    eng_input = ctx.to_engagement_input()
    from engagement_audit import EngagementAudit
    auditor = EngagementAudit()
    result = auditor.audit(eng_input)

    # Above fold metrics should record that coordinates were NOT provided
    above_fold_metrics = result.get("category_scores", {}).get("above_fold", {})
    # Should not produce ENG-FOLD-001 or ENG-FOLD-002 because coordinates were unmeasured
    fold_finding_ids = [f["id"] for f in result.get("findings", []) if f.get("category") == "above_fold"]
    assert "ENG-FOLD-001" not in fold_finding_ids
    assert "ENG-FOLD-002" not in fold_finding_ids


def test_evidence_tiers_and_severity_gating():
    """Verify explicit evidence tiers (tier_1 to tier_4) and severity gating."""
    f_tier1 = {
        "id": "T1-001",
        "title": "Server returned HTTP 500 error",
        "severity": "high",
        "evidence": "checked at https://example.com/api/v1 returned HTTP 500 status.",
        "suggested_action": {"summary": "Inspect server logs.", "priority": "high"},
    }
    norm1 = normalize_finding(f_tier1)
    assert norm1["evidence_tier"] == "tier_1"
    assert norm1["severity"] == "high"

    f_tier4_high = {
        "id": "T4-001",
        "title": "Unmeasured layout impression",
        "severity": "high",
        "evidence": "The website seems difficult to browse.",
        "suggested_action": {"summary": "Redesign layout.", "priority": "high"},
    }
    norm4 = normalize_finding(f_tier4_high)
    # Tier 4 without machine-checkable facts MUST be downgraded from high
    assert norm4["severity"] == "low"
    assert norm4["evidence_tier"] == "unverified"
    assert norm4.get("downgraded_from_high") is True
