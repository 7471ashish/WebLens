"""
Unit & Contract Test Suite for Final Audit Output Schema (test_final_output_schema.py)
Validates that the final audit output conforms strictly to the canonical audit report schema.
"""

from __future__ import annotations

import json
import os
import sys
import pytest

# Ensure skills/audit-orchestrator/scripts is in sys.path
WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "skills", "audit-orchestrator", "scripts"))

from audit_orchestrator import (
    PROACTIVE_DOMAIN_SUGGESTIONS,
    build_final_report,
    is_tier1_evidence,
    normalize_finding,
    normalize_site_identifier,
    validate_final_report_schema,
)



# 1. Site Identifier Normalization Tests


@pytest.mark.parametrize(
    "input_url, expected_site",
    [
        ("https://www.linkedin.com/", "linkedin.com"),
        ("http://linkedin.com", "linkedin.com"),
        ("https://www.linkedin.com/in/profile?id=123", "linkedin.com"),
        ("https://dhartiputracattlefeed.netlify.app/", "dhartiputracattlefeed.netlify.app"),
        ("http://subdomain.example.co.uk:8080/path/to/page", "subdomain.example.co.uk"),
        ("example.org", "example.org"),
        ("https://example.com#section", "example.com"),
    ],
)
def test_normalize_site_identifier(input_url, expected_site):
    assert normalize_site_identifier(input_url) == expected_site


def test_normalize_site_identifier_invalid():
    assert normalize_site_identifier("") == "unknown"
    assert normalize_site_identifier(None) == "unknown"



# 2. Finding Normalization Tests

def test_normalize_multimodal_finding():
    raw = {
        "id": "MM-ALT-001",
        "title": "Image missing alt text",
        "severity": "high",
        "evidence": {"image_id": "img-1", "url": "https://example.com/logo.png"},
        "recommendation": "Add a descriptive alt text attribute.",
    }
    normalized = normalize_finding(raw)
    assert normalized is not None
    assert normalized["id"] == "MM-ALT-001"
    assert normalized["title"] == "Image missing alt text"
    assert normalized["severity"] == "high"
    assert "image_id: img-1" in normalized["evidence"]
    assert normalized["suggested_action"]["summary"] == "Add a descriptive alt text attribute."
    assert normalized["suggested_action"]["priority"] == "high"


def test_normalize_engagement_finding():
    raw = {
        "id": "ENG-CTA-001",
        "title": "Primary CTA below the fold",
        "severity": "medium",
        "description": "Primary action button is located at Y=900px which is below 768px fold.",
        "evidence": {"y_coord": 900, "viewport_height": 768},
        "recommendation": "Elevate primary CTA into the hero viewport.",
    }
    normalized = normalize_finding(raw)
    assert normalized is not None
    assert normalized["id"] == "ENG-CTA-001"
    assert normalized["severity"] == "medium"
    assert normalized["suggested_action"]["priority"] == "medium"


def test_normalize_crawl_render_finding():
    raw = {
        "id": "CR-ROBOTS-001",
        "title": "Robots.txt blocks essential assets",
        "severity": "critical",
        "description": "Critical CSS stylesheets are disallowed in robots.txt.",
        "evidence": [{"source": "robots.txt", "path": "/styles/*"}],
        "suggested_action": {
            "summary": "Allow search bots to access critical CSS.",
            "priority": "critical",
        },
    }
    normalized = normalize_finding(raw)
    assert normalized is not None
    assert normalized["id"] == "CR-ROBOTS-001"
    assert normalized["severity"] == "critical"
    assert normalized["suggested_action"]["summary"] == "Allow search bots to access critical CSS."
    assert normalized["suggested_action"]["priority"] == "critical"



# 3. Final Report Top-Level & Summary Contract Tests


