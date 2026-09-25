"""
Unit test suite for popup_analyzer.py using pytest.
Tests all 32 specified popup/modal intrusiveness scenarios deterministically.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import (
    PageInputData,
    PopupEvidence,
    Viewport,
)
from popup_analyzer import (
    NormalizedPopup,
    PopupAnalyzer,
    analyze_popups,
    normalize_popup_evidence,
)


def test_1_no_popup_evidence_returns_insufficient_evidence():
    """1. Test missing popup field returns status 'insufficient_evidence'."""
    res = analyze_popups({"url": "https://example.com"})
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_2_explicitly_no_popups_passes_cleanly():
    """2. Test explicitly empty popups list returns status 'passed' with score 100."""
    res = analyze_popups({"url": "https://example.com", "popups": []})
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0


def test_3_non_intrusive_delayed_popup():
    """3. Test small, delayed, dismissible popup produces 0 intrusive findings."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "popups": [
            {
                "id": "pop-1",
                "type": "newsletter",
                "visible": True,
                "bbox": {"x": 1000, "y": 500, "width": 300, "height": 200},  # Small corner box
                "delay_ms": 15000,
                "has_close_button": True,
                "blocks_interaction": False,
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0


def test_4_hidden_popup_ignored():
    """4. Test hidden popup (visible=False) is not penalized."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": False,
                "has_close_button": False,
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0


def test_5_large_popup_screen_coverage():
    """5. Test popup covering >70% of screen area generates ENG-POP-001."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": True,
                # 1200x700 = 840,000 / 1,049,088 = 80% coverage
                "bbox": {"x": 80, "y": 30, "width": 1200, "height": 700},
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    cov_f = [f for f in res.findings if f.id == "ENG-POP-001"]
    assert len(cov_f) == 1
    assert cov_f[0].severity == "medium"


def test_6_small_popup_no_coverage_finding():
    """6. Test small popup (<30% coverage) does not trigger ENG-POP-001."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 1366, "height": 768},
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": True,
                "bbox": {"x": 100, "y": 100, "width": 300, "height": 200},
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    cov_f = [f for f in res.findings if f.id == "ENG-POP-001"]
    assert len(cov_f) == 0


def test_7_popup_covering_main_content():
    """7. Test popup covering main content generates ENG-POP-002."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "newsletter",
                "visible": True,
                "covered_elements": ["main_content", "hero_heading"],
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    cnt_f = [f for f in res.findings if f.id == "ENG-POP-002"]
    assert len(cnt_f) == 1
    assert cnt_f[0].severity == "medium"


def test_8_popup_covering_primary_cta():
    """8. Test popup covering primary CTA generates ENG-POP-003 (high severity)."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": True,
                "covered_elements": ["primary_cta"],
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "failed"
    cta_f = [f for f in res.findings if f.id == "ENG-POP-003"]
    assert len(cta_f) == 1
    assert cta_f[0].severity == "high"


def test_9_popup_covering_navigation():
    """9. Test popup covering navigation generates ENG-POP-004."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "banner",
                "visible": True,
                "covered_elements": ["navigation_menu"],
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    nav_f = [f for f in res.findings if f.id == "ENG-POP-004"]
    assert len(nav_f) == 1
    assert nav_f[0].severity == "medium"


def test_10_popup_with_visible_close_button_valid():
    """10. Test dismissible popup with close control generates 0 close-control findings."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "newsletter",
                "visible": True,
                "has_close_button": True,
                "close_button_visible": True,
            }
        ],
    }

    res = analyze_popups(data)
    close_f = [f for f in res.findings if f.id in ("ENG-POP-005", "ENG-POP-006")]
    assert len(close_f) == 0


def test_11_popup_without_close_button():
    """11. Test promotional modal without close button generates ENG-POP-005 (high severity)."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "marketing",
                "visible": True,
                "has_close_button": False,
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "failed"
    trap_f = [f for f in res.findings if f.id == "ENG-POP-005"]
    assert len(trap_f) == 1
    assert trap_f[0].severity == "high"


def test_12_popup_with_hidden_close_button():
    """12. Test modal with close button hidden/offscreen generates ENG-POP-006 (high severity)."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": True,
                "has_close_button": True,
                "close_button_visible": False,
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "failed"
    hide_f = [f for f in res.findings if f.id == "ENG-POP-006"]
    assert len(hide_f) == 1
    assert hide_f[0].severity == "high"


def test_13_immediate_blocking_popup_on_load():
    """13. Test immediate blocking overlay (delay_ms=0) generates ENG-POP-007."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "newsletter",
                "visible": True,
                "delay_ms": 0,
                "blocks_interaction": True,
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    imm_f = [f for f in res.findings if f.id == "ENG-POP-007"]
    assert len(imm_f) == 1
    assert imm_f[0].severity == "medium"


def test_14_delayed_popup_no_immediate_finding():
    """14. Test delayed popup (delay_ms=10000) does not trigger ENG-POP-007."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "newsletter",
                "visible": True,
                "delay_ms": 10000,
                "blocks_interaction": True,
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    imm_f = [f for f in res.findings if f.id == "ENG-POP-007"]
    assert len(imm_f) == 0


def test_15_repeated_popup_frequency():
    """15. Test repeated popup frequency generates ENG-POP-008."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-1",
                "type": "promo",
                "visible": True,
                "frequency": "every_page",
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    rep_f = [f for f in res.findings if f.id == "ENG-POP-008"]
    assert len(rep_f) == 1
    assert rep_f[0].severity == "medium"


def test_16_multiple_simultaneous_popups():
    """16. Test 2 visible simultaneous popups generates ENG-POP-009 (high severity)."""
    data = {
        "url": "https://example.com",
        "popups": [
            {"id": "pop-1", "type": "promo", "visible": True, "has_close_button": True},
            {"id": "pop-2", "type": "survey", "visible": True, "has_close_button": True},
        ],
    }

    res = analyze_popups(data)
    assert res.status == "failed"
    multi_f = [f for f in res.findings if f.id == "ENG-POP-009"]
    assert len(multi_f) == 1
    assert multi_f[0].severity == "high"


def test_17_mobile_interstitial_coverage():
    """17. Test mobile overlay with >50% mobile coverage generates ENG-POP-010."""
    data = {
        "url": "https://example.com",
        "viewport": {"width": 390, "height": 844},
        "popups": [
            {
                "id": "pop-mob",
                "type": "app_download",
                "visible": True,
                "is_mobile": True,
                # 350x600 = 210,000 / (390*844=329,160) = 64% mobile coverage
                "bbox": {"x": 20, "y": 100, "width": 350, "height": 600},
                "has_close_button": True,
            }
        ],
    }

    res = analyze_popups(data)
    mob_f = [f for f in res.findings if f.id == "ENG-POP-010"]
    assert len(mob_f) == 1
    assert mob_f[0].severity == "high"


def test_18_cookie_consent_not_penalized_as_intrusive_promo():
    """18. Test standard cookie consent banner is not penalized as promotional modal."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "cookie-banner",
                "type": "cookie_banner",
                "visible": True,
                "delay_ms": 0,
                "blocks_interaction": True,
            }
        ],
    }

    res = analyze_popups(data)
    # Functional cookie consent should not trigger ENG-POP-001, ENG-POP-005, or ENG-POP-007
    promo_f = [f for f in res.findings if f.id in ("ENG-POP-001", "ENG-POP-005", "ENG-POP-007")]
    assert len(promo_f) == 0


def test_19_login_modal_handled_as_functional():
    """19. Test login dialog is treated as functional modal."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "login-dialog",
                "type": "login",
                "visible": True,
                "blocks_interaction": True,
            }
        ],
    }

    res = analyze_popups(data)
    assert len(res.findings) == 0


