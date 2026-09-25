"""
Unit Test Suite for Chart, Graph & Infographic Visual Content Analyzer (test_chart_infographic_analyzer.py)
=============================================================================================================
Tests bar/line/pie/scatter graphics, axis labels, legends, units, data consistency,
embedded tables, visual hierarchy, text equivalent compensation, and determinism.
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

from chart_infographic_analyzer import ChartInfographicAnalyzer, analyze_charts
from multimodal_state import MultimodalCategoryResult, MultimodalFinding, Severity



# 1. Valid Chart Types Baseline


def test_valid_bar_chart():
    data = {
        "charts": [
            {
                "id": "chart-001",
                "title": "Quarterly Revenue 2025",
                "chart_type": "bar",
                "has_title": True,
                "has_axis_labels": True,
                "has_legend": True,
                "has_text_equivalent": True,
                "labels": ["Q1", "Q2", "Q3", "Q4"],
                "values": [120, 150, 180, 220],
                "units": "USD (k)",
            }
        ]
    }
    result = analyze_charts(data)
    assert result["category"] == "chart_infographic"
    assert result["status"] == "passed"
    assert result["score"] == 100
    assert len(result["findings"]) == 0
    assert result["metrics"]["charts_count"] == 1


def test_valid_line_chart():
    data = {
        "charts": [
            {
                "id": "chart-line",
                "title": "Monthly Website Traffic Trend",
                "chart_type": "line",
                "has_title": True,
                "has_axis_labels": True,
                "has_legend": True,
                "has_text_equivalent": True,
                "labels": ["Jan", "Feb", "Mar", "Apr"],
                "values": [1000, 1200, 1450, 1800],
                "units": "Visitors",
            }
        ]
    }
    result = analyze_charts(data)
    assert result["status"] == "passed"
    assert result["score"] == 100


def test_valid_pie_chart():
    data = {
        "charts": [
            {
                "id": "chart-pie",
                "title": "Market Share Distribution",
                "chart_type": "pie",
                "has_title": True,
                "has_legend": True,
                "has_text_equivalent": True,
                "labels": ["Product A", "Product B", "Product C"],
                "values": [50, 30, 20],
            }
        ]
    }
    result = analyze_charts(data)
    # Pie charts do not require axis labels
    assert not any(f["id"] == "MM-CHART-003" for f in result["findings"])
    assert result["status"] == "passed"


def test_no_chart_evidence():
    result = analyze_charts(None)
    assert result["status"] == "insufficient_evidence"
    assert result["score"] is None


def test_explicit_no_charts():
    result = analyze_charts({"charts": [], "charts_checked": True})
    assert result["status"] == "passed"
    assert result["score"] == 100



# 2. Structural Requirements & Defect Detection


def test_missing_title_on_standalone_chart():
    data = {
        "charts": [
            {
                "id": "chart-no-title",
                "chart_type": "bar",
                "has_title": False,
                "has_axis_labels": True,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-002" for f in result["findings"])


def test_caption_compensates_for_missing_internal_title():
    data = {
        "charts": [
            {
                "id": "chart-captioned",
                "chart_type": "bar",
                "has_title": False,
                "caption": "Figure 1: Breakdown of annual operating expenses",
                "has_axis_labels": True,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert not any(f["id"] == "MM-CHART-002" for f in result["findings"])


def test_missing_axis_labels_on_bar_chart():
    data = {
        "charts": [
            {
                "id": "chart-no-axis",
                "chart_type": "bar",
                "has_title": True,
                "has_axis_labels": False,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-003" for f in result["findings"])
    assert any(f["severity"] == "high" for f in result["findings"])


def test_missing_legend_on_multi_series_chart():
    data = {
        "charts": [
            {
                "id": "chart-multi-series",
                "chart_type": "line",
                "multi_series": True,
                "has_legend": False,
                "has_title": True,
                "has_axis_labels": True,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-005" for f in result["findings"])


def test_unclear_units_on_numerical_chart():
    data = {
        "charts": [
            {
                "id": "chart-no-units",
                "chart_type": "column",
                "has_title": True,
                "has_axis_labels": True,
                "has_units": False,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-004" for f in result["findings"])



# 3. Data Consistency & Visual Density


def test_labels_and_values_count_mismatch():
    data = {
        "charts": [
            {
                "id": "chart-mismatch",
                "chart_type": "bar",
                "labels": ["Alpha", "Beta", "Gamma"],
                "values": [10, 20],  # 3 labels vs 2 values
                "has_title": True,
                "has_axis_labels": True,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-011" for f in result["findings"])


def test_pie_chart_slice_overcrowding():
    data = {
        "charts": [
            {
                "id": "chart-overcrowded-pie",
                "chart_type": "pie",
                "labels": [f"Category {i}" for i in range(16)],  # 16 slices > 10
                "values": [1] * 16,
                "has_title": True,
                "has_text_equivalent": True,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-013" for f in result["findings"])



# 4. Embedded Tables & Infographics


def test_embedded_raster_table_without_html_equivalent():
    data = {
        "visuals": [
            {
                "id": "table-img",
                "is_table": True,
                "has_text_equivalent": False,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-010" for f in result["findings"])


def test_complex_infographic_lacking_text_summary():
    data = {
        "visuals": [
            {
                "id": "info-001",
                "media_type": "infographic",
                "sections": ["Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6", "Step 7"],
                "has_text_equivalent": False,
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-009" for f in result["findings"])


def test_visual_only_dependency_finding():
    data = {
        "charts": [
            {
                "id": "chart-visual-only",
                "chart_type": "bar",
                "has_title": True,
                "has_axis_labels": True,
                "has_text_equivalent": False,
                "values": [10, 20, 30, 40, 50],
            }
        ]
    }
    result = analyze_charts(data)
    assert any(f["id"] == "MM-CHART-008" for f in result["findings"])



# 5. Determinism & Serialization


def test_chart_analyzer_determinism():
    data = {
        "charts": [
            {"id": "c1", "chart_type": "bar", "has_title": True, "has_axis_labels": True, "has_text_equivalent": True}
        ]
    }
    res1 = analyze_charts(data)
    res2 = analyze_charts(data)
    assert res1 == res2
    assert json.dumps(res1) == json.dumps(res2)
