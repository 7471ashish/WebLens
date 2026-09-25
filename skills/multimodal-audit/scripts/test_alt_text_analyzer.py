"""
Unit Test Suite for Alternative Text & Description Quality Analyzer (test_alt_text_analyzer.py)
=================================================================================================
Tests missing alt attributes, empty alt attributes, decorative tagging, generic text,
placeholders, filename leaks, length bounds, functional affordances, and determinism.
"""

from __future__ import annotations

import json
import os
import sys
import pytest

# Ensure scripts directory is on sys.path regardless of test location
_current_dir = os.path.abspath(os.path.dirname(__file__))
for _cand in [
    os.path.join(_current_dir, "scripts"),
    os.path.join(_current_dir, "..", "scripts"),
]:
    _cand_abs = os.path.abspath(_cand)
    if os.path.isdir(_cand_abs) and _cand_abs not in sys.path:
        sys.path.insert(0, _cand_abs)

from alt_text_analyzer import AltTextAnalyzer, analyze_alt_text
from multimodal_state import MultimodalCategoryResult, MultimodalFinding, Severity



# 1. Valid Alt Text & Baseline Execution


def test_valid_informative_alt_text():
    data = {
        "url": "https://example.com/product",
        "images": [
            {
                "id": "img-001",
                "alt_text": "Adobe Photoshop user interface demonstrating generative fill tools",
                "alt_attribute_present": True,
                "decorative": False,
            }
        ],
    }
    result = analyze_alt_text(data)
    assert result["category"] == "alt_text"
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert len(result["findings"]) == 0
    assert result["metrics"]["images_with_alt"] == 1


def test_no_alt_text_evidence():
    result = analyze_alt_text(None)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None


def test_explicit_no_images():
    result = analyze_alt_text({"images": [], "images_checked": True})
    assert result["status"] == "passed"
    assert result["score"] == 100



# 2. Missing & Empty Alt Attribute Checks


def test_missing_alt_attribute():
    data = {
        "images": [
            {"id": "img-missing", "alt_attribute_present": False, "decorative": False}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-001" for f in result["findings"])
    assert result["metrics"]["images_missing_alt"] == 1


def test_unknown_alt_state_does_not_fail():
    data = {
        "images": [
            {"id": "img-unknown", "alt_attribute_present": None, "alt_text": None}
        ]
    }
    result = analyze_alt_text(data)
    # Missing/unknown evidence should not manufacture a missing alt failure
    assert not any(f["id"] == "MM-ALT-001" for f in result["findings"])


def test_empty_alt_on_informative_image():
    data = {
        "images": [
            {"id": "img-empty", "alt_text": "", "alt_attribute_present": True, "decorative": False}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-002" for f in result["findings"])


def test_whitespace_only_alt_treated_as_empty():
    data = {
        "images": [
            {"id": "img-space", "alt_text": "    ", "alt_attribute_present": True, "decorative": False}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-002" for f in result["findings"])



# 3. Decorative Image Handling


def test_decorative_image_with_empty_alt_passes():
    data = {
        "images": [
            {"id": "img-dec", "alt_text": "", "alt_attribute_present": True, "decorative": True}
        ]
    }
    result = analyze_alt_text(data)
    assert result["status"] == "passed"
    assert len(result["findings"]) == 0
    assert result["metrics"]["images_decorative"] == 1


def test_decorative_image_with_literal_decorative_label():
    data = {
        "images": [
            {"id": "img-dec-label", "alt_text": "decorative image", "decorative": True}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-003" for f in result["findings"])



# 4. Semantic Quality & Anti-Pattern Checks


@pytest.mark.parametrize("generic_word", ["image", "photo", "picture", "graphic", "img", "icon"])
def test_generic_uninformative_alt_text(generic_word):
    data = {
        "images": [
            {"id": "img-gen", "alt_text": generic_word, "alt_attribute_present": True}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-004" for f in result["findings"])


@pytest.mark.parametrize("placeholder", ["image here", "insert image", "placeholder", "test", "lorem ipsum"])
def test_placeholder_alt_text(placeholder):
    data = {
        "images": [
            {"id": "img-ph", "alt_text": placeholder, "alt_attribute_present": True}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-005" for f in result["findings"])


@pytest.mark.parametrize("filename", ["IMG_20260902.jpg", "hero-banner-final.png", "icon_01.webp", "DSC00452.JPG"])
def test_filename_in_alt_text(filename):
    data = {
        "images": [
            {"id": "img-file", "alt_text": filename, "alt_attribute_present": True}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-006" for f in result["findings"])


def test_excessively_long_alt_text():
    data = {
        "images": [
            {"id": "img-long", "alt_text": "A " * 150, "alt_attribute_present": True}  # 300 chars
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-007" for f in result["findings"])


def test_functional_image_appearance_description():
    data = {
        "images": [
            {"id": "btn-search", "alt_text": "magnifying glass", "role": "functional", "href": "/search"}
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-009" for f in result["findings"])


def test_repetitive_alt_text_across_distinct_images():
    data = {
        "images": [
            {"id": "img-1", "alt_text": "product card", "role": "content"},
            {"id": "img-2", "alt_text": "product card", "role": "content"},
            {"id": "img-3", "alt_text": "product card", "role": "content"},
        ]
    }
    result = analyze_alt_text(data)
    assert any(f["id"] == "MM-ALT-008" for f in result["findings"])



# 5. Boundaries & Determinism


def test_alt_text_analyzer_does_not_emit_other_category_findings():
    data = {
        "images": [
            {"id": "img-1", "width": -50, "ocr_text": "sample text", "broken": True}
        ]
    }
    result = analyze_alt_text(data)
    for f in result["findings"]:
        assert f["id"].startswith("MM-ALT-")


def test_alt_text_determinism():
    data = {
        "images": [
            {"id": "img-1", "alt_text": "Adobe Photoshop interface"}
        ]
    }
    res1 = analyze_alt_text(data)
    res2 = analyze_alt_text(data)
    assert res1 == res2
    assert json.dumps(res1) == json.dumps(res2)
