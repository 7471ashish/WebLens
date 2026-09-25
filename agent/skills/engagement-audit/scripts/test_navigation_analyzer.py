"""
Unit test suite for navigation_analyzer.py using pytest.
Tests all 25 navigation scenarios deterministically without external dependencies.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import (
    NavigationItem,
    PageInputData,
)
from navigation_analyzer import (
    NavigationAnalyzer,
    NormalizedNavItem,
    analyze_navigation,
    normalize_navigation_evidence,
)


def test_1_valid_primary_navigation():
    """1. Test clean, standard primary navigation passes with score 100."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Products", "href": "/products", "visible": True},
            {"id": "nav-2", "label": "Solutions", "href": "/solutions", "visible": True},
            {"id": "nav-3", "label": "Pricing", "href": "/pricing", "visible": True},
            {"id": "nav-4", "label": "Docs", "href": "/docs", "visible": True},
            {"id": "nav-5", "label": "About", "href": "/about", "visible": True},
        ],
    }

    res = analyze_navigation(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0
    assert res.metrics["primary_items_count"] == 5


def test_2_missing_navigation_evidence():
    """2. Test missing navigation evidence returns status 'insufficient_evidence'."""
    data = {"url": "https://example.com"}
    res = analyze_navigation(data)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_3_explicitly_empty_navigation():
    """3. Test explicitly empty navigation list generates warning finding."""
    data = {"url": "https://example.com", "navigation": []}
    res = analyze_navigation(data)
    assert res.status == "warning"
    assert len(res.findings) == 1
    assert res.findings[0].id == "ENG-NAV-001"


def test_4_hidden_navigation_item():
    """4. Test hidden navigation item (visible=False) generates ENG-NAV-001."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Pricing", "href": "/pricing", "visible": False}
        ],
    }

    res = analyze_navigation(data)
    assert res.status == "failed"
    hid_f = [f for f in res.findings if f.id == "ENG-NAV-001"]
    assert len(hid_f) == 1
    assert hid_f[0].severity == "high"


def test_5_obstructed_navigation_item():
    """5. Test obstructed navigation item (obstructed=True) generates ENG-NAV-002."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Account", "obstructed": True}
        ],
    }

    res = analyze_navigation(data)
    obs_f = [f for f in res.findings if f.id == "ENG-NAV-002"]
    assert len(obs_f) == 1
    assert obs_f[0].severity == "high"


def test_6_duplicate_navigation_labels():
    """6. Test duplicate navigation labels in the same level generates ENG-NAV-005."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Products", "href": "/products-1"},
            {"id": "nav-2", "label": "Products", "href": "/products-2"},
        ],
    }

    res = analyze_navigation(data)
    dup_f = [f for f in res.findings if f.id == "ENG-NAV-005"]
    assert len(dup_f) == 1
    assert dup_f[0].severity == "low"


def test_7_redundant_destinations():
    """7. Test duplicate href destinations in primary menu generates ENG-NAV-010."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Features", "href": "/features"},
            {"id": "nav-2", "label": "Capabilities", "href": "/features"},
        ],
    }

    res = analyze_navigation(data)
    dest_f = [f for f in res.findings if f.id == "ENG-NAV-010"]
    assert len(dest_f) == 1
    assert dest_f[0].severity == "low"


def test_8_deep_navigation_hierarchy():
    """8. Test menu depth of 6 levels (exceeding max 4) generates ENG-NAV-008."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {
                "id": "nav-1",
                "label": "Level 1",
                "depth": 1,
                "children": [
                    {
                        "id": "nav-2",
                        "label": "Level 2",
                        "depth": 2,
                        "children": [
                            {
                                "id": "nav-3",
                                "label": "Level 3",
                                "depth": 3,
                                "children": [
                                    {
                                        "id": "nav-4",
                                        "label": "Level 4",
                                        "depth": 4,
                                        "children": [
                                            {
                                                "id": "nav-5",
                                                "label": "Level 5",
                                                "depth": 5,
                                                "children": [
                                                    {"id": "nav-6", "label": "Level 6", "depth": 6}
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    res = analyze_navigation(data)
    depth_f = [f for f in res.findings if f.id == "ENG-NAV-008"]
    assert len(depth_f) == 1
    assert depth_f[0].severity == "medium"
    assert res.metrics["max_depth"] == 6


def test_9_broken_parent_child_relationship():
    """9. Test orphaned item referencing non-existent parent_id generates ENG-NAV-006."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Products"},
            {"id": "nav-sub-1", "label": "Sub Product", "parent_id": "non-existent-parent-99"},
        ],
    }

    res = analyze_navigation(data)
    orph_f = [f for f in res.findings if f.id == "ENG-NAV-006"]
    assert len(orph_f) == 1
    assert orph_f[0].severity == "medium"


def test_10_empty_navigation_labels():
    """10. Test unlabelled / empty navigation items generates ENG-NAV-003."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Home"},
            {"id": "nav-2", "label": ""},
        ],
    }

    res = analyze_navigation(data)
    empty_f = [f for f in res.findings if f.id == "ENG-NAV-003"]
    assert len(empty_f) == 1
    assert empty_f[0].severity == "medium"


def test_11_ambiguous_symbol_labels():
    """11. Test symbol-only labels ('>', '...') generates ENG-NAV-004."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Home"},
            {"id": "nav-2", "label": "..."},
        ],
    }

    res = analyze_navigation(data)
    symb_f = [f for f in res.findings if f.id == "ENG-NAV-004"]
    assert len(symb_f) == 1
    assert symb_f[0].severity == "low"


