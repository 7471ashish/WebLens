"""
Unit test suite for independent engagement_audit.py using pytest.
Verifies all seven engagement analysis areas, scoring, and missing data handling.
"""

from __future__ import annotations

import json
import pytest
from engagement_audit import (
    EngagementAudit,
    calculate_flesch_reading_ease,
    count_syllables_in_word,
)


@pytest.fixture
def perfect_page_data():
    """Sample data for an optimal, highly engaging landing page."""
    return {
        "url": "https://example.com/",
        "viewport": {"width": 1366, "height": 768},
        "headings": [
            {"level": 1, "text": "Build Better Products Faster", "bounding_box": {"x": 100, "y": 150, "width": 600, "height": 80}},
            {"level": 2, "text": "Key Features", "bounding_box": {"x": 100, "y": 800, "width": 400, "height": 40}},
            {"level": 3, "text": "Instant Deployment", "bounding_box": {"x": 100, "y": 900, "width": 300, "height": 30}},
        ],
        "ctas": [
            {"text": "Start Free Trial", "is_primary": True, "bounding_box": {"x": 100, "y": 280, "width": 180, "height": 50}},
        ],
        "navigation": [
            {"text": "Features"}, {"text": "Pricing"}, {"text": "Docs"}, {"text": "About"}, {"text": "Contact"}
        ],
        "popups": [],
        "text": (
            "We help teams build great digital products with fast tools and simple workflows. "
            "Our platform makes it easy to collaborate, test ideas, and ship updates quickly. "
            "Join thousands of modern product creators today."
        ),
        "mobile": {
            "viewport": {"width": 390, "height": 844},
            "horizontal_overflow": False,
            "small_touch_targets_count": 0,
            "cta_hidden_on_mobile": False,
        },
        "forms": [
            {"name": "Quick Signup", "field_count": 2}
        ],
        "links": [
            {"text": "Documentation", "href": "/docs"},
            {"text": "Pricing Guide", "href": "/pricing"},
        ],
    }


def test_1_perfect_page_high_score(perfect_page_data):
    """1. Test that optimal page produces high score with 0 friction findings."""
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    assert result["score"] >= 95
    assert len(result["findings"]) == 0


def test_2_above_the_fold_heading_pushed_down(perfect_page_data):
    """2. Test H1 located below 768px fold generates finding."""
    perfect_page_data["headings"][0]["bounding_box"]["y"] = 900
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    h_findings = [f for f in result["findings"] if f["id"] == "ENG-FOLD-001"]
    assert len(h_findings) == 1
    assert h_findings[0]["severity"] == "medium"


def test_3_above_the_fold_cta_missing(perfect_page_data):
    """3. Test primary CTA located below 768px generates high severity finding."""
    perfect_page_data["ctas"][0]["bounding_box"]["y"] = 850
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    cta_findings = [f for f in result["findings"] if f["id"] == "ENG-FOLD-002"]
    assert len(cta_findings) == 1
    assert cta_findings[0]["severity"] == "high"


def test_4_missing_cta_detected():
    """4. Test page lacking CTA elements generates finding."""
    data = {"url": "https://example.com/blog", "ctas": [], "buttons": [], "links": []}
    auditor = EngagementAudit()
    result = auditor.audit(data)
    no_cta = [f for f in result["findings"] if f["id"] == "ENG-CTA-001"]
    assert len(no_cta) == 1


def test_5_vague_cta_copy(perfect_page_data):
    """5. Test vague CTA copy 'Click Here' triggers recommendation."""
    perfect_page_data["ctas"].append({"text": "Click Here"})
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    vague = [f for f in result["findings"] if f["id"] == "ENG-CTA-002"]
    assert len(vague) == 1
    assert vague[0]["severity"] == "low"


def test_6_cluttered_navigation(perfect_page_data):
    """6. Test navigation menu with >10 items triggers cognitive load warning."""
    perfect_page_data["navigation"] = [{"text": f"Nav {i}"} for i in range(15)]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    nav_f = [f for f in result["findings"] if f["id"] == "ENG-NAV-001"]
    assert len(nav_f) == 1


def test_7_empty_navigation_labels(perfect_page_data):
    """7. Test navigation items with empty text."""
    perfect_page_data["navigation"].append({"text": ""})
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    empty_nav = [f for f in result["findings"] if f["id"] == "ENG-NAV-002"]
    assert len(empty_nav) == 1


