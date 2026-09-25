"""
Unit Test Suite for Image Quality & Visual Asset Analyzer (test_image_analyzer.py)
==================================================================================
Tests image validity, geometry, severe upscaling, aspect ratio distortion,
problematic cropping, duplicate detection, and vision provider injection.
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

from image_analyzer import ImageAnalyzer, analyze_images
from multimodal_state import MultimodalCategoryResult, MultimodalFinding, Severity



# Fixtures


@pytest.fixture
def valid_image_data() -> dict:
    return {
        "url": "https://example.com/product",
        "images": [
            {
                "id": "img-001",
                "url": "https://example.com/images/hero.jpg",
                "width": 1600,
                "height": 900,
                "visible": True,
                "position": {"x": 100, "y": 100, "width": 800, "height": 450},
                "page_context": {"title": "Adobe Product", "heading": "Create better experiences"},
            }
        ],
    }



# 1. Image Availability & Basic Execution


def test_valid_clean_image(valid_image_data):
    result = analyze_images(valid_image_data)
    assert result["category"] == "image"
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert len(result["findings"]) == 0
    assert result["metrics"]["images_analyzed"] == 1


def test_no_image_evidence():
    result = analyze_images(None)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None
    assert result["evaluated"] is False


def test_explicit_no_images_checked():
    result = analyze_images({"images": [], "images_checked": True})
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert result["evaluated"] is True



# 2. Dimensions & Validity Checks


def test_invalid_zero_and_negative_dimensions():
    data = {
        "images": [
            {"id": "img-zero", "width": 0, "height": 500},
            {"id": "img-neg", "width": 800, "height": -200},
        ]
    }
    result = analyze_images(data)
    finding_ids = [f["id"] for f in result["findings"]]
    assert "MM-IMG-002" in finding_ids
    assert result["score"] < 100


def test_broken_image_detection():
    data = {
        "images": [
            {"id": "img-broken", "url": "https://example.com/404.jpg", "broken": True}
        ]
    }
    result = analyze_images(data)
    assert result["status"] == "failed"
    assert any(f["id"] == "MM-IMG-001" for f in result["findings"])
    assert any(f["severity"] == "high" for f in result["findings"])



# 3. Geometry, Scaling & Distortion Checks


def test_severe_upscaling_detection():
    data = {
        "images": [
            {
                "id": "img-small",
                "width": 100,
                "height": 100,
                "position": {"x": 0, "y": 0, "width": 500, "height": 500},  # 5x upscale
            }
        ]
    }
    result = analyze_images(data)
    assert any(f["id"] == "MM-IMG-003" for f in result["findings"])


def test_aspect_ratio_distortion():
    data = {
        "images": [
            {
                "id": "img-distort",
                "width": 1600,
                "height": 400,  # 4:1 intrinsic
                "position": {"x": 0, "y": 0, "width": 400, "height": 400},  # 1:1 rendered
            }
        ]
    }
    result = analyze_images(data)
    assert any(f["id"] == "MM-IMG-004" for f in result["findings"])


def test_object_fit_cover_avoids_distortion_penalty():
    data = {
        "images": [
            {
                "id": "img-cropped-clean",
                "width": 1600,
                "height": 400,
                "object_fit": "cover",
                "position": {"x": 0, "y": 0, "width": 400, "height": 400},
            }
        ]
    }
    result = analyze_images(data)
    assert not any(f["id"] == "MM-IMG-004" for f in result["findings"])


def test_problematic_crop_detection():
    data = {
        "images": [
            {
                "id": "img-crop",
                "crop_issue": "Face truncated at top boundary",
            }
        ]
    }
    result = analyze_images(data)
    assert any(f["id"] == "MM-IMG-005" for f in result["findings"])



# 4. Duplicate & Decorative Handling


def test_duplicate_image_urls():
    data = {
        "images": [
            {"id": "img-1", "url": "https://example.com/asset.jpg", "width": 800, "height": 600},
            {"id": "img-2", "url": "https://example.com/asset.jpg", "width": 800, "height": 600},
        ]
    }
    result = analyze_images(data)
    assert any(f["id"] == "MM-IMG-006" for f in result["findings"])


def test_decorative_image_not_penalized():
    data = {
        "images": [
            {
                "id": "img-bg",
                "decorative": True,
                "width": 100,
                "height": 100,
                "position": {"x": 0, "y": 0, "width": 400, "height": 400},  # upscaled decorative
            }
        ]
    }
    result = analyze_images(data)
    # Decorative images should not trigger severe upscaling / distortion penalties
    assert not any(f["id"] in ("MM-IMG-003", "MM-IMG-004") for f in result["findings"])



# 5. Vision Provider Injection


def test_vision_provider_injection_success():
    def mock_vision(img, ctx):
        if img.id == "img-blur":
            return {"is_low_quality": True, "quality_reason": "Excessive JPEG compression artifacts", "confidence": 0.95}
        return None

    analyzer = ImageAnalyzer(vision_provider=mock_vision)
    data = {
        "images": [
            {"id": "img-blur", "width": 800, "height": 600}
        ]
    }
    result = analyzer.analyze(data)
    assert any(f["id"] == "MM-IMG-007" for f in result["findings"])
    assert result["metrics"]["images_with_visual_analysis"] == 1


def test_vision_provider_failure_graceful_handling():
    def failing_vision(img, ctx):
        raise RuntimeError("Vision service unavailable")

    analyzer = ImageAnalyzer(vision_provider=failing_vision)
    data = {
        "images": [{"id": "img-1", "width": 800, "height": 600}]
    }
    result = analyzer.analyze(data)
    assert result["status"] == "passed"
    assert result["score"] == 100



# 6. Boundaries & Determinism


def test_image_analyzer_does_not_emit_alt_or_ocr_findings():
    data = {
        "images": [
            {"id": "img-1", "alt_text": "", "ocr_text": "sample text"}
        ]
    }
    result = analyze_images(data)
    for f in result["findings"]:
        assert not f["id"].startswith("MM-ALT-")
        assert not f["id"].startswith("MM-OCR-")
        assert not f["id"].startswith("MM-META-")
        assert not f["id"].startswith("MM-CHART-")


def test_image_analyzer_determinism(valid_image_data):
    res1 = analyze_images(valid_image_data)
    res2 = analyze_images(valid_image_data)
    assert res1 == res2
    assert json.dumps(res1) == json.dumps(res2)
