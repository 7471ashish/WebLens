"""
Unit Test Suite for Image OCR & Graphic Text Analyzer (test_image_ocr_analyzer.py)
==================================================================================
Tests text extraction, confidence thresholds, partial OCR flags, text density,
spatial coordinates, OCR noise, language handling, and provider injection.
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

from image_ocr_analyzer import ImageOCRAnalyzer, analyze_ocr
from multimodal_state import MultimodalCategoryResult, MultimodalFinding, Severity



# 1. OCR Availability & Detection Baseline


def test_valid_ocr_text_detected():
    data = {
        "images": [
            {
                "id": "img-001",
                "ocr": {
                    "available": True,
                    "text": "Empowering creativity with Adobe tools",
                    "confidence": 0.97,
                    "language": "en",
                    "text_regions": [
                        {"text": "Empowering creativity", "confidence": 0.97, "x": 100, "y": 50, "width": 400, "height": 40}
                    ],
                },
            }
        ]
    }
    result = analyze_ocr(data)
    assert result["category"] == "ocr"
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert result["metrics"]["images_with_text"] == 1
    assert len(result["findings"]) == 0


def test_no_text_detected_clean_pass():
    data = {
        "images": [
            {
                "id": "img-scenery",
                "ocr": {"available": True, "detected": False, "text": ""},
            }
        ]
    }
    result = analyze_ocr(data)
    # Confirming no text exists should be a 100% pass without false failure
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert result["metrics"]["images_without_text"] == 1


def test_no_ocr_evidence_returns_insufficient_evidence():
    result = analyze_ocr(None)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None


def test_explicit_no_images():
    result = analyze_ocr({"images": [], "images_checked": True})
    assert result["status"] == "passed"
    assert result["score"] == 100



# 2. Confidence & Quality Thresholds


def test_low_ocr_confidence_triggers_finding():
    data = {
        "images": [
            {
                "id": "img-low-conf",
                "ocr": {"text": "Adbbe Phottoshop", "confidence": 0.35},
            }
        ]
    }
    result = analyze_ocr(data)
    assert any(f["id"] == "MM-OCR-001" for f in result["findings"])
    assert result["metrics"]["images_with_low_confidence"] == 1


def test_partial_ocr_completeness_triggers_finding():
    data = {
        "images": [
            {
                "id": "img-partial",
                "ocr": {"text": "Header only", "confidence": 0.95, "partial": True},
            }
        ]
    }
    result = analyze_ocr(data)
    assert any(f["id"] == "MM-OCR-002" for f in result["findings"])
    assert result["metrics"]["images_with_partial_ocr"] == 1


def test_text_heavy_image_triggers_warning():
    long_text = "word " * 60  # 60 words > 50 threshold
    data = {
        "images": [
            {
                "id": "img-dense",
                "ocr": {"text": long_text, "confidence": 0.95},
            }
        ]
    }
    result = analyze_ocr(data)
    assert any(f["id"] == "MM-OCR-003" for f in result["findings"])



# 3. Spatial & Noise Validation


def test_out_of_bounds_bounding_box_coordinates():
    data = {
        "images": [
            {
                "id": "img-coords",
                "width": 500,
                "height": 300,
                "ocr": {
                    "text": "Some text",
                    "confidence": 0.90,
                    "text_regions": [{"text": "Some text", "x": 800, "y": 600, "width": 200, "height": 50}],
                },
            }
        ]
    }
    result = analyze_ocr(data)
    assert any(f["id"] == "MM-OCR-004" for f in result["findings"])


def test_ocr_noise_artifact_detection():
    data = {
        "images": [
            {
                "id": "img-glitch",
                "ocr": {"text": "Adbbbbbbeeeeeee test artifact", "confidence": 0.85},
            }
        ]
    }
    result = analyze_ocr(data)
    assert any(f["id"] == "MM-OCR-005" for f in result["findings"])



# 4. Injected Provider Support


def test_injected_ocr_provider_execution():
    def mock_provider(item):
        return {"text": "Extracted text via mock engine", "confidence": 0.98}

    analyzer = ImageOCRAnalyzer(ocr_provider=mock_provider)
    result = analyzer.analyze({"images": [{"id": "img-prov"}]})
    assert result["status"] == "passed"
    assert result["metrics"]["images_with_text"] == 1


def test_injected_ocr_provider_failure_graceful_handling():
    def failing_provider(item):
        raise RuntimeError("OCR service timeout")

    analyzer = ImageOCRAnalyzer(ocr_provider=failing_provider)
    result = analyzer.analyze({"images": [{"id": "img-err"}]})
    assert result["status"] in ("insufficient_evidence", "error", "warning")
    assert result["evaluated"] is False or len(result["findings"]) >= 0



# 5. Language Handling & Determinism


def test_non_english_ocr_preservation():
    data = {
        "images": [
            {
                "id": "img-intl",
                "ocr": {"text": "Bonjour le monde", "confidence": 0.95, "language": "fr"},
            }
        ]
    }
    result = analyze_ocr(data)
    assert result["status"] == "passed"
    assert result["score"] == 100


def test_ocr_analyzer_determinism():
    data = {
        "images": [
            {"id": "img-1", "ocr": {"text": "Adobe Premiere Pro", "confidence": 0.95}}
        ]
    }
    res1 = analyze_ocr(data)
    res2 = analyze_ocr(data)
    assert res1 == res2
    assert json.dumps(res1) == json.dumps(res2)
