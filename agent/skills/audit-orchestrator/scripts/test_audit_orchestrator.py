"""
Unit tests for audit_orchestrator.py consistency reconciliation and schema validation.
"""

from __future__ import annotations

import pytest
from audit_orchestrator import build_final_report, validate_final_report_schema


def test_zero_cta_contradiction_suppression():
    """Test that a page with 0 detected CTAs (ENG-CTA-001) never produces a contradictory CTA-copy critique suggestion."""
    raw_findings = [
        {
            "id": "ENG-CTA-001",
            "category": "engagement",
            "title": "Missing or undetected Call-to-Action (CTA)",
            "description": "No actionable conversion buttons or CTA links discovered on page.",
            "severity": "high",
            "affected_url": "https://example.com/",
            "evidence": "ctas_found: 0",
            "suggested_action": {"summary": "Add primary conversion CTA button.", "priority": "high"},
            "confidence": 0.95,
        },
        {
            "id": "ENG-SUGG-CTA-001",
            "category": "engagement",
            "title": "Optimize primary CTA button copy for higher intent",
            "description": "Qualitative critique of button copy phrasing.",
            "severity": "low",
            "affected_url": "https://example.com/",
            "evidence": "qualitative_critique: 'Get Started' button copy could be improved with stronger action verbs.",
            "suggested_action": {"summary": "Change CTA button wording.", "priority": "low"},
            "confidence": 0.70,
            "type": "suggestion",
        },
        {
            "id": "ENG-READ-001",
            "category": "readability",
            "title": "Dense paragraph structure",
            "description": "Paragraph length exceeds recommended guidelines.",
            "severity": "low",
            "affected_url": "https://example.com/",
            "evidence": "average_sentence_length: 32 words",
            "suggested_action": {"summary": "Break copy into shorter blocks.", "priority": "low"},
            "confidence": 0.85,
        }
    ]

    report = build_final_report(
        target_url="https://example.com/",
        raw_findings=raw_findings,
        include_proactive_suggestions=True,
    )

    defect_ids = [d["id"] for d in report["findings"]]
    assert "ENG-CTA-001" in defect_ids

    # Verify no suggestion in report["suggestions"] critiques CTA button copy
    suggs = report.get("suggestions", [])
    for s in suggs:
        s_text = (str(s.get("id", "")) + " " + str(s.get("title", "")) + " " + str(s.get("evidence", ""))).lower()
        assert "button copy" not in s_text
        assert "cta copy" not in s_text
        assert "button wording" not in s_text
        assert s["id"] != "ENG-SUGG-CTA-001"


def test_schema_validation_passes():
    """Verify final report schema structure conforms to required keys."""
    raw_findings = [
        {
            "id": "CR-HTTP-001",
            "category": "crawl_render",
            "title": "Clean HTTP 200 response",
            "description": "Server returned 200 OK",
            "severity": "low",
            "affected_url": "https://example.com/",
            "evidence": "status_code: 200",
            "suggested_action": {"summary": "Maintain uptime.", "priority": "low"},
            "confidence": 1.0,
        }
    ]

    report = build_final_report(
        target_url="https://example.com/",
        raw_findings=raw_findings,
        include_proactive_suggestions=True,
    )

    assert "site" in report
    assert "audited_at" in report
    assert "summary" in report
    assert "findings" in report
    assert isinstance(report["findings"], list)
