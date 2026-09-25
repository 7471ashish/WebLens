"""
Unit tests for dom_comparator.py using pytest.

Tests all 30 scenarios specified in the requirements deterministically without network access.
"""

from __future__ import annotations

import json
import pytest
from dom_comparator import compare_dom


@pytest.fixture
def base_raw_result():
    return {
        "skill": "crawl-render-audit",
        "component": "raw_html_analyzer",
        "target_url": "https://example.com",
        "response": {"final_url": "https://example.com"},
        "html_metrics": {
            "html_bytes": 35000,
            "element_count": 200,
            "script_count": 12,
        },
        "title": {"exists": True, "value": "Example"},
        "meta_description": {"exists": True, "content": "Overview"},
        "canonical": {"exists": True, "value": "https://example.com"},
        "text_content": {"character_count": 300, "word_count": 50},
        "headings": {
            "h1_count": 0,
            "h2_count": 0,
            "h3_count": 0,
            "h4_count": 0,
            "h5_count": 0,
            "h6_count": 0,
            "h1_text": [],
        },
        "links": {"total": 3, "internal": 2, "external": 1, "relative": 1},
        "structured_data": {"json_ld_blocks": 0, "valid_json_ld_blocks": 0, "types": []},
        "semantic_structure": {"main": 0, "article": 0, "section": 0, "nav": 0, "header": 0, "footer": 0, "aside": 0, "figure": 0, "figcaption": 0},
        "machine_readability": {
            "has_meaningful_text": False,
            "has_title": True,
            "has_headings": False,
            "has_links": True,
            "has_structured_data": False,
            "has_semantic_main": False,
        },
        "spa_indicators": {"possible_client_rendered_shell": True},
    }


@pytest.fixture
def base_rendered_result():
    return {
        "skill": "crawl-render-audit",
        "component": "render_analyzer",
        "target_url": "https://example.com",
        "navigation": {"final_url": "https://example.com", "status_code": 200},
        "rendering": {
            "status": "success",
            "navigation_completed": True,
            "javascript_executed": True,
        },
        "dom": {"size_bytes": 250000, "element_count": 1000},
        "title": {"exists": True, "value": "Example"},
        "meta_description": {"exists": True, "content": "Overview"},
        "canonical": {"exists": True, "value": "https://example.com"},
        "text_content": {
            "visible_character_count": 12000,
            "visible_word_count": 2200,
            "meaningful_text_available": True,
        },
        "headings": {
            "h1_count": 1,
            "h2_count": 5,
            "h3_count": 0,
            "h4_count": 0,
            "h5_count": 0,
            "h6_count": 0,
            "h1_text": ["Example Product"],
        },
        "links": {"total": 45, "internal": 38, "external": 7, "relative": 10},
        "structured_data": {"json_ld_blocks": 2, "valid_json_ld_blocks": 2, "types": ["Product", "BreadcrumbList"]},
        "semantic_structure": {"main": 1, "article": 1, "section": 4, "nav": 1, "header": 1, "footer": 1, "aside": 0, "figure": 0, "figcaption": 0},
        "machine_readability": {
            "has_meaningful_text": True,
            "has_title": True,
            "has_headings": True,
            "has_links": True,
            "has_structured_data": True,
            "has_semantic_main": True,
        },
        "javascript": {"error_count": 0, "errors": []},
        "resources": {"failed_count": 0, "failed": []},
    }


def test_1_identical_raw_rendered(base_raw_result, base_rendered_result):
    """1. Test identical raw and rendered content metrics."""
    # Match rendered to raw
    base_rendered_result["text_content"]["visible_character_count"] = 300
    base_rendered_result["headings"]["h1_count"] = 0
    base_rendered_result["headings"]["h2_count"] = 0
    base_rendered_result["headings"]["h1_text"] = []
    base_rendered_result["links"]["total"] = 3
    base_rendered_result["structured_data"]["json_ld_blocks"] = 0
    base_rendered_result["structured_data"]["valid_json_ld_blocks"] = 0
    base_rendered_result["structured_data"]["types"] = []
    base_rendered_result["semantic_structure"]["main"] = 0
    base_rendered_result["semantic_structure"]["article"] = 0

    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["text_content"]["difference"] == 0
    assert res["text_content"]["substantial_content_added"] is False
    assert res["headings"]["h1_added_after_render"] is False
    assert res["structured_data"]["json_ld_added_after_render"] is False


