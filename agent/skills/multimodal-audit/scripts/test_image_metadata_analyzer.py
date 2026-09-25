"""
Unit Test Suite for Image Technical Metadata Analyzer (test_image_metadata_analyzer.py)
========================================================================================
Tests dimensions, aspect ratio consistency, MIME compatibility, file size budgets,
EXIF location privacy, orientation flags, legacy formats, and duplicate hashes.
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

from image_metadata_analyzer import ImageMetadataAnalyzer, analyze_image_metadata
from multimodal_state import MultimodalCategoryResult, MultimodalFinding, Severity



# 1. Valid Metadata & Baseline Execution


def test_valid_image_metadata():
    data = {
        "images": [
            {
                "id": "img-001",
                "filename": "hero.jpg",
                "mime_type": "image/jpeg",
                "file_size_bytes": 450000,
                "width": 1920,
                "height": 1080,
                "aspect_ratio": 1.778,
                "orientation": "landscape",
                "format": "jpeg",
            }
        ]
    }
    result = analyze_image_metadata(data)
    assert result["category"] == "image_metadata"
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert len(result["findings"]) == 0
    assert result["metrics"]["images_with_metadata"] == 1


def test_no_metadata_evidence():
    result = analyze_image_metadata(None)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None


def test_explicit_no_images():
    result = analyze_image_metadata({"images": [], "images_checked": True})
    assert result["status"] == "passed"
    assert result["score"] == 100



# 2. Dimensions & Aspect Ratio Validation


def test_invalid_zero_or_negative_dimensions():
    data = {
        "images": [
            {"id": "img-invalid-dim", "width": 0, "height": -400}
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-002" for f in result["findings"])
    assert result["metrics"]["invalid_dimension_count"] == 1


def test_excessively_large_dimensions():
    data = {
        "images": [
            {"id": "img-huge", "width": 12000, "height": 8000}  # 96MP > 36MP threshold
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-003" for f in result["findings"])


def test_aspect_ratio_inconsistency_with_dimensions():
    data = {
        "images": [
            {"id": "img-ratio-mismatch", "width": 1000, "height": 500, "aspect_ratio": 1.0}  # 2.0 vs 1.0
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-006" for f in result["findings"])


def test_invalid_non_positive_aspect_ratio():
    data = {
        "images": [
            {"id": "img-bad-ratio", "aspect_ratio": -1.5}
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-005" for f in result["findings"])



# 3. MIME Type & File Extension Compatibility


def test_mime_type_extension_mismatch():
    data = {
        "images": [
            {"id": "img-mime-err", "filename": "photo.png", "mime_type": "image/jpeg"}
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-007" for f in result["findings"])
    assert result["metrics"]["mime_mismatch_count"] == 1


def test_extensionless_url_passes_cleanly():
    data = {
        "images": [
            {"id": "img-dynamic", "url": "https://example.com/api/image?id=123", "mime_type": "image/webp"}
        ]
    }
    result = analyze_image_metadata(data)
    assert not any(f["id"] == "MM-META-007" for f in result["findings"])



# 4. File Size Budgets & Performance


def test_severely_oversized_file_size():
    data = {
        "images": [
            {"id": "img-heavy", "file_size_bytes": 9_000_000}  # 9MB > 5MB threshold
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-004" for f in result["findings"])
    assert result["status"] == "failed"


def test_moderately_oversized_file_size_warning():
    data = {
        "images": [
            {"id": "img-warn", "file_size_bytes": 2_500_000}  # 2.5MB > 1MB warning
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-004" for f in result["findings"])
    assert result["status"] == "warning"



# 5. EXIF & Privacy Considerations


def test_exif_gps_location_data_detection():
    data = {
        "images": [
            {
                "id": "img-gps",
                "exif": {"GPSLatitude": "37.7749 N", "GPSLongitude": "122.4194 W"},
            }
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-009" for f in result["findings"])
    assert result["metrics"]["exif_gps_count"] == 1


def test_invalid_orientation_flag():
    data = {
        "images": [
            {"id": "img-ori", "orientation": "upside_down_diagonal"}
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-010" for f in result["findings"])


def test_legacy_format_recommendation():
    data = {
        "images": [
            {"id": "img-bmp", "filename": "graphic.bmp", "format": "bmp"}
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-013" for f in result["findings"])



# 6. Duplicate Metadata & Determinism


def test_duplicate_url_and_hash():
    data = {
        "images": [
            {"id": "img-1", "url": "https://example.com/a.jpg", "content_hash": "abc123hash"},
            {"id": "img-2", "url": "https://example.com/a.jpg", "content_hash": "abc123hash"},
        ]
    }
    result = analyze_image_metadata(data)
    assert any(f["id"] == "MM-META-012" for f in result["findings"])


def test_metadata_analyzer_determinism():
    data = {
        "images": [
            {"id": "img-1", "width": 800, "height": 600, "mime_type": "image/png"}
        ]
    }
    res1 = analyze_image_metadata(data)
    res2 = analyze_image_metadata(data)
    assert res1 == res2
    assert json.dumps(res1) == json.dumps(res2)