def test_20_user_triggered_modal_handled():
    """20. Test user-triggered dialog is treated as functional modal."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "video-modal",
                "type": "video_player",
                "visible": True,
                "user_triggered": True,
            }
        ],
    }

    res = analyze_popups(data)
    assert len(res.findings) == 0


def test_21_missing_viewport_uses_default():
    """21. Test missing viewport uses 1366x768 default."""
    data = {
        "url": "https://example.com",
        "popups": [
            {"id": "pop-1", "visible": True, "has_close_button": True}
        ],
    }

    res = analyze_popups(data)
    assert res.status == "passed"


def test_22_missing_bbox_handled_safely():
    """22. Test missing bbox does not fail and sets coverage unknown."""
    data = {
        "url": "https://example.com",
        "popups": [
            {"id": "pop-1", "type": "promo", "visible": True, "has_close_button": True}
        ],
    }

    res = analyze_popups(data)
    assert res.status == "passed"


def test_23_malformed_popup_item_skipped():
    """23. Test malformed items in popup list are safely handled."""
    data = {
        "url": "https://example.com",
        "popups": [
            None,
            "corrupt string",
            123,
            {"id": "valid-pop", "visible": True, "has_close_button": True},
        ],
    }

    res = analyze_popups(data)
    assert res.metrics["popups_analyzed"] == 1


def test_24_deterministic_finding_ids():
    """24. Test finding IDs are completely deterministic."""
    data = {
        "url": "https://example.com",
        "popups": [
            {"id": "pop-1", "type": "promo", "visible": True, "has_close_button": False}
        ],
    }

    res1 = analyze_popups(data)
    res2 = analyze_popups(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_25_deterministic_scoring_bounds():
    """25. Test deterministic score is an int bounded between 0 and 100."""
    data = {
        "url": "https://example.com",
        "popups": [
            {"id": "pop-1", "type": "promo", "visible": True, "has_close_button": False}
        ],
    }

    res = analyze_popups(data)
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100
    assert res.score < 100


def test_26_none_input_handled():
    """26. Test None input returns status 'insufficient_evidence'."""
    res = analyze_popups(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_27_page_input_data_support():
    """27. Test direct PageInputData instance support."""
    input_data = PageInputData(
        url="https://example.com",
        popups=[
            PopupEvidence(name="Test Modal", type="newsletter", is_blocking=True, immediate=False, has_close_button=True)
        ],
    )

    res = analyze_popups(input_data)
    assert res.status == "passed"
    assert res.score == 100


def test_28_dictionary_alias_modals():
    """28. Test dictionary key alias 'modals'."""
    data = {
        "url": "https://example.com",
        "modals": [{"name": "Modal 1", "visible": True, "has_close_button": True}],
    }

    res = analyze_popups(data)
    assert res.metrics["popups_analyzed"] == 1


def test_29_dictionary_alias_overlays():
    """29. Test dictionary key alias 'overlays'."""
    data = {
        "url": "https://example.com",
        "overlays": [{"name": "Overlay 1", "visible": True, "has_close_button": True}],
    }

    res = analyze_popups(data)
    assert res.metrics["popups_analyzed"] == 1


def test_30_dictionary_alias_interstitials():
    """30. Test dictionary key alias 'interstitials'."""
    data = {
        "url": "https://example.com",
        "interstitials": [{"name": "Interstitial 1", "visible": True, "has_close_button": True}],
    }

    res = analyze_popups(data)
    assert res.metrics["popups_analyzed"] == 1


def test_31_multiple_stacked_penalties():
    """31. Test popup with multiple issues (immediate + no close + CTA blocked)."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-severe",
                "type": "promo",
                "visible": True,
                "delay_ms": 0,
                "blocks_interaction": True,
                "has_close_button": False,
                "covered_elements": ["primary_cta"],
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "failed"
    assert res.score < 60
    assert len(res.findings) == 3


def test_32_status_warning_for_medium_findings():
    """32. Test status is 'warning' when only medium findings exist."""
    data = {
        "url": "https://example.com",
        "popups": [
            {
                "id": "pop-med",
                "type": "newsletter",
                "visible": True,
                "delay_ms": 0,
                "blocks_interaction": True,
                "has_close_button": True,  # has close button -> only immediate load finding
            }
        ],
    }

    res = analyze_popups(data)
    assert res.status == "warning"
    assert len(res.findings) == 1
    assert res.findings[0].id == "ENG-POP-007"