def test_2_large_text_increase(base_raw_result, base_rendered_result):
    """2. Test detection of large visible text increase."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["text_content"]["difference"] == 11700
    assert res["text_content"]["substantial_content_added"] is True
    assert res["rendering_dependency"]["substantial_text_added"] is True


def test_3_zero_raw_text(base_raw_result, base_rendered_result):
    """3. Test zero raw text with substantial rendered text."""
    base_raw_result["text_content"]["character_count"] = 0
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["text_content"]["raw_characters"] == 0
    assert res["text_content"]["rendered_to_raw_ratio"] is None
    assert res["text_content"]["content_added_after_render"] is True
    assert res["text_content"]["substantial_content_added"] is True


def test_4_heading_added_after_rendering(base_raw_result, base_rendered_result):
    """4. Test H1 heading addition."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["headings"]["h1_added_after_render"] is True
    assert res["headings"]["rendered_h1_count"] == 1
    assert res["headings"]["rendered_h1_text"] == ["Example Product"]


def test_5_multiple_heading_changes(base_raw_result, base_rendered_result):
    """5. Test H1 and H2 heading additions."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["headings"]["h1_added_after_render"] is True
    assert res["headings"]["h2_added_after_render"] is True
    assert res["rendering_dependency"]["headings_added"] is True


def test_6_links_added(base_raw_result, base_rendered_result):
    """6. Test links count addition."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["links"]["links_added_after_render"] == 42
    assert res["links"]["substantial_link_change"] is True


def test_7_structured_data_added(base_raw_result, base_rendered_result):
    """7. Test JSON-LD structured data addition."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["structured_data"]["json_ld_added_after_render"] is True
    assert res["structured_data"]["rendered_json_ld_blocks"] == 2


def test_8_structured_data_types_added(base_raw_result, base_rendered_result):
    """8. Test detected @type additions in structured data."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert "Product" in res["structured_data"]["types_added_after_render"]
    assert "BreadcrumbList" in res["structured_data"]["types_added_after_render"]


def test_9_semantic_main_added(base_raw_result, base_rendered_result):
    """9. Test semantic <main> tag addition."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["semantic_structure"]["main"]["added_after_render"] is True
    assert res["semantic_structure"]["article"]["added_after_render"] is True


def test_10_title_changed(base_raw_result, base_rendered_result):
    """10. Test dynamic title modification."""
    base_rendered_result["title"]["value"] = "Example Product - Online Store"
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["title"]["changed"] is True
    assert res["title"]["rendered_value"] == "Example Product - Online Store"


def test_11_meta_description_changed(base_raw_result, base_rendered_result):
    """11. Test dynamic meta description change."""
    base_rendered_result["meta_description"]["content"] = "A newly rendered description"
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["meta_description"]["changed"] is True


def test_12_canonical_added(base_raw_result, base_rendered_result):
    """12. Test canonical tag injected after rendering."""
    base_raw_result["canonical"]["exists"] = False
    base_raw_result["canonical"]["value"] = None
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["canonical"]["added_after_render"] is True


def test_13_machine_readability_improved(base_raw_result, base_rendered_result):
    """13. Test machine readability metrics improvement."""
    res = compare_dom(base_raw_result, base_rendered_result)
    mr = res["machine_readability"]
    assert mr["meaningful_text"]["improved_after_render"] is True
    assert mr["headings"]["improved_after_render"] is True
    assert mr["structured_data"]["improved_after_render"] is True


def test_14_raw_shell_to_rendered_content(base_raw_result, base_rendered_result):
    """14. Test high-confidence client shell scenario."""
    res = compare_dom(base_raw_result, base_rendered_result)
    dep = res["rendering_dependency"]
    assert dep["possible_client_rendered_shell"] is True
    assert dep["meaningful_content_added_after_render"] is True
    assert dep["content_available_only_after_render"] is True


def test_15_rendering_remains_empty(base_raw_result, base_rendered_result):
    """15. Test scenario where page remains empty even after rendering."""
    base_rendered_result["text_content"]["visible_character_count"] = 150
    base_rendered_result["text_content"]["meaningful_text_available"] = False
    base_rendered_result["headings"]["h1_count"] = 0
    base_rendered_result["headings"]["h2_count"] = 0
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["rendering_dependency"]["meaningful_content_added_after_render"] is False
    assert res["rendering_dependency"]["rendering_did_not_materially_increase_content"] is True


def test_16_rendering_failed(base_raw_result, base_rendered_result):
    """16. Test browser rendering failure."""
    base_rendered_result["rendering"]["status"] = "browser_error"
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["comparison"]["status"] == "partial"
    assert res["comparison"]["confidence"] == "low"


def test_17_partial_rendering(base_raw_result, base_rendered_result):
    """17. Test partial rendering due to timeout."""
    base_rendered_result["rendering"]["status"] = "partial"
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["comparison"]["status"] == "partial"
    assert res["comparison"]["confidence"] == "medium"


def test_18_missing_raw_metrics(base_rendered_result):
    """18. Test comparator resilience when raw_result is missing sections."""
    res = compare_dom({}, base_rendered_result)
    assert res["comparison"]["status"] == "partial"
    assert res["text_content"]["raw_characters"] is None


def test_19_missing_rendered_metrics(base_raw_result):
    """19. Test comparator resilience when rendered_result is missing sections."""
    res = compare_dom(base_raw_result, {})
    assert res["comparison"]["status"] == "partial"
    assert res["text_content"]["rendered_characters"] is None


def test_20_division_by_zero(base_raw_result, base_rendered_result):
    """20. Test handling of 0 raw bytes and 0 raw characters without NaN/Inf."""
    base_raw_result["html_metrics"]["html_bytes"] = 0
    base_raw_result["text_content"]["character_count"] = 0
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["dom_size"]["percentage_change"] == 100.0
    assert res["text_content"]["rendered_to_raw_ratio"] is None


def test_21_dom_size_increase(base_raw_result, base_rendered_result):
    """21. Test DOM size increase calculation."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["dom_size"]["difference_bytes"] == 215000
    assert res["dom_size"]["percentage_change"] == 614.29