def test_8_skipped_heading_hierarchy(perfect_page_data):
    """8. Test document skipping from h1 to h4."""
    perfect_page_data["headings"] = [
        {"level": 1, "text": "Title"},
        {"level": 4, "text": "Sub section"},
    ]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    hier_f = [f for f in result["findings"] if f["id"] == "ENG-NAV-003"]
    assert len(hier_f) == 1


def test_9_immediate_blocking_popup(perfect_page_data):
    """9. Test intrusive modal appearing immediately on page load."""
    perfect_page_data["popups"] = [{"is_blocking": True, "immediate": True, "has_close_button": True}]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    pop_f = [f for f in result["findings"] if f["id"] == "ENG-POP-001"]
    assert len(pop_f) == 1


def test_10_modal_without_close_button(perfect_page_data):
    """10. Test overlay lacking close button generates high severity finding."""
    perfect_page_data["popups"] = [{"is_blocking": True, "immediate": False, "has_close_button": False}]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    trap_f = [f for f in result["findings"] if f["id"] == "ENG-POP-002"]
    assert len(trap_f) == 1
    assert trap_f[0]["severity"] == "high"


def test_11_syllable_counter():
    """11. Test English syllable counting accuracy."""
    assert count_syllables_in_word("cat") == 1
    assert count_syllables_in_word("simple") == 2
    assert count_syllables_in_word("beautiful") == 3
    assert count_syllables_in_word("infrastructure") >= 4


def test_12_flesch_reading_ease_easy():
    """12. Test simple conversational text gives high Flesch Reading Ease."""
    text = "The quick brown fox jumps over the lazy dog. It is a nice sunny day. We love to walk outside."
    metrics = calculate_flesch_reading_ease(text)
    assert metrics["status"] == "calculated"
    assert metrics["score"] > 80.0


def test_13_flesch_reading_ease_difficult(perfect_page_data):
    """13. Test highly academic text flags reading difficulty."""
    perfect_page_data["text"] = (
        "We utilize poly-asynchronous multi-tiered computational methodologies to optimize programmatic "
        "workflow orchestrations across synergistic distributed architectures, facilitating robust "
        "high-concurrency micro-service paradigms in multi-tenant environments."
    )
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    read_f = [f for f in result["findings"] if f["id"] == "ENG-READ-001"]
    assert len(read_f) == 1


def test_14_mobile_horizontal_overflow(perfect_page_data):
    """14. Test mobile horizontal scrolling generates high severity finding."""
    perfect_page_data["mobile"] = {
        "horizontal_overflow": True,
        "scroll_width": 480,
        "client_width": 390,
    }
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    mob_f = [f for f in result["findings"] if f["id"] == "ENG-MOB-001"]
    assert len(mob_f) == 1
    assert mob_f[0]["severity"] == "high"


def test_15_mobile_small_touch_targets(perfect_page_data):
    """15. Test small touch targets flag UX warning."""
    perfect_page_data["mobile"]["small_touch_targets_count"] = 6
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    touch_f = [f for f in result["findings"] if f["id"] == "ENG-MOB-002"]
    assert len(touch_f) == 1


def test_16_mobile_cta_hidden(perfect_page_data):
    """16. Test primary CTA hidden on mobile viewports."""
    perfect_page_data["mobile"]["cta_hidden_on_mobile"] = True
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    cta_m_f = [f for f in result["findings"] if f["id"] == "ENG-MOB-003"]
    assert len(cta_m_f) == 1


def test_17_missing_mobile_evidence_graceful():
    """17. Test lack of mobile evidence produces insufficient_evidence status rather than failing."""
    auditor = EngagementAudit()
    result = auditor.audit({"url": "https://example.com"})
    assert result["category_scores"]["responsiveness"]["status"] == "insufficient_evidence"


def test_18_excessive_form_fields(perfect_page_data):
    """18. Test forms with >=8 fields trigger form friction warning."""
    perfect_page_data["forms"] = [{"name": "Lead Intake", "field_count": 10}]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    form_f = [f for f in result["findings"] if f["id"] == "ENG-JRN-001"]
    assert len(form_f) == 1


def test_19_dead_end_links(perfect_page_data):
    """19. Test dead end links ('#') generate finding."""
    perfect_page_data["links"] = [{"text": f"Dummy {i}", "href": "#"} for i in range(4)]
    auditor = EngagementAudit()
    result = auditor.audit(perfect_page_data)
    dead_f = [f for f in result["findings"] if f["id"] == "ENG-JRN-002"]
    assert len(dead_f) == 1


def test_20_none_input_does_not_crash():
    """20. Test handling of None input without exception."""
    auditor = EngagementAudit()
    result = auditor.audit(None)
    assert isinstance(result, dict)
    assert "score" in result
    assert "findings" in result