def test_12_single_active_item_valid():
    """12. Test single active navigation item produces 0 active-state findings."""
    data = {
        "url": "https://example.com/pricing",
        "navigation": [
            {"id": "nav-1", "label": "Home", "active": False},
            {"id": "nav-2", "label": "Pricing", "active": True},
        ],
    }

    res = analyze_navigation(data)
    active_f = [f for f in res.findings if f.id == "ENG-NAV-011"]
    assert len(active_f) == 0


def test_13_multiple_contradictory_active_items():
    """13. Test multiple items marked active simultaneously generates ENG-NAV-011."""
    data = {
        "url": "https://example.com",
        "navigation": [
            {"id": "nav-1", "label": "Home", "active": True},
            {"id": "nav-2", "label": "Products", "active": True},
            {"id": "nav-3", "label": "Pricing", "active": True},
        ],
    }

    res = analyze_navigation(data)
    active_f = [f for f in res.findings if f.id == "ENG-NAV-011"]
    assert len(active_f) == 1
    assert active_f[0].severity == "low"


def test_14_valid_breadcrumb_hierarchy():
    """14. Test clean breadcrumb trail produces 0 breadcrumb findings."""
    data = {
        "url": "https://example.com/software",
        "navigation": {
            "primary": [{"id": "nav-1", "label": "Home"}],
            "breadcrumbs": ["Home", "Products", "Creative Software"],
        },
    }

    res = analyze_navigation(data)
    bc_f = [f for f in res.findings if f.id == "ENG-NAV-012"]
    assert len(bc_f) == 0


def test_15_invalid_breadcrumb_structure():
    """15. Test duplicate consecutive breadcrumbs generates ENG-NAV-012."""
    data = {
        "url": "https://example.com/software",
        "navigation": {
            "primary": [{"id": "nav-1", "label": "Home"}],
            "breadcrumbs": ["Home", "Products", "Products", "Software"],
        },
    }

    res = analyze_navigation(data)
    bc_f = [f for f in res.findings if f.id == "ENG-NAV-012"]
    assert len(bc_f) == 1
    assert bc_f[0].severity == "low"


def test_16_multiple_navigation_groups():
    """16. Test multiple navigation groups (primary, secondary, footer)."""
    data = {
        "url": "https://example.com",
        "navigation": {
            "primary": [{"label": "Home"}, {"label": "Products"}],
            "secondary": [{"label": "Support"}, {"label": "Login"}],
            "footer": [{"label": "Privacy Policy"}, {"label": "Terms"}],
        },
    }

    res = analyze_navigation(data)
    assert res.metrics["groups_count"] == 3
    assert res.metrics["total_items_count"] == 6


def test_17_excessive_primary_complexity():
    """17. Test primary menu with 14 items (exceeding 10) generates ENG-NAV-009."""
    data = {
        "url": "https://example.com",
        "navigation": [{"id": f"nav-{i}", "label": f"Menu {i}"} for i in range(14)],
    }

    res = analyze_navigation(data)
    comp_f = [f for f in res.findings if f.id == "ENG-NAV-009"]
    assert len(comp_f) == 1
    assert comp_f[0].severity == "low"


def test_18_mobile_navigation_missing_toggle():
    """18. Test mobile navigation missing toggle generates ENG-NAV-013."""
    data = {
        "url": "https://example.com",
        "navigation": [{"label": "Home"}],
        "mobile_navigation": {
            "missing_hamburger_toggle": True,
            "mobile_nav_hidden": True,
        },
    }

    res = analyze_navigation(data)
    mob_f = [f for f in res.findings if f.id == "ENG-NAV-013"]
    assert len(mob_f) == 1
    assert mob_f[0].severity == "high"


def test_19_missing_mobile_evidence_graceful():
    """19. Test missing mobile navigation evidence does not fail."""
    data = {
        "url": "https://example.com",
        "navigation": [{"label": "Home"}],
    }

    res = analyze_navigation(data)
    mob_f = [f for f in res.findings if f.id == "ENG-NAV-013"]
    assert len(mob_f) == 0


def test_20_malformed_navigation_item_safely_skipped():
    """20. Test malformed items (None, numbers, corrupt dict) safely parsed."""
    data = {
        "url": "https://example.com",
        "navigation": [
            None,
            "Valid String Item",
            12345,
            {"label": "Valid Dict Item"},
        ],
    }

    res = analyze_navigation(data)
    assert res.metrics["total_items_count"] == 2


def test_21_deterministic_finding_ids():
    """21. Test finding IDs are strictly deterministic."""
    data = {
        "url": "https://example.com",
        "navigation": [{"label": ""}],
    }

    res1 = analyze_navigation(data)
    res2 = analyze_navigation(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_22_deterministic_score():
    """22. Test deterministic score bounds (0-100)."""
    data = {
        "url": "https://example.com",
        "navigation": [{"label": "Home", "visible": False}],
    }

    res = analyze_navigation(data)
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100
    assert res.score < 100


def test_23_none_input_handled():
    """23. Test None input returns status 'insufficient_evidence'."""
    res = analyze_navigation(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_24_page_input_data_support():
    """24. Test direct PageInputData instance support."""
    input_data = PageInputData(
        url="https://example.com",
        navigation=[
            NavigationItem(text="Products", href="/products"),
            NavigationItem(text="Pricing", href="/pricing"),
        ],
    )

    res = analyze_navigation(input_data)
    assert res.status == "passed"
    assert res.score == 100


def test_25_dictionary_key_alias_menus():
    """25. Test dictionary key alias 'menus'."""
    data = {
        "url": "https://example.com",
        "menus": [{"label": "Home"}, {"label": "About"}],
    }

    res = analyze_navigation(data)
    assert res.metrics["total_items_count"] == 2