def test_final_report_structure_empty():
    report = build_final_report(
        target_url="https://www.example.com/",
        raw_findings=[],
        audited_at="2026-09-02T12:00:00Z",
    )

    assert report["site"] == "example.com"
    assert report["audited_at"] == "2026-09-02T12:00:00Z"
    assert report["summary"] == {
        "total_findings": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    assert report["findings"] == []


def test_final_report_structure_with_findings():
    findings = [
        {
            "id": "MM-IMG-001",
            "title": "Broken image resource",
            "severity": "critical",
            "evidence": "Image URL 404",
            "recommendation": "Fix broken image URL.",
        },
        {
            "id": "MM-ALT-001",
            "title": "Missing alt attribute",
            "severity": "high",
            "evidence": "Missing alt on informative image",
            "recommendation": "Provide concise alt text.",
        },
        {
            "id": "ENG-NAV-001",
            "title": "Excessive menu items",
            "severity": "medium",
            "evidence": "15 top level items",
            "recommendation": "Group navigation items.",
        },
        {
            "id": "MM-META-012",
            "title": "Duplicate image URL",
            "severity": "low",
            "evidence": "Reused image URL",
            "recommendation": "Reuse component instances.",
        },
    ]

    report = build_final_report(
        target_url="https://dhartiputracattlefeed.netlify.app/",
        raw_findings=findings,
        audited_at="2026-09-02T18:00:00Z",
    )

    assert report["site"] == "dhartiputracattlefeed.netlify.app"
    assert report["audited_at"] == "2026-09-02T18:00:00Z"

    # Summary count invariants
    summary = report["summary"]
    assert summary["total_findings"] == 4
    assert summary["critical"] == 1
    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["low"] == 1
    assert summary["total_findings"] == len(report["findings"])

    # Every finding contract
    for f in report["findings"]:
        assert "id" in f
        assert "title" in f
        assert "severity" in f
        assert "evidence" in f
        assert "suggested_action" in f
        assert "summary" in f["suggested_action"]
        assert "priority" in f["suggested_action"]
        assert f["severity"] in ("critical", "high", "medium", "low")
        assert f["suggested_action"]["priority"] in ("critical", "high", "medium", "low")



# 4. Deterministic Ordering Tests


def test_deterministic_finding_ordering():
    findings = [
        {"id": "F-LOW", "title": "Low issue", "severity": "low", "evidence": "e", "recommendation": "r"},
        {"id": "F-CRIT", "title": "Critical issue", "severity": "critical", "evidence": "e", "recommendation": "r"},
        {"id": "F-MED-B", "title": "Med issue B", "severity": "medium", "evidence": "e", "recommendation": "r"},
        {"id": "F-HIGH", "title": "High issue", "severity": "high", "evidence": "e", "recommendation": "r"},
        {"id": "F-MED-A", "title": "Med issue A", "severity": "medium", "evidence": "e", "recommendation": "r"},
    ]

    report = build_final_report("https://example.com", findings)
    ordered_ids = [f["id"] for f in report["findings"]]

    # Expected order: critical -> high -> medium (A then B) -> low
    assert ordered_ids == ["F-CRIT", "F-HIGH", "F-MED-A", "F-MED-B", "F-LOW"]



# 5. Schema Validation & Serialization Tests


def test_final_report_json_serializability():
    findings = [
        {"id": "F-1", "title": "Issue 1", "severity": "high", "evidence": "Evidence text", "recommendation": "Action"}
    ]
    report = build_final_report("https://www.linkedin.com", findings)
    json_str = json.dumps(report, indent=2)
    assert json_str is not None

    parsed = json.loads(json_str)
    assert parsed["site"] == "linkedin.com"
    assert parsed["summary"]["total_findings"] == 1


def test_schema_validator_catches_invalid_report():
    with pytest.raises(ValueError):
        validate_final_report_schema({"site": "example.com"})  # Missing fields

    with pytest.raises(ValueError):
        validate_final_report_schema({
            "site": "example.com",
            "audited_at": "2026-09-02T12:00:00Z",
            "summary": {"total_findings": 10, "critical": 0, "high": 0, "medium": 0},  # Count mismatch
            "findings": [],
        })



# 6. QA Defects Regression Tests (Defects 1, 4, 6, 8)


def test_qa_defect_evidence_tier_severity_gating():
    """Defect 8: High severity without Tier-1 evidence must be capped at low."""
    finding_weak = {
        "id": "ENG-CTA-001",
        "title": "Primary call-to-action lacks compelling urgency",
        "severity": "high",
        "evidence": "CTA copy is generic and does not communicate benefits.",
        "recommendation": "Reframe CTA.",
    }
    norm = normalize_finding(finding_weak)
    assert norm["severity"] == "low"
    assert norm["suggested_action"]["priority"] == "low"


def test_qa_defect_subjective_opinion_separated_to_suggestions():
    """Defect 6: Subjective UX opinions belong in suggestions, not defects array."""
    findings = [
        {
            "id": "ENG-CTA-001",
            "title": "Primary call-to-action lacks compelling urgency and clear value proposition",
            "severity": "low",
            "evidence": "Hero section CTA copy is generic.",
            "recommendation": "Reframe primary button.",
            "is_suggestion": True,
        },
        {
            "id": "MM-ALT-001",
            "title": "Image missing an alt attribute (img-006)",
            "severity": "medium",
            "evidence": "Image asset 'img-006' has no alt attribute defined at url https://example.com/logo.jpeg.",
            "recommendation": "Add alt text.",
        }
    ]
    report = build_final_report("https://example.com", findings)
    assert report["summary"]["total_findings"] == 1
    assert len(report["findings"]) == 1
    assert report["findings"][0]["id"] == "MM-ALT-001"
    assert "suggestions" in report
    assert len(report["suggestions"]) == 1
    assert report["suggestions"][0]["id"] == "ENG-CTA-001"


def test_qa_defect_duplicate_asset_merging_and_unique_ids():
    """Defect 4: Same-asset duplicate findings must be merged and IDs made unique."""
    findings = [
        {
            "id": "MM-ALT-001",
            "title": "Image missing alt (img-006)",
            "severity": "medium",
            "evidence": "image_id: img-006 url: https://example.com/logo.jpeg alt attribute: not present",
            "recommendation": "Add alt.",
        },
        {
            "id": "MM-ALT-001",
            "title": "Image missing alt (img-008)",
            "severity": "medium",
            "evidence": "image_id: img-008 url: https://example.com/post-2.png alt attribute: not present",
            "recommendation": "Add alt.",
        },
        {
            "id": "MM-IMG-006",
            "title": "Redundant duplicate image asset (img-006)",
            "severity": "low",
            "evidence": "Image 'img-006' uses identical source URL (image_id: img-006; duplicate_of: img-001).",
            "recommendation": "Consolidate image.",
        },
        {
            "id": "MM-META-012",
            "title": "Duplicate image asset metadata URL (img-006)",
            "severity": "low",
            "evidence": "Image 'img-006' shares duplicate metadata URL (image_id: img-006; duplicate_of: img-001).",
            "recommendation": "Consolidate references.",
        }
    ]
    report = build_final_report("https://example.com", findings)
    # The two duplicate image findings for img-006 should be merged into 1
    assert report["summary"]["total_findings"] == 3
    ids = [f["id"] for f in report["findings"]]
    # IDs must be unique
    assert len(ids) == len(set(ids))
    assert "MM-ALT-001" in ids
    assert "MM-ALT-001-img-008" in ids or any(i.startswith("MM-ALT-001") for i in ids)


def test_qa_defect_a_contrast_without_ratio_selector_colors_downgraded():
    """Defect A: Contrast findings without computed ratio, selector, and colors must move to suggestions."""
    unmeasured_contrast = {
        "id": "va-contrast-001",
        "title": "Secondary typography contrast below WCAG 4.5:1 threshold",
        "severity": "medium",
        "evidence": "checked at https://example.com/; secondary caption typography evaluated with measured contrast ratio below 4.5:1 on background.",
        "recommendation": "Adjust foreground text luminance.",
    }
    report = build_final_report("https://example.com", [unmeasured_contrast])
    assert report["summary"]["total_findings"] == 0
    assert len(report["findings"]) == 0
    assert "suggestions" in report
    assert len(report["suggestions"]) == 1
    sugg = report["suggestions"][0]
    assert sugg["id"] == "va-contrast-001"
    assert "severity" not in sugg
    assert sugg["type"] == "suggestion"
    assert "visual inspection suggests possible low contrast" in sugg["evidence"]
    assert isinstance(sugg["confidence"], float)


def test_qa_defect_b_general_ux_non_concrete_finding_hard_gated():
    """Defect B: GEN-AUDIT-001 or any non-concrete finding must be auto-moved to suggestions by hard gate."""
    vague_ux = {
        "id": "GEN-AUDIT-001",
        "title": "General UX and accessibility optimization opportunity",
        "severity": "low",
        "evidence": "qualitative_critique: Observed subtle layout and semantic opportunities for user experience enhancement.",
        "recommendation": "Perform targeted user testing.",
        "confidence": "0.9",
    }
    report = build_final_report("https://example.com", [vague_ux])
    assert report["summary"]["total_findings"] == 0
    assert len(report["findings"]) == 0
    assert "suggestions" in report
    assert len(report["suggestions"]) == 1
    sugg = report["suggestions"][0]
    assert sugg["id"] == "GEN-AUDIT-001"
    assert "severity" not in sugg
    assert sugg["type"] == "suggestion"
    assert isinstance(sugg["confidence"], float)
    assert sugg["confidence"] == 0.9


def test_confidence_uniformly_typed_as_float():
    """Verify confidence across all findings and suggestions is uniformly float."""
    mixed_findings = [
        {
            "id": "MM-ALT-001",
            "title": "Image missing alt attribute",
            "severity": "medium",
            "evidence": "image_id: img-006 url: https://example.com/logo.jpeg alt attribute: not present",
            "recommendation": "Add alt.",
            "confidence": "0.96",  # string float
        },
        {
            "id": "CR-SITE-001",
            "title": "Missing XML sitemap",
            "severity": "low",
            "evidence": "checked at https://example.com/sitemap.xml, returned HTTP 404 Not Found",
            "recommendation": "Deploy sitemap.",
            "confidence": "high",  # categorical string
        },
    ]
    report = build_final_report("https://example.com", mixed_findings)
    for f in report["findings"]:
        assert isinstance(f["confidence"], float)
        assert 0.0 <= f["confidence"] <= 1.0
    assert report["findings"][0]["confidence"] == 0.96
    assert report["findings"][1]["confidence"] == 0.90


def test_compounding_severity_escalation_rule():
    """Verify when sitemap is 404 AND JSON-LD count is 0 AND single-page nav is true,
    related finding is escalated to high severity and evidence contains 'escalated' or 'compounds'."""
    findings = [
        {
            "id": "CR-SITE-001",
            "title": "Missing XML sitemap (/sitemap.xml)",
            "severity": "low",
            "evidence": "checked at https://example.com/sitemap.xml, returned HTTP 404 Not Found (sitemap not discovered).",
            "suggested_action": {"summary": "Deploy sitemap.", "priority": "low"},
            "confidence": 0.9,
        },
        {
            "id": "freshness-001",
            "title": "Structured schema data lacks outbound entity relationship markup",
            "severity": "medium",
            "evidence": "checked at https://example.com/ (JSON-LD: 0 block(s) found, checked 'sameAs' attribute: not present).",
            "suggested_action": {"summary": "Add JSON-LD.", "priority": "medium"},
            "confidence": 0.9,
        },
        {
            "id": "CR-NAV-ANCHOR-001",
            "title": "Site navigation relies entirely on same-page anchor hashes",
            "severity": "medium",
            "evidence": "Checked 10 internal links at https://example.com/; 9 (90.0%) target same-page fragment anchors. Site exposes effectively only one indexable URL.",
            "suggested_action": {"summary": "Structure routes.", "priority": "medium"},
            "confidence": 0.9,
        },
    ]
    report = build_final_report("https://example.com", findings)
    anchor_f = next(f for f in report["findings"] if f["id"] == "CR-NAV-ANCHOR-001")
    # (a) Assert severity is escalated to high
    assert anchor_f["severity"] == "high"
    assert anchor_f["suggested_action"]["priority"] == "high"
    # (b) Assert evidence explicitly mentions escalation / compounding
    ev_lower = anchor_f["evidence"].lower()
    assert "escalated" in ev_lower or "compounds" in ev_lower
    assert report["summary"]["high"] >= 1


def test_compounding_severity_escalation_does_not_fire_on_single_condition():
    """Verify that when only ONE condition is true (e.g. sitemap 404, but JSON-LD exists
    and multi-page navigation is present), severity is NOT escalated."""
    findings = [
        {
            "id": "CR-SITE-001",
            "title": "Missing XML sitemap (/sitemap.xml)",
            "severity": "low",
            "evidence": "checked at https://example.com/sitemap.xml, returned HTTP 404 Not Found (sitemap not discovered).",
            "suggested_action": {"summary": "Deploy sitemap.", "priority": "low"},
            "confidence": 0.9,
        },
        {
            "id": "freshness-001",
            "title": "Structured schema data valid",
            "severity": "low",
            "evidence": "checked at https://example.com/ (JSON-LD: 3 block(s) found, Organization verified).",
            "suggested_action": {"summary": "Maintain schema.", "priority": "low"},
            "confidence": 0.9,
        },
    ]
    report = build_final_report("https://example.com", findings)
    site_f = next(f for f in report["findings"] if f["id"] == "CR-SITE-001")
    # Must remain low severity
    assert site_f["severity"] == "low"
    assert "escalated" not in site_f["evidence"].lower()
    assert "compounds" not in site_f["evidence"].lower()
    assert report["summary"]["high"] == 0


def test_marketplace_json_manifest_structure():
    """Verify marketplace.json exists at root and matches agentskills.io marketplace contract."""
    marketplace_path = os.path.join(os.path.dirname(__file__), "marketplace.json")
    assert os.path.exists(marketplace_path)
    with open(marketplace_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["name"] == "brand-ai-readiness-audit"
    assert data["version"] == "1.0.0"
    skill_ids = [s["id"] for s in data["skills"]]
    assert "audit-orchestrator" in skill_ids
    assert "crawl-render-audit" in skill_ids
    assert "freshness-corroboration-audit" in skill_ids
    assert "engagement-audit" in skill_ids
    assert "multimodal-audit" in skill_ids
    assert "visual-accessibility-audit" in skill_ids
    entrypoint = next(s for s in data["skills"] if s["id"] == "audit-orchestrator")
    assert entrypoint.get("entrypoint") is True


def test_no_finding_above_medium_without_tier1_evidence():
    """Item 1: Assert no finding above 'medium' severity ships without a Tier-1 evidence string.
    Any high/critical finding without Tier-1 facts must be downgraded to 'low' with evidence_tier: 'unverified'."""
    unverified_high = {
        "id": "SEC-WARN-001",
        "title": "Server technology signature disclosed",
        "severity": "high",
        "evidence": "Observed server technology fingerprint exposed in HTTP response headers.",
        "suggested_action": {"summary": "Suppress Server and X-Powered-By response headers.", "priority": "high"},
    }
    unverified_crit = {
        "id": "SYS-CRIT-001",
        "title": "Unauthenticated administrative endpoint candidate",
        "severity": "critical",
        "evidence": "Observed potential management interface exposed on default port.",
        "suggested_action": {"summary": "Restrict administrative interface access behind authentication.", "priority": "critical"},
    }
    verified_high = {
        "id": "SEC-FAIL-001",
        "title": "Insecure form transmission endpoint",
        "severity": "high",
        "evidence": "checked at https://example.com/checkout; form selector: form#payment posts to http://insecure.endpoint with HTTP status 200.",
        "suggested_action": {"summary": "Update form action to https://.", "priority": "high"},
    }

    report = build_final_report("https://example.com", [unverified_high, unverified_crit, verified_high])
    
    # Assert no finding above 'medium' lacks Tier-1 evidence
    for f in report["findings"]:
        if f["severity"] in ("high", "critical"):
            assert is_tier1_evidence(f["evidence"]), f"Finding {f['id']} has high/critical severity without Tier-1 evidence!"
            assert f.get("evidence_tier") != "unverified"

    # Both unverified findings were downgraded to low
    unver_f1 = next(f for f in report["findings"] if f["id"] == "SEC-WARN-001")
    assert unver_f1["severity"] == "low"
    assert unver_f1["evidence_tier"] == "unverified"

    unver_f2 = next(f for f in report["findings"] if f["id"] == "SYS-CRIT-001")
    assert unver_f2["severity"] == "low"
    assert unver_f2["evidence_tier"] == "unverified"

    # The verified finding remains high
    ver_f = next(f for f in report["findings"] if f["id"] == "SEC-FAIL-001")
    assert ver_f["severity"] == "high"
    assert report["summary"]["high"] == 1


def test_cross_domain_asset_deduplication():
    """Item 2: In merge step, confirm findings referencing the same asset/URL across different
    domain skills (e.g., multimodal-audit and visual-accessibility-audit) are merged into one finding
    with combined evidence, not duplicated in the report."""
    same_asset_url = "https://example.com/assets/hero-banner.png"
    mm_finding = {
        "id": "MM-ALT-001",
        "title": "Image missing alt attribute",
        "severity": "medium",
        "evidence": f"Image asset 'img-001' has no alt attribute defined at url {same_asset_url}.",
        "suggested_action": {"summary": "Add descriptive alt attribute.", "priority": "medium"},
    }
    va_finding = {
        "id": "VA-FOCUS-001",
        "title": "Interactive banner image missing visible keyboard focus indicator",
        "severity": "high",
        "evidence": f"checked at {same_asset_url}; selector: img.hero-banner has outline: none without visible focus ring.",
        "suggested_action": {"summary": "Add 3px solid high-contrast focus outline.", "priority": "high"},
    }

    report = build_final_report("https://example.com", [mm_finding, va_finding])

    # Should produce exactly 1 merged finding, not 2
    assert len(report["findings"]) == 1
    assert report["summary"]["total_findings"] == 1

    merged = report["findings"][0]
    # Evidence must combine both findings
    assert same_asset_url in merged["evidence"]
    assert "alt attribute" in merged["evidence"].lower()
    assert "outline: none" in merged["evidence"].lower()
    assert " | " in merged["evidence"]
    # Severity must be escalated to the higher of the two (high)
    assert merged["severity"] == "high"


def test_extended_compound_severity_escalation_robots_sitemap_jsonld():
    """Item 2: Extended compound severity escalation when multiple independent discoverability failures
    co-occur on the same site (e.g., robots.txt blocks/404 + zero JSON-LD + no sitemap)."""
    findings = [
        {
            "id": "CR-ROBOTS-001",
            "title": "Missing robots.txt directives",
            "severity": "low",
            "evidence": "checked at https://example.com/robots.txt, returned HTTP 404 Not Found (robots.txt absent).",
            "suggested_action": {"summary": "Deploy robots.txt.", "priority": "low"},
        },
        {
            "id": "CR-SITE-001",
            "title": "Missing XML sitemap (/sitemap.xml)",
            "severity": "low",
            "evidence": "checked at https://example.com/sitemap.xml, returned HTTP 404 Not Found (sitemap not discovered).",
            "suggested_action": {"summary": "Deploy XML sitemap.", "priority": "low"},
        },
        {
            "id": "freshness-001",
            "title": "Structured schema data lacks outbound entity markup",
            "severity": "low",
            "evidence": "checked at https://example.com/ (JSON-LD: 0 block(s) found, checked 'sameAs' attribute: not present).",
            "suggested_action": {"summary": "Add JSON-LD schema.", "priority": "low"},
        },
    ]

    report = build_final_report("https://example.com", findings)
    # At least one discoverability failure must be escalated
    assert report["summary"]["medium"] >= 1 or report["summary"]["high"] >= 1

    escalated_found = False
    for f in report["findings"]:
        if "escalated" in f["evidence"].lower() and "compounds with" in f["evidence"].lower():
            escalated_found = True
            assert f["severity"] in ("medium", "high")
            break
    assert escalated_found, "Expected at least one discovery finding to be escalated with compounding reason."


def test_proactive_beyond_defect_suggestions_grounded_in_mechanisms():
    """Item 3: Confirm at least 3 'beyond-defect' proactive suggestions per domain skill
    (15 total), each tagged priority and grounded in Round-2 mechanisms."""
    assert len(PROACTIVE_DOMAIN_SUGGESTIONS) >= 15

    domain_counts: dict[str, int] = {}
    for s in PROACTIVE_DOMAIN_SUGGESTIONS:
        dom = s.get("domain", "")
        domain_counts[dom] = domain_counts.get(dom, 0) + 1
        # Check schema
        assert s["type"] == "suggestion"
        assert s["suggested_action"]["priority"] in ("low", "medium", "high")
        # Action summary must state concrete fix, not vague directive
        summary = s["suggested_action"]["summary"].lower()
        assert not summary.startswith("review and resolve")
        assert "improve seo" not in summary
        # Evidence must ground in mechanism
        ev = s["evidence"].lower()
        assert "proactive_recommendation" in ev

    expected_domains = {
        "crawl-render-audit",
        "freshness-corroboration-audit",
        "engagement-audit",
        "multimodal-audit",
        "visual-accessibility-audit",
    }
    for dom in expected_domains:
        assert domain_counts.get(dom, 0) >= 3, f"Expected at least 3 proactive suggestions for {dom}, got {domain_counts.get(dom, 0)}"

    # Test report integration when include_proactive_suggestions=True (capped to max 5 recommendations per final report)
    report = build_final_report("https://example.com", [], include_proactive_suggestions=True)
    assert "suggestions" in report
    assert 0 < len(report["suggestions"]) <= 5