def test_22_dom_size_decrease(base_raw_result, base_rendered_result):
    """22. Test DOM size reduction."""
    base_raw_result["html_metrics"]["html_bytes"] = 300000
    base_rendered_result["dom"]["size_bytes"] = 150000
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["dom_size"]["difference_bytes"] == -150000
    assert res["dom_size"]["percentage_change"] == -50.0


def test_23_same_final_url(base_raw_result, base_rendered_result):
    """23. Test URL consistency."""
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["navigation"]["same_final_url"] is True


def test_24_different_final_url(base_raw_result, base_rendered_result):
    """24. Test redirect to different URL during rendering."""
    base_rendered_result["navigation"]["final_url"] = "https://example.com/login"
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["navigation"]["same_final_url"] is False
    assert res["navigation"]["rendered_final_url"] == "https://example.com/login"


def test_25_js_errors_in_rendered_result(base_raw_result, base_rendered_result):
    """25. Test propagation of JS error counts."""
    base_rendered_result["javascript"]["error_count"] = 3
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["rendering_context"]["javascript_error_count"] == 3


def test_26_failed_resources_in_rendered_result(base_raw_result, base_rendered_result):
    """26. Test propagation of failed network resource counts."""
    base_rendered_result["resources"]["failed_count"] = 2
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["rendering_context"]["failed_resource_count"] == 2


def test_27_threshold_configuration(base_raw_result, base_rendered_result):
    """27. Test custom threshold configuration."""
    custom_opts = {
        "min_text_increase_chars": 20000,  # 12000 rendered will not meet 20000
        "min_text_increase_ratio": 100.0,
    }
    res = compare_dom(base_raw_result, base_rendered_result, options=custom_opts)
    assert res["text_content"]["substantial_content_added"] is False


def test_28_false_positive_cases(base_raw_result, base_rendered_result):
    """28. Test subtle 50-character increase does NOT trigger substantial flag."""
    base_raw_result["text_content"]["character_count"] = 1000
    base_rendered_result["text_content"]["visible_character_count"] = 1050
    res = compare_dom(base_raw_result, base_rendered_result)
    assert res["text_content"]["substantial_content_added"] is False


def test_29_json_serializability(base_raw_result, base_rendered_result):
    """29. Test that comparator output is 100% JSON-serializable."""
    res = compare_dom(base_raw_result, base_rendered_result)
    serialized = json.dumps(res)
    deserialized = json.loads(serialized)
    assert deserialized["component"] == "dom_comparator"
    assert len(deserialized["evidence"]) >= 4


def test_30_deterministic_output(base_raw_result, base_rendered_result):
    """30. Test that multiple runs produce bit-exact identical output."""
    res1 = compare_dom(base_raw_result, base_rendered_result)
    res2 = compare_dom(base_raw_result, base_rendered_result)
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)
